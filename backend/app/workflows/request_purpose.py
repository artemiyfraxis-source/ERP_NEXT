"""Procurement's RequestPurposeLookup port over the Requests module's own rows.

«Весь лист» shows ЦЕЛЬ of the request a line came from. Procurement never reads Requests' tables: this is a
read-only port, one batched query per list, the same shape as ProcurementLineStageLookup.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from app.modules.requests.application import UnitOfWork as RequestsUnitOfWork


class RequestPurposeLookup:
    def __init__(self, requests_uow_factory: Callable[[], RequestsUnitOfWork]):
        self.requests_uow_factory = requests_uow_factory

    def purposes_for(self, request_ids: Sequence[UUID]) -> dict[UUID, str]:
        ids = list(dict.fromkeys(request_ids))
        if not ids:
            return {}
        with self.requests_uow_factory() as uow:
            return uow.requests.purposes_by_ids(ids)
