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
    StockVersionConflict,
)
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository


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
        return self._change(lot_id, expected_version, lambda lot: lot.reserve(quantity))

    def release(self, lot_id: UUID, quantity: Decimal, *, expected_version: int) -> StockLotView:
        return self._change(lot_id, expected_version, lambda lot: lot.release_reserve(quantity))

    def _change(self, lot_id: UUID, expected_version: int, change) -> StockLotView:
        # The repository's compare-and-swap holds the lot to the version this transaction read; the explicit check
        # holds it to the view procurement validated.
        try:
            lot = self.repository.get(lot_id)
            if lot is None:
                raise KeyError(f"Warehouse lot not found: {lot_id}")
            if lot.version != expected_version:
                raise StockVersionConflict(f"Warehouse lot changed: {lot_id}")
            try:
                updated = change(lot)
            except WarehouseValidationError as error:
                raise StockReservationError(_view(lot), str(error)) from error
            self.repository.add(updated)
        except StockVersionConflict:
            raise
        except StaleDataError as error:  # an autoflush lost the ORM's version-guarded UPDATE
            raise StockVersionConflict(str(error)) from error
        return _view(updated)


class SqlAlchemyProcurementStockUnitOfWork(SqlAlchemyProcurementUnitOfWork):
    """Procurement UoW plus the stock port, sharing exactly one SQLAlchemy session."""

    def __enter__(self) -> SqlAlchemyProcurementStockUnitOfWork:
        super().__enter__()
        assert self.session is not None
        self.stock = SqlAlchemyProcurementStockPort(self.session)
        return self

    def commit(self) -> None:
        try:
            super().commit()
        except StockVersionConflict:
            raise
        except StaleDataError as error:  # the flush lost a version-guarded UPDATE of a line, cover or lot
            raise StockVersionConflict(str(error)) from error
