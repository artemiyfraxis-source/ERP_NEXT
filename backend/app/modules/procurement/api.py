"""Procurement HTTP contract for buyer work queues and editable facts."""

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, BeforeValidator, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.shared.stored_files import decode_upload, file_response

from .application import (
    CancelBackorderCommand,
    KpRow,
    SaveKpCommand,
    ConfirmBackorderCommand,
    ExchangeRates,
    ManualRateRequired,
    RateUnavailable,
    ReconcileProcurementRateCommand,
    SaveProcurementMoneyCommand,
    SaveProcurementFactsCommand,
    BuildSupplierRequestsCommand,
    SupplierOrder,
    SplitProcurementLineCommand,
    SetReplacementCommand,
    SetTraceabilityCommand,
    SetNoteCommand,
    SetExpectedArrivalCommand,
    SetActualArrivalCommand,
    set_procurement_actual_arrival,
    ExpectedArrivalCopier,
    RecordInvoiceCommand,
    UploadInvoiceFileCommand,
    invoice_file,
    upload_invoice_file,
    ReplaceInvoiceCommand,
    MarkProcurementPaidCommand,
    OfflineAction,
    ProcurementOfflineCommand,
    TakeProcurementInWorkCommand,
    MarkPaidBatchCommand,
    MarkPaidBatchItem,
    SupplierDirectory,
    UnitOfWork,
    StockCoverUnitOfWork,
    ConcurrentProcurementUpdate,
    available_procurement,
    buyer_procurement,
    in_work_procurement,
    cancel_procurement_backorder,
    confirm_procurement_backorder,
    kp_order_lines,
    save_kp,
    save_procurement_facts,
    build_supplier_requests,
    build_replacement_permission_sheet,
    supplier_order,
    supplier_order_document,
    procurement_line,
    split_procurement_line,
    set_procurement_replacement,
    set_procurement_traceability,
    set_procurement_note,
    set_procurement_expected_arrival,
    record_invoice,
    replace_invoice,
    mark_procurement_paid,
    mark_procurement_paid_batch,
    procurement_by_supplier,
    procurement_lines,
    reconcile_procurement_rate,
    set_procurement_offline_status,
    save_procurement_money,
    take_procurement_in_work,
    take_procurement_in_work_batch,
    FactsBatchRow,
    buyer_in_work_lines,
    save_procurement_facts_batch,
    FactPriceRow,
    InvoiceSupplierOrderCommand,
    OrderLineRef,
    PaySupplierOrderCommand,
    invoice_supplier_order,
    pay_supplier_order,
    save_fact_prices_batch,
    BindStockCoverCommand,
    CoverFromStockCommand,
    ReleaseStockCoverCommand,
    bind_stock_cover,
    cover_from_stock,
    release_stock_cover,
    stock_candidates,
    without_grn_quantities,
    SetDatasheetCommand,
    StoredInvoiceFile,
    UploadLineDocumentCommand,
    invoice_file_url,
    line_documents,
    set_procurement_datasheet,
    upload_line_document,
)
from .domain import (
    ACTUAL_UNITS,
    BY_SUPPLIER_STAGES,
    DATASHEET_MAX_LENGTH,
    LINE_DOCUMENT_KINDS,
    NOTE_MAX_LENGTH,
    STATUS_ORDER,
    ProcurementStatus,
    SPLIT_MAX_COPIES,
    KpEntry,
    MoneyEntry,
    ProcurementFacts,
    ProcurementTraceability,
    ProcurementAccessDenied,
    ProcurementLine,
    ProcurementValidationError,
    ReplacementDecision,
    StockCover,
    same_unit,
    normalize_invoice_url,
)
from .stock_port import StockLotView
from app.workflows.receipt_stage import ReceivingStatusLookup
from .exchange_rates import CbrExchangeRates
from .invoice_file import invoice_date as invoice_day
from .kp import MAX_FILE_BYTES, KpFileError, extract_kp_items, match_kp_items, read_kp_grid
from .money import MoneyInput, parse_number_loose
from .application import (
    ApplyOrderDocumentCommand,
    DocumentPriceRow,
    RecognisedOrderDocument,
    RecogniseOrderDocumentCommand,
    apply_order_document,
    foreign_document_message,
    recognise_order_document,
)
from .document_items import unit_key


router = APIRouter(prefix="/api/procurement", tags=["procurement"])

# «Мои позиции»: принятые («Готово») строки показываются за столько дней.
ACCEPTED_DAYS = 30
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


class SupplierRequestsInput(BaseModel):
    """Ticked ready lines; no body — every ready line of the buyer, as SUPPLYMAP's single button did."""

    line_ids: list[UUID] = Field(min_length=1, max_length=500)


class PaidBatchItemInput(BaseModel):
    line_id: UUID
    version: int = Field(ge=1)


class PaidBatchInput(BaseModel):
    items: list[PaidBatchItemInput] = Field(min_length=1, max_length=100)


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


class FactsBatchRowInput(ProcurementFactsInput):
    line_id: UUID
    legacy_id: str = Field(default="", max_length=64)

    def to_domain(self) -> ProcurementFacts:
        values = {
            name: getattr(self, name)
            for name in self.model_fields_set
            if name not in {"version", "line_id", "legacy_id"}
        }
        return ProcurementFacts(**values)


class FactsBatchInput(BaseModel):
    rows: list[FactsBatchRowInput] = Field(min_length=1, max_length=200)


class TraceabilityInput(BaseModel):
    version: int = Field(ge=1)
    manufacturer: str = Field(default="", max_length=255)
    country: str = Field(default="", max_length=128)
    quality_document_type: str = Field(default="", max_length=128)
    quality_document_number: str = Field(default="", max_length=128)
    quality_document_date: date | None = None
    standard: str = Field(default="", max_length=255)

    def to_domain(self) -> ProcurementTraceability:
        return ProcurementTraceability(
            manufacturer=self.manufacturer,
            country=self.country,
            quality_document_type=self.quality_document_type,
            quality_document_number=self.quality_document_number,
            quality_document_date=self.quality_document_date,
            standard=self.standard,
        )


