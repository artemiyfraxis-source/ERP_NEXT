"""ОБ.019: writing parameters from ERP (plan «Станки», slice 4; owner 19.11.2026) — commands and machine corrections.

Commands (migration 0195, `production_machine_writes`). ERP never talks to the PLC: a write is queued here and the
command runner on the machine PC (`tools/machine-agent/ob019-writer.ps1`, pull transport like the bridge, the same
`machine:report` token and PC binding) claims it, checks the machine state itself, writes through the local Modbus
service mbd.ps1 (which refuses while PXS_HMI holds COM8) and reports the words it read back. ERP compares them with
what it asked for: equal → «done», different → «failed» with the registers that differ.
- queued → taken → done | failed; queued → expired (not claimed within `COMMAND_TTL` — never runs late: the state it
  was confirmed against may be gone); taken → failed («Агент не сообщил результат») after `RESULT_WAIT`.
- One open command per machine (UNIQUE partial index): a single controller, a second write waits for the first.
- Allowed only while the machine is idle: state 102 «Готов, ждёт пуска» or 107 «Тираж готов» in a fresh snapshot with
  the link «На связи» (PXS_HMI closed); the runner re-checks D766 right before writing.

Runner versions (19.11.2026): the runner 1.2.0 writes the other HMI pages and switch bits (mbd `setbit`); 1.1.0
ignores `bits` and refuses the new words. The runner names its version (`runner_version`) when it asks for a command;
a command it cannot run whole is never handed to it — it fails at once with `RUNNER_OUTDATED`, nothing is written. The
version last seen per machine is kept in memory (the runner asks twice a second; no table needed) for the card hint.

Machine commands Стоп / Сброс / Пуск / Сброс аварии (purpose `control`, column `command`, migration 0197) ride the
same queue; their rules are in production/machine_control.py: 5 s to be claimed, Stop first and exempt from the one-open
index (it may queue while a write runs, it cancels what is queued), no answer → «unknown», never assumed done.
«Закрыть / Открыть пульт на ПК» (`hmi_close` / `hmi_open`, migration 0211) are machine commands too; they need the
runner 1.4.0 and get `HMI_RESULT_WAIT` for their answer (a graceful close waits up to 15 s for the panel).

Length correction uses the PLC: runner 1.5.0 writes measured D658, pulses D600.6 and reads signed D54. D32 stays
the job length. The old length offset in `production_machine_offsets` is retained as history but is never applied.
Other parameter corrections still use the stored ERP offset in 0,01 мм, added to the ETK nominal. The ETK side goes
through the ETK revision path in app/workflows/machine_writes_api.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.clock import MOSCOW
from app.shared.persistence import Base

from . import machines as rules

COMMAND_TTL = timedelta(seconds=10)
RESULT_WAIT = timedelta(seconds=30)
IDLE_STATES = (102, 107)
STATUSES = ("queued", "taken", "done", "failed", "expired", "unknown", "cancelled")
OPEN = ("queued", "taken")
PURPOSES = ("settings", "transfer", "control")
CONTROL = "control"
STOP = "stop"
HMI_COMMANDS = ("hmi_close", "hmi_open")
COMMANDS = ("stop", "reset", "start", "alarm_clear", *HMI_COMMANDS)
CONTROL_TTL = timedelta(seconds=5)  # a machine command older than this by the server clock never runs
CONTROL_RESULT_WAIT = timedelta(seconds=15)  # taken without an answer → «Результат неизвестен»
HMI_RESULT_WAIT = timedelta(seconds=45)  # the runner waits up to 15 s for the panel to close or start, then reports
CONTROL_TOO_OLD = "ПК станка не забрал команду за 5 с — она не выполнялась"
CONTROL_NO_ANSWER = "ПК станка не сообщил результат — проверьте станок"
CANCELLED_BY_STOP = "Отменена: нажат «Стоп»"
_SQL = lambda values: ", ".join(repr(value) for value in values)  # noqa: E731
OPEN_WHERE = f"status IN ({_SQL(OPEN)}) AND (command IS NULL OR command <> '{STOP}')"
STOP_WHERE = f"status = 'queued' AND command = '{STOP}'"
STATUS_TEXTS = {"queued": ("Ждёт агента станка", "info"), "taken": ("Записывается", "info"),
                "done": ("Записано и проверено", "ok"), "failed": ("Не записано", "danger"),
                "expired": ("Не записано: агент не забрал команду за 10 с", "warn"),
                "unknown": ("Результат неизвестен", "danger"), "cancelled": ("Отменено стопом", "neutral")}
CONTROL_TEXTS = {"queued": ("Передаётся на станок", "info"), "taken": ("Выполняется", "info"),
                 "done": ("Выполнена", "ok"), "failed": ("Не выполнена", "danger"),
                 "expired": ("Не выполнена — опоздала", "warn"), "unknown": ("Результат неизвестен", "danger"),
                 "cancelled": ("Отменена стопом", "neutral")}
# Wire parameters a machine correction may shift (keys of production/machines.py OB019_FIELDS, ×0,01 мм).
OFFSET_KEYS = ("length_mm", "strip_front_mm", "pull_front_mm", "strip_back_mm", "pull_back_mm", "diameter_mm",
               "knife_back_mm")
OFFSET_LIMIT = 10000  # ±100,00 мм
STEP = Decimal("0.01")
RUNNER_NEEDED = "1.2.0"
RUNNER_OUTDATED = "Служба записи на ПК станка устарела — обновите (нужна 1.2.0)"
# The feed-roller lift switch (machines.OB019_FEED_WHEEL_ADDRESS) is written by the runner 1.3.0 or newer.
RUNNER_FEED_WHEEL = "1.3.0"
RUNNER_FEED_WHEEL_OUTDATED = "Служба записи на ПК станка устарела — обновите (нужна 1.3.0)"
# The runner 1.4.0 (20.11.2026): «Закрыть / Открыть пульт на ПК» and the inner layer words D44..D53 (mbd 1.4.0 writes them).
RUNNER_HMI = "1.4.0"
RUNNER_CORRECTION = "1.5.0"
LENGTH_CORRECTION = "length_correction"
RUNNER_HMI_OUTDATED = "Служба записи на ПК станка устарела — обновите (нужна 1.4.0)"
RUNNER_WORDS: dict[int, str] = {address: RUNNER_HMI for address in range(44, 54)}
# The words the runner 1.1.0 writes (ob019-writer.ps1 before 19.11.2026): mode, job and quantities, wire and strip,
# marking texts, order number and product. Anything else, and any switch bit, needs `RUNNER_NEEDED`.
LEGACY_WORDS = frozenset({20, *range(22, 29), *range(32, 44), *range(440, 485), *range(940, 970)})
RUNNER_SEEN = timedelta(seconds=60)
_runners: dict[UUID, tuple[str, datetime]] = {}


def _version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(text or "").strip().split("."))
    except ValueError:
        return None


def runner_current(version: str | None, needed: str = RUNNER_NEEDED) -> bool:
    """The runner may write the other HMI pages and switch bits: its version is `needed` (1.2.0) or newer."""
    parsed = _version(version or "")
    return parsed is not None and parsed >= _version(needed)


def field_needs_runner(field: rules.SettingField) -> bool:
    return field.kind == "bit0" or any(address not in LEGACY_WORDS for address in rules.field_words(field))


def runner_needed_for(bits: list[dict], addresses) -> str | None:
    """The oldest runner version that runs this write whole; None — any (1.1.0 does it)."""
    addresses = list(addresses)
    newest = [RUNNER_WORDS[address] for address in addresses if address in RUNNER_WORDS]
    if any(int(bit["addr"]) == rules.OB019_FEED_WHEEL_ADDRESS for bit in bits):
        newest.append(RUNNER_FEED_WHEEL)
    if newest:
        return max(newest, key=_version)
    if bits or any(address not in LEGACY_WORDS for address in addresses):
        return RUNNER_NEEDED
    return None


def runner_refusal(needed: str) -> str:
    if needed == RUNNER_CORRECTION:
        return "Служба записи на ПК станка устарела — обновите (нужна 1.5.0)"
    return {RUNNER_FEED_WHEEL: RUNNER_FEED_WHEEL_OUTDATED, RUNNER_HMI: RUNNER_HMI_OUTDATED}.get(needed, RUNNER_OUTDATED)


def runner_needed_for_row(row: "MachineWriteRow") -> str | None:
    if row.payload.get(LENGTH_CORRECTION):
        return RUNNER_CORRECTION
    if row.purpose == CONTROL:
        return RUNNER_HMI if row.command in HMI_COMMANDS else None
    return runner_needed_for(list(row.payload.get("bits") or []), expected_words(row))


def needs_runner(row: "MachineWriteRow") -> bool:
    """A write the runner 1.1.0 could do only in part (bits) or not at all (new words)."""
    return runner_needed_for_row(row) is not None


def note_runner(equipment_id: UUID, version: str | None, now: datetime) -> None:
    _runners[equipment_id] = (str(version or ""), now)


def runner_outdated(equipment_id: UUID, now: datetime, needed: str = RUNNER_NEEDED) -> bool:
    """The runner asked recently and is older than `needed` (no version = 1.1.0); not seen — not known."""
    seen = _runners.get(equipment_id)
    if seen is None or rules.since(seen[1], now) > RUNNER_SEEN:
        return False
    return not runner_current(seen[0], needed)


class MachineCommandRefused(Exception):
    """The machine may not be written now (409): not idle, no link, PXS_HMI open, another write in progress."""


class MachineWriteRow(Base):
    __tablename__ = "production_machine_writes"
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(item) for item in STATUSES)})",
                        name="ck_production_machine_writes_status"),
        CheckConstraint(f"purpose IN ({', '.join(repr(item) for item in PURPOSES)})",
                        name="ck_production_machine_writes_purpose"),
        CheckConstraint("status <> 'queued' OR (taken_at IS NULL AND finished_at IS NULL)",
                        name="ck_production_machine_writes_queued"),
        CheckConstraint("status <> 'taken' OR (taken_at IS NOT NULL AND finished_at IS NULL)",
                        name="ck_production_machine_writes_taken"),
        CheckConstraint("status IN ('queued', 'taken') OR finished_at IS NOT NULL",
                        name="ck_production_machine_writes_finished"),
        CheckConstraint("trim(summary) <> ''", name="ck_production_machine_writes_summary"),
        CheckConstraint(f"(purpose = '{CONTROL}') = (command IS NOT NULL)", name="ck_production_machine_writes_control"),
        CheckConstraint(f"command IS NULL OR command IN ({_SQL(COMMANDS)})", name="ck_production_machine_writes_command"),
        # One controller: one open command per machine, except a Stop (it may queue while a write or a Start runs).
        Index("ux_production_machine_writes_open", "equipment_id", unique=True,
              sqlite_where=text(OPEN_WHERE), postgresql_where=text(OPEN_WHERE)),
        Index("ux_production_machine_writes_stop", "equipment_id", unique=True,
              sqlite_where=text(STOP_WHERE), postgresql_where=text(STOP_WHERE)),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("production_machine_links.equipment_id", ondelete="CASCADE",
                         name="fk_production_machine_writes_link"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    # Machine command of purpose `control` (stop / reset / start / alarm_clear / hmi_close / hmi_open); NULL for writes.
    command: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # {"blocks": [{"addr": 32, "values": [32500, 0]}], "bits": [{"addr": 400, "bit": 0, "value": 1}],
    #  "fields": [{"key", "label", "before", "after", "unit"}]} — "bits" (switches, mbd `setbit`) since 19.11.2026.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readback: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")


class MachineOffsetRow(Base):
    __tablename__ = "production_machine_offsets"
    __table_args__ = (
        CheckConstraint(f"parameter IN ({', '.join(repr(item) for item in OFFSET_KEYS)})",
                        name="ck_production_machine_offsets_parameter"),
        CheckConstraint(f"offset_hundredths BETWEEN {-OFFSET_LIMIT} AND {OFFSET_LIMIT}",
                        name="ck_production_machine_offsets_range"),
        CheckConstraint("version >= 1", name="ck_production_machine_offsets_version"),
    )

    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_production_machine_offsets_equipment"),
        primary_key=True)
    parameter: Mapped[str] = mapped_column(String(32), primary_key=True)
    offset_hundredths: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    updated_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass(frozen=True)
class FieldWrite:
    key: str
    value: object


def _plain(value: object) -> str:
    if isinstance(value, bool):
        return "Вкл" if value else "Выкл"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f").replace(".", ",") if value != value.to_integral_value() \
            else format(value.quantize(Decimal(1)), "f")
    if isinstance(value, float):
        return _plain(Decimal(str(value)))
    return "" if value is None else str(value)


def plan_writes(writes: list[FieldWrite], words: dict[int, int]) -> tuple[list[dict], list[dict], list[dict]]:
    """Field values → contiguous register blocks (one bus write each), switch bits (one read-modify-write each on the
    PC — the intent only, never a word from the snapshot) and the shown before → after of every field.
    → (blocks, bits, shown)."""
    if not writes:
        raise rules.MachineWriteInvalid("Нечего записывать")
    seen: set[str] = set()
    targets: dict[int, int] = {}
    bits: list[dict] = []
    shown = []
    for item in writes:
        if item.key in seen:
            raise rules.MachineWriteInvalid("Параметр указан дважды")
        seen.add(item.key)
        field, values, value = rules.encode_field(item.key, item.value)
        if field.kind == "bit0":
            if any(bit["addr"] == field.address for bit in bits) or field.address in targets:
                raise rules.MachineWriteInvalid(f"{field.label}: этот регистр (D{field.address}) уже записывается "
                                                "другим параметром — оставьте один")
            bits.append({"addr": field.address, "bit": 0, "value": values[0]})
            values = []
        for offset, word in enumerate(values):
            if field.address + offset in targets or any(bit["addr"] == field.address + offset for bit in bits):  # D103: «Скорость намотки» and «Передний прижим» are one register
                raise rules.MachineWriteInvalid(f"{field.label}: этот регистр (D{field.address + offset}) уже "
                                                "записывается другим параметром — оставьте один")
            targets[field.address + offset] = word
        before = rules.field_value(item.key, words)
        shown.append({"key": item.key, "label": field.label, "unit": field.unit, "before": _plain(before),
                      "after": _plain(value)})
    blocks: list[dict] = []
    for address in sorted(targets):
        if blocks and blocks[-1]["addr"] + len(blocks[-1]["values"]) == address and len(blocks[-1]["values"]) < 60:
            blocks[-1]["values"].append(targets[address])
        else:
            blocks.append({"addr": address, "values": [targets[address]]})
    return blocks, sorted(bits, key=lambda bit: bit["addr"]), shown


def result_wait(row: MachineWriteRow) -> timedelta:
    """How long a taken machine command may stay without an answer before it reads «Результат неизвестен»."""
    return HMI_RESULT_WAIT if row.command in HMI_COMMANDS else CONTROL_RESULT_WAIT


def _expire(session: Session, equipment_id: UUID, now: datetime) -> list[MachineWriteRow]:
    """Queued too long → expired; taken without a result too long → failed. → the rows changed (for the journal)."""
    changed = []
    for row in session.scalars(select(MachineWriteRow).where(MachineWriteRow.equipment_id == equipment_id,
                                                               MachineWriteRow.status.in_(OPEN))):
        if row.purpose == CONTROL:
            if row.status == "queued" and rules.since(row.created_at, now) > CONTROL_TTL:
                row.status, row.finished_at, row.error = "expired", now, CONTROL_TOO_OLD
                changed.append(row)
            elif (row.status == "taken" and row.taken_at is not None
                  and rules.since(row.taken_at, now) > result_wait(row)):
                row.status, row.finished_at, row.error = "unknown", now, CONTROL_NO_ANSWER
                changed.append(row)
            continue
        if row.status == "queued" and rules.since(row.created_at, now) > COMMAND_TTL:
            row.status, row.finished_at, row.error = "expired", now, "Агент станка не забрал команду за 10 с"
            changed.append(row)
        elif row.status == "taken" and row.taken_at is not None and rules.since(row.taken_at, now) > RESULT_WAIT:
            row.status, row.finished_at, row.error = (
                "unknown" if row.payload.get(LENGTH_CORRECTION) else "failed", now,
                "Агент станка не сообщил результат записи")
            changed.append(row)
    session.flush()
    return changed


def idle_problem(link: rules.MachineLinkRow | None, snapshot: rules.MachineSnapshotRow | None, now: datetime) -> str:
    """Why the machine may not be written now; "" — it may."""
    if link is None:
        return "Станок не подключён к ERP"
    code = rules.link_status(snapshot, now)
    if code != "ok":
        return rules.LINK_TEXTS[code][0]
    state = rules.snapshot_words(snapshot).get(766)
    if state not in IDLE_STATES:
        text = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "неизвестно"
        return f"Записывать можно, когда станок готов и ждёт пуска или тираж готов; сейчас: {text}"
    return ""


# «Тираж» (D22) is not a count but the target the PLC compares with its cumulative T.Finish (D649): 18.11.2026 D22 =
# T.Finish + 1 cut one wire, the card showed «задано 570 / сделано 570». ERP takes «Тираж» as pieces in this job (N) and
# writes D22 = D649 + N from a snapshot at most `COUNTER_FRESH` old of an idle machine; the read-back expects D649 + N.
QUANTITY_KEY = "total_set"
COUNTER_FRESH = timedelta(seconds=5)


def quantity_as_target(writes: list[FieldWrite], snapshot: rules.MachineSnapshotRow | None,
                       now: datetime) -> tuple[list[FieldWrite], dict | None]:
    """«Тираж» N → D22 = D649 + N. → (the writes with the target, {"pieces", "counter", "target"} or None — no Тираж)."""
    item = next((write for write in writes if write.key == QUANTITY_KEY), None)
    if item is None:
        return writes, None
    try:
        pieces = int(str(item.value).strip().replace(" ", ""))
    except ValueError:
        raise rules.MachineWriteInvalid("Тираж: целое число штук в этом задании") from None
    if pieces < 1:
        raise rules.MachineWriteInvalid("Тираж: целое число штук больше нуля")
    if snapshot is None or rules.link_status(snapshot, now) != "ok" or rules.since(snapshot.reported_at, now) > COUNTER_FRESH:
        raise MachineCommandRefused("Тираж не записан: нет свежего счётчика станка (данные старше 5 с) — "
                                    "проверьте связь и повторите")
    words = rules.snapshot_words(snapshot)
    state = words.get(766)
    if state not in IDLE_STATES:
        text = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "неизвестно"
        raise MachineCommandRefused(f"Тираж не записан: станок не готов и не в «Тираж готов» (сейчас: {text}) — "
                                    "счётчик может идти")
    counter = rules._u32(words, 649)
    if counter is None:
        raise MachineCommandRefused("Тираж не записан: счётчик станка (T.Finish) не прочитан")
    target = counter + pieces
    return ([FieldWrite(write.key, target) if write is item else write for write in writes],
            {"pieces": pieces, "counter": counter, "target": target})


def enqueue(session: Session, *, equipment_id: UUID, purpose: str, writes: list[FieldWrite], summary: str,
            actor_id: UUID, actor_name: str, now: datetime) -> tuple[MachineWriteRow, list[MachineWriteRow]]:
    """Queue one write. → (the command, commands that expired meanwhile)."""
    if purpose not in PURPOSES:
        raise ValueError("Неизвестное назначение команды")
    link = session.get(rules.MachineLinkRow, equipment_id)
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    problem = idle_problem(link, snapshot, now)
    if problem:
        raise MachineCommandRefused(problem)
    blocks, bits, shown = plan_writes(writes, rules.snapshot_words(snapshot))
    needed = runner_needed_for(bits, [block["addr"] + offset for block in blocks
                                      for offset in range(len(block["values"]))])
    if needed and runner_outdated(equipment_id, now, needed):
        raise MachineCommandRefused(runner_refusal(needed))
    expired = _expire(session, equipment_id, now)
    if session.scalar(select(MachineWriteRow.id).where(MachineWriteRow.equipment_id == equipment_id,
                                                         MachineWriteRow.status.in_(OPEN))) is not None:
        raise MachineCommandRefused("На станок уже идёт запись — дождитесь её результата")
    row = MachineWriteRow(id=uuid4(), equipment_id=equipment_id, purpose=purpose, status="queued",
                            payload={"blocks": blocks, "bits": bits, "fields": shown}, summary=" ".join(summary.split())[:500],
                            created_by=actor_id, created_by_name=actor_name[:200], created_at=now)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raise MachineCommandRefused("На станок уже идёт запись — дождитесь её результата") from None
    return row, expired


def open_write(session: Session, equipment_id: UUID, now: datetime) -> MachineWriteRow | None:
    """A parameter write (not a machine command) still queued or running on this machine; expired ones are closed first
    (not committed here — the caller's unit of work decides)."""
    _expire(session, equipment_id, now)
    return session.scalar(select(MachineWriteRow).where(MachineWriteRow.equipment_id == equipment_id,
                                                        MachineWriteRow.status.in_(OPEN),
                                                        MachineWriteRow.purpose != CONTROL).limit(1))


def claim(session: Session, equipment_id: UUID, now: datetime,
          runner_version: str | None = None) -> tuple[MachineWriteRow | None, list[MachineWriteRow]]:
    """The runner takes the oldest queued command still in time. A write its version cannot run whole fails here,
    unsent. → (command or None, commands expired or failed meanwhile)."""
    note_runner(equipment_id, runner_version, now)
    expired = _expire(session, equipment_id, now)
    queued = list(session.scalars(select(MachineWriteRow).where(MachineWriteRow.equipment_id == equipment_id,
                                                                MachineWriteRow.status == "queued")
                                  .order_by(MachineWriteRow.created_at, MachineWriteRow.id)).all())
    for item in list(queued):
        needed = runner_needed_for_row(item)
        if needed is None or runner_current(runner_version, needed):
            continue
        if session.execute(update(MachineWriteRow).where(MachineWriteRow.id == item.id,
                                                         MachineWriteRow.status == "queued")
                           .values(status="failed", finished_at=now, error=runner_refusal(needed))).rowcount == 1:
            session.expire(item)
            session.refresh(item)
            expired.append(item)
        queued.remove(item)
    # Stop jumps the queue: a Stop pressed while another command waits is taken first.
    row = next((item for item in queued if item.command == STOP), queued[0] if queued else None)
    if row is None:
        return None, expired
    taken = session.execute(update(MachineWriteRow).where(MachineWriteRow.id == row.id,
                                                            MachineWriteRow.status == "queued")
                            .values(status="taken", taken_at=now)).rowcount == 1
    session.expire(row)
    session.refresh(row)
    return (row if taken else None), expired


def expected_words(row: MachineWriteRow) -> dict[int, int]:
    return {block["addr"] + offset: int(word) for block in row.payload["blocks"]
            for offset, word in enumerate(block["values"])}


def expected_bits(row: MachineWriteRow) -> list[dict]:
    return list(row.payload.get("bits") or [])


def length_correction_value(words: dict) -> Decimal | None:
    """PLC-held D54, signed little-word-first INT32 in hundredths of a millimetre."""
    low, high = words.get(54, words.get("54")), words.get(55, words.get("55"))
    if any(not isinstance(word, int) or isinstance(word, bool) or not 0 <= word <= rules.WORD_MAX
           for word in (low, high)):
        return None
    raw = low | (high << 16)
    return Decimal(raw - 0x100000000 if raw >= 0x80000000 else raw) * STEP


def enqueue_length_correction(session: Session, *, equipment_id: UUID, measured: Decimal, seen: Decimal,
                              actor_id: UUID, actor_name: str, now: datetime) -> tuple[MachineWriteRow, list]:
    link = session.get(rules.MachineLinkRow, equipment_id)
    snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
    problem = idle_problem(link, snapshot, now)
    if problem:
        raise MachineCommandRefused(problem)
    words = rules.snapshot_words(snapshot)
    length = rules._u32(words, 32)
    correction = length_correction_value(words)
    if length is None or correction is None:
        raise MachineCommandRefused("Нет свежих данных длины и поправки станка — обновите данные")
    if Decimal(length) * STEP != seen:
        raise MachineCommandRefused("Длина на станке изменилась — обновите данные и повторите")
    if not measured.is_finite() or not 0 < measured <= Decimal("999999.99") or measured != measured.quantize(STEP):
        raise rules.MachineWriteInvalid("Получилось: число больше нуля, не более 999999,99 мм, с точностью 0,01 мм")
    if runner_outdated(equipment_id, now, RUNNER_CORRECTION):
        raise MachineCommandRefused(runner_refusal(RUNNER_CORRECTION))
    expired = _expire(session, equipment_id, now)
    if session.scalar(select(MachineWriteRow.id).where(MachineWriteRow.equipment_id == equipment_id,
                                                      MachineWriteRow.status.in_(OPEN))) is not None:
        raise MachineCommandRefused("На станок уже идёт запись — дождитесь её результата")
    raw = int(measured / STEP)
    row = MachineWriteRow(id=uuid4(), equipment_id=equipment_id, purpose="settings", status="queued",
        payload={LENGTH_CORRECTION: True, "blocks": [{"addr": 658, "values": [raw & 0xFFFF, raw >> 16]}],
                 "bits": [], "fields": [], "seen": {str(a): words[a] for a in (32, 33, 54, 55)}},
        summary=f"Поправка длины станка: получилось {str(measured).replace('.', ',')} мм",
        created_by=actor_id, created_by_name=actor_name[:200], created_at=now)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raise MachineCommandRefused("На станок уже идёт запись — дождитесь её результата") from None
    return row, expired


def finish(session: Session, *, equipment_id: UUID, command_id: UUID, ok: bool, readback: dict, error: str,
           now: datetime, correction_pulsed: bool = False, before: dict | None = None,
           outcome: str | None = None) -> MachineWriteRow:
    """The runner's report. Done only when every written register reads back as asked."""
    row = session.get(MachineWriteRow, command_id)
    if row is None or row.equipment_id != equipment_id:
        raise KeyError("Команда станка не найдена")
    if row.status != "taken":
        return row  # repeated or late report: the first word stands
    read: dict[int, int] = {}
    for key, value in (readback or {}).items():
        try:
            address = int(str(key))
        except ValueError:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= rules.WORD_MAX:
            read[address] = value
    expected = expected_words(row)
    switches = expected_bits(row)
    differ = [f"D{address}" for address in sorted(address for address, word in expected.items()
                                                  if read.get(address) != word)]
    differ += [f"D{bit['addr']}.{bit['bit']}" for bit in switches if bit["addr"] not in read
               or (read[bit["addr"]] >> int(bit["bit"])) & 1 != int(bit["value"])]
    kept = set(expected) | {int(bit["addr"]) for bit in switches}
    correction = bool(row.payload.get(LENGTH_CORRECTION))
    if correction:
        kept |= {54, 55}
        if not correction_pulsed or length_correction_value(read) is None:
            differ.append("D54 / подтверждение импульса D600.06")
        if before != row.payload["seen"]:
            differ.append("длина или поправка до записи изменилась")
    row.readback = {str(address): word for address, word in sorted(read.items()) if address in kept}
    row.finished_at = now
    if ok and not differ:
        row.status, row.error = "done", ""
    else:
        row.status = "unknown" if correction and (correction_pulsed or outcome == "unknown") else "failed"
        reason = " ".join(str(error or "").split())
        if "busy" in reason or "PXS_HMI" in reason:
            reason = rules.HMI_OPEN
        elif reason.startswith("mbd outdated"):
            # «mbd outdated: 1.2.0, need 1.3.0» (the runner): the feed-roller bit needs mbd 1.3.0, the rest 1.2.0.
            need = reason.rsplit("need ", 1)[1].strip() if "need " in reason else ""
            reason = "Служба обмена со станком (mbd) устарела — обновите" + (f" до {need}" if need else "")
        elif reason.startswith("state "):
            reason = "Станок не в ожидании: " + reason.removeprefix("state ")
        elif correction and reason:
            reason = ("Длина или поправка на станке изменилась — обновите данные и повторите"
                      if reason.startswith("seen value changed") else
                      "Команда поправки опоздала — повторите после обновления данных"
                      if reason == "length correction expired" else
                      "Результат поправки не подтверждён — проверьте станок и обновите данные"
                      if row.status == "unknown" else
                      "Поправка не выполнена: служба станка не подтвердила данные")
        if not reason and differ:
            reason = "Станок не принял: " + ", ".join(differ[:8])
        row.error = (reason or "Запись не удалась")[:500]
    session.flush()
    return row


def latest(session: Session, equipment_id: UUID, now: datetime, limit: int = 5) -> list[MachineWriteRow]:
    _expire(session, equipment_id, now)
    return list(session.scalars(select(MachineWriteRow).where(MachineWriteRow.equipment_id == equipment_id)
                                .order_by(MachineWriteRow.created_at.desc()).limit(limit)))


def moment(value: datetime | None) -> str | None:
    """A stored moment for the screen, always with its zone: SQLite (tests, preview) returns it naive in shop time —
    without the zone the browser read it as its own local time and «Последние записи» disagreed with «Последний сигнал»."""
    if value is None:
        return None
    return (value.replace(tzinfo=MOSCOW) if value.tzinfo is None else value).isoformat()


def _state_text(words: object) -> str | None:
    code = words.get("766") if isinstance(words, dict) else None
    if code is None:
        return None
    return rules.OB019_STATES.get(code, (f"Код {code}", ""))[0]


def command_view(row: MachineWriteRow) -> dict:
    text, tone = (CONTROL_TEXTS if row.purpose == CONTROL else STATUS_TEXTS)[row.status]
    states = row.readback if row.purpose == CONTROL and isinstance(row.readback, dict) else {}
    correction = bool(row.payload.get(LENGTH_CORRECTION))
    shown = lambda value: None if value is None else f"{value:.2f}".replace(".", ",")
    return {"id": str(row.id), "status": row.status, "status_text": text, "tone": tone, "purpose": row.purpose,
            "length_correction_before": shown(length_correction_value(row.payload.get("seen", {}))) if correction else None,
            "length_correction_after": shown(length_correction_value(row.readback or {}))
                if correction and row.status == "done" else None,
            "command": row.command, "state_before": _state_text(states.get("before")),
            "state_after": _state_text(states.get("after")),
            # «Закрыть пульт на ПК»: how the runner closed it — graceful / forced / none (it was not open) / started —,
            # the runner's reason (`still_open` → the card offers «Закрыть принудительно») and the force flag.
            "method": states.get("method") if isinstance(states.get("method"), str) else None,
            "reason": states.get("reason") if isinstance(states.get("reason"), str) else None,
            "force": bool(row.payload.get("force")) if row.purpose == CONTROL else False,
            "summary": row.summary, "fields": row.payload.get("fields", []), "error": row.error,
            "created_by": row.created_by_name, "created_at": moment(row.created_at),
            "finished_at": moment(row.finished_at)}


# --- corrections «станок режет не так» ---

def offsets(session: Session, equipment_id: UUID) -> dict[str, MachineOffsetRow]:
    return {row.parameter: row for row in session.scalars(
        select(MachineOffsetRow).where(MachineOffsetRow.equipment_id == equipment_id))}


def offset_value(row: MachineOffsetRow | None) -> Decimal:
    return Decimal(0) if row is None else (Decimal(row.offset_hundredths) * STEP).quantize(STEP)


def set_offset(session: Session, *, equipment_id: UUID, parameter: str, offset: Decimal, reason: str, actor_name: str,
               now: datetime) -> tuple[Decimal, Decimal]:
    """Store this machine's correction of one parameter. → (old offset, new offset)."""
    if parameter not in OFFSET_KEYS:
        raise rules.MachineWriteInvalid("Поправку станка можно задать только для длины, зачистки и реза провода")
    if not isinstance(offset, Decimal) or not offset.is_finite() or offset != offset.quantize(STEP):
        raise rules.MachineWriteInvalid("Поправка: число с точностью 0,01 мм")
    hundredths = int(offset / STEP)
    if abs(hundredths) > OFFSET_LIMIT:
        raise rules.MachineWriteInvalid("Поправка станка не больше ±100 мм")
    row = session.get(MachineOffsetRow, (equipment_id, parameter))
    old = offset_value(row)
    clean = " ".join(reason.split())[:300]
    if row is None:
        session.add(MachineOffsetRow(equipment_id=equipment_id, parameter=parameter, offset_hundredths=hundredths,
                                     reason=clean, updated_by_name=actor_name[:200], updated_at=now, version=1))
    else:
        changed = session.execute(update(MachineOffsetRow).where(
            MachineOffsetRow.equipment_id == equipment_id, MachineOffsetRow.parameter == parameter,
            MachineOffsetRow.version == row.version).values(
            offset_hundredths=hundredths, reason=clean, updated_by_name=actor_name[:200], updated_at=now,
            version=row.version + 1)).rowcount
        if changed != 1:
            raise MachineCommandRefused("Поправку станка только что изменили — обновите карточку")
        session.expire(row)
    session.flush()
    return old, offset_value(session.get(MachineOffsetRow, (equipment_id, parameter)))
