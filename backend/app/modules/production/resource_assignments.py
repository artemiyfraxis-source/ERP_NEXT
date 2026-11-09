"""Explicit physical allocations; a shared operation is not implicitly a machine crew."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .resource_claims import ResourceConflict, ResourceKey, ResourceKind


@dataclass(frozen=True)
class MachineBinding:
    etk_position: int
    machine_id: str  # canonical UUID of the БД.ОБ unit (tech_operation_records.id), never an inventory number

    def __post_init__(self):
        if type(self.etk_position) is not int or self.etk_position <= 0:
            raise ResourceConflict("Нужен номер операции ЭТК")
        if not isinstance(self.machine_id, (str, UUID)):
            raise ResourceConflict("Нужен UUID единицы оборудования БД.ОБ")
        try:
            object.__setattr__(self, "machine_id", str(UUID(str(self.machine_id).strip())))
        except ValueError:
            raise ResourceConflict("Нужен UUID единицы оборудования БД.ОБ") from None
        try:
            ResourceKey(ResourceKind.MACHINE, self.machine_id)
        except ValueError as error:
            raise ResourceConflict(str(error)) from error


@dataclass(frozen=True)
class PhysicalAllocation:
    task_id: UUID
    operation_id: UUID
    operator_ids: tuple[UUID, ...]
    machines: tuple[MachineBinding, ...]
    crew: bool = False

    def __post_init__(self):
        identifiers = (self.task_id, self.operation_id, *self.operator_ids)
        if any(not isinstance(value, UUID) or not value.int for value in identifiers):
            raise ResourceConflict("Нужны точные идентификаторы задания и сотрудников")
        if not self.operator_ids or len(set(self.operator_ids)) != len(self.operator_ids):
            raise ResourceConflict("Выберите разных сотрудников")
        if type(self.crew) is not bool or (len(self.operator_ids) > 1 and not self.crew):
            raise ResourceConflict("Общий станок допускается только для явно назначенной бригады")
        if any(not isinstance(item, MachineBinding) for item in self.machines):
            raise ResourceConflict("Нужны точные назначения станков по операциям ЭТК")
        positions = [item.etk_position for item in self.machines]
        if len(set(positions)) != len(positions):
            raise ResourceConflict("Станок операции ЭТК указан дважды")


class AllocationResolver(Protocol):
    def validate(self, task, allocation: PhysicalAllocation) -> None:
        """Check the pinned ETK, exact physical catalog and site in the same transaction."""
        ...

    def assert_legacy_available(self, resource, window, *, excluding=()) -> None:
        """Unknown legacy assignments are blockers, never evidence of availability."""
        ...