class ReplacementInput(BaseModel):
    version: int = Field(ge=1)
    replacement: bool
    decision: Literal["Разрешено", "Запрещено"] | None = None


class InvoiceInput(BaseModel):
    version: int = Field(ge=1)
    invoice_url: str = Field(min_length=1, max_length=4096)
    expected_arrival: date
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str) -> str:
        normalized = normalize_invoice_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class InvoiceFileInput(BaseModel):
    line_id: UUID
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class InvoiceReplaceInput(BaseModel):
    version: int = Field(ge=1)
    invoice_url: str = Field(min_length=1, max_length=4096)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str) -> str:
        normalized = normalize_invoice_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class PaidInput(VersionInput):
    manual_exchange_rate: Rate = None


class SplitInput(VersionInput):
    copies: int = Field(ge=1, le=SPLIT_MAX_COPIES)


class StockCoverInput(VersionInput):
    # None — «Есть на складе, без ГРН»: covered without a reserve on any lot.
    lot_id: UUID | None = None
    quantity: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
    # Only without a lot, optional.
    warehouse: Literal["ЦАР", "ЖУК", ""] = ""


class StockCoverBindInput(VersionInput):
    lot_id: UUID


class StockCoverReleaseInput(VersionInput):
    cover_id: UUID | None = None


class BackorderConfirmInput(VersionInput):
    expected_arrival: date


class NoteInput(VersionInput):
    # Coarse transport bound; the domain checks the trimmed note against NOTE_MAX_LENGTH with a Russian message.
    note: str = Field(default="", max_length=NOTE_MAX_LENGTH * 2)


class DatasheetInput(VersionInput):
    # Coarse transport bound; the domain checks the trimmed text against DATASHEET_MAX_LENGTH in Russian.
    datasheet: str = Field(default="", max_length=DATASHEET_MAX_LENGTH * 2)


class LineDocumentInput(BaseModel):
    """РАЗРЕШЕНИЕ (`permit`) or ДОК. КАЧЕСТВА (`quality`) file of the line."""

    kind: str = Field(min_length=1, max_length=16)
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class ExpectedArrivalInput(VersionInput):
    expected_arrival: date


class ActualArrivalInput(VersionInput):
    actual_arrival: date | None = None


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


class KpRowInput(BaseModel):
    line_id: UUID
    version: int = Field(ge=1)
    actual_quantity: Annotated[
        Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)] | None,
        BeforeValidator(_loose_number),
    ] = None
    actual_unit: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, max_length=8)
    driver: Literal["net", "gross", "vat", "qty", "sum"] | None = None
    price: UnitPrice = None
    vat_rate: VatRate = None
    price_with_vat: UnitPrice = None
    sum: Amount = None
    invoice_item_name: str | None = Field(default=None, max_length=1024)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    expected_arrival: date | None = None

    def to_domain(self) -> KpRow:
        return KpRow(
            line_id=self.line_id,
            expected_version=self.version,
            entry=KpEntry(
                money=MoneyEntry(
                    amounts=MoneyInput(
                        currency=(self.currency or "").strip() or None,
                        price=self.price,
                        vat_rate=self.vat_rate,
                        price_with_vat=self.price_with_vat,
                        sum=self.sum,
                        driver=self.driver,
                    ),
                    invoice_item_name=self.invoice_item_name,
                    invoice_number=self.invoice_number,
                    invoice_date=self.invoice_date,
                ),
                actual_quantity=self.actual_quantity,
                actual_unit=self.actual_unit,
                expected_arrival=self.expected_arrival,
            ),
        )


class KpInput(BaseModel):
    order_reference: str = Field(min_length=1, max_length=512)
    rows: list[KpRowInput] = Field(min_length=1, max_length=200)
    manual_exchange_rate: Rate = None
    invoice_url: str | None = Field(default=None, max_length=2048)

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = normalize_invoice_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class FactPriceRowInput(BaseModel):
    line_id: UUID
    version: int = Field(ge=1)
    legacy_id: str = Field(default="", max_length=64)
    # «Цена факт» = ЦЕНА С НДС (факт) per unit in roubles, typed as in invoices («1 234,56»); null clears it.
    price_with_vat: UnitPrice = None


class FactPricesInput(BaseModel):
    rows: list[FactPriceRowInput] = Field(min_length=1, max_length=200)


class OrderLineInput(BaseModel):
    line_id: UUID
    version: int = Field(ge=1)


class OrderInvoiceInput(BaseModel):
    invoice_url: str = Field(min_length=1, max_length=4096)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    expected_arrival: date
    lines: list[OrderLineInput] = Field(min_length=1, max_length=200)

    @field_validator("invoice_url")
    @classmethod
    def validate_invoice_url(cls, value: str) -> str:
        normalized = normalize_invoice_url(value)
        if normalized is None:
            raise ValueError("Счёт обязателен")
        return normalized


class OrderPaidInput(BaseModel):
    lines: list[OrderLineInput] = Field(min_length=1, max_length=200)
    manual_exchange_rate: Rate = None


class KpFileInput(BaseModel):
    order_reference: str = Field(min_length=1, max_length=512)
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=(MAX_FILE_BYTES * 4) // 3 + 8)


