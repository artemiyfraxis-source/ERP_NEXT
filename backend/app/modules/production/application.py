"""Transactional production-task launch, assignment, and calendar use cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import logging
from math import ceil
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .dispatch import (
    DispatchCalendar,
    Move,
    NodeKey,
    Progress,
    apply_moves,
    local,
    localize,
    place,
    reflow,
    remainder,
    share_seconds,
    split_shares,
)
from .launchable import stage_name
from .domain import (
    BusyInterval,
    DEFAULT_SHIFT,
    UNPLANNED_SITES,
    FlowMode,
    OperationShare,
    OperatorSkill,
    PlanResult,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
    TaskDelivery,
    WorkShift,
    add_work_seconds,
    delivered_total,
    next_working_moment,
    plan_operation,
    skill_of,
)
from .flow import FlowPlan, FlowStep, plan_flow
from .scheduling import ScheduleVariant, schedule_task, schedule_variants, upstream_ids, waited_input

logger = logging.getLogger(__name__)


class ConcurrentProductionUpdate(RuntimeError):
    pass


@dataclass(frozen=True)
class Operator:
    id: UUID
    name: str
    site: str
    active: bool
    department: str = ""  # отдел из справочника сотрудников, для списка монтажников на экране мастера
    # Флаги карточки сотрудника (уровни доступа, 04.10): «Монтажник (работает в цеху)» и «Мастер площадки».
    shop_worker: bool = False
    site_master: bool = False

    @property
    def installer(self) -> bool:
        """Монтажник (владелец, 29.09: «тут только монтажники должны быть»): активный сотрудник с флагом «Монтажник
        (работает в цеху)» — уровень доступа и должность не важны. Без флага (директора, мастера, снабжение,
        админ-учётки) работу не получают. Люди площадок без нормирования (ЖУК, владелец 30.09) по часам не
        расписываются — им отдают задание целиком."""
        return self.active and self.shop_worker and self.site not in UNPLANNED_SITES


@dataclass(frozen=True)
class Absence:
    id: UUID
    operator_id: UUID
    from_date: date
    to_date: date
    kind: str
    # Written from a registered leave request (identity/leave.py); None — the master's own row.
    leave_request_id: UUID | None = None


ABSENCE_KINDS = frozenset({"Отпуск", "Больничный", "Отгул", "Командировка", "Учёба"})


class ProductionRepository(Protocol):
    def get(self, task_id: UUID) -> ProductionTask | None: ...
    def by_idempotency_key(self, key: str) -> ProductionTask | None: ...
    def list(self, limit: int = 200, after_id: UUID | None = None) -> Sequence[ProductionTask]: ...
    def for_project(self, project: str) -> Sequence[ProductionTask]: ...
    def with_status(self, status: ProductionStatus) -> Sequence[ProductionTask]: ...
    def planned(self, since: datetime) -> Sequence[ProductionTask]: ...
    def lock(self, task_id: UUID) -> None: ...
    def reserve_number(self) -> str: ...
    def add(self, task: ProductionTask) -> None: ...
    def worked(self, task_ids: Sequence[UUID]) -> set[UUID]: ...
    def delete(self, task_id: UUID) -> None: ...


class OperatorRepository(Protocol):
    def get(self, operator_id: UUID) -> Operator | None: ...
    def list(self) -> Sequence[Operator]: ...


class CalendarRepository(Protocol):
    def holidays(self) -> frozenset[date]: ...
    def absences(self) -> Sequence[Absence]: ...
    def shift(self) -> WorkShift: ...
    def replace(self, holidays: frozenset[date], absences: tuple[Absence, ...], shift: WorkShift) -> None: ...
    def replace_leave_absences(self, absences: tuple[Absence, ...]) -> int: ...


class SkillRepository(Protocol):
    def list(self) -> Sequence[OperatorSkill]: ...
    def replace_for_operator(self, operator_id: UUID, skills: tuple[OperatorSkill, ...]) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class SerialAllocator(Protocol):
    def ensure_task(self, task_id: UUID, task_number: str, quantity: int) -> bool: ...


class UnitOfWork(Protocol):
    production: ProductionRepository
    operators: OperatorRepository
    calendar: CalendarRepository
    skills: SkillRepository
    audit: AppendOnlyRepository
    serials: SerialAllocator

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class LaunchProductionCommand:
    idempotency_key: str
    project: str
    item: str
    name: str
    quantity: int
    fulfillment_request_id: UUID | None
    deadline: date | None
    operations: tuple[ProductionOperation, ...]
    actor: Actor
    now: datetime
    # Техкарта и её ревизия, с которых взяты нормы (план задания, «Обновить нормы из ЭТК»).
    techcard_id: UUID | None = None
    techcard_revision: int | None = None


@dataclass(frozen=True)
class AssignProductionCommand:
    task_id: UUID
    expected_version: int
    operation_id: UUID
    operator_ids: tuple[UUID, ...]
    requested_start: datetime | None
    actor: Actor
    now: datetime
    dry: bool = False


@dataclass(frozen=True)
class AssignShareCommand:
    """Доля операции монтажнику с экрана мастера: штуки и момент, с которого ставить (или снять долю)."""
    task_id: UUID
    expected_version: int
    operation_id: UUID
    operator_id: UUID
    quantity: int | None
    requested_start: datetime | None
    actor: Actor
    now: datetime
    remove: bool = False


@dataclass(frozen=True)
class HandOverProductionCommand:
    task_id: UUID
    expected_version: int
    site: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetProductionCancelledCommand:
    task_id: UUID
    expected_version: int
    cancelled: bool
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ReplaceProductionCalendarCommand:
    holidays: frozenset[date]
    absences: tuple[Absence, ...]
    actor: Actor
    now: datetime
    shift: WorkShift = DEFAULT_SHIFT


@dataclass(frozen=True)
class ReplaceOperatorSkillsCommand:
    operator_id: UUID
    skills: tuple[OperatorSkill, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class AutoScheduleCommand:
    task_id: UUID
    expected_version: int
    replan: bool
    max_crew: int
    actor: Actor
    now: datetime


def _fingerprint(command: LaunchProductionCommand) -> str:
    payload = {
        "project": command.project.strip(),
        "item": command.item.strip(),
        "name": command.name.strip(),
        "quantity": command.quantity,
        "fulfillment_request_id": str(command.fulfillment_request_id or ""),
        "deadline": command.deadline.isoformat() if command.deadline else None,
        "operations": [
            {
                "position": operation.position,
                "code": operation.code,
                "stage": operation.stage,
                "description": operation.description,
                "seconds_per_unit": str(operation.seconds_per_unit),
                "setup_seconds": str(operation.setup_seconds),
            }
            for operation in command.operations
        ],
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _audit(task: ProductionTask, event_type: str, actor: Actor, now: datetime, details: dict, uow: UnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=task.id, event_type=event_type,
        actor=actor, occurred_at=now, details=details,
    ))


def launch_production(command: LaunchProductionCommand, uow: UnitOfWork) -> ProductionTask:
    with uow:
        task, created = launch_task(command, uow)
        if created or uow.serials.ensure_task(task.id, task.number, task.quantity):
            uow.commit()
        return task


def launch_task(command: LaunchProductionCommand, uow: UnitOfWork) -> tuple[ProductionTask, bool]:
    """Запуск задания внутри открытой единицы работы, без коммита: номер ПЗ, ключ идемпотентности, серийники, аудит.
    Один путь для «Создать задание» и автоплана (он пишет задание и его расписание одним коммитом).
    Возвращает задание и True, если оно создано сейчас (False — ключ уже запускал это же задание)."""
    key = str(command.idempotency_key or "").strip()
    if not key:
        raise ProductionValidationError("Idempotency key обязателен")
    fingerprint = _fingerprint(command)
    existing = uow.production.by_idempotency_key(key)
    if existing is not None:
        if existing.source_fingerprint != fingerprint:
            raise ProductionValidationError("Idempotency key уже использован для другого задания")
        return existing, False
    task = ProductionTask.create(
        number=uow.production.reserve_number(),
        source_idempotency_key=key,
        source_fingerprint=fingerprint,
        project=command.project,
        item=command.item,
        name=command.name,
        quantity=command.quantity,
        fulfillment_request_id=command.fulfillment_request_id,
        deadline=command.deadline,
        launched_by=command.actor.name,
        operations=command.operations,
        created_at=command.now,
        techcard_id=command.techcard_id,
        techcard_revision=command.techcard_revision,
    )
    uow.production.add(task)
    uow.serials.ensure_task(task.id, task.number, task.quantity)
    _audit(task, "production.launched", command.actor, command.now, {"number": task.number}, uow)
    return task, True


def _task(command_task_id: UUID, expected_version: int, uow: UnitOfWork) -> ProductionTask:
    task = uow.production.get(command_task_id)
    if task is None:
        raise KeyError(f"Production task not found: {command_task_id}")
    if task.version != expected_version:
        raise ConcurrentProductionUpdate("Задание изменилось после загрузки формы")
    return task


def _same_timezone(value: datetime, reference: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _shift(calendar: CalendarRepository) -> WorkShift:
    loader = getattr(calendar, "shift", None)
    return loader() if loader is not None else DEFAULT_SHIFT


def _busy(tasks: Sequence[ProductionTask], task_id: UUID, operation_id: UUID, reference: datetime) -> dict[UUID, tuple[BusyInterval, ...]]:
    result: dict[UUID, list[BusyInterval]] = {}
    for task in tasks:
        if task.status is ProductionStatus.CANCELLED:
            continue
        for operation in task.operations:
            if task.id == task_id and operation.id == operation_id:
                continue
            # Занятость — по долям: у раздробленной операции у каждого своё окно.
            for share in operation.shares:
                if share.planned_start is None or share.planned_finish is None:
                    continue
                result.setdefault(share.operator_id, []).append(BusyInterval(
                    f"{task.number} · этап {operation.position}",
                    _same_timezone(share.planned_start, reference),
                    _same_timezone(share.planned_finish, reference),
                ))
    return {key: tuple(value) for key, value in result.items()}


def _with_absences(
    busy: dict[UUID, tuple[BusyInterval, ...]],
    absences: Sequence[Absence],
    operator_ids: tuple[UUID, ...],
    tzinfo,
) -> dict[UUID, tuple[BusyInterval, ...]]:
    result = {key: list(value) for key, value in busy.items()}
    selected = set(operator_ids)
    for absence in absences:
        if absence.operator_id not in selected:
            continue
        result.setdefault(absence.operator_id, []).append(BusyInterval(
            f"{absence.kind} {absence.from_date.isoformat()}…{absence.to_date.isoformat()}",
            datetime.combine(absence.from_date, time.min, tzinfo),
            datetime.combine(absence.to_date + timedelta(days=1), time.min, tzinfo),
        ))
    return {key: tuple(value) for key, value in result.items()}


def _checked_names(
    uow: UnitOfWork,
    operation: ProductionOperation,
    operator_ids: tuple[UUID, ...],
    actor: Actor,
) -> dict[UUID, str]:
    names: dict[UUID, str] = {}
    for operator_id in operator_ids:
        operator = uow.operators.get(operator_id)
        if operator is None or not operator.active:
            raise ProductionValidationError("Оператора нет в справочнике сотрудников")
        if operator.site in UNPLANNED_SITES and operator_id not in operation.operator_ids:
            raise ProductionValidationError(
                f"{operator.name} — площадка {operator.site}: по людям не расписывается, отдайте задание «Отдать в {operator.site}»"
            )
        if not operator.installer and operator_id not in operation.operator_ids:
            raise ProductionValidationError(f"{operator.name} не монтажник: работу назначают только монтажникам")
        names[operator_id] = operator.name
    # Площадка мастера (владелец, 29.09: в Жуковском распределяет свой мастер): мастер с площадкой ставит только
    # сотрудников своей площадки; без площадки (директор, технолог, снабжение) — всех. Уже стоящих не выгоняем.
    assigner = uow.operators.get(actor.id)
    if assigner is not None and assigner.site:
        strangers = [
            names[operator_id] for operator_id in operator_ids
            if operator_id not in operation.operator_ids and uow.operators.get(operator_id).site != assigner.site
        ]
        if strangers:
            raise ProductionValidationError(
                f"Площадка {assigner.site}: назначать можно только своих сотрудников, не {', '.join(strangers)}"
            )
    return names


def _skills(uow: UnitOfWork) -> dict[str, dict[UUID, OperatorSkill]]:
    skills: dict[str, dict[UUID, OperatorSkill]] = {}
    for item in uow.skills.list():
        skills.setdefault(item.operation_code, {})[item.operator_id] = item
    return skills


def assign_production(command: AssignProductionCommand, uow: UnitOfWork) -> tuple[ProductionTask, PlanResult]:
    with uow:
        task = _task(command.task_id, command.expected_version, uow)
        operation = task.operation(command.operation_id)
        names = _checked_names(uow, operation, command.operator_ids, command.actor)
        skills = _skills(uow)
        tasks = uow.production.list(limit=10_000)
        busy = _with_absences(
            _busy(tasks, task.id, operation.id, command.now),
            uow.calendar.absences(),
            command.operator_ids,
            command.now.tzinfo,
        )
        # Не раньше всего, что идёт в эту операцию по схеме входов (в цепочке — все прежние этапы).
        upstream = upstream_ids(task.operations, operation.id)
        previous_finishes = [
            _same_timezone(item.planned_finish, command.now) for item in task.operations
            if item.id in upstream and item.planned_finish is not None
        ]
        earliest = max([command.now, *previous_finishes])
        plan = plan_operation(
            operation=operation,
            quantity=task.operation_units(operation.id),
            operator_ids=command.operator_ids,
            requested_start=_same_timezone(command.requested_start, command.now) if command.requested_start else None,
            earliest_start=earliest,
            busy=busy,
            holidays=uow.calendar.holidays(),
            shift=_shift(uow.calendar),
            skills=skills,
            names=names,
        )
        # Сроки других операций пришли из базы: SQLite отдаёт их без пояса, PostgreSQL — с поясом. Новый план
        # пишется в той же форме, иначе сроки задания не сравнить между собой.
        stored = next((
            value for item in task.operations if item.id != operation.id
            for value in (item.planned_start, item.planned_finish) if value is not None
        ), None)
        if stored is not None:
            plan = replace(
                plan,
                start=_same_timezone(plan.start, stored) if plan.start else None,
                finish=_same_timezone(plan.finish, stored) if plan.finish else None,
            )
        updated = task.assign(operation.id, command.operator_ids, plan, command.actor.name)
        if command.dry:
            # prodAssign_ dry: the preview is computed exactly like the save and nothing is written.
            return task, plan
        uow.production.add(updated)
        _audit(updated, "production.assigned", command.actor, command.now, {
            "operation_id": str(operation.id),
            "operator_ids": [str(value) for value in command.operator_ids],
            "planned_start": plan.start.isoformat() if plan.start else None,
            "planned_finish": plan.finish.isoformat() if plan.finish else None,
            "warnings": list(plan.warnings),
        }, uow)
        uow.commit()
        return updated, plan


def _dispatch_calendar(uow: UnitOfWork, now: datetime) -> DispatchCalendar:
    absences: dict[UUID, list[tuple[datetime, datetime]]] = {}
    for absence in uow.calendar.absences():
        absences.setdefault(absence.operator_id, []).append((
            datetime.combine(absence.from_date, time.min, now.tzinfo),
            datetime.combine(absence.to_date + timedelta(days=1), time.min, now.tzinfo),
        ))
    return DispatchCalendar(
        uow.calendar.holidays(), _shift(uow.calendar),
        {key: tuple(value) for key, value in absences.items()},
    )


def _progress(uow, task_ids) -> Progress:
    """Сданное по операциям — чтобы начатую и выполненную работу не двигать."""
    outputs = getattr(uow, "outputs", None)
    ids = tuple(task_ids)
    if outputs is None or not ids:
        return Progress.empty()
    by_operation: dict[UUID, Decimal] = {}
    by_operator: dict[tuple[UUID, UUID], Decimal] = {}
    for output in outputs.active_for_tasks(ids):
        amount = output.good_quantity + output.defect_quantity
        by_operation[output.operation_id] = by_operation.get(output.operation_id, Decimal(0)) + amount
        key = (output.operation_id, output.operator_id)
        by_operator[key] = by_operator.get(key, Decimal(0)) + amount
    return Progress(by_operation, by_operator)


# Работа, которая по плану кончилась больше месяца назад, план по факту уже не двигает — не читаем её.
PLAN_HORIZON = timedelta(days=30)


def _live_tasks(uow: UnitOfWork, now: datetime) -> dict[UUID, ProductionTask]:
    return {item.id: localize(item, now) for item in uow.production.planned(now - PLAN_HORIZON)}


def _moment(value: datetime) -> str:
    return f"{value:%d.%m %H:%M}"


def _save_moves(
    uow: UnitOfWork,
    tasks: dict[UUID, ProductionTask],
    touched: set[UUID],
    moves: dict[NodeKey, Move],
    actor: Actor,
    now: datetime,
    reason: str,
) -> list[str]:
    """Сохраняет сдвинутую работу (версия задания +1 один раз) и возвращает строки для мастера."""
    by_task: dict[UUID, list[Move]] = {}
    for move in moves.values():
        by_task.setdefault(move.task.id, []).append(move)
    names = {item.id: item.name for item in uow.operators.list()}
    lines: list[str] = []
    for task_id, items in by_task.items():
        updated = apply_moves(tasks[task_id], items)
        if task_id not in touched:
            updated = replace(updated, version=updated.version + 1)
        tasks[task_id] = updated
        touched.add(task_id)
        _audit(updated, "production.replanned", actor, now, {
            "reason": reason,
            "moves": [{
                "operation_id": str(item.operation.id),
                "operator_ids": [str(value) for value in item.operator_ids],
                "planned_start": item.start.isoformat(), "planned_finish": item.finish.isoformat(),
            } for item in items],
        }, uow)
        for item in sorted(items, key=lambda move: move.start):
            people = ", ".join(names.get(value, "?") for value in item.operator_ids)
            lines.append(
                f"Сдвинуто: {updated.number} · этап {item.operation.position} «{stage_name(item.operation.stage)}» "
                f"({people}) — {_moment(item.start)}–{_moment(item.finish)}"
            )
    for task_id in touched:
        uow.production.add(tasks[task_id])
    return lines


def assign_share(command: AssignShareCommand, uow: UnitOfWork) -> tuple[ProductionTask, PlanResult, tuple[str, ...]]:
    """Экран мастера: доля операции монтажнику — штуки и момент, с которого ставить.

    Доля встаёт в первое свободное время монтажника с этого момента (dispatch.place): не влезает в остаток дня —
    хвост уходит на следующие рабочие дни, его работа после неё сдвигается. Штуки сверх свободных забираются у
    других долей этой операции (dispatch.split_shares), их окна укорачиваются."""
    with uow:
        # Факт монтажника по этому заданию (application.record_fact) и мастер пишут по очереди.
        uow.production.lock(command.task_id)
        loaded = _task(command.task_id, command.expected_version, uow)
        if loaded.status is ProductionStatus.DONE:
            raise ProductionValidationError(f"Задание {loaded.number} выполнено — назначать нельзя")
        now = command.now
        tasks = _live_tasks(uow, now)
        task = tasks.get(loaded.id) or localize(loaded, now)
        tasks[task.id] = task
        operation = task.operation(command.operation_id)
        current = operation.share_of(command.operator_id)
        if command.remove:
            return _remove_share(uow, tasks, task, operation, current, command)
        names = _checked_names(uow, operation, (command.operator_id,), command.actor)
        if current is not None and (current.started_at is not None or current.finished_at is not None):
            raise ProductionValidationError(f"{names[command.operator_id]} уже взял(а) эту операцию — её время идёт по факту")
        # Штуки по умолчанию: своя доля (у бригады — поровну), иначе нераспределённое, иначе поровну с назначенными.
        if command.quantity is not None:
            quantity = command.quantity
        elif current is not None:
            quantity = current.quantity or ceil(task.quantity / len(operation.shares))
        else:
            quantity = remainder(operation, task.quantity) or ceil(task.quantity / (len(operation.shares) + 1))
        split = split_shares(operation, task.quantity, command.operator_id, quantity)
        skills = _skills(uow)
        calendar = _dispatch_calendar(uow, now)
        progress = _progress(uow, tasks.keys())
        sources: dict[NodeKey, tuple[datetime, datetime] | None] = {}
        crew_window = (
            (operation.planned_start, operation.planned_finish)
            if operation.shares and not operation.split and operation.planned_start is not None else None
        )
        # Сначала доли остальных (штуки могли уменьшиться — окно короче от прежнего начала), потом окно новой доли.
        others: dict[UUID, OperationShare] = {}
        for share in split.others:
            if share.operator_id in split.resized and share.planned_start is not None:
                seconds = share_seconds(task, operation, share.quantity, (share.operator_id,), skills)
                sources[(operation.id, share.operator_id)] = crew_window or (share.planned_start, share.planned_finish)
                share = replace(share, planned_finish=add_work_seconds(share.planned_start, seconds, calendar.holidays, calendar.shift))
            others[share.operator_id] = share
        interim = replace(task, operations=tuple(
            item.with_shares(tuple(others.values()), item.assigned_by) if item.id == operation.id else item
            for item in task.operations
        ))
        requested = local(command.requested_start, now) if command.requested_start is not None else now
        start, finish = place(
            tasks=[interim if item.id == task.id else item for item in tasks.values()],
            task=interim, operation=interim.operation(operation.id), operator_id=command.operator_id,
            quantity=quantity, requested_start=requested, calendar=calendar, skills=skills, progress=progress,
        )
        target = OperationShare(command.operator_id, quantity, start, finish)
        ordered = [
            target if share.operator_id == command.operator_id else others[share.operator_id]
            for share in operation.shares
            if share.operator_id == command.operator_id or share.operator_id in others
        ]
        if current is None:
            ordered.append(target)
        updated = task.with_operations((operation.with_shares(tuple(ordered), command.actor.name),))
        tasks[task.id] = updated
        moved_before = current is not None and current.quantity is not None and current.planned_start is not None
        sources[(operation.id, command.operator_id)] = (current.planned_start, current.planned_finish) if moved_before else None
        moves = reflow(
            tasks=tasks.values(), sources=sources, pull=False, now=now,
            calendar=calendar, skills=skills, progress=progress,
        )
        touched = {task.id}
        lines = _save_moves(uow, tasks, touched, moves, command.actor, now, "share_assigned")
        warnings: list[str] = []
        check = skill_of(skills, operation.code, command.operator_id)
        if not check.allowed:
            warnings.append(f"{names[command.operator_id]}: нет допуска к операции {operation.code}")
        if split.dropped:
            everyone = {item.id: item.name for item in uow.operators.list()}
            gone = ", ".join(everyone.get(value, "?") for value in split.dropped)
            warnings.append(f"Снят(а) с операции: {gone} — все штуки у {names[command.operator_id]}")
        previous = waited_input(updated, operation.id)
        if previous is not None:
            if start < local(previous.planned_finish, now):
                warnings.append(
                    f"Этап {previous.position} идёт до {_moment(local(previous.planned_finish, now))} — "
                    f"этот этап начнётся внахлёст"
                )
        _audit(updated, "production.share_assigned", command.actor, now, {
            "operation_id": str(operation.id), "operator_id": str(command.operator_id), "quantity": quantity,
            "planned_start": start.isoformat(), "planned_finish": finish.isoformat(),
            "resized": [str(value) for value in split.resized], "dropped": [str(value) for value in split.dropped],
        }, uow)
        uow.commit()
        return tasks[task.id], PlanResult(start, finish, tuple(warnings)), tuple(lines)


def _remove_share(
    uow: UnitOfWork,
    tasks: dict[UUID, ProductionTask],
    task: ProductionTask,
    operation: ProductionOperation,
    current: OperationShare | None,
    command: AssignShareCommand,
) -> tuple[ProductionTask, PlanResult, tuple[str, ...]]:
    """Снять монтажника с операции. Нельзя, пока у него открыта работа по ней (иначе не сдать выработку) и когда свою часть
    он уже сделал. Из бригады — окно оставшихся пересчитывается на меньший состав, их работа после сдвигается."""
    now = command.now
    if current is None:
        raise ProductionValidationError("Монтажник не назначен на эту операцию")
    name = next((item.name for item in uow.operators.list() if item.id == command.operator_id), "Монтажник")
    work = getattr(uow, "work", None)
    session = work.current(command.operator_id) if work is not None else None
    if session is not None and session.task_id == task.id and session.operation_id == operation.id:
        raise ProductionValidationError(f"{name} сейчас работает над этой операцией — снять можно после сдачи или «Отменить начало»")
    if current.finished_at is not None:
        raise ProductionValidationError(f"{name} уже закончил(а) свою часть — снимать нечего")
    rest = tuple(share for share in operation.shares if share.operator_id != command.operator_id)
    sources: dict[NodeKey, tuple[datetime, datetime] | None] = {}
    crew = [share for share in rest if share.quantity is None and share.planned_start is not None and share.planned_finish is not None]
    if current.quantity is None and crew:
        skills = _skills(uow)
        calendar = _dispatch_calendar(uow, now)
        people = tuple(share.operator_id for share in crew)
        begin = min(share.planned_start for share in crew)
        before = (begin, max(share.planned_finish for share in crew))
        finish = add_work_seconds(begin, share_seconds(task, operation, None, people, skills), calendar.holidays, calendar.shift)
        rest = tuple(replace(share, planned_start=begin, planned_finish=finish) if share.quantity is None else share for share in rest)
        sources[(operation.id, None)] = before
    updated = task.with_operations((operation.with_shares(rest, command.actor.name),))
    tasks[task.id] = updated
    moves = reflow(
        tasks=tasks.values(), sources=sources, pull=False, now=now,
        calendar=_dispatch_calendar(uow, now), skills=_skills(uow), progress=_progress(uow, tasks.keys()),
    ) if sources else {}
    lines = _save_moves(uow, tasks, {task.id}, moves, command.actor, now, "share_removed")
    _audit(tasks[task.id], "production.share_removed", command.actor, now, {
        "operation_id": str(operation.id), "operator_id": str(command.operator_id),
    }, uow)
    uow.commit()
    return tasks[task.id], PlanResult(None, None, ()), tuple(lines)


def record_fact(
    uow,
    *,
    task_id: UUID,
    operation_id: UUID,
    operator_id: UUID,
    actor: Actor,
    now: datetime,
    started: bool = False,
    mine: Decimal = Decimal(0),
    total: Decimal = Decimal(0),
) -> None:
    """Факт монтажника двигает план (владелец, 30.09: «если он раньше/позже сделал, план корректировался»).

    started — «взял»: окно доли начинается сейчас. Иначе сдача выработки (mine — сдано этим монтажником по операции,
    total — всеми): сдал свои штуки — «закончил», окно кончается сейчас; не всё — конец по оставшимся штукам.
    Работа после доли сдвигается или подтягивается (dispatch.reflow). Вызывается внутри транзакции исполнения:
    план правится в точке сохранения — если не вышло, выработка всё равно сохраняется, план остаётся прежним."""
    stored = uow.production.get(task_id)
    if stored is None or stored.status is not ProductionStatus.RUNNING or stored.handed_to:
        return
    mine_share = stored.operation(operation_id).share_of(operator_id)
    if mine_share is None or mine_share.planned_start is None or mine_share.planned_finish is None:
        return
    session = getattr(uow, "session", None)
    if session is None:
        _record_fact(uow, task_id, operation_id, operator_id, actor, now, started, mine, total)
        return
    session.flush()
    uow.production.lock(task_id)
    try:
        with session.begin_nested():
            _record_fact(uow, task_id, operation_id, operator_id, actor, now, started, mine, total)
    except Exception:
        logger.exception("План по факту не сдвинут: задание %s, операция %s", task_id, operation_id)


def release_fact(uow, *, task_id: UUID, operation_id: UUID, operator_id: UUID, now: datetime) -> None:
    """«Отменить начало» без сданных штук — «взял» снимается: долю снова можно переставить или снять."""
    session = getattr(uow, "session", None)
    if session is not None:
        session.flush()
        uow.production.lock(task_id)
    task = uow.production.get(task_id)
    if task is None:
        return
    operation = task.operation(operation_id)
    share = operation.share_of(operator_id)
    if share is None or share.started_at is None or share.finished_at is not None:
        return
    if _progress(uow, (task_id,)).by_operator.get((operation_id, operator_id), Decimal(0)) > 0:
        return
    uow.production.add(task.with_operations((operation.with_shares(tuple(
        replace(item, started_at=None) if item.operator_id == operator_id else item for item in operation.shares
    ), operation.assigned_by),)))


def _record_fact(
    uow,
    task_id: UUID,
    operation_id: UUID,
    operator_id: UUID,
    actor: Actor,
    now: datetime,
    started: bool,
    mine: Decimal,
    total: Decimal,
) -> None:
    tasks = _live_tasks(uow, now)
    task = tasks.get(task_id) or localize(uow.production.get(task_id), now)
    tasks[task.id] = task
    operation = task.operation(operation_id)
    share = operation.share_of(operator_id)
    if share is None or share.planned_start is None or share.planned_finish is None:
        return
    skills = _skills(uow)
    calendar = _dispatch_calendar(uow, now)
    crew = share.quantity is None
    # mine и total — в изделиях задания (execution переводит штуки полуфабриката «N на изделие» в изделия).
    whole = not started and total >= task.quantity
    # Какие узлы плана меняются: бригада — целиком; вся операция сдана — все доли; иначе — доля монтажника.
    # «Взял» в бригаде ставит от «сейчас» только долю взявшего: окно бригады — от первой доли до последней.
    groups: list[tuple[NodeKey, list[OperationShare]]] = []
    if crew:
        groups.append(((operation.id, None), [item for item in operation.shares if item.quantity is None]))
    elif whole:
        groups.extend(((operation.id, item.operator_id), [item]) for item in operation.shares if item.finished_at is None)
    else:
        groups.append(((operation.id, operator_id), [share]))
    changed: dict[UUID, OperationShare] = {}
    sources: dict[NodeKey, tuple[datetime, datetime] | None] = {}
    for key, members in groups:
        members = [item for item in members if item.planned_start is not None and item.planned_finish is not None]
        if not members:
            continue
        before = (min(item.planned_start for item in members), max(item.planned_finish for item in members))
        people = tuple(item.operator_id for item in members)
        finished = False
        moving = members
        if started:
            if share.started_at is not None:
                return
            begin = next_working_moment(now, calendar.holidays, calendar.shift)
            seconds = share_seconds(task, operation, share.quantity, people, skills)
            window = (begin, add_work_seconds(begin, seconds, calendar.holidays, calendar.shift))
            moving = [item for item in members if item.operator_id == operator_id]
        else:
            target = task.quantity if crew else (members[0].quantity or task.quantity)
            done = total if crew else (mine if members[0].operator_id == operator_id else Decimal(0))
            finished = whole or done >= target
            if finished:
                window = (min(before[0], now), now)
            else:
                rate = sum((skill_of(skills, operation.code, value).coefficient for value in people), Decimal(0))
                units = task.operation_units(operation.id, Decimal(target) - done)
                rest = ceil(operation.seconds_per_unit * units / (rate if rate > 0 else Decimal(1)))
                begin = next_working_moment(now, calendar.holidays, calendar.shift)
                window = (min(before[0], now), add_work_seconds(begin, rest, calendar.holidays, calendar.shift))
        for item in moving:
            changed[item.operator_id] = replace(
                item, planned_start=window[0], planned_finish=window[1],
                started_at=now if started and item.operator_id == operator_id else item.started_at,
                finished_at=now if finished else item.finished_at,
            )
        after = [changed.get(item.operator_id, item) for item in members]
        after_window = (min(item.planned_start for item in after), max(item.planned_finish for item in after))
        if abs((after_window[0] - before[0]).total_seconds()) >= 60 or abs((after_window[1] - before[1]).total_seconds()) >= 60:
            sources[key] = before
    if not changed:
        return
    updated = task.with_operations((operation.with_shares(
        tuple(changed.get(item.operator_id, item) for item in operation.shares), operation.assigned_by,
    ),))
    tasks[task.id] = updated
    moves = reflow(
        tasks=tasks.values(), sources=sources, pull=True, now=now,
        calendar=calendar, skills=skills, progress=_progress(uow, tasks.keys()),
    ) if sources else {}
    _save_moves(uow, tasks, {task.id}, moves, actor, now, "started" if started else "output")


def hand_over_production(command: HandOverProductionCommand, uow: UnitOfWork) -> ProductionTask:
    with uow:
        task = _task(command.task_id, command.expected_version, uow)
        deliveries = getattr(uow, "deliveries", None)
        delivered = delivered_total(deliveries.for_task(task.id)) if deliveries is not None else 0
        updated = task.hand_over(command.site, delivered)
        uow.production.add(updated)
        _audit(updated, "production.handed_over", command.actor, command.now, {"site": updated.handed_to}, uow)
        uow.commit()
        return updated


def set_production_cancelled(command: SetProductionCancelledCommand, uow: UnitOfWork) -> ProductionTask:
    with uow:
        task = _task(command.task_id, command.expected_version, uow)
        updated = task.set_cancelled(command.cancelled)
        uow.production.add(updated)
        _audit(updated, "production.cancelled" if command.cancelled else "production.restored", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def cancel_task_in(task: ProductionTask, actor: Actor, now: datetime, uow, details: dict | None = None) -> ProductionTask:
    """«Отменено» inside the caller's transaction (the caller commits) — the same transition and audit event as
    `set_production_cancelled`, with the caller's ``details`` (why it was cancelled)."""
    updated = task.set_cancelled(True)
    uow.production.add(updated)
    _audit(updated, "production.cancelled", actor, now, dict(details or {}), uow)
    return updated


