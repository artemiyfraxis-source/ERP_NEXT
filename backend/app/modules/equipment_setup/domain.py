"""Pure setup snapshots. Persistence, authorization and resource occupancy belong to the application."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4


class SetupValidationError(ValueError):
    pass


class SetupStatus(str, Enum):
    NEW = "New"
    IN_PROGRESS = "InProgress"
    BLOCKED = "Blocked"
    READY = "Ready"
    CANCELLED = "Cancelled"


STATUS_TRANSITIONS = {
    SetupStatus.NEW: frozenset({SetupStatus.IN_PROGRESS, SetupStatus.CANCELLED}),
    SetupStatus.IN_PROGRESS: frozenset({SetupStatus.BLOCKED, SetupStatus.READY, SetupStatus.CANCELLED}),
    SetupStatus.BLOCKED: frozenset({SetupStatus.IN_PROGRESS, SetupStatus.CANCELLED}),
    SetupStatus.READY: frozenset(),
    SetupStatus.CANCELLED: frozenset(),
}


def _uuid(value: UUID, label: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise SetupValidationError(f"{label}: нужен непустой UUID")


def _integer(value: int, label: str, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise SetupValidationError(f"{label}: нужно целое число не меньше {minimum}")


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SetupValidationError(f"{label} обязательно")
    return value.strip()


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SetupValidationError("Нужны дата и время с часовым поясом")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class IssuedOperationRef:
    """An ETK position inside a production operation; the latter can group several ETK positions.

    The application must resolve this tuple against an issued revision and the production task.
    """

    card_id: UUID
    card_revision: int
    production_task_id: UUID
    production_operation_id: UUID
    etk_position: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.card_id, "Техкарта"),
            (self.production_task_id, "Производственное задание"),
            (self.production_operation_id, "Операция задания"),
        ):
            _uuid(value, label)
        _integer(self.card_revision, "Ревизия выданной ЭТК")
        _integer(self.etk_position, "Номер операции ЭТК")


@dataclass(frozen=True)
class RecipeVersionRef:
    """Pinned identity only: verified machine panel parameter schemas are not available yet."""

    recipe_id: UUID
    version: int

    def __post_init__(self) -> None:
        _uuid(self.recipe_id, "Рецепт")
        _integer(self.version, "Версия рецепта")


@dataclass(frozen=True)
class SetupTransition:
    """A transition fact to persist with the new task in the future application transaction."""

    task_id: UUID
    version: int
    previous_status: SetupStatus
    status: SetupStatus
    actor_id: UUID
    occurred_at: datetime
    reason: str
    machine_available: bool | None


@dataclass(frozen=True)
class SetupTask:
    id: UUID
    version: int
    operation: IssuedOperationRef
    machine_id: UUID  # the БД.ОБ unit (tech_operation_records.id), never its inventory number
    technician_id: UUID
    recipe: RecipeVersionRef
    planned_start: datetime
    planned_finish: datetime
    created_at: datetime
    updated_at: datetime
    status: SetupStatus = SetupStatus.NEW
    blocked_reason: str = ""
    blocked_machine_available: bool | None = None

    def __post_init__(self) -> None:
        _uuid(self.id, "Задание наладки")
        _uuid(self.technician_id, "Наладчик")
        _integer(self.version, "Версия задания", minimum=0)
        _uuid(self.machine_id, "Физический станок")
        if not isinstance(self.operation, IssuedOperationRef) or not isinstance(self.recipe, RecipeVersionRef):
            raise SetupValidationError("Нужны ссылки на выданную операцию ЭТК и версию рецепта")
        if not isinstance(self.status, SetupStatus):
            raise SetupValidationError("Неизвестный статус наладки")
        if _instant(self.planned_finish) <= _instant(self.planned_start):
            raise SetupValidationError("Конец наладки должен быть позже начала")
        if _instant(self.updated_at) < _instant(self.created_at):
            raise SetupValidationError("Изменение не может быть раньше создания задания")
        if self.status is SetupStatus.BLOCKED:
            object.__setattr__(self, "blocked_reason", _required(self.blocked_reason, "Причина блокировки"))
            if type(self.blocked_machine_available) is not bool:
                raise SetupValidationError("Укажите, доступен ли станок при блокировке")
        elif self.blocked_reason != "" or self.blocked_machine_available is not None:
            raise SetupValidationError("Причина и доступность при блокировке допустимы только в статусе Blocked")

    @classmethod
    def create(
        cls, *, operation: IssuedOperationRef, machine_id: UUID, technician_id: UUID,
        recipe: RecipeVersionRef, planned_start: datetime, planned_finish: datetime, now: datetime,
    ) -> SetupTask:
        return cls(
            id=uuid4(), version=1, operation=operation, machine_id=machine_id, technician_id=technician_id,
            recipe=recipe, planned_start=planned_start, planned_finish=planned_finish,
            created_at=now, updated_at=now,
        )

    def transition(
        self, target: SetupStatus, *, actor_id: UUID, now: datetime,
        reason: str = "", machine_available: bool | None = None,
    ) -> tuple[SetupTask, SetupTransition]:
        """Ready records the assigned technician's statement, never a QC acceptance.

        The caller authenticates actor_id and persists the returned snapshot and fact atomically,
        with a version guard and command idempotency. This method alone provides neither guarantee.
        """
        if not isinstance(target, SetupStatus) or target not in STATUS_TRANSITIONS[self.status]:
            raise SetupValidationError("Недопустимый переход статуса наладки")
        _uuid(actor_id, "Исполнитель перехода")
        if _instant(now) < _instant(self.updated_at):
            raise SetupValidationError("Переход не может быть раньше предыдущего изменения")
        if not isinstance(reason, str):
            raise SetupValidationError("Причина должна быть текстом")
        reason = reason.strip()
        if target is SetupStatus.READY and actor_id != self.technician_id:
            raise SetupValidationError("Готовность подтверждает назначенный наладчик")
        if target is SetupStatus.BLOCKED:
            reason = _required(reason, "Причина блокировки")
            if type(machine_available) is not bool:
                raise SetupValidationError("Укажите, доступен ли станок при блокировке")
        elif machine_available is not None:
            raise SetupValidationError("Доступность при блокировке указывается только для Blocked")
        updated = replace(
            self, status=target, version=self.version + 1, updated_at=now,
            blocked_reason=reason if target is SetupStatus.BLOCKED else "",
            blocked_machine_available=machine_available,
        )
        event = SetupTransition(
            task_id=self.id, version=updated.version, previous_status=self.status, status=target,
            actor_id=actor_id, occurred_at=now, reason=reason, machine_available=machine_available,
        )
        return updated, event
