"""ОБ.019 machine commands from the card (owner 18.11.2026, plan «Станки», slice 3): Стоп, Сброс, Пуск, Сброс аварии.

They ride the write queue of production/machine_writes.py (purpose `control`, column `command`, migration 0197): the
command runner on the machine PC (`tools/machine-agent/ob019-writer.ps1`, the bridge's `machine:report` token, PC bound)
claims them with the parameter writes and holds the PLC bit through the local Modbus service (mbd `pulse`: it writes
the HMI context D730=13 + date while holding, 1.5 s, and clears the bit) — proven on ОБ.019 18.11.2026.

Rules:
- Стоп — anyone who sees the card (view), no confirmation; it cancels every queued command and is claimed first.
- Сброс / Пуск / Сброс аварии — section «Управление станками» on edit, the confirmation «руки убраны» and the state the
  person saw (a different state now → refused); the state must allow the command (`ALLOWED_STATES`); one controller —
  nothing else open on the machine (the queue's UNIQUE index).
- A command older than 5 s by the server clock is never handed out; the claim carries the time left and the runner
  counts it from the moment it asked, so it never starts one older than 5 s.
- Checked again ON the PC right before the bit is held: PXS_HMI closed, the state (`$script:Controls` of the runner
  holds the same table as `ALLOWED_STATES`; a test compares them), an alarm present for Сброс аварии.
- No answer for 15 s → «Результат неизвестен», never assumed done; a late true answer still replaces it.
- No ERP emergency stop: E-stop (D599.3) is the physical button only; neither ERP nor the runner knows that bit."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import machine_writes as queue
from . import machines as rules

SECTION = "machine_control"
NAMES = {"stop": "Стоп", "reset": "Сброс", "start": "Пуск", "alarm_clear": "Сброс аварии"}
# D766 states a command may run in; None — any state (Сброс аварии: any state with an alarm, D782 ≠ 0).
ALLOWED_STATES: dict[str, frozenset[int] | None] = {
    "stop": None,
    "start": frozenset({102}),
    "reset": frozenset({100, 105, 107}),
    "alarm_clear": None,
}
# The runner's reason codes (the script is ASCII) → what the card shows.
REASONS = {
    "ok": "",
    "too_late": "Дошла до ПК станка позже 5 с — не выполнялась",
    "hmi_open": "Открыт пульт PXS_HMI — команда не выполнялась",
    "state": "Станок был не в том состоянии — команда не выполнялась",
    "no_alarm": "Аварии не было — сбрасывать нечего",
    "mbd_error": "Служба Modbus на ПК станка вернула ошибку — проверьте станок",
    "no_reply": "Служба Modbus не ответила во время команды — проверьте станок",
    "not_cleared": "Бит команды не снят — проверьте станок",
    "unknown_command": "Агент на ПК станка не знает этой команды",
}
OUTCOMES = ("done", "failed", "expired", "unknown")


class CommandRefused(Exception):
    def __init__(self, detail: str, status: int = 409):
        super().__init__(detail)
        self.status = status


def precondition_error(command: str, state: int | None, alarm: int | None) -> str:
    """Why the command may not run in this state ('' — it may)."""
    allowed = ALLOWED_STATES[command]
    if allowed is not None and state not in allowed:
        now = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "неизвестно"
        need = ", ".join(rules.OB019_STATES[code][0].lower() for code in sorted(allowed))
        return f"«{NAMES[command]}» возможен, только когда станок: {need}. Сейчас: {now.lower()}"
    if command == "alarm_clear" and not alarm:
        return "Аварии нет — сбрасывать нечего"
    return ""


def request(session: Session, *, equipment_id: UUID, command: str, actor_id: UUID, actor_name: str, can_control: bool,
            state_seen: int | None, hands_clear: bool, now: datetime
            ) -> tuple[queue.MachineWriteRow, list[queue.MachineWriteRow]]:
    """Queue a command. → (the command, commands that expired or were cancelled meanwhile — for the journal)."""
    if command not in NAMES:
        raise CommandRefused("Неизвестная команда", 422)
    link = session.get(rules.MachineLinkRow, equipment_id)
    if link is None:
        raise CommandRefused("Станок ещё не подключён к ERP", 404)
    closed = queue._expire(session, equipment_id, now)
    state_value: int | None = None
    if command == queue.STOP:
        waiting = session.scalars(select(queue.MachineWriteRow).where(
            queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.status == "queued")).all()
        for row in waiting:
            if row.command == queue.STOP:
                return row, closed  # a second press while the first Stop waits: the same command
        for row in waiting:
            if session.execute(update(queue.MachineWriteRow).where(
                    queue.MachineWriteRow.id == row.id, queue.MachineWriteRow.status == "queued")
                    .values(status="cancelled", finished_at=now, error=queue.CANCELLED_BY_STOP)
                    .execution_options(synchronize_session=False)).rowcount == 1:
                session.refresh(row)
                closed.append(row)
        state_value = state_seen
    else:
        if not can_control:
            raise CommandRefused("«Сброс», «Пуск», «Сброс аварии» — только с правом «Управление станками»", 403)
        if not hands_clear:
            raise CommandRefused("Подтвердите, что руки убраны и в зоне станка никого нет")
        snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
        code = rules.link_status(snapshot, now)
        if code != "ok":
            raise CommandRefused(f"Команду не отправить: {rules.LINK_TEXTS[code][0].lower()}")
        words = rules.snapshot_words(snapshot)
        state_value = words.get(766)
        if state_seen is None or state_seen != state_value:
            raise CommandRefused("Состояние станка изменилось — проверьте его и подтвердите ещё раз")
        reason = precondition_error(command, state_value, words.get(782))
        if reason:
            raise CommandRefused(reason)
    row = queue.MachineWriteRow(
        id=uuid4(), equipment_id=equipment_id, purpose=queue.CONTROL, command=command, status="queued",
        payload={"command": command, "state_seen": state_value}, summary=NAMES[command], created_by=actor_id,
        created_by_name=actor_name[:200], created_at=now)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        if command == queue.STOP:
            existing = session.scalar(select(queue.MachineWriteRow).where(
                queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.command == queue.STOP,
                queue.MachineWriteRow.status == "queued"))
            if existing is not None:
                return existing, closed
        busy = session.scalar(select(queue.MachineWriteRow).where(
            queue.MachineWriteRow.equipment_id == equipment_id, queue.MachineWriteRow.status.in_(queue.OPEN),
            queue.MachineWriteRow.id != row.id))
        what = f"«{busy.summary}» от {busy.created_by_name}" if busy is not None else "другая команда"
        raise CommandRefused(f"Станок уже выполняет: {what}. Дождитесь результата") from None
    return row, closed


def deadline_ms(row: queue.MachineWriteRow, now: datetime) -> int:
    """Time left for the runner to START the command (server clock)."""
    left = queue.CONTROL_TTL - rules.since(row.created_at, now)
    return max(0, int(left.total_seconds() * 1000))


def _words(raw: object) -> dict[str, int] | None:
    """D766 state and D782 alarm the runner read around the command, 16-bit words only."""
    if not isinstance(raw, dict):
        return None
    found = {key: raw[key] for key in ("766", "782")
             if isinstance(raw.get(key), int) and not isinstance(raw.get(key), bool) and 0 <= raw[key] <= rules.WORD_MAX}
    return found or None


def finish(session: Session, row: queue.MachineWriteRow, *, outcome: str, reason: str, before: object, after: object,
           now: datetime) -> bool:
    """The runner's answer to a machine command. → True when it was recorded (False: already answered).
    A late answer replaces the timeout's «unknown»; the runner's own «unknown» stands."""
    if outcome not in OUTCOMES or reason not in REASONS:
        raise rules.MachineWriteInvalid("Неизвестный результат команды")
    if row.status != "taken" and not (row.status == "unknown" and row.error == queue.CONTROL_NO_ANSWER):
        return False
    states = {"before": _words(before), "after": _words(after)}
    done = session.execute(update(queue.MachineWriteRow).where(
        queue.MachineWriteRow.id == row.id, queue.MachineWriteRow.status == row.status)
        .values(status=outcome, error=REASONS[reason], readback=states, finished_at=now)
        .execution_options(synchronize_session=False)).rowcount == 1
    session.refresh(row)
    return done
