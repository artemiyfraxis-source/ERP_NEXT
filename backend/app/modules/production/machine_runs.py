"""ОБ.019: a job run from the machine card — «Выполнение задания» (owner 19.11.2026, plan «Станки вне проекта», S2).

A run is one confirmed batch: a cutting-group job (group + cut length, the ETK path), a standalone wire batch,
or a legacy «Ручная работа» (production/manual_jobs.py). No table of its own: the run rides its write command (`production_machine_writes`,
purpose `transfer`) as `payload["run"]`; the words the command writes (`blocks`, `bits`) are not touched by it.
- Tagged when queued: `{kind, group_id, length_mm | job_id, label}` plus the quantity it was written with: `counter`
  (D649 the target was computed from), `target` (D22 = counter + pieces), `pieces`, `counter_start` (= counter, or the
  first start of the same job written again) and `quantity` (pieces counted from that start).
- Verified (the runner read every word back): the first snapshot reported after the runner took the write must still
  show D649 = `counter` (`check` ok). Until it comes (at most `PENDING_WAIT` after the answer) the check is `pending`
  («проверяется», no «Пуск») and is re-evaluated on the next card / control read (machine_writes_api.settle_run); a
  counter that moved or still could not be read after that (`moved` / `unknown`) makes the run void — «передайте заново», no
  «Пуск». A manual job is counted by the machine only after an ok check (`count_source` plc, `write_id`).
- While it runs: done = D649 − counter_start; a counter below the start → «счётчик станка сброшен» (manual entry).
- «Пуск» from ERP only for an open, checked run whose target is still on the machine (`start_blocker`): a fresh snapshot
  taken after the last write must show D22 = `target` and 0 < D22 − D649 ≤ the pieces left — a «Тираж» written from
  the settings, a closed run or a counter that moved never start the machine. The machine must also still hold the
  job itself — its length D32, order and product texts as the run wrote them (`expect`): a wire picked by hand over the
  job's settings would otherwise be counted into the job.
- Closed by «Завершить» (or a newer run of another job on the machine): `machine_count`, `counter_end`, `reset` (the
  counter went back), `unread` (no fresh counter: count unknown, not a reset), `closed_at`. Never while it cuts.
  Every change of a run's payload locks its write row first (`lock`): a close and a re-send never both count it. A cutting run's count then pre-fills «Сдать группу» in «Мои задания» (the operator confirms, materials
  move only there); a manual run finishes its manual job.
A run of the same job again (after a correction) keeps the first `counter_start`: the pieces of both writes count."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.clock import MOSCOW

from . import machine_writes as queue
from . import machines as rules

KEY = "run"
KINDS = ("cutting", "manual", "wire")
FINISHED_STATE = 107  # «Тираж готов»
FINISHED_ALARM = 5  # «Тираж готов» in the alarm word: normal, not a fault
RUNNING_STATES = (103, 104, 106)
FAILED = ("failed", "expired", "unknown", "cancelled")
LOOKBACK = 30  # the newest transfer writes of a machine searched for its open run
PENDING_WAIT = timedelta(seconds=10)  # how long a finished write waits for a snapshot reported after it was taken
COUNTER_RESET = "Счётчик станка сброшен — введите количество вручную"
NO_COUNTER = "Нет свежих данных станка — количество вводится вручную"


def counter(words: dict[int, int]) -> int | None:
    """T.Finish, D649 low word + D650 high word."""
    return rules._u32(words, 649)


def length_key(value: object) -> str:
    """«325», «325,00», «325.0» → «325»: one cut length, however it was written."""
    try:
        number = Decimal(str(value if value is not None else "").strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return ""
    return format(number.normalize(), "f") if number.is_finite() else ""


def cutting(group_id: UUID, length_mm: object, label: str, quantity: int | None) -> dict:
    return {"kind": "cutting", "group_id": str(group_id), "length_mm": length_key(length_mm), "label": label,
            "quantity": quantity}


def manual(job_id: UUID, label: str, quantity: int | None) -> dict:
    return {"kind": "manual", "job_id": str(job_id), "label": label, "quantity": quantity}


def wire(wire: str, length_mm: object, quantity: int | None) -> dict:
    """A batch entered at the machine, with no cutting group or manual-work document."""
    length = length_key(length_mm)
    return {"kind": "wire", "wire": wire, "length_mm": length, "quantity": quantity,
            "label": f"{wire} · {length.replace('.', ',')} мм"}


def info(row: queue.MachineWriteRow | None) -> dict | None:
    run = (row.payload or {}).get(KEY) if row is not None else None
    return run if isinstance(run, dict) and run.get("kind") in KINDS else None


# What says which job the machine is set for: its length and the order / product texts on the operator panel.
IDENTITY_KEYS = ("length_mm", "order_number", "product")


def expect(values: dict, *, empty: bool = False) -> dict[str, int]:
    """The words the machine holds for the job's identity once the run's write went through: {address: word}. An empty
    value (not written; `empty` — a text cleared on purpose, zero words) or one the machine refuses (the write fails
    anyway) is not expected."""
    words: dict[str, int] = {}
    for key in IDENTITY_KEYS:
        if key not in values:
            continue
        value = values[key]
        if value is None or (value == "" and not empty):
            continue
        try:
            field, encoded, _value = rules.encode_field(key, value)
        except rules.MachineWriteInvalid:
            continue
        words.update({str(field.address + index): word for index, word in enumerate(encoded)})
    return words


def same_job(a: dict, b: dict) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    if a["kind"] == "manual":
        return a.get("job_id") == b.get("job_id")
    if a["kind"] == "wire":
        return str(a.get("wire", "")).casefold() == str(b.get("wire", "")).casefold()
    return a.get("group_id") == b.get("group_id") and a.get("length_mm") == b.get("length_mm")


def _set(row: queue.MachineWriteRow, run: dict) -> None:
    row.payload = {**row.payload, KEY: dict(run)}  # a new dict: the JSON column notices the change


def lock(session: Session, row: queue.MachineWriteRow) -> queue.MachineWriteRow:
    """Re-read a run's write row under a row lock (PostgreSQL `FOR UPDATE`) before its payload changes: a close and a
    re-send of the same job in parallel wait for each other and the second sees the first one's result."""
    session.flush()
    session.refresh(row, with_for_update=True)
    return row


