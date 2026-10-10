"""The procurement-owned port for reserving warehouse stock.

Procurement does not depend on the warehouse domain model.  The workflow layer
adapts the warehouse repository to this small contract while keeping both
changes in the caller's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence
from uuid import UUID

from app.shared.persistence import StockVersionConflict

__all__ = ["StockLotView", "StockReservationError", "StockReservationPort", "StockVersionConflict"]


@dataclass(frozen=True)
class StockLotView:
    """The lot data procurement needs to display and audit a stock cover."""

    id: UUID
    version: int
    warehouse: str
    grn: str
    package: str
    article: str
    series: str
    name: str
    item_type: str
    purpose: str
    unit: str
    storage_location: str
    balance: Decimal
    reserved: Decimal
    available: Decimal


class StockReservationError(RuntimeError):
    """A lot rejected a reserve/release operation and returned its current view."""

    def __init__(self, lot: StockLotView, message: str):
        super().__init__(message)
        self.lot = lot


class StockReservationPort(Protocol):
    def get(self, lot_id: UUID) -> StockLotView | None: ...

    def list(
        self,
        limit: int = 200,
        after_id: UUID | None = None,
        query: str = "",
        warehouse: str | None = None,
        include_zero: bool = False,
    ) -> Sequence[StockLotView]: ...

    # reserve/release raise StockReservationError when the lot refuses the quantity, and StockVersionConflict when
    # the lot is no longer at `expected_version` or changes before the transaction commits (the caller rolls back).
    def reserve(self, lot_id: UUID, quantity: Decimal, *, expected_version: int) -> StockLotView: ...

    def release(self, lot_id: UUID, quantity: Decimal, *, expected_version: int) -> StockLotView: ...
