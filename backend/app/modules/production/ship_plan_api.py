"""HTTP: план от даты отгрузки и варианты первой поставки — подсказка в автоплане «По проекту», ничего не пишет."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal

from .api import closed_kbs, production_uow_factory, techcards_uow_factory
from .application import UnitOfWork
from .autoplan_api import PreviewPayload, _command, _handle, _techcards, _units, references_uow_factory
from .ship_plan_application import describe_ship_plan, ship_plan


router = APIRouter(prefix="/api/production/autoplan", tags=["production"])


class ShipPlanPayload(PreviewPayload):
    # Дата отгрузки проекта; не задана — ближайший срок его заданий и КБ.
    ship_date: date | None = None


@router.post("/ship-plan")
def preview_ship_plan(
    payload: ShipPlanPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    (cards, ready), units = _techcards(techcards_factory, closed), _units(references_factory)
    return _handle(lambda: describe_ship_plan(
        ship_plan(_command(payload, principal, now), payload.ship_date, uow_factory(), cards, units, ready),
    ))
