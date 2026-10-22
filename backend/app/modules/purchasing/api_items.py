"""Номенклатура (stage option references.items)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    procurement_edit_principal,
    procurement_view_principal,
    receiving_view_principal,
    warehouse_edit_principal,
    warehouse_view_principal,
)
from app.workflows import purchasing_items_lines as lines_flow
from app.workflows import purchasing_items_stock as stock_flow

from . import items as repo
from .deps import procurement_uow_factory, purchasing_uow_factory, receiving_uow_factory, warehouse_uow_factory
from .infrastructure import ItemRow, LineItemRow

router = APIRouter(prefix="/api/purchasing/items", tags=["purchasing"])

Uow = Annotated[Callable, Depends(purchasing_uow_factory)]
ProcurementUow = Annotated[Callable, Depends(procurement_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Editor = Annotated[Principal, Depends(procurement_edit_principal)]
WarehouseViewer = Annotated[Principal, Depends(warehouse_view_principal)]
WarehouseEditor = Annotated[Principal, Depends(warehouse_edit_principal)]
ReceivingViewer = Annotated[Principal, Depends(receiving_view_principal)]
WarehouseUow = Annotated[Callable | None, Depends(warehouse_uow_factory)]
ReceivingUow = Annotated[Callable | None, Depends(receiving_uow_factory)]


@contextmanager
def _errors():
    try:
        yield
    except repo.ItemNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except repo.ItemConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except repo.ItemInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


class ItemFields(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    part_number: str | None = Field(default=None, max_length=255)
    manufacturer: str | None = Field(default=None, max_length=255)
    category: str | None = None
    item_type: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class ItemPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, max_length=512)
    part_number: str | None = Field(default=None, max_length=255)
    manufacturer: str | None = Field(default=None, max_length=255)
    category: str | None = None
    item_type: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class AliasIn(BaseModel):
    text: str = Field(min_length=1, max_length=512)
    supplier_id: UUID | None = None


class MergeIn(BaseModel):
    keep_id: UUID
    drop_id: UUID


class LinkIn(BaseModel):
    procurement_line_id: UUID
    item_id: UUID


class ImportIn(BaseModel):
    dry_run: bool = True


class LotLinkIn(BaseModel):
    lot_id: UUID
    item_id: UUID


def _ids(text: str, what: str) -> list[UUID]:
    try:
        wanted = list(dict.fromkeys(UUID(part) for part in text.split(",") if part.strip()))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Неверный список: {what}") from None
    if len(wanted) > 500:
        raise HTTPException(status_code=422, detail="Не больше 500 за раз")
    return wanted


def _list_row(row: ItemRow, stats: dict, stock: dict | None = None) -> dict:
    return {
        **repo.item_json(row), **(stock or {"available": None, "available_unit": None, "stock_lots": 0}),
        "last_price": stats["price"]["last"], "purchases": len(stats["history"]),
        "linked_lines": stats["linked_lines"], "demand": stats["demand"],
    }


@router.get("")
def search_items(
    _: Viewer, make_uow: Uow, make_procurement: ProcurementUow, make_warehouse: WarehouseUow, make_receiving: ReceivingUow,
    q: str = "", category: str | None = None, has_price: bool = False, page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    uow = make_uow()
    with uow:
        session = uow.session
        rows, counts = repo.search(session, q, None)
        if has_price:
            # The price lives in the linked lines: the filter needs the stats of every match, not just the page.
            stats = lines_flow.stats_by_item(session, make_procurement, [row.id for row in rows])
            rows = [row for row in rows if stats[row.id]["price"]["last"] is not None]
            counts = {name: sum(1 for row in rows if row.category == name) for name in counts}
        if category:
            rows = [row for row in rows if row.category == category]
        total = len(rows)
        chunk = rows[(page - 1) * repo.PAGE_SIZE:page * repo.PAGE_SIZE]
        if not has_price:
            stats = lines_flow.stats_by_item(session, make_procurement, [row.id for row in chunk])
        stock = (
            stock_flow.available_by_item(session, make_warehouse, make_receiving, [row.id for row in chunk])
            if chunk else {}
        )
        return {
            "items": [_list_row(row, stats[row.id], stock.get(row.id)) for row in chunk],
            "counts": counts, "total": total, "page": page, "page_size": repo.PAGE_SIZE,
        }


@router.get("/for-lines")
def items_for_lines(_: Viewer, make_uow: Uow, ids: str = "") -> dict:
    """Batch for other screens: procurement line id → its item (lines without an item are absent)."""
    try:
        wanted = list(dict.fromkeys(UUID(part) for part in ids.split(",") if part.strip()))
    except ValueError:
        raise HTTPException(status_code=422, detail="Неверный список строк") from None
    if len(wanted) > 200:
        raise HTTPException(status_code=422, detail="Не больше 200 строк за раз")
    uow = make_uow()
    with uow:
        found = uow.session.execute(
            select(LineItemRow.procurement_line_id, ItemRow)
            .join(ItemRow, ItemRow.id == LineItemRow.item_id)
            .where(LineItemRow.procurement_line_id.in_(wanted))
        ).all()
        return {
            str(line_id): {"item_id": str(item.id), "name": item.name, "part_number": item.part_number, "unit": item.unit}
            for line_id, item in found
        }


@router.get("/for-lots")
def items_for_lots(_: WarehouseViewer, make_uow: Uow, make_warehouse: WarehouseUow, make_receiving: ReceivingUow, ids: str = "") -> dict:
    """Batch for «Склад»: lot id → its item and how it is known (manual / match / chain); lots without one are absent."""
    wanted = _ids(ids, "партии")
    uow = make_uow()
    with uow:
        return stock_flow.items_for_lots(uow.session, make_warehouse, make_receiving, wanted)


@router.get("/for-receiving")
def items_for_receiving(_: ReceivingViewer, make_uow: Uow, make_receiving: ReceivingUow, ids: str = "") -> dict:
    """Batch for «Приёмка»: receiving line id → the item of its procurement line."""
    wanted = _ids(ids, "строки приёмки")
    uow = make_uow()
    with uow:
        return stock_flow.items_for_receiving(uow.session, make_receiving, wanted)


@router.post("/link-lot")
def link_lot(payload: LotLinkIn, principal: WarehouseEditor, make_uow: Uow, make_warehouse: WarehouseUow, now: Now) -> dict:
    if not stock_flow.lot_exists(make_warehouse, payload.lot_id):
        raise HTTPException(status_code=404, detail="Партия не найдена")
    uow = make_uow()
    with uow, _errors():
        repo.link_lot(uow, payload.lot_id, payload.item_id, "manual", principal.actor, now)
        uow.commit()
    return {"lot_id": str(payload.lot_id), "item_id": str(payload.item_id), "via": "manual"}


@router.delete("/link-lot/{lot_id}")
def unlink_lot(lot_id: UUID, principal: WarehouseEditor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        repo.unlink_lot(uow, lot_id, principal.actor, now)
        uow.commit()
    return {"lot_id": str(lot_id)}


@router.post("/import-from-stock")
def import_from_stock(
    payload: ImportIn, principal: WarehouseEditor, make_uow: Uow, make_warehouse: WarehouseUow,
    make_receiving: ReceivingUow, now: Now,
) -> dict:
    """«Собрать со склада»: the dry run reports what apply would do and writes nothing."""
    uow = make_uow()
    with uow, _errors():
        result = stock_flow.run_import(uow, make_warehouse, make_receiving, principal.actor, now)
        if payload.dry_run:
            uow.session.rollback()
        else:
            uow.commit()
        return {**result, "dry_run": payload.dry_run}


@router.post("/import-from-procurement")
def import_from_procurement(
    payload: ImportIn, principal: Editor, make_uow: Uow, make_procurement: ProcurementUow, now: Now,
) -> dict:
    """«Собрать из закупок»: the dry run reports what apply would do and writes nothing."""
    uow = make_uow()
    with uow, _errors():
        result = lines_flow.run_import(uow, make_procurement, principal.actor, now)
        if payload.dry_run:
            uow.session.rollback()
        else:
            uow.commit()
        return {**result, "dry_run": payload.dry_run}


@router.post("/link")
def link_line(payload: LinkIn, principal: Editor, make_uow: Uow, make_procurement: ProcurementUow, now: Now) -> dict:
    if not lines_flow.line_exists(make_procurement, payload.procurement_line_id):
        raise HTTPException(status_code=404, detail="Строка закупки не найдена")
    uow = make_uow()
    with uow, _errors():
        repo.link_line(uow, payload.procurement_line_id, payload.item_id, principal.actor, now)
        uow.commit()
    return {"procurement_line_id": str(payload.procurement_line_id), "item_id": str(payload.item_id)}


@router.post("/merge")
def merge_items(payload: MergeIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        keep = repo.merge_items(uow, payload.keep_id, payload.drop_id, principal.actor, now)
        uow.commit()
        return repo.item_json(keep)


@router.post("", status_code=201)
def create_item(payload: ItemFields, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        row = repo.create_item(uow, actor=principal.actor, now=now, **payload.model_dump())
        uow.commit()
        return repo.item_json(row)


@router.get("/{item_id}")
def item_card(
    item_id: UUID, _: Viewer, make_uow: Uow, make_procurement: ProcurementUow, make_warehouse: WarehouseUow,
    make_receiving: ReceivingUow,
) -> dict:
    uow = make_uow()
    with uow, _errors():
        row = repo.get_item(uow.session, item_id)
        stats = lines_flow.stats_by_item(uow.session, make_procurement, [row.id])[row.id]
        return {
            **repo.item_json(row), "aliases": repo.aliases_of(uow.session, row.id),
            "linked_lines": stats["linked_lines"], "demand": stats["demand"], "history": stats["history"],
            "price": stats["price"], "quotes": repo.quotes_of(uow.session, row.id),
            "stock": stock_flow.stock_of_item(uow.session, make_warehouse, make_receiving, row.id),
            "receipts": stock_flow.receipts_of_item(uow.session, make_receiving, row.id),
        }


@router.patch("/{item_id}")
def update_item(item_id: UUID, payload: ItemPatch, principal: Editor, make_uow: Uow, now: Now) -> dict:
    changes = {key: getattr(payload, key) for key in payload.model_fields_set if key != "version"}
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="Наименование обязательно")
    if "category" in changes and changes["category"] is None:
        raise HTTPException(status_code=422, detail="Категория обязательна")
    uow = make_uow()
    with uow, _errors():
        row = repo.update_item(uow, item_id, payload.version, changes, principal.actor, now)
        uow.commit()
        return repo.item_json(row)


@router.post("/{item_id}/archive")
def archive_item(item_id: UUID, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        row = repo.archive_item(uow, item_id, principal.actor, now)
        uow.commit()
        return repo.item_json(row)


@router.post("/{item_id}/aliases", status_code=201)
def add_alias(item_id: UUID, payload: AliasIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        repo.add_alias(uow, item_id, payload.text, payload.supplier_id, principal.actor, now)
        uow.commit()
        return {"aliases": repo.aliases_of(uow.session, item_id)}


@router.delete("/{item_id}/aliases/{alias_id}")
def delete_alias(item_id: UUID, alias_id: UUID, principal: Editor, make_uow: Uow, now: Now) -> dict:
    uow = make_uow()
    with uow, _errors():
        repo.delete_alias(uow, item_id, alias_id, principal.actor, now)
        uow.commit()
        return {"aliases": repo.aliases_of(uow.session, item_id)}
