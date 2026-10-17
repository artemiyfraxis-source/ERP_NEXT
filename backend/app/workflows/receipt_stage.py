"""Procurement's receiving ports over the Receiving module's own delivery rows.

SUPPLYMAP's owner tick mirrored Лист ожидания and Лист закупки by System ID: «Статус» of the deliveries into the
purchase line (`syncPurchaseStatusFromWaitlist_`), their latest ФАКТ. ДАТА into it (`syncActualArrival_`) and the
buyer's ОЖИД. ДАТА back into the deliveries (`syncExpectedDateToWaitlist_`). Here Receiving keeps the delivery rows
and Procurement keeps the line: the lookups hand Procurement what it reads, the copy goes through Receiving's own
narrow use case. Same shape as ProcurementLineStageLookup and WarehouseStockBalanceLookup.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.receiving.application import (
    ConcurrentReceivingUpdate,
    CopyExpectedArrivalCommand,
    UnitOfWork as ReceivingUnitOfWork,
    copy_expected_arrival,
)
from app.shared.identity import Actor


class ReceivingStatusLookup:
    def __init__(self, receiving_uow_factory: Callable[[], ReceivingUnitOfWork]):
        self.receiving_uow_factory = receiving_uow_factory

    def statuses_for(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        ids = list(procurement_line_ids)
        if not ids:
            return {}
        with self.receiving_uow_factory() as uow:
            return uow.receiving.statuses_by_procurement_line(ids)

    def arrival_dates_for(self, procurement_line_ids: Sequence[UUID]) -> dict[UUID, date]:
        ids = list(procurement_line_ids)
        if not ids:
            return {}
        with self.receiving_uow_factory() as uow:
            return uow.receiving.latest_inspection_by_procurement_line(ids)

    def copy_expected_arrivals(self, dates: Mapping[UUID, date | None], actor: Actor, now: datetime) -> int:
        """Best effort: a delivery changed under the copy is left for the next receipt tick, which copies again."""
        if not dates:
            return 0
        try:
            return len(copy_expected_arrival(CopyExpectedArrivalCommand(dates, actor, now), self.receiving_uow_factory()))
        except (IntegrityError, StaleDataError, ConcurrentReceivingUpdate):
            logging.warning("Receiving line changed during the expected-date copy; the receipt tick copies again")
            return 0
