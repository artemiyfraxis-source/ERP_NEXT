"""Warehouse adapter used by the procurement stock-cover workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.stock_port import (
    StockLotView,
    StockReservationError,
    StockReservationPort,
)
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository, StockLotRow


def _view(lot: StockLot) -> StockLotView:
    return StockLotView(
        id=lot.id,
        version=lot.version,
        warehouse=lot.warehouse,
        grn=lot.grn,
        package=lot.package,
        article=lot.article,
        series=lot.series,
        name=lot.name,
        item_type=lot.item_type,
        purpose=lot.purpose,
        unit=lot.unit,
        storage_location=lot.storage_location,
        balance=lot.balance,
        reserved=lot.reserved,
        available=lot.available,
    )


class SqlAlchemyProcurementStockPort(StockReservationPort):
    """Adapts the warehouse aggregate without exposing it to procurement."""

    def __init__(self, session: Session):
        self.repository = SqlAlchemyWarehouseRepository(session)

    def get(self, lot_id: UUID) -> StockLotView | None:
        lot = self.repository.get(lot_id)
        return _view(lot) if lot is not None else None

    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLotView]:
        return tuple(
            _view(lot)
            for lot in self.repository.list(
                limit=limit,
                after_id=after_id,
                query=query,
                warehouse=warehouse,
                include_zero=include_zero,
            )
        )

    def reserve(self, lot_id: UUID, quantity: Decimal, *, expected_version: int) -> StockLotView:
        # Keep the row alive through add: the identity map weakly retains clean rows.
        row = self.repository.session.get(StockLotRow, lot_id)
        if row is not None and row.version != expected_version:
            raise StaleDataError(f"Warehouse lot changed: {lot_id}")
        lot = self.repository.get(lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {lot_id}")
        try:
            updated = lot.reserve(quantity)
        except WarehouseValidationError as error:
            raise StockReservationError(_view(lot), str(error)) from error
        self.repository.add(updated)
        return _view(updated)

    def release(self, lot_id: UUID, quantity: Decimal, *, expected_version: int) -> StockLotView:
        # Keep the row alive through add: the identity map weakly retains clean rows.
        row = self.repository.session.get(StockLotRow, lot_id)
        if row is not None and row.version != expected_version:
            raise StaleDataError(f"Warehouse lot changed: {lot_id}")
        lot = self.repository.get(lot_id)
        if lot is None:
            raise KeyError(f"Warehouse lot not found: {lot_id}")
        try:
            updated = lot.release_reserve(quantity)
        except WarehouseValidationError as error:
            raise StockReservationError(_view(lot), str(error)) from error
        self.repository.add(updated)
        return _view(updated)


class SqlAlchemyProcurementStockUnitOfWork(SqlAlchemyProcurementUnitOfWork):
    """Procurement UoW plus the stock port, sharing exactly one SQLAlchemy session."""

    def __enter__(self) -> SqlAlchemyProcurementStockUnitOfWork:
        super().__enter__()
        assert self.session is not None
        self.stock = SqlAlchemyProcurementStockPort(self.session)
        return self
