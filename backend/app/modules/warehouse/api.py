"""Warehouse HTTP contract for stock, movement, and writeoff."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, warehouse_edit_principal
from app.shared.warehouse_location import STORAGE_LOCATION_MAX_LENGTH
from app.modules.references.domain import TechOperationTab
from app.workflows.warehouse_article import infer_article_identity

from .application import (
    AddManualStockCommand,
    ConcurrentWarehouseUpdate,
    CorrectLotLocationCommand,
    EditLotIdentityCommand,
    ManualStockItem,
    MoveLocationCommand,
    MoveLotCommand,
    UnitOfWork,
    WriteOffCommand,
    add_manual_stock,
    correct_lot_location,
    edit_lot_identity,
    move_location,
    move_lot,
    warehouse_lots,
    write_off,
)
from .domain import StockLot, WarehouseValidationError


router = APIRouter(prefix="/api/stock", tags=["warehouse"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class WriteOffPayload(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity
    reason: str = Field(min_length=1, max_length=1024)


class MovePayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(min_length=3, max_length=16)


class CorrectLocationPayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(default="", max_length=STORAGE_LOCATION_MAX_LENGTH)


class EditIdentityPayload(BaseModel):
    version: int = Field(ge=1)
    article: str = Field(min_length=1, max_length=1024)
    name: str = Field(default="", max_length=1024)
    series: str = Field(default="", max_length=255)


class MoveLocationPayload(BaseModel):
    warehouse: str = Field(pattern="^(ЦАР|ЖУК)$")
    source_location: str = Field(min_length=3, max_length=16)
    target_location: str = Field(min_length=3, max_length=16)


class ManualStockInput(BaseModel):
    warehouse: str = Field(pattern="^(ЦАР|ЖУК)$")
    article: str = Field(min_length=1, max_length=1024, pattern=r"\S")
    series: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=1024)
    item_type: str = Field(default="", max_length=255)
    purpose: str = Field(default="Склад", min_length=1, max_length=128)
    unit: str = Field(default="шт", min_length=1, max_length=32)
    quantity: Quantity
    storage_location: str = Field(min_length=3, max_length=16)


class ManualStockPayload(BaseModel):
    items: list[ManualStockInput] = Field(min_length=1, max_length=100)


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
    }


def _run(operation) -> dict:
    try:
        lot = operation()
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentWarehouseUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    return {"lot": _result(lot)}


@router.get("")
def lots(
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
    q: Annotated[str, Query(max_length=255)] = "",
    zero: bool = False,
    warehouse: Annotated[str | None, Query(pattern="^(ЦАР|ЖУК)$")] = None,
) -> dict:
    try:
        lots, total = warehouse_lots(
            uow_factory(),
            limit=limit + 1,
            after_id=after,
            query=q,
            warehouse=warehouse,
            include_zero=zero,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    page = lots[:limit]
    return {
        "lots": [_result(lot) for lot in page],
        "total": total,
        "truncated": max(0, total - len(page)),
        "next_cursor": str(page[-1].id) if len(lots) > limit else None,
    }


@router.get("/article-info")
def article_info(
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    article: Annotated[str, Query(alias="q", min_length=1, max_length=1024)],
) -> dict:
    records = []
    factory = getattr(request.app.state, "references_uow_factory", None)
    if callable(factory):
        with factory() as uow:
            for tab in (TechOperationTab.TERMINAL, TechOperationTab.COAX):
                records.extend(uow.tech_operations.list(tab=tab, query="", limit=10_000))
    info = infer_article_identity(article, records)
    return {"info": None if info is None else {
        "series": info.series,
        "name": info.name,
        "item_type": info.item_type,
        "source": info.source,
    }}


@router.post("/batches")
def add_batches(
    payload: ManualStockPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)],
) -> dict:
    try:
        lots = add_manual_stock(
            AddManualStockCommand(
                items=tuple(
                    ManualStockItem(
                        warehouse=item.warehouse,
                        article=item.article.strip(),
                        series=item.series.strip(),
                        name=item.name.strip(),
                        item_type=item.item_type.strip(),
                        purpose=item.purpose.strip(),
                        unit=item.unit.strip(),
                        quantity=item.quantity,
                        storage_location=item.storage_location,
                    )
                    for item in payload.items
                ),
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: повторите ввод") from error
    return {"lots": [_result(lot) for lot in lots]}


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
            ),
            uow_factory(),
        )
    )


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
