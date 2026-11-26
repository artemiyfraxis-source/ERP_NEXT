"""Persistence helpers for acceptance, sharing the caller's warehouse transaction."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.shared.persistence import AuditEventRow

from .domain import WarehouseValidationError
from .grn import GrnConflict
from .grn_registry import MaterialOwnerRow, SqlAlchemyGrnAllocator


def allocator_in(uow):
    port = getattr(uow, "grn_allocator", None)
    if port is not None:
        return port
    if not uow.session.in_transaction():
        uow.session.begin()
    return SqlAlchemyGrnAllocator(uow.session)


def check_owner(uow, owner_id: UUID) -> None:
    if uow.session.get(MaterialOwnerRow, owner_id) is None:
        raise WarehouseValidationError("Владелец давальческого материала не найден")


def lot_for_identity(uow, identity, warehouse):
    return lot_by_grn(uow, identity.id, identity.number, warehouse, "")


def lot_by_grn(uow, grn_id, number, warehouse, package):
    # A retry must still find the original place after its physical relocation.
    session = getattr(uow, "session", None)
    if session is None:
        return uow.warehouse.by_key(warehouse, number, package)
    from .infrastructure import StockLotRow

    lot_ids = session.scalars(select(StockLotRow.id).where(
        StockLotRow.grn_id == grn_id, StockLotRow.package == package)).all()
    if len(lot_ids) > 1:
        raise WarehouseValidationError("По идентичности ГРН найдено несколько мест: требуется сверка склада")
    return uow.warehouse.get(lot_ids[0]) if lot_ids else None


def acceptance_event_id(acceptance_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"warehouse-receipt-accepted:{acceptance_id}")


def accepted_result(uow, acceptance_id: UUID) -> dict | None:
    row = uow.session.get(AuditEventRow, acceptance_event_id(acceptance_id))
    return dict(row.details) if row is not None else None


def quick_result_id(operation_id):
    return uuid5(NAMESPACE_URL, f"warehouse-quick-result:{operation_id}")


def quick_result(uow, operation_id):
    row = uow.session.get(AuditEventRow, quick_result_id(operation_id))
    return dict(row.details) if row is not None else None


def claim_quick_acceptance(uow, operation_id, fingerprint, actor, now):
    """One immutable batch fingerprint, including invalid rows, serialized before allocating any numbers."""
    event_id = uuid5(NAMESPACE_URL, f"warehouse-quick-accepted:{operation_id}")
    session = uow.session
    dialect = session.get_bind().dialect.name
    insert = {"sqlite": sqlite_insert, "postgresql": pg_insert}[dialect]
    table = AuditEventRow.__table__
    session.execute(insert(table).values(id=event_id, aggregate_id=operation_id,
        event_type="grn.quick_acceptance", actor_id=actor.id, actor_name=actor.name,
        actor_department=actor.department, occurred_at=now, details={"fingerprint": fingerprint})
        .on_conflict_do_nothing(index_elements=[table.c.id]))
    previous = session.get(AuditEventRow, event_id)
    if previous.details["fingerprint"] != fingerprint:
        raise GrnConflict("Повтор быстрого прихода с тем же номером содержит другие данные")
