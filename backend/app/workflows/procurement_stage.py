"""Requests' ProcurementStageLookup port over the Procurement module's own lines.

RQ-MINE: «Мои заявки» shows each position's stage through procurement and waiting for delivery, not
just the Requests aggregate's own status (New/In work). Once a request is handed off, Procurement owns
its pipeline (ProcurementStatus: В работе → Готов к запросу → Жду счет → В оплату → В пути/Карантин →
Готово); Requests never reads Procurement's tables directly, so this is a read-only port, the same
shape as PlanSnapshotProjectRegistry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from uuid import UUID

from app.modules.procurement.application import UnitOfWork as ProcurementUnitOfWork
from app.modules.requests.application import ProcurementDetails


def _joined(values: list[str | None]) -> str | None:
    distinct: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in distinct:
            distinct.append(text)
    return " / ".join(distinct) or None


class ProcurementLineStageLookup:
    def __init__(self, procurement_uow_factory: Callable[[], ProcurementUnitOfWork]):
        self.procurement_uow_factory = procurement_uow_factory

    def _lines(self, request_ids: Sequence[UUID]) -> list:
        ids = list(request_ids)
        if not ids:
            return []
        with self.procurement_uow_factory() as uow:
            return list(uow.procurement.by_source_request_ids(ids))

    def details_for(self, request_ids: Sequence[UUID]) -> dict[UUID, ProcurementDetails]:
        """«Мои заявки» shows the purchase-sheet columns to the initiator (SUPPLYMAP openMyRequests).

        A split request keeps one row: text values join distinct, ожид. дата is the latest (when the
        last part arrives), СУММА adds up the lines' fact sums (решено агентом — проверить).
        """
        by_request: dict[UUID, list] = {}
        for line in self._lines(request_ids):
            by_request.setdefault(line.source_request_id, []).append(line)
        details: dict[UUID, ProcurementDetails] = {}
        for request_id, lines in by_request.items():
            dates = [line.expected_arrival for line in lines if line.expected_arrival is not None]
            sums = [line.money.sum for line in lines if line.money.sum is not None]
            details[request_id] = ProcurementDetails(
                stage=_joined([line.status.value for line in lines]) or "",
                buyer=_joined([line.buyer.name if line.buyer else None for line in lines]),
                supplier=_joined([line.supplier for line in lines]),
                order_number=_joined([line.order_number for line in lines]),
                expected_arrival=max(dates) if dates else None,
                sum=sum(sums, Decimal("0")) if sums else None,
            )
        return details

    def stages_for(self, request_ids: Sequence[UUID]) -> dict[UUID, str]:
        lines = self._lines(request_ids)
        # A request split into several procurement lines (домен `split`) keeps one row in «Мои
        # заявки»; join distinct statuses in the order Procurement created the lines.
        stages: dict[UUID, list[str]] = {}
        for line in lines:
            values = stages.setdefault(line.source_request_id, [])
            if line.status.value not in values:
                values.append(line.status.value)
        return {request_id: " / ".join(values) for request_id, values in stages.items()}
