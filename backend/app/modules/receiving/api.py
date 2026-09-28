"""Receiving HTTP contract for receiver work queues and incoming control."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.document_url import normalize_document_url
from app.shared.http_auth import Principal, receiving_edit_principal
from app.shared.unit_mass import mass_fields_from

from .application import (
    ConcurrentReceivingUpdate,
    InspectReceivingCommand,
    PlaceReceivingCommand,
    UnitOfWork,
    inspect_receiving,
    place_receiving,
    receiving_queue,
)
from .domain import InspectionInput, ReceivingLine, ReceivingValidationError


router = APIRouter(prefix="/api/receiving", tags=["receiving"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
MassInput = Annotated[str, Field(max_length=64)] | float | None


class InspectionPayload(BaseModel):
    version: int = Field(ge=1)
    fact_quantity: Quantity
    document_quantity: Quantity
    shipping_document_url: str | None = Field(default=None, max_length=4096)
    quality_document_url: str | None = Field(default=None, max_length=4096)
    expiry_date: date | None = None
    manual_nonconformities: list[str] = Field(default_factory=list, max_length=2)
    disposition: str | None = Field(default=None, max_length=255)
    manual_grn_prefix: str | None = Field(default=None, max_length=8)
    series: str = Field(default="", max_length=255)
    # МАССА 1 ШТ: weight of 1, 10 or 100 pieces, grams; «0,5» as typed is fine (SUPPLYMAP icrNum_).
    mass_1: MassInput = None
    mass_10: MassInput = None
    mass_100: MassInput = None

    @field_validator("shipping_document_url", "quality_document_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        return normalize_document_url(value)


class PlacementPayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(min_length=3, max_length=16)


def receiving_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "receiving_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _decimal(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _result(line: ReceivingLine) -> dict:
    return {
        "id": str(line.id),
        "legacy_id": line.legacy_id,
        "status": line.status.value,
        "version": line.version,
        "warehouse": line.warehouse,
        "article": line.article,
        "series": line.series,
        "name": line.name,
        "item_type": line.item_type,
        "purpose": line.purpose,
        "supplier": line.supplier,
        "ordered_quantity": _decimal(line.ordered_quantity),
        "unit": line.unit,
        "quality_document_required": line.quality_document_required,
        "shipping_document_url": line.shipping_document_url,
        "quality_document_url": line.quality_document_url,
        "document_quantity": _decimal(line.document_quantity),
        "fact_quantity": _decimal(line.fact_quantity),
        "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
        "unit_mass_g": _decimal(line.unit_mass_g),
        "mass_fields": mass_fields_from(line.unit_mass_g),
        "disposition": line.disposition,
        "verdict": line.verdict,
        "nonconformities": list(line.nonconformities),
        "accepted_quantity": _decimal(line.accepted_quantity),
        "backorder_quantity": _decimal(line.backorder_quantity),
        "grn": line.grn,
        "package": line.package,
        "storage_location": line.storage_location,
        "inspector": line.inspector.name if line.inspector else None,
        "warehouse_event_emitted": line.warehouse_event_emitted,
    }


def _run(operation):
    try:
        return operation()
    except ReceivingValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentReceivingUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


@router.get("")
def queue(
    _principal: Annotated[Principal, Depends(receiving_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    lines = _run(
        lambda: receiving_queue(uow_factory(), limit=limit + 1, after_id=after)
    )
    page = lines[:limit]
    return {
        "lines": [_result(line) for line in page],
        "next_cursor": str(page[-1].id) if len(lines) > limit else None,
    }


@router.post("/{line_id}/inspect")
def inspect(
    line_id: UUID,
    payload: InspectionPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    inspection = InspectionInput(
        fact_quantity=payload.fact_quantity,
        document_quantity=payload.document_quantity,
        ordered_quantity=Decimal("1"),  # replaced by the server-owned value in application layer
        has_shipping_document=False,  # derived from the submitted URL in application layer
        quality_document_required=False,
        quality_document_present=False,  # derived from the submitted URL in application layer
        expiry_date=payload.expiry_date,
        inspection_date=now.date(),
        manual_nonconformities=tuple(payload.manual_nonconformities),
        disposition=payload.disposition,
        shipping_document_url=payload.shipping_document_url,
        quality_document_url=payload.quality_document_url,
        series=payload.series,
        mass_1=payload.mass_1,
        mass_10=payload.mass_10,
        mass_100=payload.mass_100,
    )
    line = _run(
        lambda: inspect_receiving(
            InspectReceivingCommand(
                line_id=line_id,
                expected_version=payload.version,
                inspection=inspection,
                actor=principal.actor,
                now=now,
                manual_grn_prefix=payload.manual_grn_prefix,
            ),
            uow_factory(),
        )
    )
    return {"line": _result(line)}


@router.post("/{line_id}/place")
def place(
    line_id: UUID,
    payload: PlacementPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    line = _run(
        lambda: place_receiving(
            PlaceReceivingCommand(
                line_id=line_id,
                expected_version=payload.version,
                storage_location=payload.storage_location,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
    return {"line": _result(line)}
