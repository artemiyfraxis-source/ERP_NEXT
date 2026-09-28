"""Transactional warehouse lot accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.warehouse_location import remap_storage_location, storage_location_has_slot

from .domain import StockLot, WarehouseValidationError, normalize_location


SYSTEM_ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")


class WarehouseRepository(Protocol):
    def get(self, lot_id: UUID) -> StockLot | None: ...
    def by_key(self, warehouse: str, grn: str, package: str) -> StockLot | None: ...
    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLot]: ...
    def count(
        self,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> int: ...
    def at_location(self, warehouse: str, storage_location: str) -> Sequence[StockLot]: ...
    def reserve_manual_numbers(self, year: int, count: int) -> int: ...
    def add(self, lot: StockLot) -> None: ...


class OutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    warehouse: WarehouseRepository
    audit: AppendOnlyRepository
    outbox: OutboxRepository

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


class PoisonWarehouseMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


class ConcurrentWarehouseUpdate(RuntimeError):
    pass


@dataclass(frozen=True)
class WriteOffCommand:
    lot_id: UUID
    expected_version: int
    quantity: Decimal
    reason: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class MoveLotCommand:
    lot_id: UUID
    expected_version: int
    storage_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class CorrectLotLocationCommand:
    lot_id: UUID
    expected_version: int
    storage_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class EditLotIdentityCommand:
    lot_id: UUID
    expected_version: int
    article: str
    name: str
    series: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class MoveLocationCommand:
    warehouse: str
    source_location: str
    target_location: str
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class ManualStockItem:
    warehouse: str
    name: str
    item_type: str
    purpose: str
    unit: str
    quantity: Decimal
    storage_location: str
    article: str = ""
    series: str = ""


@dataclass(frozen=True)
class AddManualStockCommand:
    items: tuple[ManualStockItem, ...]
    actor: Actor
    now: datetime


def _lot_from_payload(payload: dict) -> StockLot:
    try:
        return StockLot.create(
            warehouse=str(payload["warehouse"]),
            grn=str(payload["grn"]),
            package=str(payload.get("package") or ""),
            name=str(payload["name"]),
            item_type=str(payload["item_type"]),
            purpose=str(payload["purpose"]),
            unit=str(payload["unit"]),
            quantity=Decimal(str(payload["quantity"])),
            storage_location=str(payload["storage_location"]),
            source_receiving_ids=(str(payload["receiving_line_id"]),),
            article=str(payload.get("article") or ""),
            series=str(payload.get("series") or ""),
        )
    except (KeyError, ValueError, TypeError, DecimalException) as error:
        if isinstance(error, WarehouseValidationError):
            raise
        raise WarehouseValidationError(f"Invalid warehouse.received payload: {error}") from error


def dispatch_next_warehouse_received(now: datetime, uow: UnitOfWork) -> StockLot | None:
    with uow:
        message = uow.outbox.next_unpublished("warehouse.received", now)
        if message is None:
            return None
        try:
            incoming = _lot_from_payload(message.payload)
            existing = uow.warehouse.by_key(incoming.warehouse, incoming.grn, incoming.package)
            lot = incoming if existing is None else existing.receive(
                incoming.inbound,
                incoming.source_receiving_ids[0],
                article=incoming.article,
                series=incoming.series,
            )
        except WarehouseValidationError as error:
            uow.outbox.mark_failed(message.id, now, str(error))
            uow.commit()
            raise PoisonWarehouseMessage(message.id, str(error)) from error
        uow.warehouse.add(lot)
        if existing is None or lot != existing:
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=lot.id,
                    event_type="warehouse.received",
                    actor=SYSTEM_ACTOR,
                    occurred_at=now,
                    details={"grn": lot.grn, "package": lot.package, "quantity": str(incoming.inbound)},
                )
            )
        uow.outbox.mark_published(message.id, now)
        uow.commit()
        return lot


def add_manual_stock(command: AddManualStockCommand, uow: UnitOfWork) -> list[StockLot]:
    if not command.items:
        raise WarehouseValidationError("Добавьте хотя бы одну позицию")
    if len(command.items) > 100:
        raise WarehouseValidationError("За раз можно внести не больше 100 позиций")
    with uow:
        first = uow.warehouse.reserve_manual_numbers(command.now.year, len(command.items))
        lots = []
        for offset, item in enumerate(command.items):
            grn = f"OLD.{command.now.year % 100:02d}.{first + offset:07d}"
            source_id = f"manual:{uuid4()}"
            lot = StockLot.create(
                warehouse=item.warehouse,
                grn=grn,
                package="",
                name=item.name,
                item_type=item.item_type,
                purpose=item.purpose,
                unit=item.unit,
                quantity=item.quantity,
                storage_location=item.storage_location,
                source_receiving_ids=(source_id,),
                article=item.article,
                series=item.series,
            )
            uow.warehouse.add(lot)
            uow.audit.add(
                AuditEvent(
                    id=uuid4(),
                    aggregate_id=lot.id,
                    event_type="warehouse.manually_received",
                    actor=command.actor,
                    occurred_at=command.now,
                    details={
                        "grn": grn,
                        "quantity": str(item.quantity),
                        "storage_location": lot.storage_location,
                    },
                )
            )
            lots.append(lot)
        uow.commit()
        return lots


def _versioned(lot_id: UUID, expected_version: int, uow: UnitOfWork) -> StockLot:
    lot = uow.warehouse.get(lot_id)
    if lot is None:
        raise KeyError(f"Warehouse lot not found: {lot_id}")
    if lot.version != expected_version:
        raise ConcurrentWarehouseUpdate("Партия изменилась после загрузки формы")
    return lot


def write_off(command: WriteOffCommand, uow: UnitOfWork) -> StockLot:
    if not str(command.reason or "").strip():
        raise WarehouseValidationError("Основание списания обязательно")
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.write_off(command.quantity)
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.written_off",
                actor=command.actor, occurred_at=command.now,
                details={"quantity": str(command.quantity), "reason": command.reason.strip()},
            )
        )
        uow.commit()
        return updated


def move_lot(command: MoveLotCommand, uow: UnitOfWork) -> StockLot:
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.move(command.storage_location)
        if updated == current:
            return current
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.moved",
                actor=command.actor, occurred_at=command.now,
                details={"from": current.storage_location, "to": updated.storage_location},
            )
        )
        uow.commit()
        return updated


def correct_lot_location(command: CorrectLotLocationCommand, uow: UnitOfWork) -> StockLot:
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.correct_location(command.storage_location)
        if updated == current:
            return current
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.location_corrected",
                actor=command.actor, occurred_at=command.now,
                details={"from": current.storage_location, "to": updated.storage_location},
            )
        )
        uow.commit()
        return updated


def edit_lot_identity(command: EditLotIdentityCommand, uow: UnitOfWork) -> StockLot:
    with uow:
        current = _versioned(command.lot_id, command.expected_version, uow)
        updated = current.edit_identity(
            article=command.article,
            name=command.name,
            series=command.series,
        )
        if updated == current:
            return current
        uow.warehouse.add(updated)
        uow.audit.add(
            AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.identity_edited",
                actor=command.actor, occurred_at=command.now,
                details={
                    "article": updated.article,
                    "name": updated.name,
                    "series": updated.series,
                },
            )
        )
        uow.commit()
        return updated


def move_location(command: MoveLocationCommand, uow: UnitOfWork) -> list[StockLot]:
    warehouse = str(command.warehouse or "").strip()
    if warehouse not in {"ЦАР", "ЖУК"}:
        raise WarehouseValidationError("Неизвестный склад")
    source = normalize_location(command.source_location)
    target = normalize_location(command.target_location)
    if not storage_location_has_slot(source) and storage_location_has_slot(target):
        raise WarehouseValidationError("Место целиком переносится без ячейки")
    if source == target:
        raise WarehouseValidationError("Новое место совпадает со старым")
    with uow:
        current_lots = list(uow.warehouse.at_location(warehouse, source))
        if not current_lots:
            raise WarehouseValidationError(f"На месте {source} ({warehouse}) ничего не лежит")
        moved = []
        for current in current_lots:
            remapped = remap_storage_location(current.storage_location, source, target)
            if remapped is None:
                continue
            updated = current.move(remapped)
            uow.warehouse.add(updated)
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=updated.id, event_type="warehouse.location_moved",
                actor=command.actor, occurred_at=command.now,
                details={"from": current.storage_location, "to": updated.storage_location},
            ))
            moved.append(updated)
        if not moved:
            raise WarehouseValidationError(f"На месте {source} ({warehouse}) ничего не лежит")
        uow.commit()
        return moved


def warehouse_lots(
    uow: UnitOfWork,
    *,
    limit: int = 200,
    after_id: UUID | None = None,
    query: str = "",
    warehouse: str | None = None,
    include_zero: bool = False,
) -> tuple[list[StockLot], int]:
    with uow:
        if after_id is not None and uow.warehouse.get(after_id) is None:
            raise KeyError(f"Warehouse cursor not found: {after_id}")
        lots = uow.warehouse.list(limit, after_id, query, warehouse, include_zero)
        return list(lots), uow.warehouse.count(query, warehouse, include_zero)