def replace_production_calendar(command: ReplaceProductionCalendarCommand, uow: UnitOfWork) -> None:
    with uow:
        operators = {operator.id for operator in uow.operators.list() if operator.active}
        # A screen that sends the leave-request absences back (they are listed with the master's) does not turn them
        # into the master's rows: the same person, period and kind as a leave row is that row.
        from_leave = {
            (item.operator_id, item.from_date, item.to_date, item.kind)
            for item in uow.calendar.absences() if item.leave_request_id is not None
        }
        manual = tuple(
            absence for absence in command.absences
            if absence.leave_request_id is None
            and (absence.operator_id, absence.from_date, absence.to_date, absence.kind) not in from_leave
        )
        for absence in manual:
            if absence.operator_id not in operators:
                raise ProductionValidationError("Оператора нет в справочнике сотрудников")
            if absence.to_date < absence.from_date:
                raise ProductionValidationError("Конец отсутствия раньше начала")
            if absence.kind not in ABSENCE_KINDS:
                raise ProductionValidationError("Неизвестный вид отсутствия")
        command = replace(command, absences=manual)
        uow.calendar.replace(command.holidays, command.absences, command.shift)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.actor.id, event_type="production.calendar_replaced",
            actor=command.actor, occurred_at=command.now,
            details={
                "holidays": len(command.holidays), "absences": len(command.absences),
                "shift_start": command.shift.start.isoformat(timespec="minutes"),
                "shift_finish": command.shift.finish.isoformat(timespec="minutes"),
                "effective_seconds": command.shift.effective_seconds,
            },
        ))
        uow.commit()



