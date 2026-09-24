"""HTTP contract for finished goods and shipment execution."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, shipping_manager_principal, shipping_view_principal, shipping_warehouse_principal

from .application import (
    AcceptGoodCommand, ConcurrentShippingUpdate, CreateShipmentCommand, RegisterGoodCommand,
    RequestedGood, ShipmentCommand, UnitOfWork, UnpickCommand, UpdateDocumentsCommand,
    accept_good, cancel_shipment, close_shipment, create_shipment, mark_shipped,
    pack_shipment, pick_shipment, register_good, unpick_line, update_documents,
)
from .domain import DeliveryTerms, FinishedGood, Shipment, ShippingValidationError, UpdType


router = APIRouter(prefix="/api/shipping", tags=["shipping"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


class GoodPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=255)
    customer: str = Field(default="", max_length=512)
    article: str = Field(default="", max_length=255)
    name: str = Field(min_length=1, max_length=512)
    ordered: Quantity
    unit: str = Field(default="шт", min_length=1, max_length=64)
    note: str = Field(default="", max_length=5000)


class AcceptPayload(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity
    received_at: date | None = None


class ShipmentLinePayload(BaseModel):
    good_id: UUID
    quantity: Quantity


class ShipmentPayload(BaseModel):
    customer: str = Field(default="", max_length=512)
    receiver: str = Field(min_length=1, max_length=512)
    shipping_point: str = Field(min_length=1, max_length=5000)
    contact: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=128)
    terms: DeliveryTerms
    carrier: str = Field(default="", max_length=255)
    deadline: date | None = None
    upd_type: UpdType
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    upd_url: str = Field(default="", max_length=5000)
    note: str = Field(default="", max_length=5000)
    lines: list[ShipmentLinePayload] = Field(min_length=1, max_length=500)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class UnpickPayload(VersionPayload):
    line_id: UUID


class DocumentsPayload(VersionPayload):
    upd_type: UpdType
    upd_number: str = Field(default="", max_length=255)
    upd_date: date | None = None
    upd_url: str = Field(default="", max_length=5000)
    track_number: str = Field(default="", max_length=255)


def shipping_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "shipping_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _good_result(good: FinishedGood) -> dict:
    return {
        "id": str(good.id), "version": good.version, "project": good.project,
        "item_index": good.item_index, "customer": good.customer, "article": good.article,
        "name": good.name, "ordered": _number(good.ordered), "unit": good.unit,
        "accepted": _number(good.accepted), "shipped": _number(good.shipped),
        "remaining": _number(good.remaining),
        "received_at": good.received_at.isoformat() if good.received_at else None,
        "grn": good.grn, "note": good.note,
    }


def _shipment_result(shipment: Shipment) -> dict:
    return {
        "id": str(shipment.id), "number": shipment.number, "version": shipment.version,
        "created_at": shipment.created_at.isoformat(), "status": shipment.status.value,
        "customer": shipment.customer, "receiver": shipment.receiver,
        "shipping_point": shipment.shipping_point, "contact": shipment.contact,
        "phone": shipment.phone, "terms": shipment.terms.value, "carrier": shipment.carrier,
        "deadline": shipment.deadline.isoformat() if shipment.deadline else None,
        "upd_type": shipment.upd_type.value, "upd_number": shipment.upd_number,
        "upd_date": shipment.upd_date.isoformat() if shipment.upd_date else None,
        "upd_url": shipment.upd_url, "pl_number": shipment.pl_number,
        "dn_number": shipment.dn_number, "track_number": shipment.track_number,
        "packed_at": shipment.packed_at.isoformat() if shipment.packed_at else None,
        "shipped_at": shipment.shipped_at.isoformat() if shipment.shipped_at else None,
        "manager": shipment.manager, "note": shipment.note,
        "lines": [{
            "id": str(line.id), "good_id": str(line.finished_good_id),
            "project": line.project, "item_index": line.item_index, "article": line.article,
            "name": line.name, "requested": _number(line.requested),
            "picked": _number(line.picked), "shortage": _number(line.shortage),
            "unit": line.unit, "grn": line.grn,
        } for line in shipment.lines],
    }


def _run(operation):
    try:
        return operation()
    except ShippingValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentShippingUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


@router.get("/goods")
def goods(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict:
    with uow_factory() as uow: rows = uow.goods.list(limit)
    return {"goods": [_good_result(good) for good in rows]}


@router.post("/goods")
def add_good(
    payload: GoodPayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    good = _run(lambda: register_good(RegisterGoodCommand(**payload.model_dump(), actor=principal.actor, now=now), uow_factory()))
    return {"good": _good_result(good)}


@router.post("/goods/{good_id}/accept")
def receive_good(
    good_id: UUID, payload: AcceptPayload,
    principal: Annotated[Principal, Depends(shipping_warehouse_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    good = _run(lambda: accept_good(AcceptGoodCommand(
        good_id=good_id, expected_version=payload.version, quantity=payload.quantity,
        received_at=payload.received_at or now.date(), actor=principal.actor, now=now,
    ), uow_factory()))
    return {"good": _good_result(good)}


@router.get("/shipments")
def shipments(
    _principal: Annotated[Principal, Depends(shipping_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    with uow_factory() as uow: rows = uow.shipments.list(limit)
    return {"shipments": [_shipment_result(shipment) for shipment in rows]}


@router.post("/shipments")
def add_shipment(
    payload: ShipmentPayload,
    principal: Annotated[Principal, Depends(shipping_manager_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)],
) -> dict:
    data = payload.model_dump(exclude={"lines"})
    shipment = _run(lambda: create_shipment(CreateShipmentCommand(
        **data, lines=tuple(RequestedGood(good_id=line.good_id, quantity=line.quantity) for line in payload.lines),
        actor=principal.actor, now=now,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


def _command(shipment_id: UUID, payload: VersionPayload, principal: Principal, now: datetime) -> ShipmentCommand:
    return ShipmentCommand(shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now)


@router.post("/shipments/{shipment_id}/pick")
def pick(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: pick_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/unpick")
def unpick(shipment_id: UUID, payload: UnpickPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: unpick_line(UnpickCommand(shipment_id=shipment_id, expected_version=payload.version, line_id=payload.line_id, actor=principal.actor, now=now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/pack")
def pack(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: pack_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.put("/shipments/{shipment_id}/documents")
def documents(shipment_id: UUID, payload: DocumentsPayload, principal: Annotated[Principal, Depends(shipping_manager_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: update_documents(UpdateDocumentsCommand(
        shipment_id=shipment_id, expected_version=payload.version, actor=principal.actor, now=now,
        upd_type=payload.upd_type, upd_number=payload.upd_number, upd_date=payload.upd_date,
        upd_url=payload.upd_url, track_number=payload.track_number,
    ), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/ship")
def ship(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: mark_shipped(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/close")
def close(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_warehouse_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: close_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}


@router.post("/shipments/{shipment_id}/cancel")
def cancel(shipment_id: UUID, payload: VersionPayload, principal: Annotated[Principal, Depends(shipping_manager_principal)], now: Annotated[datetime, Depends(moscow_now)], uow_factory: Annotated[Callable[[], UnitOfWork], Depends(shipping_uow_factory)]) -> dict:
    shipment = _run(lambda: cancel_shipment(_command(shipment_id, payload, principal, now), uow_factory()))
    return {"shipment": _shipment_result(shipment)}
