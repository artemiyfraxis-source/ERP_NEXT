"""Setup commands and transaction ports; persistence and shared physical occupancy are not implemented here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
import json
from typing import Protocol
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.http_auth import Principal
from app.shared.identity import Actor
from app.shared.notifications import notification_requested

from .domain import IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupTask, SetupTransition, SetupValidationError


class SetupPermissionError(PermissionError):
    pass


class ConcurrentSetupUpdate(RuntimeError):
    pass


class SetupResourceConflict(RuntimeError):
    """The shared production resource adapter rejected this setup command."""

    pass


class SetupCommandConflict(ConcurrentSetupUpdate):
    pass


@dataclass(frozen=True)
class SetupPerson:
    """Server-loaded employee; eligibility is activity and site, never a guessed job title."""

    actor: Actor
    site: str
    active: bool
    site_master: bool = False


@dataclass(frozen=True)
class IssuedSetupOperation:
    operation: IssuedOperationRef
    site: str
    eligible_machine_ids: tuple[str, ...]


@dataclass(frozen=True)
class SetupMachine:
    id: str
    site: str


class SetupContext(Protocol):
    """Reads use the same transaction. None means missing, inactive source, or unresolved exact identity.

    issued_operation verifies the production task/operation and issued card revision/ETK position together.
    machine resolves one physical inventory identity, never a model. recipe_exists verifies the pinned
    version's applicability to that machine. These values must be loaded by adapters, never API DTOs.
    """

    def person(self, user_id: UUID) -> SetupPerson | None: ...
    def issued_operation(self, reference: IssuedOperationRef) -> IssuedSetupOperation | None: ...
    def machine(self, machine_id: str) -> SetupMachine | None: ...
    def recipe_exists(self, reference: RecipeVersionRef, machine_id: str) -> bool: ...


class SetupRepository(Protocol):
    def get(self, task_id: UUID) -> SetupTask | None: ...
    def save(self, task: SetupTask, *, expected_version: int) -> None:
        """Atomic insert (expected_version=0) or CAS; mismatch raises ConcurrentSetupUpdate."""
        ...


class SetupReservations(Protocol):
    """One port for the shared production/setup occupancy adapter, within this UOW transaction.

    reserve atomically checks conflicting physical-machine occupancy and reserves the task's window;
    for an existing reservation it verifies/retains that same reservation. It raises on conflict.
    release removes only this task's setup reservation. Both roll back with task/audit/outbox writes.
    No implementation here establishes production overlap protection or PostgreSQL concurrency safety.
    """

    def reserve(self, task: SetupTask) -> None: ...
    def release(self, task: SetupTask) -> None: ...
    def block(self, task: SetupTask) -> None:
        """Release active technician; retain machine only when explicitly unavailable.

        This is an execution transition, never a side effect of changing a planned window.
        """
        ...


class SetupAction(str, Enum):
    START = "start"
    BLOCK = "block"
    RESUME = "resume"
    READY = "ready"
    CANCEL = "cancel"


def _command_fields(key: str, version: int, minimum: int) -> None:
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise SetupValidationError("Нужен ключ команды длиной от 1 до 128 символов")
    if key != key.strip():
        raise SetupValidationError("Ключ команды не должен содержать пробелы по краям")
    if type(version) is not int or version < minimum:
        raise SetupValidationError("Некорректная ожидаемая версия задания")


@dataclass(frozen=True)
class AssignSetupCommand:
    idempotency_key: str
    expected_version: int
    operation: IssuedOperationRef
    machine_id: str
    technician_id: UUID
    recipe: RecipeVersionRef
    planned_start: datetime
    planned_finish: datetime

    def __post_init__(self) -> None:
        _command_fields(self.idempotency_key, self.expected_version, 0)
        if self.expected_version != 0:
            raise SetupValidationError("Новое задание требует ожидаемую версию 0")
        # Reuse the pure snapshot's validation without inventing recipe parameters.
        SetupTask.create(
            operation=self.operation, machine_id=self.machine_id, technician_id=self.technician_id,
            recipe=self.recipe, planned_start=self.planned_start, planned_finish=self.planned_finish,
            now=self.planned_start,
        )


@dataclass(frozen=True)
class ChangeSetupCommand:
    idempotency_key: str
    expected_version: int
    task_id: UUID
    action: SetupAction
    reason: str = ""
    machine_available: bool | None = None

    def __post_init__(self) -> None:
        _command_fields(self.idempotency_key, self.expected_version, 1)
        if not isinstance(self.task_id, UUID) or not self.task_id.int:
            raise SetupValidationError("Нужен UUID задания наладки")
        if not isinstance(self.action, SetupAction):
            raise SetupValidationError("Неизвестная команда наладки")
        if not isinstance(self.reason, str):
            raise SetupValidationError("Причина должна быть текстом")
        object.__setattr__(self, "reason", self.reason.strip())
        if self.action is SetupAction.BLOCK:
            if not self.reason or type(self.machine_available) is not bool:
                raise SetupValidationError("Укажите причину блокировки и доступность станка")
        elif self.machine_available is not None:
            raise SetupValidationError("Доступность станка указывается только при блокировке")


@dataclass(frozen=True)
class SetupCommandReceipt:
    key: str
    fingerprint: str
    result: SetupTask


class SetupCommands(Protocol):
    def claim(self, key: str) -> SetupCommandReceipt | None:
        """Serialize this key (including absent keys) until commit/rollback, returning a prior receipt."""
        ...

    def add(self, receipt: SetupCommandReceipt) -> None: ...


class SetupTransitions(Protocol):
    def add(self, transition: SetupTransition) -> None: ...


class SetupAudit(Protocol):
    def add(self, event: AuditEvent) -> None: ...


class SetupOutbox(Protocol):
    def add(self, message: OutboxMessage) -> None: ...


class SetupUnitOfWork(Protocol):
    tasks: SetupRepository
    commands: SetupCommands
    context: SetupContext
    reservations: SetupReservations
    transitions: SetupTransitions
    audit: SetupAudit
    outbox: SetupOutbox

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


def _json_value(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported command value: {type(value).__name__}")


def _fingerprint(command: AssignSetupCommand | ChangeSetupCommand, actor_id: UUID) -> str:
    payload = {"command": type(command).__name__, "actor_id": actor_id, "payload": asdict(command)}
    return sha256(json.dumps(payload, sort_keys=True, default=_json_value).encode()).hexdigest()


def _actor(principal: Principal, context: SetupContext) -> SetupPerson:
    if not isinstance(principal, Principal) or principal.service or principal.viewer is not None:
        raise SetupPermissionError("Нужна действующая личная сессия сотрудника")
    person = context.person(principal.actor.id)
    if person is None or person.actor.id != principal.actor.id or not person.active:
        raise SetupPermissionError("Сотрудник неактивен или не найден")
    return person


def _site(context: SetupContext, task: SetupTask, *, require_eligible_technician: bool = True) -> str:
    operation = context.issued_operation(task.operation)
    machine = context.machine(task.machine_id)
    if operation is None or operation.operation != task.operation:
        raise SetupValidationError("Выданная операция и точная ревизия ЭТК не найдены")
    if machine is None or machine.id != task.machine_id:
        raise SetupValidationError("Физический станок не найден")
    if operation.site not in {"ЦАР", "ЖУК"} or machine.site != operation.site:
        raise SetupPermissionError("Операция и станок должны быть с одной известной площадки")
    if require_eligible_technician:
        if task.machine_id not in operation.eligible_machine_ids:
            raise SetupValidationError("Станок не подходит выданной операции ЭТК")
        technician = context.person(task.technician_id)
        if technician is None or technician.actor.id != task.technician_id or not technician.active:
            raise SetupValidationError("Назначенный наладчик неактивен или не найден")
        if technician.site != operation.site:
            raise SetupPermissionError("Назначенный наладчик должен быть с площадки операции и станка")
    return operation.site


def _authorize(principal: Principal, person: SetupPerson, site: str, *, manager: bool, task: SetupTask) -> None:
    if manager:
        if principal.full_access or (person.site_master and person.site == site):
            return
        raise SetupPermissionError("Назначать и отменять наладку может мастер своей площадки или администратор")
    if person.actor.id != task.technician_id or person.site != site:
        raise SetupPermissionError("Переход выполняет только назначенный наладчик своей площадки")


def _replay(command, actor_id: UUID, uow: SetupUnitOfWork) -> tuple[SetupTask | None, str]:
    fingerprint = _fingerprint(command, actor_id)
    receipt = uow.commands.claim(command.idempotency_key)
    if receipt is not None and receipt.fingerprint != fingerprint:
        raise SetupCommandConflict("Ключ команды уже использован для другого запроса")
    return (receipt.result if receipt is not None else None), fingerprint


_STATUS_LABELS = {
    SetupStatus.NEW: "Назначена", SetupStatus.IN_PROGRESS: "В работе", SetupStatus.BLOCKED: "Заблокирована",
    SetupStatus.READY: "Готова", SetupStatus.CANCELLED: "Отменена",
}


def _save(task: SetupTask, command, fingerprint: str, person: SetupPerson,
          now: datetime, uow: SetupUnitOfWork, action: str) -> SetupTask:
    uow.tasks.save(task, expected_version=command.expected_version)
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=task.id, event_type=f"equipment_setup.{action}",
        actor=person.actor, occurred_at=now,
        details={"version": str(task.version), "status": task.status.value,
                 "command_key": command.idempotency_key},
    ))
    message = notification_requested(
        key=f"equipment_setup:{task.id}:{task.version}", now=now,
        title="Задание наладки", body=f"Станок {task.machine_id}: {_STATUS_LABELS[task.status]}",
        kind="equipment_setup", reference=str(task.id), user_ids=(task.technician_id,),
    )
    if message is not None:
        uow.outbox.add(message)
    uow.commands.add(SetupCommandReceipt(command.idempotency_key, fingerprint, task))
    uow.commit()
    return task


def assign_setup_task(command: AssignSetupCommand, uow: SetupUnitOfWork, *,
                      principal: Principal, now: datetime) -> SetupTask:
    with uow:
        person = _actor(principal, uow.context)
        prior, fingerprint = _replay(command, person.actor.id, uow)
        if prior is not None:
            site = _site(uow.context, prior, require_eligible_technician=False)
            _authorize(principal, person, site, manager=True, task=prior)
            return prior
        task = SetupTask.create(
            operation=command.operation, machine_id=command.machine_id, technician_id=command.technician_id,
            recipe=command.recipe, planned_start=command.planned_start, planned_finish=command.planned_finish,
            now=now,
        )
        site = _site(uow.context, task)
        _authorize(principal, person, site, manager=True, task=task)
        if not uow.context.recipe_exists(task.recipe, task.machine_id):
            raise SetupValidationError("Версия рецепта для станка не найдена")
        uow.reservations.reserve(task)
        return _save(task, command, fingerprint, person, now, uow, "assigned")


_ACTIONS = {
    SetupAction.START: (frozenset({SetupStatus.NEW}), SetupStatus.IN_PROGRESS),
    SetupAction.BLOCK: (frozenset({SetupStatus.IN_PROGRESS}), SetupStatus.BLOCKED),
    SetupAction.RESUME: (frozenset({SetupStatus.BLOCKED}), SetupStatus.IN_PROGRESS),
    SetupAction.READY: (frozenset({SetupStatus.IN_PROGRESS}), SetupStatus.READY),
    SetupAction.CANCEL: (frozenset({SetupStatus.NEW, SetupStatus.IN_PROGRESS, SetupStatus.BLOCKED}), SetupStatus.CANCELLED),
}


def change_setup_task(command: ChangeSetupCommand, uow: SetupUnitOfWork, *,
                      principal: Principal, now: datetime) -> SetupTask:
    with uow:
        person = _actor(principal, uow.context)
        prior, fingerprint = _replay(command, person.actor.id, uow)
        if prior is not None:
            site = _site(uow.context, prior, require_eligible_technician=False)
            _authorize(principal, person, site, manager=command.action is SetupAction.CANCEL, task=prior)
            return prior
        task = uow.tasks.get(command.task_id)
        if task is None:
            raise SetupValidationError("Задание наладки не найдено")
        cancelling = command.action is SetupAction.CANCEL
        # A manager must still be able to release setup after the assignee leaves or changes site.
        site = _site(uow.context, task, require_eligible_technician=not cancelling)
        _authorize(principal, person, site, manager=cancelling, task=task)
        if task.version != command.expected_version:
            raise ConcurrentSetupUpdate("Задание наладки уже изменилось — обновите данные")
        allowed, target = _ACTIONS[command.action]
        if task.status not in allowed:
            raise SetupValidationError("Недопустимый переход статуса наладки")
        updated, transition = task.transition(
            target, actor_id=person.actor.id, now=now, reason=command.reason,
            machine_available=command.machine_available,
        )
        if command.action in {SetupAction.START, SetupAction.RESUME}:
            uow.reservations.reserve(updated)
        elif target is SetupStatus.BLOCKED:
            uow.reservations.block(updated)
        elif target in {SetupStatus.READY, SetupStatus.CANCELLED}:
            uow.reservations.release(updated)
        uow.transitions.add(transition)
        return _save(updated, command, fingerprint, person, now, uow, command.action.value)
