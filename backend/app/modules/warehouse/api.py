"""Warehouse HTTP contract for stock, movement, writeoff and its return."""

from collections.abc import Callable
from datetime import date, date as date_type, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.article_maker import split_article_maker
from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    warehouse_admin_principal,
    warehouse_edit_principal,
    warehouse_journal_principal,
    warehouse_view_principal,
)
from app.shared.events import AuditEvent
from app.shared.warehouse_location import STORAGE_LOCATION_MAX_LENGTH, RackLayout, current_rack_layout, rack_layout_from_config
from app.modules.references.domain import TechOperationTab
from app.workflows.stock_receipt import expected_in_receiving, procurement_line, purchasing_item
from app.workflows.warehouse_article import infer_article_identity

from .application import (
    MANUAL_STOCK_LIMIT,
    AddManualStockCommand,
    ConcurrentWarehouseUpdate,
    CorrectLotLocationCommand,
    EditLotIdentityCommand,
    EditLotRowCommand,
    ManualStockItem,
    MoveLocationCommand,
    MoveLotCommand,
    ReturnWriteOffCommand,
    UndoLastCommand,
    UnitOfWork,
    shelf_occupancy,
    WriteOffCommand,
    add_manual_stock,
    correct_lot_location,
    edit_lot_identity,
    edit_lot_row,
    edited_unit_mass,
    move_location,
    move_lot,
    lot_destinations,
    return_writeoff,
    scan_stock,
    undo_last_movement,
    undo_preview,
    warehouse_lots,
    warehouse_totals,
    write_off,
)
from .domain import LOT_DETAIL_LIMITS, LOT_ROW_FIELDS, StockLot, WarehouseReceipt, WarehouseValidationError
from .item_types import ITEM_TYPE_MAX_LENGTH, ITEM_TYPES_MAX
from .journal import warehouse_journal
from .receipts_application import article_facts, lot_receipts
from .reference_fields import (
    BULK_EDIT_FIELDS,
    BULK_EDIT_LIMIT,
    BulkEditResult,
    ReferenceEdit,
    export_rows,
    import_reference_fields,
    item_type_dictionary,
    rename_item_type,
    save_item_types,
)


