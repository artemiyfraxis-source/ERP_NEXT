"""ОБ.019: a job run from the machine card — «Выполнение задания» (owner 19.11.2026, plan «Станки вне проекта», S2).

A run is one «Передать на станок» of a job: a cutting-group job (group + cut length, the ETK path) or a «Ручная работа»
(production/manual_jobs.py). No table of its own: the run rides its write command (`production_machine_writes`,
purpose `transfer`) as `payload["run"]`; the words the command writes (`blocks`, `bits`) are not touched by it.
- Tagged when queued: `{kind, group_id, length_mm | job_id, label, quantity}`.
- Verified (the runner read every word back): `counter_start` = T.Finish (D649–D650, u32) from the machine's fresh
  snapshot at that moment; a manual job gets it too (`count_source` plc, `write_id`). No fresh snapshot → no start, the
  count is typed by hand with a mark.
- While it runs: done = D649 − counter_start; a counter below the start → «счётчик станка сброшен» (manual entry).
- Closed by «Завершить» (or a newer run of another job on the machine): `machine_count`, `counter_end`, `reset`,
  `closed_at`. A cutting run's count then pre-fills «Сдать группу» in «Мои задания» (the operator confirms, materials
  move only there); a manual run finishes its manual job.
A run of the same job again (after a correction) keeps the first `counter_start`: the pieces of both writes count."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import machine_writes as queue
from . import machines as rules

KEY = "run"
KINDS = ("cutting", "manual")
FINISHED_STATE = 107  # «Тираж готов»
FINISHED_ALARM = 5  # «Тираж готов» in the alarm word: normal, not a fault
RUNNING_STATES = (103, 104, 106)
FAILED = ("failed", "expired", "unknown", "cancelled")
LOOKBACK = 30  # the newest transfer writes of a machine searched for its open run
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


def info(row: queue.MachineWriteRow | None) -> dict | None:
    run = (row.payload or {}).get(KEY) if row is not None else None
    return run if isinstance(run, dict) and run.get("kind") in KINDS else None


def same_job(a: dict, b: dict) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    if a["kind"] == "manual":
        return a.get("job_id") == b.get("job_id")
    return a.get("group_id") == b.get("group_id") and a.get("length_mm") == b.get("length_mm")


def _set(row: queue.MachineWriteRow, run: dict) -> None:
    row.payload = {**row.payload, KEY: dict(run)}  # a new dict: the JSON column notices the change


def current(session: Session, equipment_id: UUID) -> queue.MachineWriteRow | None:
    """The newest run of the machine (open, closed or failed); None — no run yet."""
    for row in session.scalars(select(queue.MachineWriteRow).where(
            queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.purpose == "transfer")
            .order_by(queue.MachineWriteRow.created_at.desc()).limit(LOOKBACK)):
        if info(row) is not None:
            return row
    return None


def is_open(row: queue.MachineWriteRow | None) -> bool:
    run = info(row)
    return run is not None and not run.get("closed_at") and row.status not in FAILED


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
    elif start is None and not run.get("verified_at"):
        stage = "writing"
    elif reset:
        stage = "reset"
    elif not link_ok or start is None:
        stage = "nolink"
    elif done and (state == FINISHED_STATE or alarm == FINISHED_ALARM):
        stage = "finished"
    elif state in RUNNING_STATES or done:
        stage = "running"
    else:
        stage = "ready"
    return {"stage": stage, "counter_start": start, "counter": now, "done": done, "reset": reset,
            "state": state, "alarm": alarm, "total_set": rules._u32(words, 22)}


def verified(session: Session, row: queue.MachineWriteRow, now: datetime) -> dict | None:
    """The write of a run read back right: its counter start from the fresh snapshot. → the run, or None (no run)."""
    run = info(row)
    if run is None or run.get("verified_at"):
        return run
    run = dict(run)
    if run.get("counter_start") is None:  # a continued run keeps the start of its first write
        snapshot = session.get(rules.MachineSnapshotRow, row.equipment_id)
        run["counter_start"] = counter(rules.snapshot_words(snapshot)) \
            if rules.link_status(snapshot, now) == "ok" else None
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
                "machine_count": seen["done"], "counter_end": seen["counter"],
                "reset": bool(seen["reset"]) or seen["done"] is None})
    _set(row, run)
    return run


def supersede(session: Session, equipment_id: UUID, new: dict, now: datetime, actor_name: str) -> dict:
    """Before a new run is queued: the open run of the same job lends its counter start; an open run of another job
    is closed with its count. → the new run (with the inherited start, if any)."""
    row = current(session, equipment_id)
    if not is_open(row):
        return new
    old = info(row) or {}
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    link_ok = rules.link_status(snapshot, now) == "ok"
    words = rules.snapshot_words(snapshot)
    if same_job(old, new) and old.get("counter_start") is not None:
        _set(row, {**old, "closed_at": queue.moment(now), "closed_by": actor_name[:200],
                   "closed_why": "Передано на станок ещё раз", "continued": True})
        return {**new, "counter_start": old["counter_start"]}
    close(row, words, link_ok, now, actor_name, "Передано другое задание")
    return new


def view(row: queue.MachineWriteRow, words: dict[int, int], link_ok: bool) -> dict:
    run = info(row) or {}
    seen = progress(row, words, link_ok)
    text, tone = queue.STATUS_TEXTS[row.status]
    return {"id": str(row.id), "kind": run.get("kind"), "label": run.get("label", ""), "group_id": run.get("group_id"),
            "length_mm": run.get("length_mm"), "job_id": run.get("job_id"), "quantity": run.get("quantity"),
            "write": {"status": row.status, "status_text": text, "tone": tone, "error": row.error},
            "verified_at": run.get("verified_at"), "closed_at": run.get("closed_at"),
            "closed_why": run.get("closed_why", ""), "machine_count": run.get("machine_count"),
            "closed_reset": bool(run.get("reset")), "created_by": row.created_by_name,
            "created_at": queue.moment(row.created_at), **seen}


def tag(row: queue.MachineWriteRow, run: dict) -> None:
    """Mark a queued write as the start of a job run."""
    _set(row, run)
