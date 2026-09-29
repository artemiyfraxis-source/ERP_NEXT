"""Transactional production-task launch, assignment, and calendar use cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import (
    BusyInterval,
    DEFAULT_SHIFT,
    OperatorSkill,
    PlanResult,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
    WorkShift,
    plan_operation,
)
from .scheduling import ScheduleVariant, schedule_task, schedule_variants


class ConcurrentProductionUpdate(RuntimeError):
    pass


@dataclass(frozen=True)
class Operator:
    id: UUID
    name: str
    site: str
    active: bool
    # Отдел из справочника сотрудников: «Монтаж» отбирает монтажников на экране мастера.
    department: str = ""


@dataclass(frozen=True)
class Absence:
    id: UUID
    operator_id: UUID
    from_date: date
    to_date: date
    kind: str


class ProductionRepository(Protocol):
    def get(self, task_id: UUID) -> ProductionTask | None: ...
    def by_idempotency_key(self, key: str) -> ProductionTask | None: ...
    def list(self, limit: int = 200, after_id: UUID | None = None) -> Sequence[ProductionTask]: ...
    def reserve_number(self) -> str: ...
    def add(self, task: ProductionTask) -> None: ...


class OperatorRepository(Protocol):
    def get(self, operator_id: UUID) -> Operator | None: ...
    def list(self) -> Sequence[Operator]: ...


class CalendarRepository(Protocol):
    def holidays(self) -> frozenset[date]: ...
    def absences(self) -> Sequence[Absence]: ...
    def shift(self) -> WorkShift: ...
    def replace(self, holidays: frozenset[date], absences: tuple[Absence, ...], shift: WorkShift) -> None: ...


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
        key = str(command.idempotency_key or "").strip()
        if not key:
            raise ProductionValidationError("Idempotency key обязателен")
        fingerprint = _fingerprint(command)
        existing = uow.production.by_idempotency_key(key)
        if existing is not None:
            if existing.source_fingerprint != fingerprint:
                raise ProductionValidationError("Idempotency key уже использован для другого задания")
            if uow.serials.ensure_task(existing.id, existing.number, existing.quantity):
                uow.commit()
            return existing
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
        )
        uow.production.add(task)
        uow.serials.ensure_task(task.id, task.number, task.quantity)
        _audit(task, "production.launched", command.actor, command.now, {"number": task.number}, uow)
        uow.commit()
        return task


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
            if operation.planned_start is None or operation.planned_finish is None:
                continue
            for operator_id in operation.operator_ids:
                result.setdefault(operator_id, []).append(BusyInterval(
                    f"{task.number} · этап {operation.position}",
                    _same_timezone(operation.planned_start, reference),
                    _same_timezone(operation.planned_finish, reference),
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


def assign_production(command: AssignProductionCommand, uow: UnitOfWork) -> tuple[ProductionTask, PlanResult]:
    with uow:
        task = _task(command.task_id, command.expected_version, uow)
        operation = task.operation(command.operation_id)
        names: dict[UUID, str] = {}
        for operator_id in command.operator_ids:
            operator = uow.operators.get(operator_id)
            if operator is None or not operator.active:
                raise ProductionValidationError("Оператора нет в справочнике сотрудников")
            names[operator_id] = operator.name
        skills: dict[str, dict[UUID, OperatorSkill]] = {}
        for item in uow.skills.list():
            skills.setdefault(item.operation_code, {})[item.operator_id] = item
        tasks = uow.production.list(limit=10_000)
        busy = _with_absences(
            _busy(tasks, task.id, operation.id, command.now),
            uow.calendar.absences(),
            command.operator_ids,
            command.now.tzinfo,
        )
        previous_finishes = [
            _same_timezone(item.planned_finish, command.now) for item in task.operations
            if item.position < operation.position and item.planned_finish is not None
        ]
        earliest = max([command.now, *previous_finishes])
        plan = plan_operation(
            operation=operation,
            quantity=task.quantity,
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


def set_production_cancelled(command: SetProductionCancelledCommand, uow: UnitOfWork) -> ProductionTask:
    with uow:
        task = _task(command.task_id, command.expected_version, uow)
        updated = task.set_cancelled(command.cancelled)
        uow.production.add(updated)
        _audit(updated, "production.cancelled" if command.cancelled else "production.restored", command.actor, command.now, {}, uow)
        uow.commit()
        return updated


def replace_production_calendar(command: ReplaceProductionCalendarCommand, uow: UnitOfWork) -> None:
    with uow:
        operators = {operator.id for operator in uow.operators.list() if operator.active}
        for absence in command.absences:
            if absence.operator_id not in operators:
                raise ProductionValidationError("Оператора нет в справочнике сотрудников")
            if absence.to_date < absence.from_date:
                raise ProductionValidationError("Конец отсутствия раньше начала")
            if absence.kind not in {"Отпуск", "Больничный", "Отгул", "Командировка", "Учёба"}:
                raise ProductionValidationError("Неизвестный вид отсутствия")
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
    task = _task(command.task_id, command.expected_version, uow)
    operators = tuple(item.id for item in uow.operators.list() if item.active)
    skill_rows = tuple(uow.skills.list())
    skills: dict[str, dict[UUID, OperatorSkill]] = {}
    for item in skill_rows:
        skills.setdefault(item.operation_code, {})[item.operator_id] = item
    team = tuple(
        operator_id for operator_id in operators
        if any(item.operator_id == operator_id and item.allowed for item in skill_rows)
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
