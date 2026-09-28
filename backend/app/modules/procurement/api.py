"""Procurement HTTP contract for buyer work queues and editable facts."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, BeforeValidator, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.document_url import normalize_document_url
from app.shared.http_auth import Principal, procurement_edit_principal

from .application import (
    CancelBackorderCommand,
    ConfirmBackorderCommand,
    ExchangeRates,
    ManualRateRequired,
    RateUnavailable,
    ReconcileProcurementRateCommand,
    SaveProcurementMoneyCommand,
    SaveProcurementFactsCommand,
    BuildSupplierRequestsCommand,
    SupplierOrder,
    RecordInvoiceCommand,
    ReplaceInvoiceCommand,
    MarkProcurementPaidCommand,
    OfflineAction,
    ProcurementOfflineCommand,
    TakeProcurementInWorkCommand,
    UnitOfWork,
    ConcurrentProcurementUpdate,
    available_procurement,
    buyer_procurement,
    cancel_procurement_backorder,
    confirm_procurement_backorder,
    save_procurement_facts,
    build_supplier_requests,
    supplier_order,
    record_invoice,
    replace_invoice,
    mark_procurement_paid,
    reconcile_procurement_rate,
    set_procurement_offline_status,
    save_procurement_money,
    take_procurement_in_work,
)
from .domain import MoneyEntry, ProcurementFacts, ProcurementLine, ProcurementValidationError
from .exchange_rates import CbrExchangeRates
from .money import MoneyInput, parse_number_loose


router = APIRouter(prefix="/api/procurement", tags=["procurement"])
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]


def _loose_number(value: object) -> object:
    # Money is typed the way it is written in invoices: «1 234,56», «1,234.56», «$1,234.56».
    if value is None or isinstance(value, (int, Decimal)):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str) and not value.strip():
        return None
    parsed = parse_number_loose(value)
    if parsed is None:
        raise ValueError("Не удалось прочитать число")
    return parsed


UnitPrice = Annotated[
    Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)] | None,
    BeforeValidator(_loose_number),
]
Amount = Annotated[
    Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=2)] | None,
    BeforeValidator(_loose_number),
]
VatRate = Annotated[
    Annotated[Decimal, Field(ge=0, max_digits=5, decimal_places=2)] | None,
    BeforeValidator(_loose_number),
]
Rate = Annotated[
    Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=10)] | None,
    BeforeValidator(_loose_number),
]


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


class InvoiceReplaceInput(BaseModel):
    version: int = Field(ge=1)
    invoice_url: str = Field(min_length=1, max_length=4096)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str) -> str:
        normalized = normalize_document_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class PaidInput(VersionInput):
    manual_exchange_rate: Rate = None


class BackorderConfirmInput(VersionInput):
    expected_arrival: date


class MoneyPayload(BaseModel):
    version: int = Field(ge=1)
    driver: Literal["net", "gross", "vat", "qty", "sum"] | None = None
    currency: str = Field(min_length=1, max_length=8)
    price: UnitPrice = None
    vat_rate: VatRate = None
    price_with_vat: UnitPrice = None
    sum: Amount = None
    invoice_item_name: str | None = Field(default=None, max_length=1024)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    manual_exchange_rate: Rate = None

    def to_domain(self) -> MoneyEntry:
        return MoneyEntry(
            amounts=MoneyInput(
                currency=self.currency,
                price=self.price,
                vat_rate=self.vat_rate,
                price_with_vat=self.price_with_vat,
                sum=self.sum,
                driver=self.driver,
            ),
            invoice_item_name=self.invoice_item_name,
            invoice_number=self.invoice_number,
            invoice_date=self.invoice_date,
        )


def procurement_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def procurement_exchange_rates(request: Request) -> ExchangeRates:
    rates = getattr(request.app.state, "procurement_exchange_rates", None)
    if rates is None:
        rates = CbrExchangeRates()
        request.app.state.procurement_exchange_rates = rates
    return rates


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
        "order_number": line.order_number,
        "order_date": line.order_date.isoformat() if line.order_date else None,
        "expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else None,
        "invoice_url": line.invoice_url,
        "paid_at": line.paid_at.isoformat() if line.paid_at else None,
        "currency": line.money.currency,
        "actual_price": _decimal_result(line.money.price),
        "actual_vat_rate": _decimal_result(line.money.vat_rate),
        "actual_price_with_vat": _decimal_result(line.money.price_with_vat),
        "actual_sum": _decimal_result(line.money.sum),
        "source_currency": line.money.source_currency,
        "source_price": _decimal_result(line.money.source_price),
        "source_price_with_vat": _decimal_result(line.money.source_price_with_vat),
        "source_sum": _decimal_result(line.money.source_sum),
        "exchange_rate": _decimal_result(line.money.exchange_rate),
        "exchange_rate_date": (
            line.money.exchange_rate_date.isoformat() if line.money.exchange_rate_date else None
        ),
        "exchange_rate_manual": line.money.exchange_rate_manual,
        "invoice_item_name": line.invoice_item_name,
        "invoice_number": line.invoice_number,
        "invoice_date": line.invoice_date.isoformat() if line.invoice_date else None,
        "pay_missing": line.pay_missing(),
        "is_backorder": line.is_backorder,
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
    except ManualRateRequired as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manual_rate_required",
                "currency": error.currency,
                "date": error.day.isoformat(),
                "message": str(error),
            },
        ) from error
    except RateUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"line": _line_result(line)}


@router.patch("/{line_id}/money")
def money(
    line_id: UUID,
    payload: MoneyPayload,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    return _transition(
        lambda: save_procurement_money(
            SaveProcurementMoneyCommand(
                line_id=line_id,
                expected_version=payload.version,
                entry=payload.to_domain(),
                actor=principal.actor,
                now=now,
                manual_rate=payload.manual_exchange_rate,
            ),
            uow_factory(),
            rates,
        )
    )


@router.post("/{line_id}/exchange-rate/reconcile")
def reconcile_rate(
    line_id: UUID,
    payload: VersionInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    return _transition(
        lambda: reconcile_procurement_rate(
            ReconcileProcurementRateCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
            rates,
        )
    )


def _order_result(order: SupplierOrder) -> dict:
    return {
        "order_number": order.order_number,
        "order_date": order.order_date.isoformat(),
        "supplier": order.supplier,
        "task_type": order.task_type,
        "delivery_point": order.delivery_point,
        "buyer": order.buyer.name,
        "letter": order.letter,
        "line_ids": [str(line_id) for line_id in order.line_ids],
    }


@router.post("/supplier-requests")
def supplier_requests(
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    try:
        result = _retry_transaction(
            lambda: build_supplier_requests(
                BuildSupplierRequestsCommand(actor=principal.actor, now=now),
                uow_factory(),
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "orders": [_order_result(order) for order in result.orders],
        "lines": [_line_result(line) for line in result.lines],
        "skipped": result.skipped,
        "other_buyer": result.other_buyer,
    }


@router.get("/supplier-requests/{order_number}")
def supplier_request_order(
    order_number: str,
    _principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    try:
        return {"order": _order_result(supplier_order(order_number, uow_factory()))}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


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


@router.post("/{line_id}/invoice/replace")
def invoice_replace(
    line_id: UUID,
    payload: InvoiceReplaceInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: replace_invoice(
            ReplaceInvoiceCommand(
                line_id=line_id,
                expected_version=payload.version,
                invoice_url=payload.invoice_url,
                actor=principal.actor,
                now=now,
                invoice_number=payload.invoice_number,
                invoice_date=payload.invoice_date,
            ),
            uow_factory(),
        )
    )


@router.post("/{line_id}/paid")
def paid(
    line_id: UUID,
    payload: PaidInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    return _transition(
        lambda: mark_procurement_paid(
            MarkProcurementPaidCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
                manual_rate=payload.manual_exchange_rate,
            ),
            uow_factory(),
            rates,
        )
    )


@router.post("/{line_id}/backorder/confirm")
def confirm_backorder(
    line_id: UUID,
    payload: BackorderConfirmInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: confirm_procurement_backorder(
            ConfirmBackorderCommand(
                line_id=line_id,
                expected_version=payload.version,
                expected_arrival=payload.expected_arrival,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.post("/{line_id}/backorder/cancel")
def cancel_backorder(
    line_id: UUID,
    payload: VersionInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: cancel_procurement_backorder(
            CancelBackorderCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


def _offline(action: OfflineAction):
    def endpoint(
        line_id: UUID,
        payload: VersionInput,
        principal: Annotated[Principal, Depends(procurement_edit_principal)],
        now: Annotated[datetime, Depends(moscow_now)],
        uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    ) -> dict:
        return _transition(
            lambda: set_procurement_offline_status(
                ProcurementOfflineCommand(
                    line_id=line_id,
                    expected_version=payload.version,
                    action=action,
                    actor=principal.actor,
                    now=now,
                ),
                uow_factory(),
            )
        )

    return endpoint


# «Согласование» / «Отменено» (`PUR_STATUS_OFFLINE`): outside the linear order, any stage before payment.
router.post("/{line_id}/approval")(_offline(OfflineAction.APPROVAL))
router.post("/{line_id}/approval/resume")(_offline(OfflineAction.RESUME))
router.post("/{line_id}/cancel")(_offline(OfflineAction.CANCEL))