router = APIRouter(prefix="/api/stock", tags=["warehouse"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
MassInput = Annotated[str, Field(max_length=64)] | float | None


class WriteOffPayload(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity
    purpose: str = Field(default="", max_length=255)  # НАЗНАЧЕНИЕ; emptiness is refused with the SUPPLYMAP text
    reason: str = Field(default="", max_length=1024)  # ОСНОВАНИЕ; empty means «Выдача»
    # ДАТА списания (Writeoff.html: по умолчанию сегодня, можно задним числом); пусто — сейчас.
    date: date_type | None = None


class ReturnWriteOffPayload(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity
    source: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=1024)


class MovePayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(default="", max_length=16)
    warehouse: str = Field(default="", max_length=8)


class CorrectLocationPayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(default="", max_length=STORAGE_LOCATION_MAX_LENGTH)


class EditIdentityPayload(BaseModel):
    version: int = Field(ge=1)
    article: str = Field(min_length=1, max_length=1024)
    name: str = Field(default="", max_length=1024)
    series: str = Field(default="", max_length=255)


def _blank_is_none(value):
    # SUPPLYMAP Stock05.gs:1058: an empty number input means «don't touch», never zero.
    return None if isinstance(value, str) and not value.strip() else value


EditQuantity = Annotated[
    Annotated[Decimal, Field(max_digits=18, decimal_places=6)] | None, BeforeValidator(_blank_is_none)
]


class EditRowPayload(BaseModel):
    """Manual edit of a stock row (SK-7). A field left out (or null) is not touched; "" clears a text field.
    ГРН, ПАКЕТ and СКЛАД are the row key and are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    article: str | None = Field(default=None, max_length=1024)
    series: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=1024)
    item_type: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=32)
    purpose: str | None = Field(default=None, max_length=128)
    storage_location: str | None = Field(default=None, max_length=STORAGE_LOCATION_MAX_LENGTH)
    unit_mass_g: str | None = Field(default=None, max_length=64)  # "" = no mass; a number as typed
    expiry_date: str | None = Field(default=None, max_length=10)  # "" = no date; ГГГГ-ММ-ДД
    manufacturer: str | None = Field(default=None, max_length=LOT_DETAIL_LIMITS["manufacturer"])
    description: str | None = Field(default=None, max_length=LOT_DETAIL_LIMITS["description"])
    storage_conditions: str | None = Field(default=None, max_length=LOT_DETAIL_LIMITS["storage_conditions"])
    datasheet: str | None = Field(default=None, max_length=LOT_DETAIL_LIMITS["datasheet"])
    certificate: str | None = Field(default=None, max_length=LOT_DETAIL_LIMITS["certificate"])
    inbound: EditQuantity = None
    written_off: EditQuantity = None
    reserved: EditQuantity = None

    def changes(self) -> dict[str, object]:
        """The fields to set, typed for `StockLot.edit_row`."""
        changes: dict[str, object] = {}
        for field_name in LOT_ROW_FIELDS:
            value = getattr(self, field_name)
            if value is None:
                continue
            if field_name == "unit_mass_g":
                value = edited_unit_mass(value)
            elif field_name == "expiry_date":
                try:
                    value = date.fromisoformat(value.strip()) if value.strip() else None
                except ValueError as error:
                    raise WarehouseValidationError(f"Срок годности — дата ГГГГ-ММ-ДД, а не «{value}»") from error
            changes[field_name] = value
        return changes


class MoveLocationPayload(BaseModel):
    warehouse: str = Field(pattern="^(ЦАР|ЖУК)$")
    source_location: str = Field(min_length=3, max_length=16)
    target_location: str = Field(min_length=3, max_length=16)


class StockItemInput(BaseModel):
    """A quick-entry row without its warehouse (a delivery line takes the delivery's). Only the shape and the column
    lengths are checked here — a bad value in a row is the row's error, not the request's (SK-10, Stock05.gs:744-757)."""

    article: str = Field(default="", max_length=1024)  # empty → the name (Warehouse.gs:522)
    series: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=1024)
    item_type: str = Field(default="", max_length=255)
    purpose: str = Field(default="", max_length=128)  # empty → «Склад»
    unit: str = Field(default="", max_length=32)  # empty → «шт»
    quantity: Annotated[str, Field(max_length=64)] | int | float | None = None
    storage_location: str = Field(default="", max_length=64)
    # Lot passport (SK-6), all optional. МАССА 1 ШТ: weight of 1, 10 or 100 pieces, grams, as typed.
    mass_1: MassInput = None
    mass_10: MassInput = None
    mass_100: MassInput = None
    expiry_date: date | None = None
    manufacturer: str = Field(default="", max_length=LOT_DETAIL_LIMITS["manufacturer"])
    description: str = Field(default="", max_length=LOT_DETAIL_LIMITS["description"])
    storage_conditions: str = Field(default="", max_length=LOT_DETAIL_LIMITS["storage_conditions"])
    datasheet: str = Field(default="", max_length=LOT_DETAIL_LIMITS["datasheet"])
    certificate: str = Field(default="", max_length=LOT_DETAIL_LIMITS["certificate"])

    def manual_item(self, warehouse: str = "") -> ManualStockItem:
        return ManualStockItem(
            warehouse=warehouse,
            article=self.article,
            series=self.series.strip(),
            name=self.name,
            item_type=self.item_type.strip(),
            purpose=self.purpose,
            unit=self.unit,
            quantity=self.quantity,
            storage_location=self.storage_location,
            mass_1=self.mass_1,
            mass_10=self.mass_10,
            mass_100=self.mass_100,
            expiry_date=self.expiry_date,
            manufacturer=self.manufacturer,
            description=self.description,
            storage_conditions=self.storage_conditions,
            datasheet=self.datasheet,
            certificate=self.certificate,
        )


class ManualStockInput(StockItemInput):
    """A row of «Внести позиции» (`errors[{i, error}]` per row)."""

    warehouse: str = Field(default="", max_length=8)  # empty → ЦАР


class ManualStockPayload(BaseModel):
    items: list[ManualStockInput] = Field(min_length=1, max_length=MANUAL_STOCK_LIMIT)


def warehouse_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "warehouse_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _result(lot: StockLot) -> dict:
    return {
        "id": str(lot.id),
        "version": lot.version,
        "warehouse": lot.warehouse,
        "grn": lot.grn,
        "package": lot.package,
        "article": lot.article,
        "series": lot.series,
        "name": lot.name,
        "item_type": lot.item_type,
        "purpose": lot.purpose,
        "unit": lot.unit,
        "storage_location": lot.storage_location,
        "inbound": _number(lot.inbound),
        "written_off": _number(lot.written_off),
        "reserved": _number(lot.reserved),
        "balance": _number(lot.balance),
        "available": _number(lot.available),
        "source_receiving_ids": list(lot.source_receiving_ids),
        "unit_mass_g": _number(lot.unit_mass_g) if lot.unit_mass_g is not None else None,
        "expiry_date": lot.expiry_date.isoformat() if lot.expiry_date else None,
        "manufacturer": lot.manufacturer,
        "description": lot.description,
        "storage_conditions": lot.storage_conditions,
        "datasheet": lot.datasheet,
        "certificate": lot.certificate,
        "upd_name": lot.upd_name,
        "invoice_name": lot.invoice_name,
    }


def receipt_brief(receipt: WarehouseReceipt | None) -> dict | None:
    """The delivery a lot came with, as the lot list shows it (its document columns read the receipt, not the lot)."""
    if receipt is None:
        return None
    return {
        "id": str(receipt.id),
        "supplier": receipt.supplier,
        "invoice_number": receipt.invoice_number,
        "invoice_date": receipt.invoice_date.isoformat() if receipt.invoice_date else None,
        "upd_number": receipt.upd_number,
        "upd_date": receipt.upd_date.isoformat() if receipt.upd_date else None,
        "prd_number": receipt.prd_number,
        "prd_date": receipt.prd_date.isoformat() if receipt.prd_date else None,
        "scan_file": receipt.scan_file,
    }


def _guarded(operation):
    try:
        return operation()
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentWarehouseUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


def _run(operation) -> dict:
    return {"lot": _result(_guarded(operation))}


def _kitting_reserve(request: Request, lot_id: UUID) -> Decimal:
    """Reserve kitting lines hold on the lot, read through the kitting unit of work (no import of that
    module here). A kitting bind or unbind after this read bumps the lot version, so the edit then fails
    with 409 instead of trusting a stale number."""
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    if not callable(factory):
        return Decimal("0")
    with factory() as uow:
        return uow.fulfillment.reserved_on_lot(lot_id)


def _procurement_reserve(request: Request, lot_id: UUID) -> Decimal:
    """Reserve «Со склада» of procurement lines on the lot, read through the procurement unit of work the same
    way as `_kitting_reserve` (a cover or release after this read bumps the lot version → 409)."""
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if not callable(factory):
        return Decimal("0")
    with factory() as uow:
        covers = getattr(uow, "stock_covers", None)
        return covers.reserved_on_lot(lot_id) if covers is not None else Decimal("0")


def _reserve_holders(request: Request, lots: list[StockLot]) -> dict[UUID, list[dict]]:
    """Who holds each lot's РЕЗЕРВ: procurement «Со склада» covers («SM-20261002-01 / 591К.3 — 80 шт», owner
    2026-10-04), techcard reserves («728К.2 · КБ1 · техкарта — 10 шт») and kit requests («REQ-… · назначение — 5 шт»).
    Each module is read through its own unit of work factory, no import of it here; a module that is not wired
    (or has no such repository) adds nothing."""
    if not lots:
        return {}
    ids = [lot.id for lot in lots]
    holders: dict[UUID, list[dict]] = {}
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if callable(factory):
        with factory() as uow:
            covers = getattr(uow, "stock_covers", None)
            for cover in covers.active_on_lots(ids) if covers is not None else ():
                holders.setdefault(cover.lot_id, []).append({
                    "kind": "procurement",
                    "label": cover.label,
                    "procurement_line_id": str(cover.line_id),
                    "quantity": _number(cover.quantity),
                    "unit": cover.unit,
                })
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    if callable(factory):
        with factory() as uow:
            reserves = getattr(uow, "kit_reserves", None)
            for reserve in reserves.active_on_lots(ids) if reserves is not None else ():
                holders.setdefault(reserve.lot_id, []).append({
                    "kind": "techcard",
                    "label": f"{reserve.label} · техкарта",
                    "project": reserve.project,
                    "item_index": reserve.item_index,
                    "quantity": _number(reserve.quantity),
                    "unit": reserve.unit,
                })
            for request_id, number, purpose, lot_id, unit, reserved in uow.fulfillment.holders_on_lots(ids):
                holders.setdefault(lot_id, []).append({
                    "kind": "kit",
                    "label": f"{number} · {purpose}".rstrip(" ·"),
                    "request_id": str(request_id),
                    "request_number": number,
                    "quantity": _number(reserved),
                    "unit": unit,
                })
    return holders


WHOLE_STOCK = 10**9  # "no limit" for the paged read: SQL LIMIT and slices take it as all


def _reserve_note(holders: list[dict]) -> str:
    if not holders:
        return ""
    return "резерв: " + "; ".join(f"{item['label']} — {item['quantity']} {item['unit']}".rstrip() for item in holders)


@router.get("")
def lots(
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    # Without `limit` — the whole filter in one answer, always (owner 28.10.2026: no «Показать ещё» while scrolling;
    # 673 lots ≈ 0.5 s): the sorted read orders the whole filter anyway, pages only re-did it. `limit`/`after` stay
    # for other callers that want a page.
    limit: Annotated[int | None, Query(ge=1)] = None,
    after: UUID | None = None,
    q: Annotated[str, Query(max_length=255)] = "",
    zero: bool = False,
    warehouse: Annotated[str | None, Query(pattern="^(ЦАР|ЖУК)$")] = None,
    sort: Annotated[str | None, Query(pattern="^(type|grn|balance|reserved|available|lots|place|series|article|name|warehouse|maker|upd_name|invoice_name|invoice_number|invoice_date|upd_number|upd_date|prd_number|prd_date)$")] = None,
    desc: bool = False,
    group: Annotated[str, Query(pattern="^(type|place|name|series|grn)?$")] = "",
) -> dict:
    """`sort` (+ `desc`, `group` as in the grouped table) orders the WHOLE filter before the page is cut."""
    try:
        lots, total = warehouse_lots(
            uow_factory(),
            limit=(limit + 1) if limit is not None else WHOLE_STOCK,
            after_id=after,
            query=q,
            warehouse=warehouse,
            include_zero=zero,
            sort=sort,
            desc=desc,
            group=group,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    page = lots[:limit] if limit is not None else lots
    holders = _reserve_holders(request, [lot for lot in page if lot.reserved > 0])
    receipts = lot_receipts(uow_factory(), page)
    return {
        "lots": [
            {
                **_result(lot),
                "reserve_holders": holders.get(lot.id, []),
                "reserve_note": _reserve_note(holders.get(lot.id, [])),
                "receipt": receipt_brief(receipts.get(lot.receipt_id)),
            }
            for lot in page
        ],
        "total": total,
        "truncated": max(0, total - len(page)),
        "next_cursor": str(page[-1].id) if limit is not None and len(lots) > limit else None,
    }


def _totals(item) -> dict:
    return {
        "balance": _number(item.balance),
        "reserved": _number(item.reserved),
        "available": _number(item.available),
        "lots": item.lots,
        "units": sorted(item.units),
    }


@router.get("/totals")
def totals(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    group: Annotated[str, Query(pattern="^(type|place|name|series|grn)?$")] = "",
    q: Annotated[str, Query(max_length=255)] = "",
    zero: bool = False,
    warehouse: Annotated[str | None, Query(pattern="^(ЦАР|ЖУК)$")] = None,
) -> dict:
    """Sums per group and per article inside a group over ALL lots of the filter (q/zero/warehouse as in the list):
    balance, reserve, available, lot count. The grouped table shows them whatever number of lots it has loaded."""
    groups, articles = warehouse_totals(uow_factory(), group=group, query=q, warehouse=warehouse, include_zero=zero)
    return {
        "group": group,
        "groups": [{"group": key, "articles": len(item.articles), **_totals(item)} for key, item in groups.items()],
        "articles": [{"group": key[0], "article": key[1], **_totals(item)} for key, item in articles.items()],
    }


@router.get("/scan")
def scan(
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    code: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """Скан: ГРН (with or without «-П2», any case, look-alikes, Russian layout) → its lot(s);
    a place code → the lots with a balance lying there (both warehouses); else 404 «Не найдено: …».
    The ТСД screen scans through the same route."""
    try:
        result = scan_stock(uow_factory(), code)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    holders = _reserve_holders(request, [lot for lot in result.lots if lot.reserved > 0])
    receipts = lot_receipts(uow_factory(), result.lots)
    return {
        "kind": result.kind,
        "code": result.code,
        "place": result.place,
        "lots": [
            {**_result(lot), "reserve_holders": holders.get(lot.id, []),
             "reserve_note": _reserve_note(holders.get(lot.id, [])),
             "receipt": receipt_brief(receipts.get(lot.receipt_id))}
            for lot in result.lots
        ],
    }


@router.get("/journal")
def journal(
    _principal: Annotated[Principal, Depends(warehouse_journal_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    q: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """SK-16 «Журнал склада» (portalStock05Log): events + write-offs, returns and issues, newest first, the last
    400 matches of the search. Who works with the warehouse (owner 12.10.2026: the storekeeper sees a lot's history)."""
    page = warehouse_journal(uow_factory(), q)
    return {
        "rows": [
            {"occurred_at": row.occurred_at.isoformat(), "kind": row.kind, "lot": row.lot, "name": row.name,
             "event": row.event, "who": row.who, "warehouse": row.warehouse}
            for row in page.rows
        ],
        "total": page.total,
        "truncated": page.truncated,
    }


class RackLayoutPayload(BaseModel):
    racks: int
    shelves: int
    perRack: dict[str, int] = Field(default_factory=dict, max_length=99)


def _layout_dict(layout: RackLayout) -> dict:
    return {
        "racks": layout.racks,
        "shelves": layout.shelves,
        "perRack": {str(rack): count for rack, count in sorted(layout.per_rack.items())},
    }


def rack_layout_store(request: Request):
    store = getattr(request.app.state, "rack_layout_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return store


@router.get("/rack-layout")
def rack_layout(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    """«Раскладка»: сколько стеллажей, полок по умолчанию и стеллажи со своим числом полок."""
    return {"layout": _layout_dict(current_rack_layout())}


@router.put("/rack-layout")
def save_rack_layout(
    payload: RackLayoutPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    store=Depends(rack_layout_store),
) -> dict:
    """portalStock05PlacesSave / stock05PlacesSave_: те же проверки и то же сообщение; правка — в журнал."""
    try:
        layout = rack_layout_from_config(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    message = (
        f"Стеллажей: {layout.racks}, полок по умолчанию: {layout.shelves}"
        + (f", особых стеллажей: {len(layout.per_rack)}" if layout.per_rack else "")
        + "."
    )
    store.save(layout, AuditEvent(
        id=uuid4(), aggregate_id=UUID(int=0), event_type="warehouse.rack_layout_saved",
        actor=principal.actor, occurred_at=now,
        details={**_layout_dict(layout), "message": f"раскладка стеллажей — {message}"},
    ))
    return {"layout": _layout_dict(layout), "message": message}


@router.get("/places")
def shelf_places(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    warehouse: Annotated[str, Query(pattern="^(ЦАР|ЖУК)$")],
    shelf: Annotated[str, Query(min_length=1, max_length=8)],
) -> dict:
    """SK-15: places taken on a shelf («12C») and its smallest free number. A hint, never a refusal."""
    result = _guarded(lambda: shelf_occupancy(uow_factory(), warehouse, shelf))
    return {
        "warehouse": result.warehouse,
        "shelf": result.shelf,
        "occupied": result.occupied,
        "next_number": result.next_number,
    }


def _factory(request: Request, name: str):
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


@router.get("/article-info")
def article_info(
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    article: Annotated[str, Query(alias="q", min_length=1, max_length=1024)],
    supplier: Annotated[str, Query(max_length=255)] = "",
    invoice_number: Annotated[str, Query(max_length=128)] = "",
    warehouse: Annotated[str | None, Query(pattern="^(ЦАР|ЖУК)$")] = None,
) -> dict:
    """What «Внести позиции» / «Приход на склад» fill for a typed article. Sources in priority order, each filling
    only what is still empty: the last lot of the article, the reference DB (code rules, БД.ТЕР, БД.КОАКС), the
    purchasing nomenclature, the procurement line of the delivery's invoice. `places` — where the article lies with a
    balance (in `warehouse`), newest first, at most 5; `expected` — the line «Приёмка» is still waiting for.
    «XHP-4@JST» is looked up as «XHP-4», and «JST» fills an empty «Производитель» (owner 23.10.2026)."""
    split = split_article_maker(article)
    article = split.article or article
    records = []
    factory = _factory(request, "references_uow_factory")
    if factory is not None:
        with factory() as uow:
            for tab in (TechOperationTab.TERMINAL, TechOperationTab.COAX):
                records.extend(uow.tech_operations.list(tab=tab, query="", limit=10_000))
    identity = infer_article_identity(article, records)
    item = purchasing_item(_factory(request, "purchasing_uow_factory"), article)
    line = procurement_line(
        _factory(request, "procurement_uow_factory"), supplier=supplier, invoice_number=invoice_number, article=article,
    )
    reference = None if identity is None else {
        "series": identity.series, "name": identity.name, "item_type": identity.item_type,
    }
    facts = article_facts(uow_factory(), article, warehouse, (
        ("артикул", {"manufacturer": split.maker} if split.maker else None),
        (identity.source if identity is not None else "", reference),
        ("номенклатура закупок", item),
        (f"строка закупки {line['legacy_id']}" if line else "", line),
    ))
    expected = expected_in_receiving(
        _factory(request, "receiving_uow_factory"), _factory(request, "procurement_uow_factory"),
        supplier=supplier, invoice_number=invoice_number, article=article,
    )
    return {
        "info": facts.info,
        "places": facts.places,
        "expected": None if expected is None else {"order_number": expected.order_number, "supplier": expected.supplier},
    }


@router.post("/batches")
def add_batches(
    payload: ManualStockPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    try:
        result = add_manual_stock(
            AddManualStockCommand(
                items=tuple(item.manual_item(item.warehouse) for item in payload.items),
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: повторите ввод") from error
    # stock05Add_ answer: {added, errors:[{i, error}], assigned:[{i, grn, …}]}; i is the 0-based row index.
    return {
        "added": len(result.assigned),
        "lots": [_result(row.lot) for row in result.assigned],
        "assigned": [
            {
                "i": row.i,
                "id": str(row.lot.id),
                "grn": row.lot.grn,
                "article": row.lot.article,
                "quantity": _number(row.lot.inbound),
                "unit": row.lot.unit,
                "storage_location": row.lot.storage_location,
                "warehouse": row.lot.warehouse,
            }
            for row in result.assigned
        ],
        "errors": [{"i": row.i, "error": row.error} for row in result.errors],
    }


@router.get("/batches/{lot_id}/issued-to")
def issued_to(
    lot_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """«Откуда» of a return: where this lot was written off to and how much is still there."""
    destinations = _guarded(lambda: lot_destinations(lot_id, uow_factory()))
    return {"destinations": [
        {"name": item.name, "written_off": _number(item.written_off), "returned": _number(item.returned),
         "outstanding": _number(item.outstanding), "unit": item.unit, "last_date": item.last_date.isoformat()}
        for item in destinations
    ]}


@router.post("/batches/{lot_id}/writeoff")
def writeoff(
    lot_id: UUID,
    payload: WriteOffPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    return _run(
        lambda: write_off(
            WriteOffCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                quantity=payload.quantity,
                purpose=payload.purpose,
                reason=payload.reason,
                actor=principal.actor,
                now=now,
                entry_date=payload.date,
            ),
            uow_factory(),
        )
    )


@router.post("/batches/{lot_id}/return")
def return_to_stock(
    lot_id: UUID,
    payload: ReturnWriteOffPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    return _run(
        lambda: return_writeoff(
            ReturnWriteOffCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                quantity=payload.quantity,
                source=payload.source,
                reason=payload.reason,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.post("/batches/{lot_id}/move")
def move(
    lot_id: UUID,
    payload: MovePayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    return _run(
        lambda: move_lot(
            MoveLotCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                storage_location=payload.storage_location,
                actor=principal.actor,
                now=now,
                warehouse=payload.warehouse,
            ),
            uow_factory(),
        )
    )


class UndoPayload(BaseModel):
    version: int = Field(ge=1)


@router.get("/batches/{lot_id}/undo")
def undo_last_preview(
    lot_id: UUID,
    principal: Annotated[Principal, Depends(warehouse_view_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """What «Отменить последнее» would take back, or why nothing can be."""
    try:
        preview = undo_preview(lot_id, principal.actor, now, uow_factory())
    except WarehouseValidationError as error:
        return {"undo": None, "reason": str(error)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"undo": {"label": preview.label, "occurred_at": preview.occurred_at.isoformat()}, "reason": ""}


@router.post("/batches/{lot_id}/undo")
def undo_last(
    lot_id: UUID,
    payload: UndoPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """«Отменить последнее»: own write-off / return / move of the last 10 minutes, taken back by a
    compensating entry (the journal keeps both)."""
    lot, label = _guarded(lambda: undo_last_movement(
        UndoLastCommand(lot_id=lot_id, expected_version=payload.version, actor=principal.actor, now=now),
        uow_factory(),
    ))
    return {"lot": _result(lot), "undone": label}


@router.put("/batches/{lot_id}/location")
def correct_location(
    lot_id: UUID,
    payload: CorrectLocationPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    return _run(
        lambda: correct_lot_location(
            CorrectLotLocationCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                storage_location=payload.storage_location,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.put("/batches/{lot_id}/identity")
def edit_identity(
    lot_id: UUID,
    payload: EditIdentityPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    return _run(
        lambda: edit_lot_identity(
            EditLotIdentityCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                article=payload.article,
                name=payload.name,
                series=payload.series,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.put("/batches/{lot_id}/row")
def edit_row(
    lot_id: UUID,
    payload: EditRowPayload,
    request: Request,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """SUPPLYMAP portalStock05Edit: the whole row by hand — admin and storekeeper (portalStock05CanFix_)."""

    def operation():
        changes = payload.changes()
        return edit_lot_row(
            EditLotRowCommand(
                lot_id=lot_id,
                expected_version=payload.version,
                changes=changes,
                actor=principal.actor,
                now=now,
                kitting_reserve=_kitting_reserve(request, lot_id) if "reserved" in changes else Decimal("0"),
                procurement_reserve=(
                    _procurement_reserve(request, lot_id) if "reserved" in changes else Decimal("0")
                ),
            ),
            uow_factory(),
        )

    lot, changes = _guarded(operation)
    label = lot.grn + (f"/{lot.package}" if lot.package else "")
    return {
        "lot": _result(lot),
        "changes": [{"field": field, "column": column, "from": was, "to": to} for field, column, was, to in changes],
        "message": f"Строка {label} обновлена: {len(changes)} поле(й)." if changes else "Изменений нет.",
    }


@router.post("/locations/move")
def move_storage_location(
    payload: MoveLocationPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    try:
        moved = move_location(MoveLocationCommand(
            warehouse=payload.warehouse,
            source_location=payload.source_location,
            target_location=payload.target_location,
            actor=principal.actor,
            now=now,
        ), uow_factory())
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    return {"lots": [_result(lot) for lot in moved]}


# ── Item type dictionary, rename, export / import of reference fields (SK-14) ──────────────────────


class ItemTypesPayload(BaseModel):
    # One type per entry; blank, too long and duplicate lines are dropped by the rule, not refused here.
    types: list[Annotated[str, Field(max_length=1024)]] = Field(max_length=ITEM_TYPES_MAX * 5)


class ItemTypeRenamePayload(BaseModel):
    source: str = Field(max_length=255)
    target: str = Field(max_length=255)


class ReferenceEditInput(BaseModel):
    """A row of the imported table: the key and the columns the table has. A column left out is not touched."""

    model_config = ConfigDict(extra="forbid")

    warehouse: str = Field(default="", max_length=64)
    grn: str = Field(min_length=1, max_length=64)
    package: str = Field(default="", max_length=64)
    article: str | None = Field(default=None, max_length=4096)
    series: str | None = Field(default=None, max_length=4096)
    name: str | None = Field(default=None, max_length=4096)
    item_type: str | None = Field(default=None, max_length=4096)
    unit: str | None = Field(default=None, max_length=4096)
    purpose: str | None = Field(default=None, max_length=4096)
    storage_location: str | None = Field(default=None, max_length=4096)
    unit_mass_g: str | None = Field(default=None, max_length=4096)
    expiry_date: str | None = Field(default=None, max_length=4096)
    manufacturer: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=8192)
    storage_conditions: str | None = Field(default=None, max_length=4096)


class ReferenceImportPayload(BaseModel):
    edits: list[ReferenceEditInput] = Field(max_length=BULK_EDIT_LIMIT)
    apply: bool = False


def _dictionary(uow_factory) -> dict:
    dictionary = item_type_dictionary(uow_factory())
    return {"types": dictionary.types, "counts": dictionary.counts, "choices": dictionary.choices}


@router.get("/item-types")
def item_types(
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """portalStock05TypesGet + the dropdown of stock05Refs_: the dictionary, rows per type, the choices."""
    return _dictionary(uow_factory)


@router.put("/item-types")
def put_item_types(
    payload: ItemTypesPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """portalStock05TypesSave — admin and storekeeper (portalStock05FixGuard_)."""
    types = _guarded(lambda: save_item_types(payload.types, principal.actor, now, uow_factory()))
    return {**_dictionary(uow_factory), "message": f"Справочник типов сохранён: {len(types)}."}


@router.post("/item-types/rename")
def rename_type(
    payload: ItemTypeRenamePayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """portalStock05TypeRename — the type in every stock row, each row journaled."""
    result = _guarded(lambda: rename_item_type(payload.source, payload.target, principal.actor, now, uow_factory()))
    source, target = payload.source.strip(), payload.target.strip()
    message = (
        f"Тип «{source}» → «{target}»: строк {result.rows}." if result.found
        else f"Строк с типом «{source}» на складе нет."
    )
    return {**_bulk_result(result), "message": message}


def _bulk_result(result: BulkEditResult) -> dict:
    """stock05BulkEdit_ answer: the first 500 changes and 200 missing keys, with the full counts."""
    return {
        "applied": result.applied,
        "rows": result.rows,
        "count": len(result.changes),
        "changes": [
            {"warehouse": c.warehouse, "grn": c.grn, "package": c.package, "name": c.name,
             "column": c.column, "was": c.was, "now": c.now}
            for c in result.changes[:500]
        ],
        "missing": list(result.missing[:200]),
        "missing_count": len(result.missing),
        "errors": [
            {"warehouse": e.warehouse, "grn": e.grn, "package": e.package, "error": e.error} for e in result.errors
        ],
    }


@router.get("/export")
def export_stock(
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """portalStock05Export: the whole stock (both warehouses, zero balance too) for editing in Excel."""
    return {"rows": export_rows(uow_factory())}


@router.post("/import")
def import_stock_table(
    payload: ReferenceImportPayload,
    principal: Annotated[Principal, Depends(warehouse_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    """portalStock05Bulk — the administrator only. apply=false is the preview."""
    edits = [
        ReferenceEdit(
            grn=row.grn, package=row.package, warehouse=row.warehouse,
            fields={name: getattr(row, name) for name in BULK_EDIT_FIELDS if getattr(row, name) is not None},
        )
        for row in payload.edits
    ]
    result = _guarded(lambda: import_reference_fields(edits, payload.apply, principal.actor, now, uow_factory()))
    return _bulk_result(result)