def current(session: Session, equipment_id: UUID, *, for_update: bool = False) -> queue.MachineWriteRow | None:
    """The newest run of the machine (open, closed or failed); None — no run yet. `for_update` — locked, to change it."""
    for row in session.scalars(select(queue.MachineWriteRow).where(
            queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.purpose == "transfer")
            .order_by(queue.MachineWriteRow.created_at.desc()).limit(LOOKBACK)):
        if info(row) is not None:
            return lock(session, row) if for_update else row
    return None


def aware(value: datetime | None) -> datetime | None:
    """A stored moment with its zone: SQLite (tests, preview) gives it back naive in shop time, PostgreSQL aware."""
    return None if value is None else value.replace(tzinfo=MOSCOW) if value.tzinfo is None else value


def fresh_words(session: Session, equipment_id: UUID, now: datetime,
                after: datetime | None) -> dict[int, int] | None:
    """The machine's words from a snapshot on the link, at most `COUNTER_FRESH` old and reported at or after `after`
    (the write it has to show); None — no such snapshot."""
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    if snapshot is None or rules.link_status(snapshot, now) != "ok" \
            or rules.since(snapshot.reported_at, now) > queue.COUNTER_FRESH:
        return None
    if after is not None and aware(snapshot.reported_at) < aware(after):
        return None
    return rules.snapshot_words(snapshot)


VOID_CHECKS = ("moved", "unknown")
MOVED = "Счётчик станка сдвинулся, пока задание записывалось, — передайте задание заново"
UNREAD = "При проверке записи не было свежего счётчика станка — передайте задание заново"
RESET_START = ("Счётчик станка сброшен после записи тиража — передайте задание заново, иначе станок не остановится "
               "на нужном числе")


