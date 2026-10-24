"""Receiving HTTP contract for receiver work queues and incoming control."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.stored_files import decode_upload, file_response
from app.shared.http_auth import Principal, receiving_edit_principal, receiving_view_principal
from app.shared.unit_mass import mass_fields_from

from .application import (
    AcceptDeliveryCommand,
    AttachReceivingFileCommand,
    DeliveryLineInput,
    AttachShippingDocumentsCommand,
    ConcurrentReceivingUpdate,
    InspectBatchesCommand,
    InspectReceivingCommand,
    OpenVkRevisionCommand,
    PlaceReceivingCommand,
    StockBalanceLookup,
    SupplierReturnCommand,
    SupplierReturnItem,
    UnitOfWork,
    UnpackCommand,
    accept_delivery,
    attach_receiving_file,
    attach_shipping_documents,
    inspect_receiving,
    inspect_receiving_batches,
    build_putaway_print_sheet,
    open_vk_revision,
    place_receiving,
    receiving_act_sheet,
    receiving_file_url,
    receiving_queue,
    return_to_supplier,
    supplier_return_candidates,
    unpack_receiving,
)
from .domain import (
    ACT_KINDS,
    InspectionInput,
    ReceivingLine,
    ReceivingValidationError,
    SHIPPING_DOCUMENT_PENDING_NOTE,
    SUPPLIER_RETURN_REASONS,
    grn_prefix_for,
    normalize_document_url,
    SupplierReturnMode,
    verdict_reference_date,
)


router = APIRouter(prefix="/api/receiving", tags=["receiving"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
MassInput = Annotated[str, Field(max_length=64)] | float | None


class BatchPayload(BaseModel):
    """Одна партия изготовителя в поставке (Receiving.gs batchFields_): своё количество и реквизиты партии."""

    fact_quantity: Quantity | None = None
    document_quantity: Quantity | None = None
    series: str = Field(default="", max_length=255)
    expiry_date: date | None = None
    manufacture_date: date | None = None
    quality_document_url: str | None = Field(default=None, max_length=4096)
    mass_1: MassInput = None
    mass_10: MassInput = None
    mass_100: MassInput = None
    # КОЛ-ВО БРАКА этой партии — когда отмечен брак.
    defect_quantity: Quantity | None = None

    @field_validator("quality_document_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        return normalize_document_url(value)


class InspectionPayload(BaseModel):
    version: int = Field(ge=1)
    # RC-1 (Receiving.gs:676-678): одно из двух количеств можно не вводить — берётся другое.
    fact_quantity: Quantity | None = None
    document_quantity: Quantity | None = None
    shipping_document_url: str | None = Field(default=None, max_length=4096)
    quality_document_url: str | None = Field(default=None, max_length=4096)
    expiry_date: date | None = None
    manual_nonconformities: list[str] = Field(default_factory=list, max_length=2)
    # КОЛ-ВО БРАКА: обязательно с «Механическое повреждение / брак», 0 < брак ≤ факт; без брака не передаётся.
    defect_quantity: Quantity | None = None
    disposition: str | None = Field(default=None, max_length=255)
    manual_grn_prefix: str | None = Field(default=None, max_length=8)
    series: str = Field(default="", max_length=255)
    # МАССА 1 ШТ: weight of 1, 10 or 100 pieces, grams; «0,5» as typed is fine (SUPPLYMAP icrNum_).
    mass_1: MassInput = None
    mass_10: MassInput = None
    mass_100: MassInput = None
    historical: bool = False
    document_date: date | None = None
    arrival_date: date | None = None
    document_number: str = Field(default="", max_length=128)
    document_name: str = Field(default="", max_length=1024)
    note: str = Field(default="", max_length=2000)
    manufacture_date: date | None = None
    nonconformity_description: str = Field(default="", max_length=2000)
    manufacturer: str = Field(default="", max_length=255)
    country: str = Field(default="", max_length=128)
    quality_document_type: str = Field(default="", max_length=128)
    quality_document_number: str = Field(default="", max_length=128)
    standard: str = Field(default="", max_length=255)
    quality_document_date: date | None = None
    # RC-13: ≥ 2 партии — строка делится, каждая партия проходит ВК со своим ГРН.
    batches: list[BatchPayload] = Field(default_factory=list, max_length=20)

    @field_validator("shipping_document_url", "quality_document_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        return normalize_document_url(value)


class DocumentLinePayload(BaseModel):
    id: UUID
    version: int


class DocumentsPayload(BaseModel):
    lines: list[DocumentLinePayload] = Field(min_length=1, max_length=500)
    shipping_document_url: str | None = Field(default=None, max_length=4096)
    document_number: str = Field(default="", max_length=128)
    document_date: date | None = None

    @field_validator("shipping_document_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        return normalize_document_url(value)


class DeliveryLinePayload(BaseModel):
    id: UUID
    version: int = Field(ge=1)
    # Typed only on a discrepancy: no УПД quantity → the ordered one, no fact → the УПД one.
    document_quantity: Quantity | None = None
    fact_quantity: Quantity | None = None
    storage_location: str | None = Field(default=None, max_length=16)
    deviation: Literal["none", "brak", "peresort", "no_quality_doc", "expired"] = "none"
    # «Брак»: КОЛ-ВО БРАКА строки, обязательно; другие отклонения — только в описание несоответствия.
    deviation_quantity: Quantity | None = None
    expiry_date: date | None = None
    series: str = Field(default="", max_length=255)
    manual_grn_prefix: str | None = Field(default=None, max_length=8)


class DeliveryPayload(BaseModel):
    lines: list[DeliveryLinePayload] = Field(min_length=1, max_length=200)
    shipping_document_url: str | None = Field(default=None, max_length=4096)
    # The УПД is optional: it often comes by ЭДО later («Внести УПД»).
    document_number: str = Field(default="", max_length=128)
    document_date: date | None = None
    warehouse: Literal["ЦАР", "ЖУК"] | None = None

    @field_validator("shipping_document_url")
    @classmethod
    def validate_document_url(cls, value: str | None) -> str | None:
        return normalize_document_url(value)


class FilePayload(BaseModel):
    line_id: UUID
    kind: Literal["upd", "quality"]
    file_name: str = Field(min_length=1, max_length=512)
    content_type: str = Field(default="", max_length=255)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class UnpackPayload(BaseModel):
    version: int = Field(ge=1)
    quantities: list[Quantity] = Field(min_length=2, max_length=100)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class PlacementPayload(BaseModel):
    version: int = Field(ge=1)
    storage_location: str = Field(min_length=3, max_length=16)


class SupplierReturnItemPayload(BaseModel):
    id: UUID
    version: int = Field(ge=1)
    # Empty → КОЛ-ВО ФАКТ of the line (SupplierReturn.gs: `it.qty` or the line's own quantity).
    quantity: Quantity | None = None


class SupplierReturnPayload(BaseModel):
    items: list[SupplierReturnItemPayload] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=255)
    mode: SupplierReturnMode
    claim: str = Field(default="", max_length=255)
    comment: str = Field(default="", max_length=1000)


def receiving_stock_lookup(request: Request) -> StockBalanceLookup:
    lookup = getattr(request.app.state, "receiving_stock_lookup", None)
    if lookup is None:
        raise HTTPException(status_code=503, detail="Склад ERP не подключён")
    return lookup


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
        # grnPrefixForPurpose_: what the ГРН will start with; None — the form asks for CSM or PPM up front.
        "grn_prefix": grn_prefix_for(purpose=line.purpose, link=line.source_link, goal=line.goal),
        "status": line.status.value,
        "version": line.version,
        "warehouse": line.warehouse,
        "article": line.article,
        "series": line.series,
        "name": line.name,
        "item_type": line.item_type,
        "purpose": line.purpose,
        "goal": line.goal,
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
        "defect_quantity": _decimal(line.defect_quantity),
        # Non-blocking: accepted without УПД (ЭДО later); gone once «Внести УПД» adds it.
        "shipping_document_note": SHIPPING_DOCUMENT_PENDING_NOTE if line.shipping_document_pending else None,
        "accepted_quantity": _decimal(line.accepted_quantity),
        "backorder_quantity": _decimal(line.backorder_quantity),
        "grn": line.grn,
        "package": line.package,
        "storage_location": line.storage_location,
        "inspector": line.inspector.name if line.inspector else None,
        "warehouse_event_emitted": line.warehouse_event_emitted,
        "inspected_on": line.inspected_on.isoformat() if line.inspected_on else None,
        "revision_of_id": str(line.revision_of_id) if line.revision_of_id else None,
        "nonconformity_description": line.nonconformity_description,
        "supplier_returns": line.supplier_returns,
        "order_number": line.order_number,
        "expected_arrival": line.expected_arrival.isoformat() if line.expected_arrival else None,
        "document_number": line.document_number,
        "document_date": line.document_date.isoformat() if line.document_date else None,
        "document_name": line.document_name,
        "inspection_note": line.inspection_note,
        "manufacture_date": line.manufacture_date.isoformat() if line.manufacture_date else None,
        "manufacturer": line.manufacturer,
        "country": line.country,
        "quality_document_type": line.quality_document_type,
        "quality_document_number": line.quality_document_number,
        "standard": line.standard,
        "quality_document_date": line.quality_document_date.isoformat() if line.quality_document_date else None,
        "split_of_id": str(line.split_of_id) if line.split_of_id else None,
        "acts": [
            {"kind": kind, "title": ACT_KINDS[kind][1], "number": number, "date": day.isoformat() if day else None}
            for kind in ACT_KINDS
            for number, day in [line.act(kind)]
            if number
        ],
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
    _principal: Annotated[Principal, Depends(receiving_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
    preset: Literal["open", "to_stock", "done", "archive", "all"] | None = None,
    status: Annotated[list[str] | None, Query(max_length=6)] = None,
) -> dict:
    """Open deliveries first; `preset` (open / to_stock / done / archive / all) or `status` (repeatable,
    the Russian status) filter on the server. «Архив ВК» only on request."""
    lines = _run(
        lambda: receiving_queue(
            uow_factory(), limit=limit + 1, after_id=after, preset=preset, statuses=tuple(status or ())
        )
    )
    page = lines[:limit]
    return {
        "lines": [_result(line) for line in page],
        "next_cursor": str(page[-1].id) if len(lines) > limit else None,
    }


@router.get("/putaway-sheet")
def putaway_sheet_endpoint(
    _principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    """WH-6: «Лист перемещения на склад» — принятые позиции с ГРН, которым ещё не назначено место
    (SUPPLYMAP Warehouse.gs:349-382 buildTransferSheet). Печатается кладовщиком через тот же
    печатный механизм, что и FF-8 (frontend/src/print.ts), разносится по местам от руки, потом
    каждая строка уходит через POST /{line_id}/place."""
    sheet = _run(lambda: build_putaway_print_sheet(uow_factory(), now))
    return {"sheet": sheet.to_dict()}


@router.get("/supplier-return")
def supplier_return_list(
    _principal: Annotated[Principal, Depends(receiving_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
    stock: Annotated[StockBalanceLookup, Depends(receiving_stock_lookup)],
) -> dict:
    """RC-6 (SupplierReturn.gs supplierReturnRows_): принятые позиции и что по их ГРН лежит на складе."""
    lines = _run(lambda: supplier_return_candidates(uow_factory()))
    candidates = []
    for line in lines:
        lot = (
            stock.lot_balance(line.grn, line.package)
            if line.grn and line.warehouse_event_emitted
            else None
        )
        candidates.append({
            **_result(line),
            "stock_warehouse": lot.warehouse if lot else None,
            "stock_balance": _decimal(lot.balance) if lot else None,
        })
    return {
        "lines": candidates,
        "reasons": list(SUPPLIER_RETURN_REASONS),
        "modes": {mode.value: label for mode, label in (
            (SupplierReturnMode.WAIT, "Ждём замену — строка снова «В пути»"),
            (SupplierReturnMode.CLOSE, "Заказ закрыт — «Возврат»"),
        )},
    }


@router.post("/supplier-return")
def supplier_return(
    payload: SupplierReturnPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
    stock: Annotated[StockBalanceLookup, Depends(receiving_stock_lookup)],
) -> dict:
    result = _run(
        lambda: return_to_supplier(
            SupplierReturnCommand(
                items=tuple(
                    SupplierReturnItem(line_id=item.id, expected_version=item.version, quantity=item.quantity)
                    for item in payload.items
                ),
                reason=payload.reason,
                mode=payload.mode,
                claim=payload.claim,
                comment=payload.comment,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
            stock,
        )
    )
    return {
        "lines": [_result(line) for line in result.lines],
        "writeoffs": result.writeoffs,
        "notes": list(result.notes),
        "message": result.message,
    }


@router.post("/{line_id}/inspect")
def inspect(
    line_id: UUID,
    payload: InspectionPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    def quantities(fact: Decimal | None, document: Decimal | None) -> tuple[Decimal, Decimal]:
        if fact is None and document is None:
            raise HTTPException(status_code=422, detail="Укажите количество по УПД или фактическое")
        return (fact if fact is not None else document), (document if document is not None else fact)

    def inspection_for(data: InspectionPayload | BatchPayload) -> InspectionInput:
        fact, document = quantities(data.fact_quantity, data.document_quantity)
        return InspectionInput(
            fact_quantity=fact,
            document_quantity=document,
            ordered_quantity=Decimal("1"),  # replaced by the server-owned value in application layer
            has_shipping_document=False,  # derived from the submitted URL in application layer
            quality_document_required=False,
            quality_document_present=False,  # derived from the submitted URL in application layer
            expiry_date=data.expiry_date,
            inspection_date=now.date(),
            verdict_date=verdict_reference_date(
                today=now.date(),
                historical=payload.historical,
                document_date=payload.document_date,
                arrival_date=payload.arrival_date,
            ),
            manual_nonconformities=tuple(payload.manual_nonconformities),
            disposition=payload.disposition,
            shipping_document_url=payload.shipping_document_url,
            quality_document_url=data.quality_document_url or payload.quality_document_url,
            series=data.series,
            mass_1=data.mass_1,
            mass_10=data.mass_10,
            mass_100=data.mass_100,
            document_number=payload.document_number.strip(),
            document_date=payload.document_date,
            document_name=payload.document_name.strip(),
            note=payload.note.strip(),
            manufacture_date=data.manufacture_date,
            nonconformity_description=payload.nonconformity_description.strip(),
            manufacturer=payload.manufacturer.strip(),
            country=payload.country.strip(),
            quality_document_type=payload.quality_document_type.strip(),
            quality_document_number=payload.quality_document_number.strip(),
            standard=payload.standard.strip(),
            quality_document_date=payload.quality_document_date,
            defect_quantity=data.defect_quantity,
        )

    if len(payload.batches) == 1:
        raise HTTPException(status_code=422, detail="Для разбивки нужно минимум 2 партии")
    if payload.batches and payload.defect_quantity is not None:
        raise HTTPException(status_code=422, detail="При разбивке на партии количество брака указывается у каждой партии")
    if payload.batches:
        batches = tuple(inspection_for(batch) for batch in payload.batches)
        lines = _run(
            lambda: inspect_receiving_batches(
                InspectBatchesCommand(
                    line_id=line_id,
                    expected_version=payload.version,
                    batches=batches,
                    actor=principal.actor,
                    now=now,
                    manual_grn_prefix=payload.manual_grn_prefix,
                ),
                uow_factory(),
            )
        )
        return {"line": _result(lines[0]), "lines": [_result(line) for line in lines]}
    inspection = inspection_for(payload)
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


@router.post("/documents")
def attach_documents(
    payload: DocumentsPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    lines = _run(
        lambda: attach_shipping_documents(
            AttachShippingDocumentsCommand(
                lines=tuple((item.id, item.version) for item in payload.lines),
                url=payload.shipping_document_url,
                number=payload.document_number,
                document_date=payload.document_date,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
    return {"lines": [_result(line) for line in lines]}


@router.post("/deliveries")
def accept_delivery_endpoint(
    payload: DeliveryPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    """«Провести поставку»: one УПД, its positions inspected, placed and handed to the warehouse at once."""
    result = _run(
        lambda: accept_delivery(
            AcceptDeliveryCommand(
                lines=tuple(
                    DeliveryLineInput(
                        line_id=item.id,
                        expected_version=item.version,
                        document_quantity=item.document_quantity,
                        fact_quantity=item.fact_quantity,
                        storage_location=(item.storage_location or "").strip() or None,
                        deviation=item.deviation,
                        deviation_quantity=item.deviation_quantity,
                        expiry_date=item.expiry_date,
                        series=item.series.strip(),
                        manual_grn_prefix=item.manual_grn_prefix,
                    )
                    for item in payload.lines
                ),
                document_number=payload.document_number,
                document_date=payload.document_date,
                shipping_document_url=payload.shipping_document_url,
                warehouse=payload.warehouse,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
    return {"delivery_id": str(result.delivery_id), "lines": [_result(line) for line in result.lines]}


@router.post("/{line_id}/unpack")
def unpack(
    line_id: UUID,
    payload: UnpackPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    lines = _run(
        lambda: unpack_receiving(
            UnpackCommand(
                line_id=line_id,
                expected_version=payload.version,
                quantities=tuple(payload.quantities),
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
    return {"lines": [_result(line) for line in lines]}


@router.post("/files")
def upload_file(
    payload: FilePayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    """Portal «Документ УПД/М-15 (файл)»: the scan is stored in ERP, its link goes into the ВК form."""
    content = decode_upload(payload.content_base64, "Файл повреждён")
    stored = _run(lambda: attach_receiving_file(AttachReceivingFileCommand(
        line_id=payload.line_id, kind=payload.kind, file_name=payload.file_name, content_type=payload.content_type,
        content=content, actor=principal.actor, now=now,
    ), uow_factory()))
    return {"url": receiving_file_url(stored.id), "file_name": stored.file_name, "size": stored.size}


@router.get("/files/{file_id}")
def download_file(
    file_id: UUID,
    _principal: Annotated[Principal, Depends(receiving_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> Response:
    with uow_factory() as uow:
        found = uow.files.get(file_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    meta, content = found
    return file_response(content, meta.file_name)


@router.get("/{line_id}/acts/{kind}")
def act_sheet(
    line_id: UUID,
    kind: str,
    _principal: Annotated[Principal, Depends(receiving_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    if kind not in ACT_KINDS:
        raise HTTPException(status_code=404, detail="Неизвестный вид акта")
    return {"sheet": _run(lambda: receiving_act_sheet(line_id, kind, uow_factory())).to_dict()}


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


@router.post("/{line_id}/vk-revision")
def vk_revision(
    line_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(receiving_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(receiving_uow_factory)],
) -> dict:
    archived, revision = _run(
        lambda: open_vk_revision(
            OpenVkRevisionCommand(
                line_id=line_id,
                expected_version=payload.version,
                actor=principal.actor,
                now=now,
            ),
            uow_factory(),
        )
    )
    return {"archived": _result(archived), "line": _result(revision)}
