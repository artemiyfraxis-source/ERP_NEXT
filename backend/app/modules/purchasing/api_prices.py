"""Цены и карточки позиции и поставщика (stage option references.price_book): справочные цены, цены из УПД, счета
Kaiten, каталог закупок и «Завести позиции из УПД». Money in kopecks (price refs keep unit_price4)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal

from . import items as items_repo
from . import upd_prices
from .deps import purchasing_uow_factory
from .infrastructure import SupplierRow

router = APIRouter(prefix="/api/purchasing/prices", tags=["purchasing"])

Uow = Annotated[Callable, Depends(purchasing_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Editor = Annotated[Principal, Depends(procurement_edit_principal)]


class ApplyIn(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=5000)


@router.get("/item/{item_id}")
def item_prices(item_id: UUID, _: Viewer, make_uow: Uow) -> dict:
    uow = make_uow()
    with uow:
        try:
            item = items_repo.get_item(uow.session, item_id)
        except items_repo.ItemNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return upd_prices.item_prices(uow.session, item)


@router.get("/supplier/{supplier_id}")
def supplier_prices(supplier_id: UUID, _: Viewer, make_uow: Uow) -> dict:
    uow = make_uow()
    with uow:
        supplier = uow.session.get(SupplierRow, supplier_id)
        if supplier is None:
            raise HTTPException(status_code=404, detail="Поставщик не найден")
        return upd_prices.supplier_prices(uow.session, supplier)


@router.get("/catalog")
def catalog(_: Viewer, make_uow: Uow, q: str = "", page: Annotated[int, Query(ge=1)] = 1) -> dict:
    uow = make_uow()
    with uow:
        return upd_prices.catalog(uow.session, q, page)


@router.get("/catalog/points")
def catalog_points(_: Viewer, make_uow: Uow, key: str = Query(min_length=3, max_length=600)) -> list[dict]:
    uow = make_uow()
    with uow:
        return upd_prices.catalog_points(uow.session, key)


@router.get("/upd-items/plan")
def plan_upd_items(_: Viewer, make_uow: Uow) -> dict:
    """«Завести позиции из УПД», шаг 1: what would be created (nothing is written)."""
    uow = make_uow()
    with uow:
        return upd_prices.plan_upd_items(uow.session)


@router.post("/upd-items/apply")
def apply_upd_items(payload: ApplyIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow:
        result = upd_prices.apply_upd_items(uow, payload.keys, principal.actor, now)
        uow.commit()
        return result
