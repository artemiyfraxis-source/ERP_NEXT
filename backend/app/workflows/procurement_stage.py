"""Requests' ProcurementStageLookup port over the Procurement module's own lines.

RQ-MINE: «Мои заявки» shows each position's stage through procurement and waiting for delivery, not
just the Requests aggregate's own status (New/In work). Once a request is handed off, Procurement owns
its pipeline (ProcurementStatus: В работе → Готов к запросу → Жду счет → В оплату → В пути/Карантин →
Готово); Requests never reads Procurement's tables directly, so this is a read-only port, the same
shape as PlanSnapshotProjectRegistry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from app.modules.procurement.application import UnitOfWork as ProcurementUnitOfWork


class ProcurementLineStageLookup:
    def __init__(self, procurement_uow_factory: Callable[[], ProcurementUnitOfWork]):
        self.procurement_uow_factory = procurement_uow_factory

    def stages_for(self, request_ids: Sequence[UUID]) -> dict[UUID, str]:
        ids = list(request_ids)
        if not ids:
            return {}
        with self.procurement_uow_factory() as uow:
            lines = list(uow.procurement.by_source_request_ids(ids))
        # A request split into several procurement lines (домен `split`) keeps one row in «Мои
        # заявки»; join distinct statuses in the order Procurement created the lines.
        stages: dict[UUID, list[str]] = {}
        for line in lines:
            values = stages.setdefault(line.source_request_id, [])
            if line.status.value not in values:
                values.append(line.status.value)
        return {request_id: " / ".join(values) for request_id, values in stages.items()}
