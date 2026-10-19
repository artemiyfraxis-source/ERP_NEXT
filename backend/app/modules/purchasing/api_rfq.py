"""Запросы КП, КП, сравнение и решение (stage option procurement.rfq)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.stored_files import decode_upload
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal, section_allowed
from app.workflows.purchasing_procurement_bridge import ProcurementLinesBridge
from app.workflows.purchasing_rfq_stats import PurchaseStatsBridge

from . import quote_import
from . import rfq as service
from . import rfq_pool
from .deps import procurement_uow_factory, purchasing_uow_factory, require_option


def rfq_option(request: Request, principal: Annotated[Principal, Depends(procurement_view_principal)]) -> None:
    """After the section gate: a token or a stranger is refused (401/403) before the option says 404, and a write by
    someone who holds «Лист закупки» on view only is refused (403) before it too (view-mode contract, ID-6)."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not section_allowed(principal, ("procurement",), "edit"):
        raise HTTPException(status_code=403, detail="Раздел «Лист закупки» открыт вам только на просмотр.")
    require_option("procurement.rfq")


router = APIRouter(prefix="/api/purchasing/rfqs", tags=["purchasing"], dependencies=[Depends(rfq_option)])

View = Annotated[Principal, Depends(procurement_view_principal)]
Edit = Annotated[Principal, Depends(procurement_edit_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
Uow = Annotated[Callable, Depends(purchasing_uow_factory)]


def procurement_lines(
    request: Request, factory: Annotated[Callable, Depends(procurement_uow_factory)],
) -> service.ProcurementLines:
    return ProcurementLinesBridge(factory, getattr(request.app.state, "procurement_supplier_directory", None))


Lines = Annotated[service.ProcurementLines, Depends(procurement_lines)]


def purchase_stats(factory: Annotated[Callable, Depends(procurement_uow_factory)]) -> rfq_pool.PurchaseStats:
    return PurchaseStatsBridge(factory)


Stats = Annotated[rfq_pool.PurchaseStats, Depends(purchase_stats)]

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
Text = Annotated[str, Field(max_length=2000)]


def _run(operation):
    """Map the application's errors to HTTP; a lost race (unique number, double insert) is retried once."""
    for attempt in range(2):
        try:
            return operation()
        except service.RfqError as error:
            detail: object = str(error)
            if error.violations:
                detail = {
                    "code": "decision_reasons", "message": str(error),
                    "violations": [{"field": v.field, "message": v.message} for v in error.violations],
                }
            raise HTTPException(status_code=error.status, detail=detail) from error
        except (IntegrityError, StaleDataError) as error:
            if attempt == 1:
                raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите и повторите") from error
    raise AssertionError("unreachable")


# The five stored reasons plus the suggestion's own (mapped to a stored one by `rfq.stored_reason`).
Reason = Literal["key", "category", "rotation", "plan", "manual", "items", "category_history", "original"]


class AddSupplierInput(BaseModel):
    supplier_id: UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    reason: Reason = "manual"


class PickSupplierInput(AddSupplierInput):
    # The procurement line ids (of `line_ids`) asked of this supplier; omitted — every line of the request.
    line_ids: list[UUID] | None = Field(default=None, max_length=200)


class CreateRfqInput(BaseModel):
    line_ids: list[UUID] = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=255)
    suppliers: list[PickSupplierInput] = Field(default_factory=list, max_length=100)


class SupplierLinesInput(BaseModel):
    line_ids: list[UUID] = Field(max_length=500)  # request line ids; every line — asked for all


class DeclineInput(BaseModel):
    note: str | None = Field(default=None, max_length=512)
    declined: bool = True


class TierInput(BaseModel):
    from_quantity: Amount = Field(alias="from")
    price: Amount


class QuoteLineModel(BaseModel):
    rfq_line_id: UUID
    unit_price: Amount | None = None
    vat_included: bool = True
    vat_rate: Annotated[Decimal, Field(ge=0, lt=100, max_digits=5, decimal_places=2)] | None = None
    moq: Amount | None = None
    pack: Amount | None = None
    tiers: list[TierInput] = Field(default_factory=list, max_length=20)
    lead_days: Annotated[int, Field(ge=0, le=3650)] | None = None
    in_stock: bool = False
    declined: bool = False
    analog: bool = False
    analog_note: str | None = Field(default=None, max_length=512)


class AliasModel(BaseModel):
    rfq_line_id: UUID
    text: str = Field(min_length=1, max_length=512)


class QuoteModel(BaseModel):
    supplier_id: UUID
    received_on: date | None = None
    valid_until: date | None = None
    delivery_cost: Money | None = None
    min_order_sum: Money | None = None
    source: Literal["manual", "paste", "file"] = "manual"
    note: Text | None = None
    number: str | None = Field(default=None, max_length=64)
    lines: list[QuoteLineModel] = Field(min_length=1, max_length=500)
    # Supplier wordings the buyer confirmed in «КП из PDF и текста»; stored only while that option is on.
    aliases: list[AliasModel] = Field(default_factory=list, max_length=500)

    def to_input(self) -> service.QuoteInput:
        return service.QuoteInput(
            supplier_id=self.supplier_id, received_on=self.received_on, valid_until=self.valid_until,
            delivery_cost=self.delivery_cost, min_order_sum=self.min_order_sum, source=self.source, note=self.note,
            number=self.number,
            lines=tuple(
                service.QuoteLineInput(
                    rfq_line_id=line.rfq_line_id, unit_price=line.unit_price, vat_included=line.vat_included,
                    vat_rate=line.vat_rate, moq=line.moq, pack=line.pack,
                    tiers=tuple((tier.from_quantity, tier.price) for tier in line.tiers), lead_days=line.lead_days,
                    in_stock=line.in_stock, declined=line.declined, analog=line.analog, analog_note=line.analog_note,
                )
                for line in self.lines
            ),
        )


class PasteInput(BaseModel):
    supplier_id: UUID | None = None
    text: str = Field(min_length=1, max_length=500_000)


class ImportInput(BaseModel):
    supplier_id: UUID | None = None
    text: str | None = Field(default=None, max_length=500_000)
    file_name: str | None = Field(default=None, max_length=300)
    content_base64: str | None = Field(default=None, max_length=28_000_000)  # a 20 MB PDF in base64


class DecisionModel(BaseModel):
    rfq_line_id: UUID
    quote_line_id: UUID
    expected_version: int | None = Field(default=None, ge=1)
    gate_override_reason: Text | None = None
    not_cheapest_reason: Text | None = None
    analog_approved_by: str | None = Field(default=None, max_length=255)


# Static paths before /{rfq_id}.


@router.get("/candidates")
def rfq_candidates(principal: Edit, uow: Uow, lines: Lines) -> dict:
    return {"lines": _run(lambda: service.candidates(uow(), lines, principal.actor))}


@router.get("/directory")
def rfq_directory(_principal: View, uow: Uow) -> dict:
    return {"suppliers": _run(lambda: service.directory(uow()))}


@router.get("/pool")
def rfq_pool_lines(principal: Edit, uow: Uow, lines: Lines, stats: Stats) -> dict:
    return _run(lambda: rfq_pool.pool(uow(), lines, stats, principal.actor))


def _uuids(values: list[str]) -> list[UUID]:
    """`?line_ids=a,b` and `?line_ids=a&line_ids=b` alike."""
    try:
        return [UUID(part) for value in values for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Неверный id строки") from error


@router.get("/suggest")
def rfq_suggest_by_stats(
    principal: Edit, uow: Uow, lines: Lines, stats: Stats,
    line_ids: Annotated[list[str], Query(max_length=200)] = [],  # noqa: B006 — FastAPI copies the default
    rfq_id: UUID | None = None,
) -> dict:
    ids = _uuids(line_ids)
    if len(ids) > 200:
        raise HTTPException(status_code=422, detail="Не больше 200 строк в одном запросе")
    return _run(lambda: rfq_pool.suggest(uow(), lines, stats, principal.actor, line_ids=ids, rfq_id=rfq_id))


@router.get("/suggest-suppliers")
def rfq_suggest(rfq_id: UUID, principal: Edit, uow: Uow, lines: Lines) -> dict:
    """The earlier suggestion of a request (directory only); the client asks /suggest now."""
    return _run(lambda: service.suggest_suppliers(uow(), lines, principal.actor, rfq_id))


@router.delete("/quotes/{quote_id}", status_code=204)
def rfq_delete_quote(quote_id: UUID, principal: Edit, now: Now, uow: Uow) -> Response:
    _run(lambda: service.delete_quote(uow(), principal.actor, now, quote_id))
    return Response(status_code=204)


@router.get("")
def rfq_list(principal: View, uow: Uow, scope: Literal["mine", "all"] = "mine") -> dict:
    return {"rfqs": _run(lambda: service.list_rfqs(uow(), principal.actor, scope == "all"))}


@router.post("", status_code=201)
def rfq_create(payload: CreateRfqInput, principal: Edit, now: Now, uow: Uow, lines: Lines) -> dict:
    picks = [
        service.SupplierPick(item.supplier_id, item.name, item.reason,
                             None if item.line_ids is None else tuple(item.line_ids))
        for item in payload.suppliers
    ]
    return _run(lambda: service.create_rfq(uow(), lines, principal.actor, now, payload.line_ids, payload.title, picks))


@router.get("/{rfq_id}")
def rfq_matrix(rfq_id: UUID, principal: View, now: Now, uow: Uow, lines: Lines) -> dict:
    return _run(lambda: service.matrix(uow(), lines, principal.actor, rfq_id, now.date()))


@router.post("/{rfq_id}/suppliers", status_code=201)
def rfq_add_supplier(rfq_id: UUID, payload: AddSupplierInput, principal: Edit, now: Now, uow: Uow) -> dict:
    return _run(lambda: service.add_supplier(
        uow(), principal.actor, now, rfq_id, supplier_id=payload.supplier_id, name=payload.name, reason=payload.reason,
    ))


@router.delete("/{rfq_id}/suppliers/{supplier_id}", status_code=204)
def rfq_remove_supplier(rfq_id: UUID, supplier_id: UUID, principal: Edit, uow: Uow) -> Response:
    _run(lambda: service.remove_supplier(uow(), principal.actor, rfq_id, supplier_id))
    return Response(status_code=204)


@router.put("/{rfq_id}/suppliers/{supplier_id}/lines")
def rfq_supplier_lines(
    rfq_id: UUID, supplier_id: UUID, payload: SupplierLinesInput, principal: Edit, now: Now, uow: Uow,
) -> dict:
    return _run(lambda: service.set_supplier_lines(uow(), principal.actor, now, rfq_id, supplier_id, payload.line_ids))


@router.post("/{rfq_id}/suppliers/{supplier_id}/sent")
def rfq_mark_sent(rfq_id: UUID, supplier_id: UUID, principal: Edit, now: Now, uow: Uow) -> dict:
    _run(lambda: service.mark_sent(uow(), principal.actor, now, rfq_id, supplier_id))
    return {"ok": True}


@router.post("/{rfq_id}/suppliers/{supplier_id}/declined")
def rfq_mark_declined(
    rfq_id: UUID, supplier_id: UUID, payload: DeclineInput, principal: Edit, now: Now, uow: Uow,
) -> dict:
    _run(lambda: service.mark_declined(uow(), principal.actor, now, rfq_id, supplier_id, payload.note, payload.declined))
    return {"ok": True}


@router.post("/{rfq_id}/quotes", status_code=201)
def rfq_add_quote(rfq_id: UUID, payload: QuoteModel, principal: Edit, now: Now, uow: Uow) -> dict:
    saved = _run(lambda: service.add_quote(uow(), principal.actor, now, rfq_id, payload.to_input()))
    if payload.aliases and stage_options.enabled("procurement.quote_import"):
        quote_import.remember_aliases(
            uow, principal.actor, now, rfq_id, payload.supplier_id, [(item.rfq_line_id, item.text) for item in payload.aliases])
    return saved


@router.post("/{rfq_id}/quotes/paste")
def rfq_paste_quote(rfq_id: UUID, payload: PasteInput, principal: Edit, uow: Uow) -> dict:
    return _run(lambda: service.paste_preview(uow(), principal.actor, rfq_id, payload.text, payload.supplier_id))


@router.post("/{rfq_id}/quotes/import")
def rfq_import_quote(rfq_id: UUID, payload: ImportInput, principal: Edit, uow: Uow) -> dict:
    """«КП из PDF и текста»: proposed quote lines for the form; nothing is saved."""
    require_option("procurement.quote_import")
    require_option("procurement.rfq")
    if bool(payload.text) == bool(payload.content_base64):
        raise HTTPException(status_code=422, detail="Вставьте текст КП или приложите PDF")
    if payload.content_base64:
        text = _run(lambda: quote_import.text_of_pdf(decode_upload(payload.content_base64 or "", "Файл повреждён")))
    else:
        text = payload.text or ""
    return _run(lambda: quote_import.parse_quote(
        uow(), principal.actor, rfq_id, text, payload.supplier_id, "file" if payload.content_base64 else "paste"))


@router.post("/{rfq_id}/decisions", status_code=201)
def rfq_decide(rfq_id: UUID, payload: DecisionModel, principal: Edit, now: Now, uow: Uow, lines: Lines) -> dict:
    data = service.DecisionInput(**payload.model_dump())
    return _run(lambda: service.decide(uow(), lines, principal.actor, now, rfq_id, data))


@router.delete("/{rfq_id}/decisions/{decision_id}", status_code=204)
def rfq_undo_decision(rfq_id: UUID, decision_id: UUID, principal: Edit, now: Now, uow: Uow, lines: Lines) -> Response:
    _run(lambda: service.undo_decision(uow(), lines, principal.actor, now, rfq_id, decision_id))
    return Response(status_code=204)


@router.post("/{rfq_id}/close")
def rfq_close(rfq_id: UUID, principal: Edit, now: Now, uow: Uow) -> dict:
    _run(lambda: service.close_rfq(uow(), principal.actor, now, rfq_id))
    return {"ok": True}