@dataclass(frozen=True)
class SyncLeaveAbsencesCommand:
    absences: tuple[Absence, ...]  # one per registered leave request, `leave_request_id` set
    actor: Actor
    now: datetime


def sync_leave_absences(command: SyncLeaveAbsencesCommand, uow: UnitOfWork) -> int:
    """Registered leave requests → the shop calendar (cabinet plan T6): the planner and «Загрузка по людям» then do not
    put the person on those days. Idempotent: the calendar's leave rows become exactly the given set; the master's own
    absences are never touched. Returns the number of rows changed (0 — already in step, nothing written)."""
    for absence in command.absences:
        if absence.leave_request_id is None:
            raise ProductionValidationError("Отсутствие по заявлению без заявления")
        if absence.to_date < absence.from_date:
            raise ProductionValidationError("Конец отсутствия раньше начала")
        if absence.kind not in ABSENCE_KINDS:
            raise ProductionValidationError("Неизвестный вид отсутствия")
    with uow:
        changed = uow.calendar.replace_leave_absences(command.absences)
        if not changed:
            return 0
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.actor.id, event_type="production.leave_absences_synced",
            actor=command.actor, occurred_at=command.now,
            details={"absences": len(command.absences), "changed": changed},
        ))
        uow.commit()
        return changed

