"""Procurement's ReceiptStatusLookup port over the Receiving module's own delivery rows.

SUPPLYMAP's owner tick `syncPurchaseStatusFromWaitlist_` mirrored the «Статус» of Лист ожидания into the
purchase line by System ID. Here Receiving keeps the delivery rows and Procurement keeps the line, so
this read-only port hands Procurement the receiving statuses of its lines, the same shape as
ProcurementLineStageLookup and WarehouseStockBalanceLookup.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from app.modules.receiving.application import UnitOfWork as ReceivingUnitOfWork


class ReceivingStatusLookup:
    def __init__(self, receiving_uow_factory: Callable[[], ReceivingUnitOfWork]):
        self.receiving_uow_factory = receiving_uow_factory

    def statuses_for(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        ids = list(procurement_line_ids)
        if not ids:
            return {}
        with self.receiving_uow_factory() as uow:
            return uow.receiving.statuses_by_procurement_line(ids)
