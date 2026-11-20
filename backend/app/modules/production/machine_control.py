"""ОБ.019 machine commands from the card (owner 18.11.2026, plan «Станки», slice 3): Стоп, Сброс, Пуск, Сброс аварии.

They ride the write queue of production/machine_writes.py (purpose `control`, column `command`, migration 0197): the
command runner on the machine PC (`tools/machine-agent/ob019-writer.ps1`, the bridge's `machine:report` token, PC bound)
claims them with the parameter writes and holds the PLC bit through the local Modbus service (mbd `pulse`: it writes
the HMI context D730=13 + date while holding, 1.5 s, and clears the bit) — proven on ОБ.019 18.11.2026.

Rules:
- Стоп — anyone who sees the card (view), no confirmation; it cancels every queued command and is claimed first.
- Сброс / Пуск / Сброс аварии — section «Управление станками» on edit and the state the person saw (a different state
  now → refused); one press on the card, no dialog (owner 19.11.2026): the card sends `hands_clear: true` itself, the
  journal says «одним нажатием с карточки», not a person's confirmation. The state must allow the command
  (`ALLOWED_STATES`), no switch of `BLOCKED_BY_BITS` may be on (Пуск with the feed rollers lifted, D604.0); one
  controller — nothing else open on the machine (the queue's UNIQUE index).
- A command older than 5 s by the server clock is never handed out; the claim carries the time left and the runner
  counts it from the moment it asked, so it never starts one older than 5 s.
- Checked again ON the PC right before the bit is held: PXS_HMI closed, the state and the blocking bits
  (`$script:Controls` of the runner holds the same tables as `ALLOWED_STATES` / `BLOCKED_BY_BITS`; a test compares
  them), an alarm present for Сброс аварии.
- No answer for 15 s → «Результат неизвестен», never assumed done; a late true answer still replaces it.
- No ERP emergency stop: E-stop (D599.3) is the physical button only; neither ERP nor the runner knows that bit.

«Закрыть пульт на ПК» / «Открыть пульт на ПК» (owner 20.11.2026, `hmi_close` / `hmi_open`, migration 0211): COM8 has one
master — the vendor operator panel PXS_HMI or ERP's Modbus service; while the panel runs ERP sees nothing, and nothing on
the machine PC says whether the machine cuts (its TextLog holds only Open-System / Close-System, checked 20.11.2026).
Same right as Сброс / Пуск, runner 1.4.0 or newer (machine_writes.RUNNER_HMI); the card asks its own confirmation, which
says that ERP cannot see the machine while the panel is open (`hands_clear`).
- `hmi_close`: the runner asks a helper task in the operator's session to close the panel (WM_CLOSE) and waits 15 s:
  closed → done; still open (it may be asking the operator something) → failed `still_open`, and the card offers
  «Закрыть принудительно» — the same command with `force` after its own confirmation that the machine stands; then the
  runner ends the process at once (journaled with the flag). The server takes `force` only as the answer to such a
  failure (`force_allowed`: the newest panel command, within 5 minutes, after the machine was last read «На связи»). No helper → failed `no_helper`, nothing is killed.
- Refusal rule of `hmi_close` (`hmi_problem`; graceful or forced alike). The PC must report (a fresh snapshot). Panel
  closed (link «На связи»): refused while the live D766 is in a cycle (103 «Работает», 104 «Пауза», 106 «Пучок готов»,
  `IN_CYCLE`). Panel open (link `port_busy`, no words): ERP cannot read the machine, so the state of the last snapshot
  read before the panel took the port decides (`last_state`, kept by production/machines.report): in a cycle → refused,
  and the text says it is the state before the panel opened; never read → allowed only after the card's confirmation
  (the person at the card vouches). PLC or Modbus service silent → refused.
- `hmi_open`: refused while the panel is open, while the live D766 is in a cycle, and while an ERP job run is open on
  the machine (production/machine_runs.py) — the panel would take the port from under it."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import machine_writes as queue
from . import machines as rules

SECTION = "machine_control"
NAMES = {"stop": "Стоп", "reset": "Сброс", "start": "Пуск", "alarm_clear": "Сброс аварии",
         "hmi_close": "Закрыть пульт на ПК", "hmi_open": "Открыть пульт на ПК"}
HMI_COMMANDS = queue.HMI_COMMANDS
# D766 states of a running cycle: «Работает», «Пауза», «Пучок готов» (the next bundle follows by itself).
IN_CYCLE = frozenset({103, 104, 106})
IN_CYCLE_TEXT = "Станок в цикле — пульт закрывать нельзя, дождитесь остановки"
BEFORE_PULT_TEXT = ("До открытия пульта станок был в цикле — ERP не видит, остановился ли он; закройте пульт на ПК, "
                    "когда станок встанет")
OPEN_IN_CYCLE_TEXT = "Станок в цикле — откройте пульт после остановки"
OPEN_RUN_TEXT = "Станок выполняет задание из ERP — откройте пульт после остановки"
FORCED = "Закрыть пульт на ПК принудительно"
# «Закрыть принудительно» answers one graceful close that did not work, and only for a while.
FORCE_WINDOW = timedelta(minutes=5)
FORCE_FIRST = "Сначала нажмите «Закрыть пульт на ПК»: принудительно — только если пульт не закрылся"
# D766 states a command may run in; None — any state (Сброс аварии: any state with an alarm, D782 ≠ 0).
ALLOWED_STATES: dict[str, frozenset[int] | None] = {
    "stop": None,
    "start": frozenset({102}),
    "reset": frozenset({100, 105, 107}),
    "alarm_clear": None,
}
# Switch bits (address, bit) that must be off for a command: Пуск with the feed rollers lifted would run the cycle with
# no wire feed (owner 19.11.2026). Not read (old bridge) — not blocked; the runner re-reads it on the PC.
BLOCKED_BY_BITS: dict[str, tuple[tuple[int, int], ...]] = {"start": ((rules.OB019_FEED_WHEEL_ADDRESS, 0),)}
BLOCKED_TEXTS = {rules.OB019_FEED_WHEEL_ADDRESS: "подающие ролики подняты — сначала опустите их"}
# The runner's reason codes (the script is ASCII) → what the card shows.
REASONS = {
    "ok": "",
    "too_late": "Дошла до ПК станка позже 5 с — не выполнялась",
    "hmi_open": "Открыт пульт станка на ПК — команда не выполнялась",
    "state": "Станок был не в том состоянии — команда не выполнялась",
    "no_alarm": "Аварии не было — сбрасывать нечего",
    "mbd_error": "Служба Modbus на ПК станка вернула ошибку — проверьте станок",
    "no_reply": "Служба Modbus не ответила во время команды — проверьте станок",
    "not_cleared": "Бит команды не снят — проверьте станок",
    "unknown_command": "Агент на ПК станка не знает этой команды",
    "rollers_up": "Подающие ролики подняты — сначала опустите их; команда не выполнялась",
    # «Закрыть / Открыть пульт на ПК» (runner 1.4.0). Never the words of the panel-open banner: the card would read
    # them as that banner (machineControlApi.operatorText).
    "still_open": "Пульт не закрылся (возможно, ждёт ответа оператора)",
    "hmi_still_open": "Пульт на ПК не закрылся — закройте его на ПК станка",
    "hmi_already_open": "Пульт на ПК уже запущен",
    "hmi_not_started": "Пульт на ПК не запустился — запустите его на ПК станка",
    "no_helper": "На ПК станка нет помощника пульта — обновите службу",
    "pc_error": "Ошибка на ПК станка — проверьте пульт на ПК",
}
# How the runner handled the panel (readback["method"]).
METHODS = ("graceful", "forced", "none", "started")
OUTCOMES = ("done", "failed", "expired", "unknown")


class CommandRefused(Exception):
    def __init__(self, detail: str, status: int = 409):
        super().__init__(detail)
        self.status = status


def precondition_error(command: str, state: int | None, alarm: int | None, words: dict[int, int] | None = None) -> str:
    """Why the command may not run in this state ('' — it may). `words` — the snapshot, for `BLOCKED_BY_BITS`."""
    allowed = ALLOWED_STATES[command]
    if allowed is not None and state not in allowed:
        now = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "неизвестно"
        need = ", ".join(rules.OB019_STATES[code][0].lower() for code in sorted(allowed))
        return f"«{NAMES[command]}» возможен, только когда станок: {need}. Сейчас: {now.lower()}"
    if command == "alarm_clear" and not alarm:
        return "Аварии нет — сбрасывать нечего"
    for address, bit in BLOCKED_BY_BITS.get(command, ()):
        word = (words or {}).get(address)
        if word is not None and (word >> bit) & 1:
            return f"«{NAMES[command]}» невозможен: {BLOCKED_TEXTS.get(address, f'включён D{address}.{bit}')}"
    return ""


def _first_low(text: str) -> str:
    return text[:1].lower() + text[1:]


def hmi_problem(command: str, snapshot: rules.MachineSnapshotRow | None, now: datetime,
                run_open: bool = False) -> tuple[str, int | None]:
    """Why «Закрыть / Открыть пульт на ПК» may not be queued ('' — it may) and the machine state it was decided on
    (the live D766, or with the panel holding the port the last one read before it; None — not known). `run_open` —
    an ERP job run is open on the machine (hmi_open only)."""
    code = rules.link_status(snapshot, now)
    if code in ("stale", "not_installed"):
        return f"{NAMES[command]} нельзя: {_first_low(rules.LINK_TEXTS[code][0])}", None
    live = rules.snapshot_words(snapshot).get(766) if code == "ok" else None
    if command == "hmi_open":
        if code == "port_busy":
            return "Пульт на ПК уже открыт", None
        if live in IN_CYCLE:
            return OPEN_IN_CYCLE_TEXT, live
        return (OPEN_RUN_TEXT if run_open else ""), live
    if code == "ok":
        return (IN_CYCLE_TEXT if live in IN_CYCLE else ""), live
    if code != "port_busy":
        return f"Пульт не закрыть: {_first_low(rules.LINK_TEXTS[code][0])} — состояние станка неизвестно", None
    last = snapshot.last_state if snapshot is not None else None
    if last in IN_CYCLE:
        return BEFORE_PULT_TEXT, last
    return "", last  # never read: the card's confirmation said ERP cannot see the machine


def force_allowed(session: Session, equipment_id: UUID, snapshot: rules.MachineSnapshotRow | None, now: datetime) -> bool:
    """«Закрыть принудительно» answers a graceful close that did not work: the newest panel command of this machine is
    an `hmi_close` that finished `still_open` within `FORCE_WINDOW`, and after the last snapshot that read the machine
    «На связи» (`last_state_at`): ERP keeps no moment the panel took the port, so the last moment it was certainly
    closed stands for it — a still_open from before the panel was closed and opened again does not count."""
    last = session.scalar(select(queue.MachineWriteRow).where(
        queue.MachineWriteRow.equipment_id == equipment_id,
        queue.MachineWriteRow.command.in_(HMI_COMMANDS)).order_by(queue.MachineWriteRow.created_at.desc()).limit(1))
    if last is None or last.command != "hmi_close" or last.status != "failed" or last.finished_at is None:
        return False
    if not isinstance(last.readback, dict) or last.readback.get("reason") != "still_open":
        return False
    if rules.since(last.finished_at, now) > FORCE_WINDOW:
        return False
    seen = snapshot.last_state_at if snapshot is not None else None
    return seen is None or rules.since(seen, last.finished_at) > timedelta(0)


def _run_open(session: Session, equipment_id: UUID) -> bool:
    from . import machine_runs  # machine_runs imports the queue only; imported here to keep this module light

    return machine_runs.is_open(machine_runs.current(session, equipment_id))


def request(session: Session, *, equipment_id: UUID, command: str, actor_id: UUID, actor_name: str, can_control: bool,
            state_seen: int | None, hands_clear: bool, now: datetime, force: bool = False
            ) -> tuple[queue.MachineWriteRow, list[queue.MachineWriteRow]]:
    """Queue a command. → (the command, commands that expired or were cancelled meanwhile — for the journal).
    `force` — «Закрыть принудительно» (hmi_close only): the runner ends the panel's process without asking it."""
    if command not in NAMES:
        raise CommandRefused("Неизвестная команда", 422)
    if force and command != "hmi_close":
        raise CommandRefused("Принудительно можно только закрыть пульт", 422)
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
    elif command in HMI_COMMANDS:
        if not can_control:
            raise CommandRefused("Пульт на ПК — только с правом «Управление станками»", 403)
        if not hands_clear:  # the card asks its own confirmation and sends it
            raise CommandRefused("Команда без подтверждения карточки — обновите страницу")
        if queue.runner_outdated(equipment_id, now, queue.RUNNER_HMI):
            raise CommandRefused(queue.RUNNER_HMI_OUTDATED)
        run_open = command == "hmi_open" and _run_open(session, equipment_id)
        snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
        reason, state_value = hmi_problem(command, snapshot, now, run_open)
        if reason:
            raise CommandRefused(reason)
        if force and not force_allowed(session, equipment_id, snapshot, now):
            raise CommandRefused(FORCE_FIRST)
    else:
        if not can_control:
            raise CommandRefused("«Сброс», «Пуск», «Сброс аварии» — только с правом «Управление станками»", 403)
        if not hands_clear:  # the card sends it with every press (no dialog since 19.11.2026)
            raise CommandRefused("Команда без подтверждения карточки — обновите страницу")
        snapshot = session.get(rules.MachineSnapshotRow, equipment_id)
        code = rules.link_status(snapshot, now)
        if code != "ok":
            raise CommandRefused(f"Команду не отправить: {rules.LINK_TEXTS[code][0].lower()}")
        words = rules.snapshot_words(snapshot)
        state_value = words.get(766)
        if state_seen is None or state_seen != state_value:
            raise CommandRefused("Состояние станка изменилось — проверьте его и подтвердите ещё раз")
        reason = precondition_error(command, state_value, words.get(782), words)
        if reason:
            raise CommandRefused(reason)
    row = queue.MachineWriteRow(
        id=uuid4(), equipment_id=equipment_id, purpose=queue.CONTROL, command=command, status="queued",
        payload={"command": command, "state_seen": state_value, **({"force": True} if force else {})},
        summary=FORCED if force else NAMES[command], created_by=actor_id,
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
           now: datetime, method: str | None = None) -> bool:
    """The runner's answer to a machine command. → True when it was recorded (False: already answered).
    A late answer replaces the timeout's «unknown»; the runner's own «unknown» stands. `method` — how the runner
    handled the operator panel (`METHODS`), kept with the states."""
    if outcome not in OUTCOMES or reason not in REASONS or (method and method not in METHODS):
        raise rules.MachineWriteInvalid("Неизвестный результат команды")
    if row.status != "taken" and not (row.status == "unknown" and row.error == queue.CONTROL_NO_ANSWER):
        return False
    states: dict = {"before": _words(before), "after": _words(after)}
    if row.command in HMI_COMMANDS:  # the card offers «Закрыть принудительно» on `still_open`
        states["reason"] = reason
        if method:
            states["method"] = method
    done = session.execute(update(queue.MachineWriteRow).where(
        queue.MachineWriteRow.id == row.id, queue.MachineWriteRow.status == row.status)
        .values(status=outcome, error=REASONS[reason], readback=states, finished_at=now)
        .execution_options(synchronize_session=False)).rowcount == 1
    session.refresh(row)
    return done