def replace_operator_skills(command: ReplaceOperatorSkillsCommand, uow: UnitOfWork) -> None:
    with uow:
        operator = uow.operators.get(command.operator_id)
        if operator is None or not operator.active:
            raise ProductionValidationError("Оператора нет в справочнике сотрудников")
        if any(item.operator_id != command.operator_id for item in command.skills):
            raise ProductionValidationError("Матрица содержит компетенции другого оператора")
        codes = [item.operation_code for item in command.skills]
        if len(set(codes)) != len(codes):
            raise ProductionValidationError("Код операции указан дважды")
        uow.skills.replace_for_operator(command.operator_id, command.skills)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.operator_id,
            event_type="production.skills_replaced", actor=command.actor,
            occurred_at=command.now,
            details={
                "operator": operator.name, "skills": len(command.skills),
                "allowed": sum(item.allowed for item in command.skills),
            },
        ))
        uow.commit()


def _auto_schedule(
    command: AutoScheduleCommand,
    uow: UnitOfWork,
    transfer_batch: int | None = None,
) -> tuple[ProductionTask, tuple[ScheduleVariant, ...]]:
    # Сроки из базы — в поясе «сейчас» (SQLite отдаёт их без пояса): иначе закреплённый этап не сравнить с now.
    task = localize(_task(command.task_id, command.expected_version, uow), command.now)
    # Авторасчёт берёт только монтажников; мастер площадки — только своей (как при ручном назначении).
    assigner = uow.operators.get(command.actor.id)
    operators = tuple(
        item.id for item in uow.operators.list()
        if item.installer and not (assigner is not None and assigner.site and item.site != assigner.site)
    )
    skill_rows = tuple(uow.skills.list())
    skills: dict[str, dict[UUID, OperatorSkill]] = {}
    for item in skill_rows:
        skills.setdefault(item.operation_code, {})[item.operator_id] = item
    team = tuple(
        operator_id for operator_id in operators
        if any(item.operator_id == operator_id and item.allowed for item in skill_rows)
    )
    if task.team:
        # Команда задания (план задания): авторасчёт ставит только её людей.
        team = tuple(operator_id for operator_id in task.team if operator_id in team)
        if not team:
            raise ProductionValidationError(
                "В команде задания нет монтажников с допуском — добавьте людей в «План задания» или заведите компетенции"
            )
    if not team:
        raise ProductionValidationError("Для авторасчёта нужно заполнить матрицу компетенций")
    other_tasks = [item for item in uow.production.list(limit=10_000) if item.id != task.id]
    busy = _with_absences(
        _busy(other_tasks, task.id, UUID(int=0), command.now),
        uow.calendar.absences(), team, command.now.tzinfo,
    )
    parameters = dict(
        task=task, team=team, skills=skills, busy=busy, now=command.now,
        holidays=uow.calendar.holidays(), shift=_shift(uow.calendar),
        max_crew=command.max_crew, replan=command.replan,
    )
    # Партия, выбранная в плане задания, сильнее вариантов: последовательно — целиком, иначе — своя партия.
    chosen = task.quantity if task.flow_mode is FlowMode.SEQUENTIAL else task.transfer_batch
    if transfer_batch is None and chosen is not None:
        transfer_batch = chosen
    if transfer_batch is not None:
        variants = (schedule_task(transfer_batch=transfer_batch, **parameters),)
    else:
        variants = schedule_variants(**parameters)
    return task, variants


