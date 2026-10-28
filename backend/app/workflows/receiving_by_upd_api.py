"""«Приёмка → Принять по УПД» — HTTP contract (owner 26.10.2026; the matching — `receiving_by_upd.py`).

Every route is the receiving edit right (ID-6: a view-only «Приёмка» is refused):
- the scan, as «Склад → Поставка» reads it (the same queue of the warehouse PC agent, the same files and answers —
  `warehouse.scan_jobs_api`): `POST /api/receiving/upd/scan-jobs` (scanner), `GET|DELETE /api/receiving/upd/scan-jobs/
  {id}`, `POST /api/receiving/upd/scan` (multipart `file`: PDF / photo / ЭДО XML / ZIP), `POST /api/receiving/upd/
  scan-merge` `{files}` («+ Страница»);
- `POST /api/receiving/upd/proposal` `{recognised, lines, warehouse?, supplier?}` (the scan's answer; `supplier` — the
  «Ожидаются» group it was started from) → the delivery pre-filled: `{warehouse, other_warehouses, invoice_numbers,
  lines: [{line, ticked, document_quantity, confidence, by_invoice, rows, warnings}], rows, unmatched, matched, orders}`.
  Read-only;
- `POST /api/receiving/upd/accept` — «Провести поставку» (`/api/receiving/deliveries` payload) plus `scan_file` (the
  scan's link) and `recognised`: the scanned file becomes the УПД file of the accepted lines, the lines go through
  `accept_delivery` (all or nothing; lines of one purchasing supplier under different texts are allowed together when
  the УПД's seller or the suppliers' book says so), then the file goes to purchasing «Реестр УПД» (never fails the
  acceptance). Answer as «Провести поставку» plus `upd_file` and `registry`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.modules.receiving.api import (
    DeliveryPayload,
    delivery_command,
    delivery_view,
    line_view,
    receiving_uow_factory,
    run_receiving,
)
from app.modules.receiving.application import (
    AttachReceivingFileCommand,
    accept_delivery,
    attach_receiving_file,
    receiving_file_url,
)
from app.modules.warehouse import scan_jobs_api
from app.modules.warehouse.api import warehouse_uow_factory
from app.modules.warehouse.receipts_application import scan_file, scan_file_id
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, receiving_edit_principal
from app.workflows import receipt_upd_registry
from app.workflows.receiving_by_upd import (
    ROWS_LIMIT,
    header_from,
    propose,
    proposal_view,
    rows_from,
    supplier_aliases,
    supplier_book,
    upd_registry_header,
    waiting_lines,
)

router = APIRouter(prefix="/api/receiving/upd", tags=["receiving"])
Editor = Annotated[Principal, Depends(receiving_edit_principal)]
Now = Annotated[datetime, Depends(moscow_now)]
WarehouseFactory = Annotated[Callable, Depends(warehouse_uow_factory)]
ReceivingFactory = Annotated[Callable, Depends(receiving_uow_factory)]
NO_SCAN = "Скан УПД не найден — отсканируйте или выберите файл заново"
log = logging.getLogger(__name__)


def _factory(request: Request, name: str) -> Callable | None:
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


@router.post("/scan-jobs")
def new_scan_job(request: Request, principal: Editor, now: Now, uow_factory: WarehouseFactory,
                 payload: scan_jobs_api.ScanStartInput | None = None) -> dict:
    return scan_jobs_api.new_scan_job(request, principal, now, uow_factory, payload)


@router.get("/scan-jobs/{job_id}")
def scan_job(job_id: UUID, request: Request, background: BackgroundTasks, principal: Editor, now: Now,
             uow_factory: WarehouseFactory) -> dict:
    return scan_jobs_api.scan_job(job_id, request, background, principal, now, uow_factory)


@router.delete("/scan-jobs/{job_id}")
def cancel_scan_job(job_id: UUID, principal: Editor, now: Now, uow_factory: WarehouseFactory) -> dict:
    return scan_jobs_api.cancel_scan_job(job_id, principal, now, uow_factory)


@router.post("/scan")
async def scan_document(request: Request, background: BackgroundTasks, principal: Editor, now: Now,
                        uow_factory: WarehouseFactory) -> dict:
    return await scan_jobs_api.scan_receipt_document(request, background, principal, now, uow_factory)


@router.post("/scan-merge")
async def merge_scans(payload: scan_jobs_api.ScanMergeInput, request: Request, background: BackgroundTasks,
                      principal: Editor, now: Now, uow_factory: WarehouseFactory) -> dict:
    return await scan_jobs_api.merge_receipt_scans(payload, request, background, principal, now, uow_factory)


class RecognisedInput(BaseModel):
    supplier: str = Field(default="", max_length=512)
    inn: str = Field(default="", max_length=16)
    upd_number: str = Field(default="", max_length=128)
    upd_date: str | None = Field(default=None, max_length=32)
    invoice_number: str = Field(default="", max_length=255)
    invoice_date: str | None = Field(default=None, max_length=32)
    prd_number: str | None = Field(default=None, max_length=255)
    prd_date: str | None = Field(default=None, max_length=32)


class UpdLineInput(BaseModel):
    article: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=2048)
    upd_name: str | None = Field(default="", max_length=2048)
    quantity: str | None = Field(default="", max_length=64)
    unit: str | None = Field(default="", max_length=32)
    invoice_quantity: str | None = Field(default="", max_length=64)
    invoice_unit: str | None = Field(default="", max_length=32)
    supplier_code: str | None = Field(default="", max_length=128)


class ProposalInput(BaseModel):
    recognised: RecognisedInput = Field(default_factory=RecognisedInput)
    lines: list[UpdLineInput] = Field(default_factory=list, max_length=ROWS_LIMIT)
    warehouse: Literal["ЦАР", "ЖУК"] | None = None
    supplier: str = Field(default="", max_length=512)


def _proposal(request: Request, payload: ProposalInput, receiving: Callable) -> dict:
    header = header_from(payload.recognised.model_dump())
    rows = rows_from([line.model_dump() for line in payload.lines])
    proposal = propose(
        header, rows, waiting_lines(receiving, _factory(request, "procurement_uow_factory")),
        supplier_book(_factory(request, "purchasing_uow_factory")),
        warehouse=payload.warehouse, hint_supplier=payload.supplier,
    )
    return {"supplier": header.supplier, **proposal_view(proposal, line_view)}


@router.post("/proposal")
async def proposal(payload: ProposalInput, request: Request, _principal: Editor, receiving: ReceivingFactory) -> dict:
    """The recognised УПД laid over the waiting list: which delivery it is, pre-filled."""
    return await run_in_threadpool(_proposal, request, payload, receiving)


class UpdAcceptInput(DeliveryPayload):
    scan_file: str = Field(default="", max_length=255)
    recognised: RecognisedInput = Field(default_factory=RecognisedInput)


def _scan_copy(request: Request, payload: UpdAcceptInput, principal: Principal, now: datetime,
               receiving: Callable) -> tuple[str, UUID | None]:
    """The scanned УПД stored as the receiving document of the delivery (its link on every accepted line)."""
    if not payload.scan_file:
        return payload.shipping_document_url or "", None
    file_id = scan_file_id(payload.scan_file)
    warehouse = _factory(request, "warehouse_uow_factory")
    found = scan_file(warehouse(), file_id) if file_id and warehouse else None
    if found is None:
        raise HTTPException(status_code=422, detail=NO_SCAN)
    file_name, content = found
    extension = file_name[file_name.rfind("."):].lower() if "." in file_name else ".pdf"
    stored = run_receiving(lambda: attach_receiving_file(AttachReceivingFileCommand(
        line_id=payload.lines[0].id, kind="upd", file_name=f"УПД{extension}", content_type="", content=content,
        actor=principal.actor, now=now,
    ), receiving()))
    return receiving_file_url(stored.id), file_id


def _accept(request: Request, payload: UpdAcceptInput, principal: Principal, now: datetime,
            receiving: Callable) -> dict:
    header = header_from(payload.recognised.model_dump())
    with receiving() as uow:
        texts = []
        for item in payload.lines:
            line = uow.receiving.get(item.id)
            if line is not None:
                texts.append(line.supplier)
    aliases = supplier_aliases(texts, header, supplier_book(_factory(request, "purchasing_uow_factory")))
    url, file_id = _scan_copy(request, payload, principal, now, receiving)
    result = run_receiving(lambda: accept_delivery(
        delivery_command(payload, principal, now, shipping_document_url=url or None, supplier_aliases=aliases),
        receiving(),
    ))
    registry = _push(request, payload, file_id, result.lines[0].warehouse if result.lines else "", principal, now)
    return {**delivery_view(result), "upd_file": url, "registry": registry}


def _push(request: Request, payload: UpdAcceptInput, file_id: UUID | None, warehouse: str, principal: Principal,
          now: datetime) -> dict[str, Any] | None:
    """The scan into purchasing «Реестр УПД», as «Поставка» does on «Принять»: never fails the delivery."""
    warehouse_factory = _factory(request, "warehouse_uow_factory")
    purchasing = _factory(request, "purchasing_uow_factory")
    if file_id is None or warehouse_factory is None or purchasing is None:
        return None
    try:
        stored = receipt_upd_registry.stored_file(warehouse_factory, file_id)
        if stored is None:
            return None
        receipt = upd_registry_header(payload.recognised.model_dump(), document_number=payload.document_number.strip(),
                                      document_date=payload.document_date, warehouse=warehouse)
        result = receipt_upd_registry.push_file(warehouse_factory, purchasing, receipt, stored, principal.actor, now)
    except Exception:  # noqa: BLE001 — the registry is purchasing's business; the delivery is accepted
        log.exception("УПД приёмки (файл %s) не попал в реестр УПД", file_id)
        return None
    if result.get("status") == "error":
        log.warning("УПД приёмки (файл %s) не попал в реестр УПД: %s", file_id, result.get("message"))
    return {key: result.get(key) for key in ("status", "document_id", "origin")}


@router.post("/accept")
async def accept(payload: UpdAcceptInput, request: Request, principal: Editor, now: Now,
                 receiving: ReceivingFactory) -> dict:
    """«Провести поставку» of a recognised УПД (see the module doc)."""
    return await run_in_threadpool(_accept, request, payload, principal, now, receiving)