def is_open(row: queue.MachineWriteRow | None) -> bool:
    run = info(row)
    return (run is not None and not run.get("closed_at") and row.status not in FAILED
            and run.get("check") not in VOID_CHECKS)


def with_quantity(run: dict, quantity: dict | None) -> dict:
    """The quantity the write carries (production/machine_writes.quantity_as_target) → the run's counter, target and
    start; a continued run keeps its first start and counts the pieces done before plus the new ones."""
    run = dict(run)
    if quantity is None:
        return run
    run.update(counter=quantity["counter"], target=quantity["target"], pieces=quantity["pieces"])
    if run.get("counter_start") is None:
        run["counter_start"] = quantity["counter"]
    run["quantity"] = quantity["counter"] - run["counter_start"] + quantity["pieces"]
    return run


def progress(row: queue.MachineWriteRow, words: dict[int, int], link_ok: bool) -> dict:
    """What the card shows of a run now: done of planned, the stage and why the count is manual."""
    run = info(row) or {}
    state, alarm = words.get(766), words.get(782)
    start = run.get("counter_start")
    now = counter(words) if link_ok else None
    reset = start is not None and now is not None and now < start
    done = now - start if start is not None and now is not None and not reset else None
    if run.get("closed_at"):
        stage = "closed"
    elif row.status in queue.OPEN:
        stage = "writing"
    elif row.status in FAILED:
        stage = "failed"
    elif run.get("check") in VOID_CHECKS:
        stage = "moved"
    elif run.get("check") == "pending":
        stage = "writing"  # read back right, the counter check waits for a fresh snapshot
    elif start is None and not run.get("verified_at"):
        stage = "writing"
    elif reset:
        stage = "reset"
    elif not link_ok or start is None:
        stage = "nolink"
    elif done and (state == FINISHED_STATE or alarm == FINISHED_ALARM
                   or (run.get("quantity") is not None and done >= run["quantity"])):
        stage = "finished"
    elif state in RUNNING_STATES or done:
        stage = "running"
    else:
        stage = "ready"
    return {"stage": stage, "counter_start": start, "counter": now, "done": done, "reset": reset,
            "state": state, "alarm": alarm, "total_set": rules._u32(words, 22)}


def pending(row: queue.MachineWriteRow | None) -> bool:
    """A run whose write was read back right and whose counter check is not settled yet."""
    run = info(row)
    return run is not None and row.status == "done" and not run.get("verified_at")


def verified(session: Session, row: queue.MachineWriteRow, now: datetime) -> dict | None:
    """The write of a run read back right: its counter start from the fresh snapshot. → the run when its check settled
    by this call; None — no run, settled before, or still `pending` (no snapshot after the write yet, within
    `PENDING_WAIT` of the answer)."""
    if not pending(row):
        return None
    lock(session, row)
    if not pending(row):
        return None
    run = dict(info(row))
    # Only a snapshot reported after the runner took the write says what D649 was while it wrote: an older one may
    # predate a piece cut meanwhile.
    snapshot = session.get(rules.MachineSnapshotRow, row.equipment_id)
    fresh = (row.taken_at is not None and snapshot is not None and rules.link_status(snapshot, now) == "ok"
             and aware(snapshot.reported_at) >= aware(row.taken_at))
    finished = aware(row.finished_at) or aware(now)
    if not fresh and aware(now) - finished < PENDING_WAIT:
        if run.get("check") != "pending":
            _set(row, {**run, "check": "pending"})
            session.flush()
        return None
    seen = counter(rules.snapshot_words(snapshot)) if fresh else None
    run["counter_seen"] = seen
    if run.get("counter") is not None:
        # The target D22 was computed from `counter`: anything else on the machine now makes it wrong.
        run["check"] = "unknown" if seen is None else "ok" if seen == run["counter"] else "moved"
    else:
        run.pop("check", None)
        if run.get("counter_start") is None:
            run["counter_start"] = seen
    run["verified_at"] = queue.moment(now)
    _set(row, run)
    session.flush()
    return run