def procurement_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def procurement_stock_uow_factory(request: Request) -> Callable[[], StockCoverUnitOfWork]:
    factory = getattr(request.app.state, "procurement_stock_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def procurement_supplier_directory(request: Request) -> SupplierDirectory | None:
    return getattr(request.app.state, "procurement_supplier_directory", None)


def procurement_receiving(request: Request) -> ExpectedArrivalCopier | None:
    """Receiving's ОЖИД. ДАТА copy (`app.workflows.receipt_stage`); without Receiving wired the receipt tick copies."""
    factory = getattr(request.app.state, "receiving_uow_factory", None)
    return ReceivingStatusLookup(factory) if callable(factory) else None


def procurement_purposes(request: Request) -> Callable[[Sequence[ProcurementLine]], dict[UUID, str]]:
    """ЦЕЛЬ строк листа: `requests.purpose` по source_request_id, одним запросом на список (порт `app.workflows`)."""
    lookup = getattr(request.app.state, "request_purpose_lookup", None)
    if lookup is None:
        return lambda lines: {}
    return lambda lines: lookup.purposes_for([line.source_request_id for line in lines])


def procurement_exchange_rates(request: Request) -> ExchangeRates:
    rates = getattr(request.app.state, "procurement_exchange_rates", None)
    if rates is None:
        rates = CbrExchangeRates()
        request.app.state.procurement_exchange_rates = rates
    return rates


def _decimal_result(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _file_result(stored: StoredInvoiceFile) -> dict:
    return {
        "id": str(stored.id), "kind": stored.kind, "file_name": stored.file_name, "url": invoice_file_url(stored.id),
        "uploaded_by": stored.uploaded_by, "uploaded_at": stored.uploaded_at.isoformat(),
    }


def _line_result(
    line: ProcurementLine, purposes: Mapping[UUID, str] | None = None, *, without_grn: Decimal | None = None,
    documents: Sequence[StoredInvoiceFile] | None = None,
) -> dict:
    """The line for the screens. `without_grn` and `documents` — only where the caller looked them up (the lists:
    «Мои позиции», «Весь лист», «По поставщикам»); a command's answer leaves them out and the screen keeps its own."""
    extra: dict = {} if without_grn is None else {"stock_without_grn": _decimal_result(without_grn)}
    if documents is not None:
        # «Файлы разрешения» / «Док. качества» of the sheet: the line's РАЗРЕШЕНИЕ and ДОК. КАЧЕСТВА files.
        extra["permit_files"] = [_file_result(item) for item in documents if item.kind == "permit"]
        extra["quality_files"] = [_file_result(item) for item in documents if item.kind == "quality"]
    return {
        "purpose": (purposes or {}).get(line.source_request_id),
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
        # ЦЕНА / ЦЕНА С НДС / СУММА (Расчет): the calc prices the request came with.
        "calculated_price": _decimal_result(line.calculated_price),
        "calculated_price_with_vat": _decimal_result(line.calculated_price_with_vat),
        "calculated_sum": _decimal_result(line.calculated_sum),
        "buyer": line.buyer.name if line.buyer else None,
        "supplier": line.supplier,
        "site_name": line.site_name,
        "actual_quantity": _decimal_result(line.actual_quantity),
        "actual_unit": line.actual_unit,
        "quality_document": line.quality_document,
        "supplier_request_reference": line.supplier_request_reference,
        "order_number": line.order_number,
        "order_date": line.order_date.isoformat() if line.order_date else None,
        # «ЗАКАЗ» (SupplierRequest.gs): ссылка на документ запроса в строке.
        "order_url": order_document_url(line.order_number) if line.order_number else None,
        "expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else None,
        # ФАКТ. ДАТА: the latest inspection date of the line's deliveries (set by the receipt tick).
        "actual_arrival": line.actual_arrival.isoformat() if line.actual_arrival else None,
        # КОММЕНТАРИЙ: the buyer's free note.
        "note": line.note,
        # DATASHEET: a link or a text, edited like КОММЕНТАРИЙ.
        "datasheet": line.datasheet,
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
        "replacement": line.replacement,
        "replacement_decision": line.replacement_decision.value if line.replacement_decision else None,
        "replacement_open": line.replacement_open,
        "permission_number": line.permission_number,
        "permission_date": line.permission_date.isoformat() if line.permission_date else None,
        "manufacturer": line.trace.manufacturer,
        "country": line.trace.country,
        "quality_document_type": line.trace.quality_document_type,
        "quality_document_number": line.trace.quality_document_number,
        "quality_document_date": (
            line.trace.quality_document_date.isoformat() if line.trace.quality_document_date else None
        ),
        "standard": line.trace.standard,
        # «Со склада»: reserved on our lots for this line, and what is still to buy.
        "stock_covered": _decimal_result(line.stock_covered),
        "to_buy": _decimal_result(line.to_buy),
        # «Есть на складе, без ГРН» still waiting for «Привязать ГРН» (present only where looked up).
        **extra,
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


def _page_result(
    lines: list[ProcurementLine], limit: int, purposes_of=None,
    without_grn: Callable[[list[ProcurementLine]], dict] | None = None,
    documents: Callable[[list[ProcurementLine]], dict] | None = None,
) -> dict:
    page = lines[:limit]
    return {
        "lines": _lines_result(page, purposes_of, without_grn, documents),
        "next_cursor": str(page[-1].id) if len(lines) > limit else None,
    }


def _lines_result(
    page: list[ProcurementLine], purposes_of=None,
    without_grn: Callable[[list[ProcurementLine]], dict] | None = None,
    documents: Callable[[list[ProcurementLine]], dict] | None = None,
) -> list[dict]:
    """A list of lines with what the list looks up in one query each: ЦЕЛЬ, «без ГРН», the РАЗРЕШЕНИЕ / ДОК.
    КАЧЕСТВА files. A lookup left out leaves its keys out of the lines."""
    purposes = purposes_of(page) if purposes_of else None
    lotless = without_grn(page) if without_grn is not None else {}
    files = documents(page) if documents is not None else None
    return [
        _line_result(
            line, purposes, without_grn=lotless.get(line.id),
            documents=None if files is None else files.get(line.id, []),
        )
        for line in page
    ]


def _list_lookups(uow_factory: Callable[[], UnitOfWork]) -> dict:
    """«без ГРН» covers and the РАЗРЕШЕНИЕ / ДОК. КАЧЕСТВА files of a list — the same for every list view."""
    return {
        "without_grn": lambda page: without_grn_quantities(page, uow_factory()),
        "documents": lambda page: line_documents([line.id for line in page], uow_factory()),
    }


@router.get("/available")
def available_lines(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    purposes_of: Annotated[Callable[[Sequence[ProcurementLine]], dict[UUID, str]], Depends(procurement_purposes)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    try:
        lines = available_procurement(
            uow_factory(), limit=limit + 1, after_id=after
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _page_result(lines, limit, purposes_of)


@router.get("/mine")
def my_lines(
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    purposes_of: Annotated[Callable[[Sequence[ProcurementLine]], dict[UUID, str]], Depends(procurement_purposes)],
    now: Annotated[datetime, Depends(moscow_now)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    after: UUID | None = None,
) -> dict:
    """The buyer's open lines plus those accepted («Готово») in the last 30 days; «Отменено» never."""
    try:
        lines = buyer_procurement(
            principal.actor,
            uow_factory(),
            limit=limit + 1,
            after_id=after,
            accepted_since=now.date() - timedelta(days=ACCEPTED_DAYS),
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    # «Готово · без ГРН»: the screen offers «Привязать ГРН» on the lines that still have a cover without a lot;
    # the line card lists the РАЗРЕШЕНИЕ / ДОК. КАЧЕСТВА files.
    return _page_result(lines, limit, purposes_of, **_list_lookups(uow_factory))


@router.get("/in-work")
def in_work_lines(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    purposes_of: Annotated[Callable[[Sequence[ProcurementLine]], dict[UUID, str]], Depends(procurement_purposes)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    after: UUID | None = None,
) -> dict:
    """Read-only queue for «режим просмотра» (ID-6): lines taken by any buyer and not yet done."""
    try:
        lines = in_work_procurement(uow_factory(), limit=limit + 1, after_id=after)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _page_result(lines, limit, purposes_of)


@router.get("/meta")
def procurement_meta(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
) -> dict:
    """Lists for the sheet's selects: ЕД.ИЗМ (факт) and the statuses in pipeline order."""
    statuses = [status.value for status in STATUS_ORDER] + [
        ProcurementStatus.APPROVAL.value,
        ProcurementStatus.CANCELLED.value,
    ]
    return {"units": list(ACTUAL_UNITS), "statuses": statuses}


@router.get("/lines")
def all_lines(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    purposes_of: Annotated[Callable[[Sequence[ProcurementLine]], dict[UUID, str]], Depends(procurement_purposes)],
    q: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[str | None, Query(max_length=32)] = None,
    buyer: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    after: UUID | None = None,
) -> dict:
    """The whole procurement sheet: every status and buyer, search and filters, paged by cursor."""
    try:
        lines, buyers = procurement_lines(
            uow_factory(), query=q, status=status, buyer=buyer, limit=limit + 1, after_id=after
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {**_page_result(lines, limit, purposes_of, **_list_lookups(uow_factory)), "buyers": buyers}


@router.get("/by-supplier")
def lines_by_supplier(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """`portalPurchaseBySupplier`: every buyer's open lines for the per-supplier overview."""
    lines = procurement_by_supplier(uow_factory())
    return {
        "lines": _lines_result(lines, **_list_lookups(uow_factory)),
        "stages": [status.value for status in BY_SUPPLIER_STAGES],
    }


@router.post("/paid")
def paid_batch(
    payload: PaidBatchInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    """`portalMarkPaid`: «Оплачено → В пути» for several lines; each line independently."""
    try:
        result = _retry_transaction(
            lambda: mark_procurement_paid_batch(
                MarkPaidBatchCommand(
                    items=tuple(MarkPaidBatchItem(item.line_id, item.version) for item in payload.items),
                    actor=principal.actor,
                    now=now,
                ),
                uow_factory,
                rates,
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "lines": [_line_result(line) for line in result.lines],
        "done": len(result.lines),
        "already": result.already,
        "no_invoice": result.no_invoice,
        "failed": [{"legacy_id": legacy_id, "error": error} for legacy_id, error in result.failed],
    }


@router.get("/in-work/mine")
def my_in_work_lines(
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """`portalPurchaseInWork`: every own «В работе» line for the «Заполнить данные» form."""
    return {"lines": [_line_result(line) for line in buyer_in_work_lines(principal.actor, uow_factory())]}


@router.post("/facts")
def save_facts_batch(
    payload: FactsBatchInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    suppliers: Annotated[SupplierDirectory | None, Depends(procurement_supplier_directory)],
) -> dict:
    """`portalSavePurchaseFacts`: the marked rows of «Заполнить данные» in one call, row by row."""
    try:
        result = _retry_transaction(
            lambda: save_procurement_facts_batch(
                [
                    FactsBatchRow(row.line_id, row.version, row.to_domain(), row.legacy_id)
                    for row in payload.rows
                ],
                principal.actor,
                now,
                uow_factory,
                suppliers,
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "lines": [_line_result(line) for line in result.lines],
        "saved": len(result.lines),
        "ready": result.ready,
        "skipped": result.skipped,
        "failed": [{"legacy_id": legacy_id, "error": error} for legacy_id, error in result.failed],
    }


@router.post("/take")
def take_lines(
    payload: TakeInWorkInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    suppliers: Annotated[SupplierDirectory | None, Depends(procurement_supplier_directory)],
) -> dict:
    """`portalTakeInWork`: the lines become the buyer's; an empty ПОСТАВЩИК fills from the link's domain at once,
    the page-dependent fields (name on the site, manufacturer, country) fill in the background."""
    try:
        lines, skipped = _retry_transaction(
            lambda: take_procurement_in_work_batch(
                TakeProcurementInWorkCommand(
                    line_ids=tuple(payload.line_ids),
                    actor=principal.actor,
                    now=now,
                ),
                uow_factory(),
                suppliers,
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "lines": [_line_result(line) for line in lines],
        "skipped": [line.legacy_id for line in skipped],
    }


@router.patch("/{line_id}/facts")
def update_facts(
    line_id: UUID,
    payload: ProcurementFactsInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[
        Callable[[], UnitOfWork], Depends(procurement_uow_factory)
    ],
    suppliers: Annotated[SupplierDirectory | None, Depends(procurement_supplier_directory)],
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
                suppliers,
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
    return {"line": _line_result(_command_errors(operation))}


def _price_warnings(lines: Sequence[ProcurementLine]) -> list[str]:
    """«Счёт без цен — предупреждение, а не запрет» (owner, 06.10): the rows that went «В оплату» or got their invoice
    replaced without a price; «Оплачено» waits for it."""
    warnings = []
    for line in lines:
        missing = line.price_missing() if line.status is ProcurementStatus.TO_PAY else []
        if missing:
            warnings.append(f"{line.legacy_id} — нет цены: {', '.join(missing)}")
    return warnings


def _invoiced(operation) -> dict:
    """`_transition` for a single-line invoice: the row and its price warning."""
    line = _command_errors(operation)
    return {"line": _line_result(line), "warnings": _price_warnings([line])}


def _command_errors(operation):
    """Run a write command (retried once on a lost race) and map its errors to HTTP."""
    try:
        return _retry_transaction(operation)
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


def _kp_errors(operation):
    try:
        return operation()
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/kp")
def kp_order(
    reference: Annotated[str, Query(min_length=1, max_length=512)],
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    lines = _kp_errors(lambda: kp_order_lines(reference, principal.actor, uow_factory()))
    return {"order_reference": reference.strip(), "lines": [_line_result(line) for line in lines]}


@router.post("/kp/parse")
def kp_parse(
    payload: KpFileInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """`parseKpFile` + KpForm `applyImport`: offer rows and the order lines they match by name."""
    lines = _kp_errors(lambda: kp_order_lines(payload.order_reference, principal.actor, uow_factory()))
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Файл КП повреждён") from error
    try:
        items = extract_kp_items(read_kp_grid(payload.file_name, content))
    except KpFileError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    matched, not_found = match_kp_items(
        items, [(str(line.id), (line.calculated_name, line.site_name)) for line in lines],
    )
    return {
        "items": [item.__dict__ for item in items],
        "matches": [{"line_id": line_id, **item.__dict__} for line_id, item in matched],
        "not_found": not_found,
    }


@router.post("/kp")
def kp_save(
    payload: KpInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    command = SaveKpCommand(
        order_reference=payload.order_reference,
        rows=[row.to_domain() for row in payload.rows],
        actor=principal.actor,
        now=now,
        manual_rate=payload.manual_exchange_rate,
        invoice_url=payload.invoice_url,
    )
    outcome = {}

    def operation():
        outcome["result"] = save_kp(command, uow_factory(), rates)
        return outcome["result"].lines[0]

    _transition(operation)
    result = outcome["result"]
    return {
        "lines": [_line_result(line) for line in result.lines],
        "invoice_error": result.invoice_error,
        "warnings": _price_warnings(result.lines),
    }


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


def order_document_url(order_number: str) -> str:
    return f"/api/procurement/supplier-requests/{quote(order_number, safe='')}/document"


def _order_result(order: SupplierOrder) -> dict:
    document = order.document
    return {
        "order_number": order.order_number,
        "order_date": order.order_date.isoformat(),
        "supplier": order.supplier,
        "task_type": order.task_type,
        "delivery_point": order.delivery_point,
        "buyer": order.buyer.name,
        "letter": order.letter,
        "line_ids": [str(line_id) for line_id in order.line_ids],
        "document": {
            "url": order_document_url(order.order_number),
            "file_name": document.file_name,
            "folder": document.folder,
        } if document else None,
    }


@router.post("/supplier-requests")
def supplier_requests(
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    payload: SupplierRequestsInput | None = None,
) -> dict:
    """Ticked lines: what cannot go is listed in `skipped` ({line_id, legacy_id, reason}), the rest are formed."""
    line_ids = tuple(dict.fromkeys(payload.line_ids)) if payload else None
    try:
        result = _retry_transaction(
            lambda: build_supplier_requests(
                BuildSupplierRequestsCommand(actor=principal.actor, now=now, line_ids=line_ids),
                uow_factory(),
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "orders": [_order_result(order) for order in result.orders],
        "lines": [_line_result(line) for line in result.lines],
        "skipped": [
            {"line_id": str(item.line_id), "legacy_id": item.legacy_id, "reason": item.reason}
            for item in result.skipped
        ],
        "other_buyer": result.other_buyer,
    }


@router.post("/fact-prices")
def save_fact_prices(
    payload: FactPricesInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """«Цена факт» of order rows (gross unit price, RUB), row by row like `/facts`: stale rows are skipped."""
    try:
        result = _retry_transaction(
            lambda: save_fact_prices_batch(
                [FactPriceRow(row.line_id, row.version, row.price_with_vat, row.legacy_id) for row in payload.rows],
                principal.actor,
                now,
                uow_factory,
            )
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "lines": [_line_result(line) for line in result.lines],
        "saved": len(result.lines),
        "skipped": result.skipped,
        "failed": [{"legacy_id": legacy_id, "error": error} for legacy_id, error in result.failed],
    }


@router.post("/orders/{order_number}/invoice")
def invoice_order(
    order_number: str,
    payload: OrderInvoiceInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """One invoice for the order: all its «Жду счет» rows go «В оплату», or all its «В оплату» rows get it
    replaced; all or none (409 when the rows sent are not that set or a row is stale, 422 on the invoice gate). Rows
    without a price still go «В оплату»; `warnings` names them."""
    lines = _command_errors(
        lambda: invoice_supplier_order(
            InvoiceSupplierOrderCommand(
                order_number=order_number,
                invoice_url=payload.invoice_url,
                expected_arrival=payload.expected_arrival,
                lines=[OrderLineRef(row.line_id, row.version) for row in payload.lines],
                actor=principal.actor,
                now=now,
                invoice_number=payload.invoice_number,
                invoice_date=payload.invoice_date,
            ),
            uow_factory(),
        )
    )
    return {"lines": [_line_result(line) for line in lines], "warnings": _price_warnings(lines)}


@router.post("/orders/{order_number}/paid")
def pay_order(
    order_number: str,
    payload: OrderPaidInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    rates: Annotated[ExchangeRates, Depends(procurement_exchange_rates)],
) -> dict:
    """«Оплачено» once for the order: all its «В оплату» rows go «В пути», all or none."""
    lines = _command_errors(
        lambda: pay_supplier_order(
            PaySupplierOrderCommand(
                order_number=order_number,
                lines=[OrderLineRef(row.line_id, row.version) for row in payload.lines],
                actor=principal.actor,
                now=now,
                manual_rate=payload.manual_exchange_rate,
            ),
            uow_factory(),
            rates,
        )
    )
    return {"lines": [_line_result(line) for line in lines]}


@router.get("/supplier-requests/{order_number}")
def supplier_request_order(
    order_number: str,
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    try:
        return {"order": _order_result(supplier_order(order_number, uow_factory()))}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/supplier-requests/{order_number}/document")
def supplier_request_document(
    order_number: str,
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    download: bool = False,
) -> Response:
    """Документ запроса поставщику — PDF «Запрос PO-001.pdf»: открывается в просмотрщике браузера
    (inline), `?download=1` — вложением (в SUPPLYMAP — ссылка на Google Doc)."""
    try:
        meta, content = supplier_order_document(order_number, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Документ запроса не найден") from error
    return file_response(content, f"Запрос {order_number}.pdf", download=download)


@router.patch("/{line_id}/replacement")
def replacement(
    line_id: UUID,
    payload: ReplacementInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _transition(
        lambda: set_procurement_replacement(
            SetReplacementCommand(
                line_id=line_id,
                expected_version=payload.version,
                replacement=payload.replacement,
                decision=ReplacementDecision(payload.decision) if payload.decision else None,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.patch("/{line_id}/traceability")
def traceability(
    line_id: UUID,
    payload: TraceabilityInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    suppliers: Annotated[SupplierDirectory | None, Depends(procurement_supplier_directory)],
) -> dict:
    return _transition(
        lambda: set_procurement_traceability(
            SetTraceabilityCommand(
                line_id=line_id,
                expected_version=payload.version,
                trace=payload.to_domain(),
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
            suppliers,
        )
    )


@router.patch("/{line_id}/note")
def note(
    line_id: UUID,
    payload: NoteInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """КОММЕНТАРИЙ: the buyer's free note, from «В работе» on; not on «Новый»/«Отменено»."""
    return _transition(
        lambda: set_procurement_note(
            SetNoteCommand(
                line_id=line_id,
                expected_version=payload.version,
                note=payload.note,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.patch("/{line_id}/datasheet")
def datasheet(
    line_id: UUID,
    payload: DatasheetInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """DATASHEET: a link or a text, by the line's buyer from «В работе» on; not on «Новый»/«Отменено»."""
    return _transition(
        lambda: set_procurement_datasheet(
            SetDatasheetCommand(
                line_id=line_id, expected_version=payload.version, datasheet=payload.datasheet,
                actor=principal.actor, now=now,
            ),
            uow_factory(),
        )
    )


@router.get("/{line_id}/files")
def line_files(
    line_id: UUID,
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """The line's РАЗРЕШЕНИЕ and ДОК. КАЧЕСТВА files with their kind, oldest first (invoices and КП are not listed)."""
    found = line_documents([line_id], uow_factory()).get(line_id, [])
    return {"files": [_file_result(item) for item in found]}


@router.post("/{line_id}/files")
def upload_line_file(
    line_id: UUID,
    payload: LineDocumentInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """A РАЗРЕШЕНИЕ or ДОК. КАЧЕСТВА file to the line; a quality document also sets ДОК. КАЧЕСТВА «Да». Answers with
    the stored file and the line with all its files."""
    if payload.kind not in LINE_DOCUMENT_KINDS:
        raise HTTPException(status_code=422, detail="Вид файла: разрешение или документ качества")
    content = decode_upload(payload.content_base64, "Файл повреждён")
    line, stored = _command_errors(
        lambda: upload_line_document(
            UploadLineDocumentCommand(
                line_id=line_id, kind=payload.kind, file_name=payload.file_name, content_type=payload.content_type,
                content=content, actor=principal.actor, now=now,
            ),
            uow_factory(),
        )
    )
    files = line_documents([line.id], uow_factory()).get(line.id, [])
    return {"file": _file_result(stored), "line": _line_result(line, documents=files)}


@router.patch("/{line_id}/expected-arrival")
def expected_arrival(
    line_id: UUID,
    payload: ExpectedArrivalInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    receiving: Annotated[ExpectedArrivalCopier | None, Depends(procurement_receiving)],
) -> dict:
    """ОЖИД. ДАТА by hand, «Жду счет» … «Карантин»; open deliveries in Receiving (and their batches) follow it."""
    return _transition(
        lambda: set_procurement_expected_arrival(
            SetExpectedArrivalCommand(
                line_id=line_id,
                expected_version=payload.version,
                expected_arrival=payload.expected_arrival,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
            receiving,
        )
    )


@router.patch("/{line_id}/actual-arrival")
def actual_arrival(
    line_id: UUID,
    payload: ActualArrivalInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """ФАКТ. ДАТА by hand (optional), from «В пути» on; empty clears. Receiving still fills it on acceptance."""
    return _transition(
        lambda: set_procurement_actual_arrival(
            SetActualArrivalCommand(
                line_id=line_id,
                expected_version=payload.version,
                actual_arrival=payload.actual_arrival,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )


@router.get("/{line_id}/replacement-permission")
def replacement_permission(
    line_id: UUID,
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    try:
        line = procurement_line(line_id, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        sheet = build_replacement_permission_sheet(line)
    except ProcurementValidationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"sheet": sheet.to_dict()}


@router.post("/{line_id}/invoice")
def invoice(
    line_id: UUID,
    payload: InvoiceInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _invoiced(
        lambda: record_invoice(
            RecordInvoiceCommand(
                line_id=line_id,
                expected_version=payload.version,
                invoice_url=payload.invoice_url,
                expected_arrival=payload.expected_arrival,
                actor=principal.actor,
                now=now,
                invoice_number=payload.invoice_number,
                invoice_date=payload.invoice_date,
            ),
            uow_factory(),
        )
    )


@router.post("/invoice-files")
def upload_invoice(
    payload: InvoiceFileInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """`uploadInvoice`: the file goes to storage, the form gets its link and the recognised № and date."""
    content = decode_upload(payload.content_base64, "Файл счёта повреждён")
    try:
        uploaded = upload_invoice_file(
            UploadInvoiceFileCommand(
                line_id=payload.line_id, file_name=payload.file_name, content_type=payload.content_type,
                content=content, actor=principal.actor, now=now,
            ),
            uow_factory(),
        )
    except ProcurementAccessDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Строка закупки не найдена") from error
    parsed = invoice_day(uploaded.info.date)
    return {
        "url": uploaded.url,
        "file_name": uploaded.file.file_name,
        "invoice_number": uploaded.info.number,
        "invoice_date": parsed.isoformat() if parsed else "",
        "hint": uploaded.hint,
    }


@router.get("/files/{file_id}")
def download_invoice(
    file_id: UUID,
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> Response:
    found = invoice_file(file_id, uow_factory())
    if found is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    meta, content = found
    return file_response(content, meta.file_name)


# --- «Вставить КП» / «Вставить счёт» (owner, 05.10): prices read from the supplier's document --------------------


class DocumentRecognizeInput(BaseModel):
    kind: Literal["invoice", "kp"]
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class DocumentPriceRowInput(BaseModel):
    line_id: UUID
    version: int = Field(ge=1)
    # ЦЕНА С НДС per unit in roubles; null — the document does not price this row, the stored price stays.
    price_with_vat: UnitPrice = None
    price: UnitPrice = None
    vat_rate: VatRate = None
    invoice_item_name: str | None = Field(default=None, max_length=1024)


class ApplyDocumentInput(BaseModel):
    kind: Literal["invoice", "kp"]
    file_url: str = Field(min_length=1, max_length=4096)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    expected_arrival: date | None = None
    lines: list[DocumentPriceRowInput] = Field(min_length=1, max_length=200)


def _plain_decimal(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _recognised_result(found: RecognisedOrderDocument) -> dict:
    document = found.document
    by_key = {str(line.id): line for line in found.lines}
    lines = []
    for match in found.matches:
        line = by_key[match.key]
        item = document.items[match.item_index]
        warnings = []
        if item.quantity is not None and line.actual_quantity is not None and item.quantity != line.actual_quantity:
            warnings.append(f"Кол-во в документе {_plain_decimal(item.quantity)}, в строке {_plain_decimal(line.actual_quantity)}")
        if item.unit and line.actual_unit and unit_key(item.unit) != unit_key(line.actual_unit):
            warnings.append(f"Ед. в документе «{item.unit}», в строке «{line.actual_unit}»")
        lines.append({
            "line_id": str(line.id),
            "legacy_id": line.legacy_id,
            "version": line.version,
            "status": line.status.value,
            "price_with_vat": _plain_decimal(item.price_with_vat),
            "price": _plain_decimal(item.price),
            "vat_rate": _plain_decimal(item.vat_rate),
            "quantity": _plain_decimal(item.quantity),
            "unit": item.unit,
            "sum": _plain_decimal(item.sum),
            "matched_name": item.name,
            "article": item.article,
            "confidence": match.confidence,
            "source": item.source,
            "warnings": warnings,
        })
    parsed_date = invoice_day(document.date)
    auto = document.currency == "RUB"
    hint = document.hint or ("" if auto else foreign_document_message(document.currency))
    return {
        "file": {"url": found.url, "name": found.file.file_name},
        "kind": found.kind,
        "invoice_number": document.number,
        "invoice_date": parsed_date.isoformat() if parsed_date else "",
        "currency": document.currency,
        "auto": auto,
        "vat_mode": document.vat_mode,
        "vat_rate": _plain_decimal(document.vat_rate),
        "hint": hint,
        "lines": lines,
        "unmatched_items": [
            {
                "name": document.items[index].name,
                "article": document.items[index].article,
                "quantity": _plain_decimal(document.items[index].quantity),
                "unit": document.items[index].unit,
                "price_with_vat": _plain_decimal(document.items[index].price_with_vat),
            }
            for index in found.unmatched_items
        ],
        "unmatched_lines": [line.legacy_id for line in found.unmatched_lines],
        "totals": {
            "sum": _plain_decimal(document.totals.sum),
            "vat": _plain_decimal(document.totals.vat),
            "sum_with_vat": _plain_decimal(document.totals.sum_with_vat),
        },
    }


@router.post("/orders/{order_number}/recognize")
def recognize_order_document(
    order_number: str,
    payload: DocumentRecognizeInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """The КП or the invoice dropped on the order: stored, its items read and matched to the order lines in «Жду счет»
    / «В оплату». Nothing on the lines changes; `auto` is false for a foreign-currency document («Внести КП»)."""
    content = decode_upload(payload.content_base64, "Файл повреждён")
    try:
        found = recognise_order_document(
            RecogniseOrderDocumentCommand(
                order_number=order_number, kind=payload.kind, file_name=payload.file_name,
                content_type=payload.content_type, content=content, actor=principal.actor, now=now,
            ),
            uow_factory(),
        )
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error).strip("'\"")) from error
    return _recognised_result(found)


@router.post("/orders/{order_number}/apply-document")
def apply_document(
    order_number: str,
    payload: ApplyDocumentInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """Prices from the document in one transaction, all rows or none; an invoice also goes on every order row
    («Жду счет» → «В оплату», or the invoice of «В оплату» rows is replaced). 409 — the order moved on or a row is
    stale; 422 — a row refused (stage, invoice gate) or a foreign-currency document. A row without a price is not
    refused: `warnings` names it («нет цены: …»)."""
    lines = _command_errors(
        lambda: apply_order_document(
            ApplyOrderDocumentCommand(
                order_number=order_number,
                kind=payload.kind,
                file_url=payload.file_url.strip(),
                rows=[
                    DocumentPriceRow(
                        line_id=row.line_id, expected_version=row.version, price_with_vat=row.price_with_vat,
                        price=row.price, vat_rate=row.vat_rate, invoice_item_name=row.invoice_item_name,
                    )
                    for row in payload.lines
                ],
                actor=principal.actor,
                now=now,
                invoice_number=payload.invoice_number,
                invoice_date=payload.invoice_date,
                expected_arrival=payload.expected_arrival,
            ),
            uow_factory(),
        )
    )
    return {"kind": payload.kind, "lines": [_line_result(line) for line in lines], "warnings": _price_warnings(lines)}


@router.post("/{line_id}/invoice/replace")
def invoice_replace(
    line_id: UUID,
    payload: InvoiceReplaceInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    return _invoiced(
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
        uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
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
# The manual move into «Согласование» is off (owner, 04.10.2026): the endpoint stays and answers 422 with the
# reason; the exit (`/resume`) still serves a line parked before the switch.
router.post("/{line_id}/approval")(_offline(OfflineAction.APPROVAL))
router.post("/{line_id}/approval/resume")(_offline(OfflineAction.RESUME))
router.post("/{line_id}/cancel")(_offline(OfflineAction.CANCEL))


@router.post("/{line_id}/split")
def split(
    line_id: UUID,
    payload: SplitInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """«Раздробить по поставщикам»: N copies with new System IDs land in the «Новые» queue."""
    copies: list[ProcurementLine] = []

    def operation() -> ProcurementLine:
        source, created = split_procurement_line(
            SplitProcurementLineCommand(
                line_id=line_id,
                expected_version=payload.version,
                copies=payload.copies,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
        copies[:] = created
        return source

    result = _transition(operation)
    return {**result, "copies": [_line_result(copy) for copy in copies]}


# --- «Со склада» (owner 2026-10-04) ------------------------------------------------------------------

def _cover_result(cover: StockCover) -> dict:
    return {
        "id": str(cover.id),
        "line_id": str(cover.line_id),
        "label": cover.label,
        "lot_id": str(cover.lot_id) if cover.lot_id else None,
        "warehouse": cover.warehouse,
        "grn": cover.grn,
        "package": cover.package,
        "storage_location": cover.storage_location,
        "unit": cover.unit,
        "quantity": _decimal_result(cover.quantity),
        "consumed": _decimal_result(cover.consumed),
        "consumed_by": cover.consumed_by,
        "active": cover.active,
        "created_at": cover.created_at.isoformat(),
        "created_by": cover.created_by,
        "released_at": cover.released_at.isoformat() if cover.released_at else None,
    }


def _reserve_note(covers: list[StockCover]) -> str:
    """«резерв: SM-20261002-01 / 591К.3 — 80 шт» — who holds the lot's РЕЗЕРВ through «Со склада»."""
    if not covers:
        return ""
    return "резерв: " + "; ".join(
        f"{cover.label} — {_decimal_result(cover.quantity)} {cover.unit}".rstrip() for cover in covers
    )


def _candidate_result(lot: StockLotView, line: ProcurementLine, holders: list[StockCover]) -> dict:
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
        "balance": _decimal_result(lot.balance),
        "reserved": _decimal_result(lot.reserved),
        "available": _decimal_result(max(lot.available, Decimal("0"))),
        "unit_matches": same_unit(line.calculated_unit, lot.unit),
        "holders": [_cover_result(cover) for cover in holders],
        "reserve_note": _reserve_note(holders),
    }


@router.get("/{line_id}/stock-candidates")
def stock_cover_candidates(
    line_id: UUID,
    _principal: Annotated[Principal, Depends(procurement_edit_principal)],
    uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
    q: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """«Со склада» — the lots that can cover the line (warehouse search by the typed text, otherwise by АРТИКУЛ,
    then by НАИМЕНОВАНИЕ), with what is available and who already holds their reserve."""
    try:
        found = stock_candidates(line_id, uow_factory(), query=q)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "line": _line_result(found.line),
        "query": found.query,
        "lots": [_candidate_result(lot, found.line, found.holders.get(lot.id, [])) for lot in found.lots],
        "covers": [_cover_result(cover) for cover in found.covers],
    }


@router.post("/{line_id}/stock-cover")
def cover_line_from_stock(
    line_id: UUID,
    payload: StockCoverInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
) -> dict:
    """«Со склада»: reserve `quantity` of lot `lot_id` for the line; all of it covered → «Готово», no money.
    Without `lot_id` — «Есть на складе, без ГРН»: the same line rules, no reserve (`warehouse` optional)."""
    covers: list[StockCover] = []

    def operation() -> ProcurementLine:
        line, cover = cover_from_stock(
            CoverFromStockCommand(
                line_id=line_id,
                expected_version=payload.version,
                lot_id=payload.lot_id,
                quantity=payload.quantity,
                actor=principal.actor,
                now=now,
                warehouse=payload.warehouse,
            ),
            uow_factory(),
        )
        covers[:] = [cover]
        return line

    result = _transition(operation)
    return {**result, "cover": _cover_result(covers[0])}


@router.post("/{line_id}/stock-cover/{cover_id}/bind")
def bind_line_stock_cover(
    line_id: UUID,
    cover_id: UUID,
    payload: StockCoverBindInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
) -> dict:
    """«Привязать ГРН»: a cover «без ГРН» reserves its whole quantity on lot `lot_id`, or nothing (422)."""
    covers: list[StockCover] = []

    def operation() -> ProcurementLine:
        line, cover = bind_stock_cover(
            BindStockCoverCommand(
                line_id=line_id,
                expected_version=payload.version,
                cover_id=cover_id,
                lot_id=payload.lot_id,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
        covers[:] = [cover]
        return line

    result = _transition(operation)
    return {**result, "cover": _cover_result(covers[0])}


@router.post("/{line_id}/stock-cover/release")
def release_line_stock_cover(
    line_id: UUID,
    payload: StockCoverReleaseInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
) -> dict:
    """«Снять "Со склада"»: every active cover of the line (or only `cover_id`) goes back to its lot."""
    released: list[StockCover] = []

    def operation() -> ProcurementLine:
        line, covers = release_stock_cover(
            ReleaseStockCoverCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
                cover_id=payload.cover_id,
            ),
            uow_factory(),
        )
        released[:] = covers
        return line

    result = _transition(operation)
    return {**result, "released": [_cover_result(cover) for cover in released]}