def preview_auto_schedule(command: AutoScheduleCommand, uow: UnitOfWork) -> tuple[ScheduleVariant, ...]:
    with uow:
        _, variants = _auto_schedule(command, uow)
        return variants


def apply_auto_schedule(
    command: AutoScheduleCommand,
    transfer_batch: int,
    uow: UnitOfWork,
) -> tuple[ProductionTask, ScheduleVariant]:
    with uow:
        task, variants = _auto_schedule(command, uow, transfer_batch)
        variant = variants[0]
        updated = task.apply_schedule(tuple(
            (item.operation_id, item.operator_ids, item.start, item.finish)
            for item in variant.operations
        ), command.actor.name)
        uow.production.add(updated)
        _audit(updated, "production.auto_scheduled", command.actor, command.now, {
            "transfer_batch": variant.transfer_batch,
            "max_crew": command.max_crew,
            "replan": command.replan,
            "first_ready": variant.first_ready.isoformat(),
            "finish": variant.finish.isoformat(),
            "idle_seconds": variant.idle_seconds,
        }, uow)
        uow.commit()
        return updated, variant


# ── Цель 2: план задания, «Сдано N шт» площадки, статус «Выполнено» ──────────────────────────────────────────


@dataclass(frozen=True)
class UpdateFlowCommand:
    task_id: UUID
    expected_version: int
    flow_mode: FlowMode
    transfer_batch: int | None
    early_quantity: int | None
    team: tuple[UUID, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RefreshNormsCommand:
    """«Обновить нормы из ЭТК»: нормы выданной техкарты по номерам этапов {номер: (код, сек на штуку, подготовка)}."""
    task_id: UUID
    expected_version: int
    techcard_id: UUID
    revision: int
    norms: dict[int, tuple[str, Decimal, Decimal]]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class RecordDeliveryCommand:
    """«Сдано N шт»; quantity None — весь остаток одним разом (по умолчанию ЖУК сдаёт всё сразу)."""
    task_id: UUID
    expected_version: int
    quantity: int | None
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class CancelDeliveryCommand:
    task_id: UUID
    expected_version: int
    delivery_id: UUID
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class SetTaskDoneCommand:
    task_id: UUID
    expected_version: int
    done: bool  # True — «Выполнено», False — «Вернуть в работу»
    actor: Actor
    now: datetime


def _locked(task_id: UUID, expected_version: int, uow) -> ProductionTask:
    """Строка задания под замок (PostgreSQL) и проверка версии формы: сдачи и план пишутся по очереди."""
    lock = getattr(uow.production, "lock", None)
    if lock is not None:
        lock(task_id)
    return _task(task_id, expected_version, uow)


def update_flow(command: UpdateFlowCommand, uow: UnitOfWork) -> ProductionTask:
    """План задания: режим передачи, передаточная партия, «первые k раньше», команда (люди из справочника по id)."""
    with uow:
        task = _locked(command.task_id, command.expected_version, uow)
        team = tuple(command.team)
        assigner = uow.operators.get(command.actor.id)
        for operator_id in team:
            operator = uow.operators.get(operator_id)
            if operator is None or not operator.active:
                raise ProductionValidationError("Человека нет в справочнике сотрудников")
            if not operator.shop_worker and operator_id not in task.team:
                raise ProductionValidationError(f"{operator.name} не монтажник: в команду задания берут только монтажников")
            # Мастер площадки собирает команду из своих (как при назначении); уже стоящих не выгоняем.
            if assigner is not None and assigner.site and operator.site != assigner.site and operator_id not in task.team:
                raise ProductionValidationError(f"Площадка {assigner.site}: в команду можно взять только своих, не {operator.name}")
        updated = task.with_flow(
            flow_mode=command.flow_mode, transfer_batch=command.transfer_batch,
            early_quantity=command.early_quantity, team=team,
        )
        if updated is task:
            return task
        uow.production.add(updated)
        _audit(updated, "production.flow_updated", command.actor, command.now, {
            "flow_mode": updated.flow_mode.value, "transfer_batch": updated.transfer_batch,
            "early_quantity": updated.early_quantity, "team": [str(value) for value in updated.team],
        }, uow)
        uow.commit()
        return updated


def refresh_norms(command: RefreshNormsCommand, uow: UnitOfWork) -> ProductionTask:
    """Нормы из ЭТК — только по кнопке мастера (решение 5): тихо задание нормы не меняет."""
    with uow:
        task = _locked(command.task_id, command.expected_version, uow)
        before = {operation.position: str(operation.norm_seconds) for operation in task.operations}
        updated = task.refresh_norms(command.norms, command.techcard_id, command.revision)
        uow.production.add(updated)
        _audit(updated, "production.norms_refreshed", command.actor, command.now, {
            "techcard_id": str(command.techcard_id), "from_revision": task.techcard_revision,
            "revision": command.revision,
            "norm_seconds": {
                str(operation.position): [before[operation.position], str(operation.norm_seconds)]
                for operation in updated.operations
            },
        }, uow)
        uow.commit()
        return updated


def record_delivery(command: RecordDeliveryCommand, uow: UnitOfWork) -> tuple[ProductionTask, TaskDelivery]:
    """«Сдано N шт» площадки, которой задание отдано целиком. Весь тираж сдан — задание «Выполнено»."""
    with uow:
        task = _locked(command.task_id, command.expected_version, uow)
        if not task.handed_to:
            raise ProductionValidationError(
                f"Задание {task.number} распределяется по людям — сдача идёт коробками, «Сдано N шт» только у площадки"
            )
        task._check_open("сдавать нельзя")
        delivered = delivered_total(uow.deliveries.for_task(task.id))
        left = task.quantity - delivered
        quantity = left if command.quantity is None else command.quantity
        if left <= 0:
            raise ProductionValidationError(f"Задание {task.number}: весь тираж уже сдан")
        if quantity <= 0:
            raise ProductionValidationError("Сдать можно целое число штук больше нуля")
        if quantity > left:
            raise ProductionValidationError(
                f"Задание {task.number}: осталось сдать {left} шт из {task.quantity} — {quantity} шт больше остатка"
            )
        delivery = TaskDelivery(
            id=uuid4(), task_id=task.id, site=task.handed_to, quantity=quantity,
            delivered_at=command.now, delivered_by=command.actor.name,
        )
        uow.deliveries.save(delivery)
        total = delivered + quantity
        updated = (
            task.complete(Decimal(total), Decimal(task.quantity)) if total >= task.quantity
            else replace(task, version=task.version + 1)
        )
        uow.production.add(updated)
        _audit(updated, "production.delivered", command.actor, command.now, {
            "delivery_id": str(delivery.id), "site": delivery.site, "quantity": quantity,
            "delivered": total, "of": task.quantity, "status": updated.status.value,
        }, uow)
        uow.commit()
        return updated, delivery


def cancel_delivery(command: CancelDeliveryCommand, uow: UnitOfWork) -> ProductionTask:
    """Отмена ошибочной сдачи: строка остаётся с отметкой; выполненное задание с недостачей — снова «Запущено»."""
    with uow:
        task = _locked(command.task_id, command.expected_version, uow)
        deliveries = uow.deliveries.for_task(task.id)
        delivery = next((item for item in deliveries if item.id == command.delivery_id), None)
        if delivery is None:
            raise KeyError(f"Delivery not found: {command.delivery_id}")
        if not delivery.active:
            raise ProductionValidationError("Эта сдача уже отменена")
        if task.status is ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {task.number} отменено — сдачи не меняются")
        uow.deliveries.save(replace(delivery, cancelled_at=command.now, cancelled_by=command.actor.name))
        total = delivered_total(deliveries) - delivery.quantity
        updated = (
            task.reopen() if task.status is ProductionStatus.DONE and total < task.quantity
            else replace(task, version=task.version + 1)
        )
        uow.production.add(updated)
        _audit(updated, "production.delivery_cancelled", command.actor, command.now, {
            "delivery_id": str(delivery.id), "quantity": delivery.quantity, "delivered": total,
            "status": updated.status.value,
        }, uow)
        uow.commit()
        return updated


def _done_quantity(task: ProductionTask, uow) -> tuple[Decimal, Decimal]:
    """(сдано, нужно) для «Выполнено»: у площадки — «Сдано N шт», у цеха — годные финального этапа."""
    if task.handed_to:
        return Decimal(delivered_total(uow.deliveries.for_task(task.id))), Decimal(task.quantity)
    final = task.final_operation
    good = sum(
        (output.good_quantity for output in uow.outputs.active_for_tasks((task.id,)) if output.operation_id == final.id),
        Decimal(0),
    )
    return good, task.operation_units(final.id)


def set_task_done(command: SetTaskDoneCommand, uow: UnitOfWork) -> ProductionTask:
    """«Выполнено» — когда сдан тираж; «Вернуть в работу» — выполненное задание снова в работе."""
    with uow:
        task = _locked(command.task_id, command.expected_version, uow)
        done, required = _done_quantity(task, uow)
        if command.done:
            updated = task.complete(done, required)
        else:
            if task.handed_to and task.status is ProductionStatus.DONE and done >= required:
                raise ProductionValidationError(
                    f"{task.handed_to} сдал весь тираж — чтобы вернуть задание в работу, отмените сдачу"
                )
            updated = task.reopen()
        uow.production.add(updated)
        _audit(updated, "production.done" if command.done else "production.reopened", command.actor, command.now, {
            "done": str(done), "required": str(required),
        }, uow)
        uow.commit()
        return updated


@dataclass(frozen=True)
class FlowStepView:
    operation_id: UUID
    position: int
    people: Decimal
    start: datetime
    first_out: datetime
    finish: datetime
    wait_seconds: int


@dataclass(frozen=True)
class FlowView:
    """«План задания»: расчёт flow.plan_flow в датах смены от начала задания."""
    task: ProductionTask
    anchor: datetime
    plan: FlowPlan
    steps: tuple[FlowStepView, ...]
    first_ready: datetime
    finish: datetime
    baseline_finish: datetime
    baseline_early_ready: datetime | None
    warnings: tuple[str, ...]
    deliveries: tuple[TaskDelivery, ...]


def _flow_people(task: ProductionTask, operation: ProductionOperation, skills) -> Decimal:
    """Скорость этапа: назначенные люди (сумма коэффициентов); не назначен — последовательно вся команда задания
    с допуском (этапы идут по очереди), иначе один человек."""
    people = operation.operator_ids
    if not people and task.flow_mode is FlowMode.SEQUENTIAL and task.team:
        people = tuple(value for value in task.team if skill_of(skills, operation.code, value).allowed)
    if not people:
        return Decimal(1)
    return sum((skill_of(skills, operation.code, value).coefficient for value in people), Decimal(0)) or Decimal(1)


def flow_view(task_id: UUID, uow: UnitOfWork, now: datetime) -> FlowView:
    with uow:
        stored = uow.production.get(task_id)
        if stored is None:
            raise KeyError(f"Production task not found: {task_id}")
        task = localize(stored, now)
        skills = _skills(uow)
        holidays = uow.calendar.holidays()
        shift = _shift(uow.calendar)
        deliveries = getattr(uow, "deliveries", None)
        history = tuple(deliveries.for_task(task.id)) if deliveries is not None else ()
    factors = task.unit_factors
    steps = tuple(FlowStep(
        operation_id=operation.id, position=operation.position,
        inputs=tuple(item.input_operation_id for item in operation.inputs),
        setup_seconds=operation.setup_seconds,
        unit_seconds=operation.seconds_per_unit * factors.get(operation.id, Decimal(1)),
        people=_flow_people(task, operation, skills),
    ) for operation in task.operations)
    plan = plan_flow(steps, task.quantity, task.flow_mode, task.transfer_batch, task.early_quantity)
    starts = [operation.planned_start for operation in task.operations if operation.planned_start is not None]
    # От начала по плану, если этапы уже поставлены; иначе — от ближайшего рабочего момента.
    anchor = next_working_moment(min(starts) if starts else now, holidays, shift)

    def at(seconds: Decimal) -> datetime:
        return add_work_seconds(anchor, ceil(seconds), holidays, shift)

    people = {step.operation_id: step.people for step in steps}
    finish = at(plan.finish)
    warnings = list(plan.warnings)
    if task.deadline and finish.date() > task.deadline:
        warnings.append(f"Всё готово {finish:%d.%m} — позже срока {task.deadline:%d.%m}")
    return FlowView(
        task=task, anchor=anchor, plan=plan,
        steps=tuple(FlowStepView(
            item.operation_id, item.position, people[item.operation_id],
            at(item.start), at(item.first_out), at(item.finish), ceil(item.wait),
        ) for item in plan.steps),
        first_ready=at(plan.first_ready), finish=finish, baseline_finish=at(plan.baseline_finish),
        baseline_early_ready=at(plan.baseline_early_ready) if plan.baseline_early_ready is not None else None,
        warnings=tuple(warnings), deliveries=history,
    )