def close(row: queue.MachineWriteRow, words: dict[int, int], link_ok: bool, now: datetime, actor_name: str,
          why: str = "Завершено") -> dict:
    """Close a run with what the counter says now. → the closed run."""
    run = dict(info(row) or {})
    seen = progress(row, words, link_ok)
    run.update({"closed_at": queue.moment(now), "closed_by": " ".join(actor_name.split())[:200], "closed_why": why,
                "machine_count": seen["done"], "counter_end": seen["counter"], "reset": bool(seen["reset"]),
                "unread": seen["done"] is None and not seen["reset"]})
    _set(row, run)
    return run


def supersede(session: Session, equipment_id: UUID, new: dict, now: datetime, actor_name: str) -> dict:
    """Before a new run is queued: the open run of the same job lends its counter start; an open run of another job
    is closed with its count. → the new run (with the inherited start, if any)."""
    row = current(session, equipment_id, for_update=True)
    if not is_open(row):
        return new
    old = info(row) or {}
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    link_ok = rules.link_status(snapshot, now) == "ok"
    words = rules.snapshot_words(snapshot)
    if same_job(old, new) and old.get("counter_start") is not None \
            and progress(row, words, link_ok)["stage"] not in ("reset", "nolink"):
        _set(row, {**old, "closed_at": queue.moment(now), "closed_by": actor_name[:200],
                   "closed_why": "Передано на станок ещё раз", "continued": True})
        carried = {"expect": old["expect"]} if "expect" not in new and old.get("expect") is not None else {}
        return {**new, "counter_start": old["counter_start"], **carried}
    close(row, words, link_ok, now, actor_name, "Передано другое задание")
    return new


def follow(session: Session, equipment_id: UUID, values: dict) -> None:
    """An edit of the job's length, order or product written through ERP while its run is open (a size corrected on the
    drawing, a «Проба и поправка» length): the run expects the new words from now on, so «Пуск» is not refused for
    ERP's own edit. A run without `expect` (written before the check) stays unchecked."""
    row = current(session, equipment_id, for_update=True)
    run = info(row)
    if not is_open(row) or run.get("expect") is None:
        return
    changed = expect(values, empty=True)
    if changed:
        _set(row, {**run, "expect": {**run["expect"], **changed}})


def view(row: queue.MachineWriteRow, words: dict[int, int], link_ok: bool) -> dict:
    run = info(row) or {}
    seen = progress(row, words, link_ok)
    text, tone = queue.STATUS_TEXTS[row.status]
    if run.get("check") == "pending":
        text, tone = "Проверяется по счётчику станка", "info"
    return {"id": str(row.id), "kind": run.get("kind"), "label": run.get("label", ""), "group_id": run.get("group_id"),
            "length_mm": run.get("length_mm"), "job_id": run.get("job_id"), "wire": run.get("wire"),
            "quantity": run.get("quantity"),
            "write": {"status": row.status, "status_text": text, "tone": tone, "error": row.error},
            "verified_at": run.get("verified_at"), "closed_at": run.get("closed_at"),
            "closed_why": run.get("closed_why", ""), "machine_count": run.get("machine_count"),
            "closed_reset": bool(run.get("reset")), "closed_unread": bool(run.get("unread")),
            "created_by": row.created_by_name,
            "created_at": queue.moment(row.created_at), **seen}


def tag(row: queue.MachineWriteRow, run: dict) -> None:
    """Mark a queued write as the start of a job run."""
    _set(row, run)


def done_before(session: Session, equipment_id: UUID, new: dict, now: datetime) -> int:
    """Pieces the open run of this same job has already made (a re-transfer writes only the rest); 0 — none."""
    row = current(session, equipment_id)
    if not is_open(row) or not same_job(info(row) or {}, new):
        return 0
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    seen = progress(row, rules.snapshot_words(snapshot), rules.link_status(snapshot, now) == "ok")
    return seen["done"] or 0 if seen["stage"] not in ("reset", "nolink") else 0


