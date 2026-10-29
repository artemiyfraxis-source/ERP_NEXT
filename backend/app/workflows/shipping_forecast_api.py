"""HTTP: прогноз готовности проектов для «Данных отправок» — дата, статус и запас дней из «Загрузки», только чтение.
Менеджер по отправкам (Ульяна) может не иметь прав на производство, поэтому ответ открыт по праву на отправки и несёт
только {проект: {ready, status, slack_days}}: ни часов, ни людей, ни площадок."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.modules.production.api import closed_kbs, production_uow_factory, techcards_uow_factory
from app.modules.production.application import UnitOfWork
from app.shared.clock import moscow_today
from app.shared.http_auth import Principal, shipping_view_principal
from app.workflows.production_workload_api import _shipping, workload

router = APIRouter(prefix="/api/shipping/forecast", tags=["shipping"])


@router.get("")
def forecast(
    principal: Annotated[Principal, Depends(shipping_view_principal)],
    today: Annotated[date, Depends(moscow_today)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    shipping_factory: Annotated[Callable[[], Any] | None, Depends(_shipping)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    body = workload(principal, today, uow_factory, techcards_factory, shipping_factory, closed)
    return {"projects": {
        row["project"]: {"ready": row["ready"], "status": row["status"], "slack_days": row["slack_days"]}
        for row in body["projects"] if row["ready"]
    }}
