"""Автоплан: загрузка данных, предпросмотр и применение (правила расстановки — autoplan.py).

Два режима (владелец, 12.10.2026: «авто раскидывается на всех с перерасчётом всего, что есть в плане, и по проекту
с выделением людей на проект»):
- «Весь цех» — вся работа на всех монтажников ЦАР: запущенные задания (кроме исключённых мастером) и КБ с выданной
  техкартой без задания;
- «По проекту» — только задания и КБ одного проекта, только на выбранных людей; чужие назначения — их занятость.
В обоих режимах заново ставится только то, что раньше поставил автоплан и что ещё не начато; назначенное вручную и
начатая работа не трогаются.

КБ с выданной техкартой без задания («техкарта выдана, задания ещё нет») идёт в расчёт будущим заданием: тираж — из
карточки набора, срок — техкарты, операции — маршрут выданной техкарты (тот же список, что «Создать задание» на доске).
Предпросмотр ничего не пишет; применение создаёт такие задания тем же путём, что и ручной запуск (launch_task: номер
ПЗ, ключ идемпотентности, серийники, аудит), и пишет расписание тем же коммитом.

Период не выбирают: план идёт от «сейчас» (вверх до четверти часа) до самого позднего срока включённой работы или
дальше, сколько нужно, чтобы всё поставить, но не дальше MAX_HORIZON_DAYS. Можно задать «до даты» вручную.

Предпросмотр отдаёт отпечаток входных данных (задания с версиями, КБ к запуску с версиями техкарт, монтажники,
компетенции, календарь, выработка, техкарты, станки БД.ОБ, режим, проект, люди, исключения). Применение пересчитывает
план с тем же отпечатком — данные изменились после предпросмотра → 409. Назначение помечается «Автоплан · <мастер>»."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number

from .application import (
    DuplicateLaunch,
    UnitOfWork,
    _busy,
    _load_clock,
    _progress,
    _shift,
    _skills,
    _with_absences,
    launch_task,
)
from .autoplan import (
    AUTOPLAN_MARK,
    AUTOPLAN_SITE,
    AutoplanInput,
    AutoplanResult,
    Batch,
    Placement,
    SetupKey,
    Unplaced,
    batches,
    operation_keys,
    plan,
)
# Будущее задание КБ и его запуск — общие с доской мастера (board.py).
from .board import (
    NO_RUN_REASON,
    CardSnapshot,
    NewTask,
    Skipped,
    card_key,
    future_task as _new_task,
    launch_command as _launch_command,
    run_of as _run_of,
    unlaunched_cards as _ready_unlaunched,
)
from .dispatch import localize
from .domain import ProductionStatus, ProductionTask, ProductionValidationError
from .launchable import ReadyCard, stage_name
from .machine_load import MachineStep, task_steps
from .scheduling import work_seconds_between

# Дальше восьми недель автоплан не заглядывает (владелец, 12.10: «capped at a sane maximum»).
MAX_HORIZON_DAYS = 56
# Без сроков у работы и без «до даты» мастер видит людей на ближайшую неделю.
DEFAULT_CONTEXT_DAYS = 7
# Начало расчёта округляется вверх до четверти часа: предпросмотр и применение через минуту дают один и тот же план.
_ROUND_MINUTES = 15


class AutoplanForbidden(PermissionError):
    pass


class StaleAutoplan(RuntimeError):
    pass


class AutoplanMode(str, Enum):
    SHOP = "shop"  # «Весь цех»
    PROJECT = "project"  # «По проекту»


@dataclass(frozen=True)
class AutoplanCommand:
    actor: Actor
    now: datetime
    mode: AutoplanMode = AutoplanMode.SHOP
    project: str = ""  # «По проекту»: номер проекта
    people: tuple[UUID, ...] = ()  # «По проекту»: монтажники, выделенные на проект
    exclude: frozenset[UUID] = frozenset()  # оба режима: исключённые задания (id) и КБ без задания (id техкарты)
    date_to: date | None = None  # «до даты» вручную; None — горизонт по работе
    runs: tuple[tuple[UUID, int], ...] = ()  # тиражи новых заданий по id техкарты КБ; нет — весь тираж КБ


@dataclass(frozen=True)
class ApplyAutoplanCommand:
    plan: AutoplanCommand  # тот же запрос, что в предпросмотре: он входит в отпечаток
    items: tuple[tuple[UUID, UUID, UUID], ...]  # (задание, операция, монтажник) — подмножество предпросмотра
    fingerprint: str


@dataclass(frozen=True)
class AutoplanPreview:
    result: AutoplanResult
    unplaced: tuple[Unplaced, ...]
    warnings: tuple[str, ...]
    fingerprint: str
    tasks: dict[UUID, ProductionTask]  # запущенные и будущие (по id в расчёте)
    new_tasks: dict[UUID, NewTask]
    skipped: tuple[Skipped, ...]
    names: dict[UUID, str]
    batches: dict[UUID, tuple[Batch, ...]]
    period: tuple[datetime, date]  # начало расчёта и последний день плана
    scope: tuple[int, int, int]  # заданий, новых КБ, монтажников в расчёте

    @property
    def placements(self) -> tuple[Placement, ...]:
        return self.result.placements


@dataclass(frozen=True)
class AppliedAutoplan:
    tasks: tuple[ProductionTask, ...]
    created: tuple[ProductionTask, ...]
    operations: int
    preview: AutoplanPreview


def _round_up(moment: datetime) -> datetime:
    floor = moment.replace(second=0, microsecond=0)
    if floor < moment:
        floor += timedelta(minutes=1)
    return floor + timedelta(minutes=(-floor.minute) % _ROUND_MINUTES)


def _horizon(command: AutoplanCommand) -> tuple[datetime, datetime]:
    """Начало расчёта и граница, дальше которой не ставим: «до даты» мастера или MAX_HORIZON_DAYS от сегодня."""
    start = _round_up(command.now)
    tz = command.now.tzinfo
    today = command.now.date()
    if command.date_to is not None:
        if command.date_to < today:
            raise ProductionValidationError("Дата «до» уже прошла")
        if (command.date_to - today).days + 1 > MAX_HORIZON_DAYS:
            raise ProductionValidationError(f"Автоплан смотрит не дальше {MAX_HORIZON_DAYS // 7} недель")
        return start, datetime.combine(command.date_to + timedelta(days=1), time.min, tz)
    return start, datetime.combine(today + timedelta(days=MAX_HORIZON_DAYS), time.min, tz)


def _auto_movable(operation, progress: Decimal, start: datetime, end: datetime) -> bool:
    """Поставлено автопланом раньше, не начато и стоит в периоде — автоплан ставит его заново."""
    return (
        str(operation.assigned_by or "").startswith(AUTOPLAN_MARK)
        and bool(operation.shares) and progress <= 0
        and all(share.started_at is None and share.finished_at is None for share in operation.shares)
        and operation.planned_start is not None and operation.planned_finish is not None
        and operation.planned_finish > start and operation.planned_start < end
    )


def _fingerprint(payload: dict) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class _Scope:
    """Что входит в расчёт: запущенные задания, будущие задания КБ, пропущенные КБ и люди."""
    stored: tuple[ProductionTask, ...]  # все запущенные (их назначения — занятость)
    tasks: dict[UUID, ProductionTask]  # запущенные в поясе «сейчас»
    eligible: tuple[ProductionTask, ...]  # запущенные, которые расписываем
    new_tasks: dict[UUID, NewTask]
    skipped: tuple[Skipped, ...]
    considered: tuple[ReadyCard, ...]  # КБ к запуску в границах режима (и взятые, и пропущенные)
    team_rows: tuple[Any, ...]
    operators: tuple[Any, ...]
    warnings: tuple[str, ...]
    assigner_site: str


def _in_project(command: AutoplanCommand, project: str) -> bool:
    return command.mode is AutoplanMode.SHOP or normalize_project_number(project) == normalize_project_number(command.project)


def _scope(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard],
) -> _Scope:
    assigner = uow.operators.get(command.actor.id)
    if assigner is not None and assigner.site and assigner.site != AUTOPLAN_SITE:
        raise AutoplanForbidden(
            f"Автоплан расписывает площадку {AUTOPLAN_SITE}; мастер площадки {assigner.site} её не планирует"
        )
    project_mode = command.mode is AutoplanMode.PROJECT
    if project_mode and not str(command.project or "").strip():
        raise ProductionValidationError("Выберите проект")
    now = command.now
    # Все запущенные, по индексу статуса: первые 10 000 заданий ленты теряли запущенные за границей (U06).
    stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
    tasks = {item.id: localize(item, now) for item in stored}
    # Снятые галочки — в обоих режимах: «Весь цех» и КБ проекта в «По проекту».
    exclude = command.exclude
    warnings: list[str] = []
    eligible = tuple(
        task for task in sorted(tasks.values(), key=lambda value: value.number)
        if not task.handed_to and task.id not in exclude and _in_project(command, task.project)
    )
    new_tasks: dict[UUID, NewTask] = {}
    skipped: list[Skipped] = []
    considered: list[ReadyCard] = []
    runs = dict(command.runs)
    for card in _ready_unlaunched(uow, stored, ready, cards):
        if not _in_project(command, card.project):
            continue
        considered.append(card)
        if card.techcard_id in exclude:
            continue
        if card.quantity is None:
            skipped.append(Skipped(card, NO_RUN_REASON))
            continue
        quantity = _run_of(card, runs.get(card.techcard_id))
        try:
            task = _new_task(card, quantity, command.actor, now)
        except ProductionValidationError as error:
            skipped.append(Skipped(card, f"Маршрут техкарты не собрался: {error}"))
            continue
        new_tasks[task.id] = NewTask(card, task)
    operators = tuple(uow.operators.list())
    team_rows = tuple(sorted(
        (item for item in operators if item.installer and item.site in (AUTOPLAN_SITE, "")),
        key=lambda item: (item.name, str(item.id)),
    ))
    if project_mode:
        chosen = set(command.people)
        if not chosen:
            raise ProductionValidationError("Выберите людей на проект")
        known = {item.id for item in team_rows}
        if not chosen <= known:
            raise ProductionValidationError(f"На проект можно выделить только монтажников {AUTOPLAN_SITE}")
        team_rows = tuple(item for item in team_rows if item.id in chosen)
    if not team_rows:
        warnings.append(f"Нет монтажников {AUTOPLAN_SITE} — ставить работу некому")
    return _Scope(
        stored=stored, tasks=tasks, eligible=eligible, new_tasks=new_tasks, skipped=tuple(skipped),
        considered=tuple(considered), team_rows=team_rows, operators=operators, warnings=tuple(warnings),
        assigner_site=assigner.site if assigner is not None else "",
    )


def _prepare(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
    ready: Sequence[ReadyCard] = (),
) -> AutoplanPreview:
    scope = _scope(command, uow, cards, ready)
    start, end = _horizon(command)
    now = command.now
    tasks = scope.tasks
    warnings = list(scope.warnings)
    progress = _progress(uow, [task.id for task in scope.eligible])
    free: set[UUID] = set()
    unplaced: list[Unplaced] = []
    movable: set[UUID] = set()
    for task in scope.eligible:
        for operation in task.operations:
            done = progress.by_operation.get(operation.id, Decimal(0))
            # Сдано в штуках операции: ветка «2 на изделие» сделана, когда сдано вдвое больше тиража.
            if done >= task.operation_units(operation.id):
                continue
            if not operation.shares:
                if done > 0:
                    unplaced.append(Unplaced(task.id, operation.id, "Работа уже начата без назначения — назначьте вручную"))
                else:
                    free.add(operation.id)
            elif _auto_movable(operation, done, start, end):
                free.add(operation.id)
                movable.add(operation.id)
    for item in scope.new_tasks.values():
        free.update(operation.id for operation in item.task.operations)
    # Переставляемые операции идут в расчёт без своих прежних окон: их время свободно.
    planned_tasks = tuple(
        replace(task, operations=tuple(
            operation.with_shares((), operation.assigned_by) if operation.id in movable else operation
            for operation in task.operations
        )) if any(operation.id in movable for operation in task.operations) else task
        for task in sorted(tasks.values(), key=lambda value: (value.created_at, str(value.id)))
    ) + tuple(item.task for item in sorted(scope.new_tasks.values(), key=lambda value: (value.card.project, value.card.index)))
    team = tuple(item.id for item in scope.team_rows)
    absences = tuple(uow.calendar.absences())
    busy = _with_absences(
        _busy(planned_tasks, UUID(int=0), UUID(int=0), now, _load_clock(uow, now, planned_tasks)), absences, team, now.tzinfo,
    )
    skills = _skills(uow)
    holidays = uow.calendar.holidays()
    shift = _shift(uow.calendar)
    keys: dict[UUID, SetupKey] = {}
    steps: dict[UUID, tuple[MachineStep, ...]] = {}
    used_cards: dict[str, int] = {}
    free_tasks = {task.id for task in planned_tasks if any(operation.id in free for operation in task.operations)}
    for task in planned_tasks:
        planned = task.id in free_tasks or any(operation.planned_start for operation in task.operations)
        if not planned:
            continue
        card = cards.get(card_key(task.project, task.item))
        if card is not None:
            used_cards[str(card.id)] = card.version
        task_keys, task_warnings = operation_keys(task, card.document if card is not None else None)
        keys.update(task_keys)
        # Станки по операциям ЭТК; код, разошедшийся с техкартой, уже в предупреждениях наладки.
        steps.update(task_steps(task, card.document if card is not None else None)[0])
        if task.id in free_tasks:
            warnings.extend(task_warnings)
    machines = machine_names(keys, steps)
    capacity = {machine: max(1, int(units.get(machine, 0)) or 1) for machine in machines}
    needed = machine_names({item: keys[item] for item in free}, {item: steps[item] for item in free if item in steps})
    for machine in machines:
        if machine in needed and machine not in units:
            warnings.append(f"Станок {machine} не найден в БД.ОБ — считаю, что он один")
    data = AutoplanInput(
        tasks=planned_tasks, free=frozenset(free), keys=keys, team=team, skills=skills, busy=busy,
        capacity=capacity, start=start, end=end, holidays=holidays, shift=shift, steps=steps,
    )
    result = plan(data)
    fingerprint = _fingerprint({
        "mode": command.mode.value,
        "project": normalize_project_number(command.project) if command.mode is AutoplanMode.PROJECT else "",
        "people": sorted(str(value) for value in command.people) if command.mode is AutoplanMode.PROJECT else [],
        "exclude": sorted(str(value) for value in command.exclude),
        "date_to": command.date_to, "actor_site": scope.assigner_site,
        "runs": sorted([str(key), value] for key, value in command.runs),
        "tasks": sorted([str(task.id), task.version] for task in scope.stored),
        # КБ к запуску: список и версия выданной техкарты (новая ревизия, тираж, уже запущенное или срок — другой план).
        "ready": sorted([str(card.techcard_id), card.version, card.revision, card.run, card.launched, card.deadline] for card in scope.considered),
        "team": [[str(item.id), item.name, item.site] for item in scope.team_rows],
        "skills": sorted(
            [code, str(operator_id), skill.allowed, str(skill.coefficient)]
            for code, rows in skills.items() for operator_id, skill in rows.items()
        ),
        "absences": sorted([str(item.operator_id), item.from_date, item.to_date] for item in absences),
        "holidays": sorted(holidays), "shift": repr(shift),
        "progress": sorted([str(key), str(value)] for key, value in progress.by_operation.items()),
        "cards": sorted(used_cards.items()), "units": sorted(units.items()),
    })
    all_tasks = {**tasks, **{task_id: item.task for task_id, item in scope.new_tasks.items()}}
    return AutoplanPreview(
        result=result,
        unplaced=(*unplaced, *result.unplaced),
        warnings=(*warnings, *result.warnings),
        fingerprint=fingerprint,
        tasks=all_tasks,
        new_tasks=dict(scope.new_tasks),
        skipped=scope.skipped,
        names={item.id: item.name for item in scope.operators},
        batches=batches(result.placements),
        period=(start, _last_day(command, start, end, result.placements, [*scope.eligible, *(item.task for item in scope.new_tasks.values())])),
        scope=(len([task for task in scope.eligible if task.id in free_tasks]), len(scope.new_tasks), len(team)),
    )


def machine_names(keys: Mapping[UUID, SetupKey], steps: Mapping[UUID, Sequence[MachineStep]]) -> list[str]:
    """Учётные станки работы: каждой операции ЭТК со станком, у операции без шагов — станок ключа наладки."""
    return sorted(
        {step.machine for own in steps.values() for step in own}
        | {key.machine for operation_id, key in keys.items() if key.limited and operation_id not in steps}
    )


def _last_day(
    command: AutoplanCommand, start: datetime, end: datetime, placements: Sequence[Placement],
    tasks: Sequence[ProductionTask],
) -> date:
    """Последний день плана: «до даты» мастера, иначе самый поздний срок включённой работы или окончание
    последней поставленной операции — что позже, но не дальше горизонта."""
    if command.date_to is not None:
        return command.date_to
    limit = (end - timedelta(microseconds=1)).date()
    days = [task.deadline for task in tasks if task.deadline is not None and task.deadline >= start.date()]
    days += [(item.finish - timedelta(microseconds=1)).date() for item in placements]
    return min(max(days, default=start.date()), limit)


def preview_autoplan(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
    ready: Sequence[ReadyCard] = (),
) -> AutoplanPreview:
    with uow:
        return _prepare(command, uow, cards, units, ready)


def apply_autoplan(
    command: ApplyAutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
    ready: Sequence[ReadyCard] = (),
) -> AppliedAutoplan:
    if not command.items:
        raise ProductionValidationError("Не выбрано ни одной операции")
    if len({item[1] for item in command.items}) != len(command.items):
        raise ProductionValidationError("Операция выбрана дважды")
    actor, now = command.plan.actor, command.plan.now
    with uow:
        for task_id in sorted({item[0] for item in command.items}, key=str):
            uow.production.lock(task_id)
        # Номера запусков КБ — до расчёта: «Создать задание», прошедшее после этого чтения, займёт тот же номер, и
        # запуск автоплана упрётся в уникальность, а не создаст второе задание по КБ.
        seen = uow.production.last_launch_seqs([card.techcard_id for card in ready])
        preview = _prepare(command.plan, uow, cards, units, ready)
        if preview.fingerprint != command.fingerprint:
            raise StaleAutoplan("Данные изменились после предпросмотра — пересчитайте автоплан")
        proposed = {item.operation_id: item for item in preview.placements}
        chosen: dict[UUID, dict[UUID, Placement]] = {}
        for task_id, operation_id, operator_id in command.items:
            placement = proposed.get(operation_id)
            if placement is None or placement.task_id != task_id or placement.operator_id != operator_id:
                raise StaleAutoplan("План изменился после предпросмотра — пересчитайте автоплан")
            chosen.setdefault(task_id, {})[operation_id] = placement
        mark = f"{AUTOPLAN_MARK}{actor.name}"
        updated: list[ProductionTask] = []
        created: list[ProductionTask] = []
        # Сначала запущенные (по номеру), потом новые КБ (по проекту и индексу) — номера ПЗ идут в этом порядке.
        order = sorted(chosen, key=lambda value: (
            value in preview.new_tasks,
            (preview.new_tasks[value].card.project, preview.new_tasks[value].card.index)
            if value in preview.new_tasks else (preview.tasks[value].number, ""),
        ))
        for task_id in order:
            own = chosen[task_id]
            if task_id in preview.new_tasks:
                new = preview.new_tasks[task_id]
                # Один ключ на техкарту в этом плане: повтор того же применения не запустит КБ дважды.
                try:
                    task, fresh = launch_task(_launch_command(
                        new.card, new.task.quantity, f"autoplan:{new.card.techcard_id}:{command.fingerprint[:32]}",
                        actor, now, seen.get(new.card.techcard_id, 0),
                    ), uow)
                except DuplicateLaunch as error:
                    raise StaleAutoplan("Задание по этой КБ уже создано — пересчитайте автоплан") from error
                if not fresh:
                    raise StaleAutoplan("Задание по этой КБ уже создано — пересчитайте автоплан")
                created.append(task)
                # Операции созданного задания — те же этапы маршрута, что в расчёте: сопоставляем по номеру этапа.
                by_position = {operation.position: operation.id for operation in task.operations}
                own = {
                    by_position[operation.position]: replace(own[operation.id], task_id=task.id, operation_id=by_position[operation.position])
                    for operation in new.task.operations if operation.id in own
                }
            else:
                task = preview.tasks[task_id]
            result = task.apply_schedule(tuple(
                (operation.id, (own[operation.id].operator_id,), own[operation.id].start, own[operation.id].finish)
                if operation.id in own else
                (operation.id, operation.operator_ids, operation.planned_start, operation.planned_finish)
                for operation in task.operations
            ), mark)
            uow.production.add(result)
            updated.append(result)
        applied = [placement for own in chosen.values() for placement in own.values()]
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=actor.id, event_type="production.autoplan_applied",
            actor=actor, occurred_at=now,
            details={
                "mode": command.plan.mode.value, "project": command.plan.project,
                "people": [str(value) for value in command.plan.people],
                "date_from": preview.period[0].date().isoformat(), "date_to": preview.period[1].isoformat(),
                "master": actor.name,
                "operations": len(applied), "tasks": [item.number for item in updated],
                "created": [item.number for item in created],
                "proposed": len(preview.placements), "unplaced": len(preview.unplaced),
                "changeovers": sum(1 for item in applied if item.changeover_seconds > 0),
                "saved_seconds": sum(item.setup_seconds - item.changeover_seconds for item in applied),
                "fingerprint": command.fingerprint,
            },
        ))
        uow.commit()
        return AppliedAutoplan(tuple(updated), tuple(created), len(applied), preview)


def _merged(intervals: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, finish in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], finish))
        else:
            result.append((start, finish))
    return result


def autoplan_context(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    ready: Sequence[ReadyCard] = (),
) -> dict:
    """Что увидит автоплан до расчёта: задания и КБ к запуску, проекты по сроку, монтажники со свободными часами
    на ориентировочный период (до самого позднего срока работы или до «до даты»). Ничего не пишет."""
    with uow:
        shop = replace(command, mode=AutoplanMode.SHOP, exclude=frozenset(), people=())
        whole = _scope(shop, uow, cards, ready)
        project_mode = command.mode is AutoplanMode.PROJECT and str(command.project or "").strip()
        start, end = _horizon(command)
        tasks = [task for task in whole.eligible if not project_mode or _in_project(command, task.project)]
        new = [card for card in whole.considered if not project_mode or _in_project(command, card.project)]
        if command.date_to is not None:
            last = command.date_to
        else:
            days = [value for value in (*(task.deadline for task in tasks), *(card.deadline for card in new))
                    if value is not None and value >= start.date()]
            limit = (end - timedelta(microseconds=1)).date()
            last = min(max(days, default=start.date() + timedelta(days=DEFAULT_CONTEXT_DAYS - 1)), limit)
        finish = datetime.combine(last + timedelta(days=1), time.min, command.now.tzinfo)
        team = tuple(item.id for item in whole.team_rows)
        holidays = uow.calendar.holidays()
        shift = _shift(uow.calendar)
        planned = tuple(whole.tasks.values())
        busy = _with_absences(_busy(planned, UUID(int=0), UUID(int=0), command.now, _load_clock(uow, command.now, planned)),
                              tuple(uow.calendar.absences()), team, command.now.tzinfo)
        total = work_seconds_between(start, finish, holidays, shift) if finish > start else 0

        def free_seconds(operator_id: UUID) -> int:
            taken = sum(
                work_seconds_between(max(item_start, start), min(item_finish, finish), holidays, shift)
                for item_start, item_finish in _merged([(item.start, item.finish) for item in busy.get(operator_id, ())])
                if item_finish > start and item_start < finish
            )
            return max(0, total - taken)

        skipped = {item.card.techcard_id: item.reason for item in whole.skipped}
        projects: dict[str, dict] = {}
        # Проекты для «По проекту»: где есть что ставить — запущенные задания и КБ с тиражом без задания.
        for project, deadline, kind in (
            *((task.project, task.deadline, "tasks") for task in whole.eligible),
            *((card.project, card.deadline, "new") for card in whole.considered if card.techcard_id not in skipped),
        ):
            row = projects.setdefault(normalize_project_number(project), {"project": project, "deadline": None, "tasks": 0, "new": 0})
            row[kind] += 1
            if deadline is not None and (row["deadline"] is None or deadline < row["deadline"]):
                row["deadline"] = deadline
        return {
            "period": {"start": start.isoformat(), "last_day": last.isoformat(), "auto": command.date_to is None},
            "max_days": MAX_HORIZON_DAYS,
            "tasks": [{
                "id": str(task.id), "number": task.number, "project": task.project, "item": task.item,
                "name": task.name, "deadline": task.deadline.isoformat() if task.deadline else None,
            } for task in tasks],
            "new": [{
                "techcard_id": str(card.techcard_id), "project": card.project, "index": card.index, "name": card.name,
                "run": card.run, "launched": card.launched, "quantity": card.remainder,
                "deadline": card.deadline.isoformat() if card.deadline else None,
                "operations": len(card.route.steps), "skipped": skipped.get(card.techcard_id, ""),
            } for card in new],
            "projects": [{**row, "deadline": row["deadline"].isoformat() if row["deadline"] else None} for row in sorted(
                projects.values(), key=lambda row: (row["deadline"] or date.max, row["project"]),
            )],
            "fitters": [{
                "id": str(item.id), "name": item.name, "site": item.site, "free_seconds": free_seconds(item.id),
            } for item in whole.team_rows],
        }


def describe(preview: AutoplanPreview) -> dict:
    """Ответ предпросмотра для экрана мастера."""
    tasks = preview.tasks
    operations = {operation.id: operation for task in tasks.values() for operation in task.operations}

    def placement(item: Placement) -> dict:
        task = tasks[item.task_id]
        operation = operations[item.operation_id]
        return {
            "task_id": str(item.task_id), "task_version": task.version, "task_number": task.number,
            "new": item.task_id in preview.new_tasks,
            "operation_id": str(item.operation_id), "position": operation.position,
            "stage": stage_name(operation.stage), "code": operation.code,
            "operator_id": str(item.operator_id), "operator_name": preview.names.get(item.operator_id, ""),
            "start": item.start.isoformat(), "finish": item.finish.isoformat(),
            "machine": item.key.machine_label, "setup_label": item.key.label,
            "changeover_seconds": item.changeover_seconds, "setup_seconds": item.setup_seconds,
            "batched": item.pulled,
        }

    result = preview.result
    return {
        "period": {"start": preview.period[0].isoformat(), "last_day": preview.period[1].isoformat()},
        "scope": {"tasks": preview.scope[0], "new": preview.scope[1], "fitters": preview.scope[2]},
        "new_tasks": [{
            "task_id": str(task_id), "techcard_id": str(item.card.techcard_id), "project": item.card.project,
            "index": item.card.index, "name": item.card.name, "quantity": item.task.quantity,
            "deadline": item.card.deadline.isoformat() if item.card.deadline else None,
        } for task_id, item in sorted(preview.new_tasks.items(), key=lambda pair: (pair[1].card.project, pair[1].card.index))],
        "proposals": [placement(item) for item in result.placements],
        "batches": [{
            "operator_id": str(operator_id), "operator_name": preview.names.get(operator_id, ""),
            "batches": [{
                "label": batch.label, "machine": batch.machine_label, "operations": len(batch.operations),
                "tasks": list(dict.fromkeys(tasks[item.task_id].number for item in batch.operations)),
                "start": batch.start.isoformat(), "finish": batch.finish.isoformat(),
                "changeover_seconds": batch.changeover_seconds,
            } for batch in own],
        } for operator_id, own in sorted(preview.batches.items(), key=lambda item: (preview.names.get(item[0], ""), str(item[0])))],
        "unplaced": [{
            "operation_id": str(item.operation_id), "task_id": str(item.task_id),
            "task_number": tasks[item.task_id].number if item.task_id in tasks else "",
            "position": operations[item.operation_id].position if item.operation_id in operations else None,
            "reason": item.reason,
        } for item in preview.unplaced] + [{
            "operation_id": None, "task_id": None, "techcard_id": str(item.card.techcard_id),
            "task_number": f"{item.card.project} {item.card.index}", "position": None, "reason": item.reason,
        } for item in preview.skipped],
        "warnings": list(dict.fromkeys(preview.warnings)),
        "stats": {
            "changeovers": result.changeovers, "changeovers_naive": result.changeovers_naive,
            "saved_seconds": result.saved_seconds,
        },
        "fingerprint": preview.fingerprint,
    }


def equipment_numbers(records: Sequence[Mapping[str, str]]) -> list[str]:
    """Инвентарные номера записей БД.ОБ (колонка «Инв. номер»)."""
    result: list[str] = []
    for fields in records:
        value = next((text for name, text in fields.items() if "инв" in str(name).casefold()), "")
        if str(value or "").strip():
            result.append(str(value))
    return result
