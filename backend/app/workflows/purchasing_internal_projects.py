"""Internal projects ↔ the rest of ERP: what hangs on each «ВП-…» and who answers for it.

Purchasing owns the projects table; the procurement lines, the requests and the staff live in other modules, so the
counts and names come through this read-only port over the same session (a read model, no writes)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing import internal_projects as service
from app.modules.requests.domain import RequestStatus
from app.modules.requests.infrastructure import RequestRow

_FINISHED_REQUESTS = {RequestStatus.CLOSED.value, RequestStatus.CANCELLED.value}


def usage(session: Session, numbers: Iterable[str]) -> dict[str, dict[str, Any]]:
    """number → {lines: {stage: count}, open_requests: n}; every number is present."""
    wanted = list(numbers)
    result: dict[str, dict[str, Any]] = {number: {"lines": {}, "open_requests": 0} for number in wanted}
    if not wanted:
        return result
    stages: dict[str, Counter] = defaultdict(Counter)
    for reference, status in session.execute(
        select(ProcurementLineRow.project_reference, ProcurementLineRow.status).where(
            ProcurementLineRow.project_reference.in_(wanted))
    ):
        stages[reference][status] += 1
    for reference, status in session.execute(
        select(RequestRow.project_reference, RequestRow.status).where(RequestRow.project_reference.in_(wanted))
    ):
        if status not in _FINISHED_REQUESTS:
            result[reference]["open_requests"] += 1
    for reference, counter in stages.items():
        result[reference]["lines"] = dict(counter)
    return result


def responsible_names(session: Session, ids: Iterable[UUID | None]) -> dict[UUID, str]:
    wanted = {item for item in ids if item is not None}
    if not wanted:
        return {}
    return {
        user_id: name for user_id, name in session.execute(
            select(IdentityUserRow.id, IdentityUserRow.name).where(IdentityUserRow.id.in_(wanted)))
    }


def listing(session: Session) -> list[dict[str, Any]]:
    rows = service.list_rows(session)
    counts = usage(session, [row.number for row in rows])
    names = responsible_names(session, [row.responsible_id for row in rows])
    return [
        {
            **service.serialize(row),
            "responsible": names.get(row.responsible_id) if row.responsible_id else None,
            "lines": counts[row.number]["lines"], "open_requests": counts[row.number]["open_requests"],
        }
        for row in rows
    ]


def open_summaries(uow_factory: Callable[[], Any]) -> list[dict[str, str]]:
    """Open projects as {number, name, purpose}: the registry of the request form and its «Внутренние проекты» group."""
    with uow_factory() as uow:
        return [
            {"number": row.number, "name": row.name, "purpose": row.purpose}
            for row in service.open_rows(uow.session)
        ]