NO_JOB = "Нет подготовленного тиража — выберите провод или задание и подтвердите загрузку на станок"
WRITING = "Задание ещё записывается на станок — дождитесь результата"
CHECKING = "Запись задания проверяется по счётчику станка — повторите «Пуск» через секунду"
NOT_FRESH = "Нет свежих данных станка после записи задания — проверьте связь и повторите Пуск через секунду"
MISMATCH = "Тираж на станке не совпадает с заданием — передайте задание заново"
TOO_MANY = "Тираж на станке больше, чем осталось по заданию, — передайте задание заново"
DONE_ALREADY = "Тираж задания уже выполнен — завершите задание или передайте новое"
CUTTING = "Станок режет — сначала нажмите «Стоп», потом завершайте задание"
CLOSE_CHECKING = "Запись задания ещё проверяется по счётчику станка — повторите через секунду"


def other_setup(label: str) -> str:
    return f"Станок настроен не на задание «{label}» — выберите задание заново или закройте его"


class RunRefused(Exception):
    """The run may not change now (409): the machine is cutting."""


def cutting_now(words: dict[int, int], link_ok: bool) -> bool:
    """The machine is cutting (or feeding) now: a run is not closed under it."""
    return link_ok and words.get(766) in RUNNING_STATES


def start_blocker(session: Session, equipment_id: UUID, now: datetime) -> str:
    """Why «Пуск» from ERP must not go ('' — it may): only an open run whose write was checked starts the machine, and
    only while a fresh snapshot after the last write shows its target D22 with 0 < D22 − D649 ≤ the pieces left and the
    job's length, order and product (`expect`). A «Тираж» written from the settings, a closed or void run, a counter
    moved or reset — the machine would not stop at the job's count; another wire set by hand — it would cut that."""
    row = current(session, equipment_id)
    run = info(row)
    if run is None or run.get("closed_at") or row.status in FAILED:
        return NO_JOB
    if row.status in queue.OPEN:
        return WRITING
    if run.get("check") == "moved":
        return MOVED
    if run.get("check") == "pending":
        return CHECKING
    if run.get("check") != "ok" or run.get("target") is None or run.get("counter_start") is None:
        return UNREAD
    last = session.scalar(select(queue.MachineWriteRow.finished_at).where(
        queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.purpose != queue.CONTROL,
        queue.MachineWriteRow.finished_at.is_not(None)).order_by(queue.MachineWriteRow.finished_at.desc()).limit(1))
    words = fresh_words(session, equipment_id, now, last)
    if words is None:
        return NOT_FRESH
    seen = progress(row, words, True)
    if seen["stage"] == "reset":
        return RESET_START
    # A wire picked by hand (or a length / order typed) after the job's write: the machine cuts something else.
    if any(words.get(int(address)) != word for address, word in (run.get("expect") or {}).items()):
        return other_setup(run.get("label", ""))
    target, now_count = rules._u32(words, 22), seen["counter"]
    if target is None or now_count is None or seen["done"] is None:
        return NOT_FRESH
    if target != run["target"]:
        return MISMATCH
    left = target - now_count
    if left <= 0:
        return DONE_ALREADY
    if run.get("quantity") is None or left > run["quantity"] - seen["done"]:
        return TOO_MANY
    return ""


def close_for_manual(session: Session, equipment_id: UUID, job_id: UUID, now: datetime, actor_name: str,
                     why: str) -> dict | None:
    """A manual job finished or cancelled from its own window closes its open run on the machine. → the closed run.
    RunRefused while the machine cuts it: «Стоп» first."""
    row = current(session, equipment_id, for_update=True)
    run = info(row)
    if not is_open(row) or run.get("kind") != "manual" or run.get("job_id") != str(job_id):
        return None
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    words, link_ok = rules.snapshot_words(snapshot), rules.link_status(snapshot, now) == "ok"
    if cutting_now(words, link_ok):
        raise RunRefused(CUTTING)
    if run.get("check") == "pending":
        raise RunRefused(CLOSE_CHECKING)
    return close(row, words, link_ok, now, actor_name, why)
