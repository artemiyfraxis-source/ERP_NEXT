"""Procurement HTTP contract for buyer work queues and editable facts."""

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
from app.shared.http_auth import Principal, procurement_edit_principal

from .application import (
    SaveProcurementFactsCommand,
    SendSupplierRequestCommand,
    RecordInvoiceCommand,
    MarkProcurementPaidCommand,
    TakeProcurementInWorkCommand,
    UnitOfWork,
    ConcurrentProcurementUpdate,
    available_procurement,
    buyer_procurement,
    save_procurement_facts,
    send_supplier_request,
    record_invoice,
    mark_procurement_paid,
    take_procurement_in_work,
)
from .domain import ProcurementFacts, ProcurementLine, ProcurementValidationError


router = APIRouter(prefix="/api/procurement", tags=["procurement"])
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]


class TakeInWorkInput(BaseModel):
    line_ids: list[UUID] = Field(min_length=1, max_length=100)


class ProcurementFactsInput(BaseModel):
    version: int = Field(ge=1)
    supplier: str | None = Field(default=None, max_length=512)
    site_name: str | None = Field(default=None, max_length=512)
    link: str | None = Field(default=None, max_length=4096)
    design_article: str | None = Field(default=None, max_length=512)
    calculated_name: str | None = Field(default=None, max_length=1024)
    actual_quantity: Quantity | None = None
    actual_unit: str | None = Field(default=None, max_length=32)

    def to_domain(self) -> ProcurementFacts:
        values = {
            name: getattr(self, name)
            for name in self.model_fields_set
            if name != "version"
        }
        return ProcurementFacts(**values)


class SupplierRequestInput(BaseModel):
    version: int = Field(ge=1)
    reference: str = Field(min_length=1, max_length=4096)


class InvoiceInput(BaseModel):
    version: int = Field(ge=1)
    invoice_url: str = Field(min_length=1, max_length=4096)
    expected_arrival: date

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str) -> str:
        normalized = normalize_document_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class VersionInput(BaseModel):
    version: int = Field(ge=1)


def procurement_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _decimal_result(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _line_result(line: ProcurementLine) -> dict:
    return {
        "id": str(line.id),
        "legacy_id": line.legacy_id,
        "revision": line.revision,
        "status": line.status.value,
        "version": line.version,
        "request_date": line.request_date.isoformat(),
        "initiator": line.initiator,
        "department": line.department,
        "task_type": line.task_type,
        "designation": line.designation,
        "project_reference": line.project_reference,
        "priority": line.priority,
        "delivery_point": line.delivery_point,
        "planned_date": line.planned_date.isoformat(),
        "link": line.link,
        "item_type": line.item_type,
        "design_article": line.design_article,
        "category": line.category,
        "calculated_name": line.calculated_name,
        "calculated_quantity": _decimal_result(line.calculated_quantity),
        "calculated_unit": line.calculated_unit,
        "buyer": line.buyer.name if line.buyer else None,
        "supplier": line.supplier,
        "site_name": line.site_name,
        "actual_quantity": _decimal_result(line.actual_quantity),
        "actual_unit": line.actual_unit,
        "quality_document": line.quality_document,
        "supplier_request_reference": line.supplier_request_reference,
        "expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else None,
        "invoice_url": line.invoice_url,
        "paid_at": line.paid_at.isoformat() if line.paid_at else None,
    }


def _retry_transaction(operation):
    for attempt in range(2):
        try:
            return operation()
        except (IntegrityError, StaleDataError) as error:
            if attempt == 1:
                raise HTTPException(
                    status_code=409,
                    detail="Конкурентное изменение: обновите данные и повторите команду",
                ) from error
    raise AssertionError("unreachable")


def _page_result(lines: list[ProcurementLine], limit: int) -> dict:
    page = lines[:limit]
    return {
        "lines": [_line_result(line) for line in page],
        "next_cursor": str(page[-1].id) if len(lines) > limit else None,
    }


@router.get("/available")
def available_lines(
    _principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    try:
        lines = available_procurement(
            uow_factory(), limit=limit + 1, after_id=after
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _page_result(lines, limit)


@router.get("/mine")
def my_lines(
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    try:
        lines = buyer_procurement(
            principal.actor, uow_factory(), limit=limit + 1, after_id=after
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _page_result(lines, limit)


@router.post("/take")
def take_lines(
    payload: TakeInWorkInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
) -> dict:
    try:
        lines = _retry_transaction(
            lambda: take_procurement_in_work(
                TakeProcurementInWorkCommand(
                    line_ids=tuple(payload.line_ids),
                    actor=principal.actor,
                    now=now,
                ),
                uow_factory(),
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"lines": [_line_result(line) for line in lines]}


@router.patch("/{line_id}/facts")
def update_facts(
    line_id: UUID,
    payload: ProcurementFactsInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
) -> dict:
    try:
        line = _retry_transaction(
            lambda: save_procurement_facts(
                SaveProcurementFactsCommand(
                    line_id=line_id,
                    facts=payload.to_domain(),
                    expected_version=payload.version,
                    actor=principal.actor,
                    now=now,
                ),
                uow_factory(),
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentProcurementUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"line": _line_result(line)}


def _transition(operation) -> dict:
    try:
        line = _retry_transaction(operation)
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentProcurementUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"line": _line_result(line)}


@router.post("/{line_id}/supplier-request")
def supplier_request(
    line_id: UUID,
    payload: SupplierRequestInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: send_supplier_request(
            SendSupplierRequestCommand(
                line_id=line_id,
                expected_version=payload.version,
                reference=payload.reference,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.post("/{line_id}/invoice")
def invoice(
    line_id: UUID,
    payload: InvoiceInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: record_invoice(
            RecordInvoiceCommand(
                line_id=line_id,
                expected_version=payload.version,
                invoice_url=payload.invoice_url,
                expected_arrival=payload.expected_arrival,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.post("/{line_id}/paid")
def paid(
    line_id: UUID,
    payload: VersionInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: mark_procurement_paid(
            MarkProcurementPaidCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
