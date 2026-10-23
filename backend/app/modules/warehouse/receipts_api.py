"""«Приход на склад» HTTP contract (owner 23.10.2026). Writes take warehouse edit (as «Внести позиции»), reads view.

- `POST /api/stock/receipts` `{warehouse, supplier, invoice_number, invoice_date, upd_number, upd_date, purpose,
  scan_file}` → the receipt; `PUT /api/stock/receipts/{id}` the same + `version` → the receipt (409 — stale version);
  `GET /api/stock/receipts?limit=20` → `{receipts}` newest first; `GET /api/stock/receipts/{id}` → `{receipt, lots}`.
  A receipt: `{id, version, warehouse, supplier, invoice_number, invoice_date, upd_number, upd_date, purpose,
  scan_file, created_at, created_by, lots_count}`; dates ГГГГ-ММ-ДД or null.
- `POST /api/stock/receipts/{id}/accept` — one line → `{lot, label_job_id, label_error}`; 422 — the line's error,
  409 — the article is awaited in «Приёмка» (re-send with `confirm_expected: true`) or the receipt is unknown 404.
- `POST /api/stock/receipts/scan` (multipart `file`: PDF, JPG, PNG ≤ 20 MB) → `{scan_file, recognised: {supplier, inn,
  upd_number, upd_date, invoice_number, invoice_date}, lines: [{article, name, quantity, unit}], note}`;
  `GET /api/stock/receipts/files/{id}` serves the stored scan. The scanner of the warehouse PC — `scan_jobs_api.py`."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.file_types import sniffed_type
from app.shared.http_auth import Principal, warehouse_edit_principal, warehouse_view_principal
from app.shared.stored_files import file_response
from app.workflows.stock_receipt import expected_in_receiving, upd_document

from .api import StockItemInput, _result, receipt_brief, warehouse_uow_factory
from .application import UnitOfWork
from .domain import ReceiptConflict, ReceiptHeader, WarehouseReceipt, WarehouseValidationError, receipt_date
from .receipt_scan import RecognisedUpd, parse_upd_text, recognise_upd
from .receipts_application import (
    SCAN_FILE_LIMIT,
    AcceptLineCommand,
    ExpectedInReceiving,
    accept_receipt_line,
    create_receipt,
    receipt_with_lots,
    recent_receipts,
    scan_file,
    store_scan,
    update_receipt,
)
router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]


class ReceiptPayload(BaseModel):
    """Texts and dates are checked by the receipt itself, so a bad value answers with a Russian `detail`."""

    warehouse: str = Field(default="", max_length=64)
    supplier: str = Field(default="", max_length=1024)
    invoice_number: str = Field(default="", max_length=1024)
    invoice_date: str | None = Field(default=None, max_length=64)
    upd_number: str = Field(default="", max_length=1024)
    upd_date: str | None = Field(default=None, max_length=64)
    purpose: str = Field(default="", max_length=1024)
    scan_file: str = Field(default="", max_length=1024)

    def header(self) -> ReceiptHeader:
        return ReceiptHeader(
            warehouse=self.warehouse, supplier=self.supplier, invoice_number=self.invoice_number,
            invoice_date=receipt_date(self.invoice_date, "Дата счёта"), upd_number=self.upd_number,
            upd_date=receipt_date(self.upd_date, "Дата УПД"), purpose=self.purpose, scan_file=self.scan_file,
        )


class ReceiptUpdatePayload(ReceiptPayload):
    version: int = Field(ge=1)


class AcceptPayload(StockItemInput):
    """A delivery line: the quick-entry row (its warehouse, when sent, is ignored) + the bags and the guard answer."""

    labels: int = Field(default=1)
    confirm_expected: bool = False


def receipt_dict(receipt: WarehouseReceipt, lots_count: int) -> dict:
    return {
        **receipt_brief(receipt),
        "version": receipt.version,
        "warehouse": receipt.warehouse,
        "purpose": receipt.purpose,
        "scan_file": receipt.scan_file,
        "created_at": receipt.created_at.isoformat(),
        "created_by": receipt.created_by,
        "lots_count": lots_count,
    }


def _lot(lot, receipt: WarehouseReceipt | None) -> dict:
    return {**_result(lot), "receipt": receipt_brief(receipt)}


def _checked(operation):
    try:
        return operation()
    except WarehouseValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ReceiptConflict, ExpectedInReceiving) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: повторите") from error


@router.get("/receipts")
def receipts(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return {"receipts": [receipt_dict(receipt, count) for receipt, count in recent_receipts(uow_factory(), limit)]}


@router.post("/receipts")
def new_receipt(
    payload: ReceiptPayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    receipt = _checked(lambda: create_receipt(uow_factory(), payload.header(), principal.actor, now))
    return receipt_dict(receipt, 0)


@router.get("/receipts/files/{file_id}")
def receipt_scan_file(
    file_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
) -> Response:
    found = scan_file(uow_factory(), file_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    name, content = found
    return file_response(content, name)


@router.get("/receipts/{receipt_id}")
def receipt(
    receipt_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    uow_factory: UowFactory,
) -> dict:
    found, lots = _checked(lambda: receipt_with_lots(uow_factory(), receipt_id))
    return {"receipt": receipt_dict(found, len(lots)), "lots": [_lot(lot, found) for lot in lots]}


@router.put("/receipts/{receipt_id}")
def edit_receipt(
    receipt_id: UUID,
    payload: ReceiptUpdatePayload,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    updated, count = _checked(lambda: update_receipt(
        uow_factory(), receipt_id, payload.version, payload.header(), principal.actor, now,
    ))
    return receipt_dict(updated, count)


def _factory(request: Request, name: str):
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


@router.post("/receipts/{receipt_id}/accept")
def accept_line(
    receipt_id: UUID,
    payload: AcceptPayload,
    request: Request,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """«Принять»: the line goes on stock as one lot, `labels` copies of its label go to the print queue."""
    receiving, procurement = _factory(request, "receiving_uow_factory"), _factory(request, "procurement_uow_factory")

    def expected(receipt: WarehouseReceipt, article: str):
        return expected_in_receiving(
            receiving, procurement, supplier=receipt.supplier, invoice_number=receipt.invoice_number, article=article,
        )

    accepted = _checked(lambda: accept_receipt_line(
        AcceptLineCommand(
            receipt_id=receipt_id, item=payload.manual_item(), labels=payload.labels,
            confirm_expected=payload.confirm_expected, actor=principal.actor, now=now,
        ),
        uow_factory(),
        expected,
    ))
    return {
        "lot": _lot(accepted.lot, accepted.receipt),
        "label_job_id": str(accepted.label_job_id) if accepted.label_job_id else None,
        "label_error": accepted.label_error,
    }


_BOUNDARY = re.compile(r'boundary="?([^";]+)"?', re.I)
_PART_NAME = re.compile(rb'\bname="([^"]*)"', re.I)
_PART_FILE = re.compile(rb'\bfilename="([^"]*)"', re.I)


def multipart_file(content_type: str, body: bytes, field: str = "file") -> tuple[str, bytes] | None:
    """The `field` part of a multipart/form-data body: (file name, bytes). The server has no multipart library, and one
    file part is all this route reads."""
    boundary = _BOUNDARY.search(content_type or "")
    if not boundary or "multipart/form-data" not in (content_type or "").lower():
        return None
    for part in body.split(b"--" + boundary[1].encode("latin-1"))[1:]:
        if part.startswith(b"--"):
            break
        head, separator, content = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        name = _PART_NAME.search(head)
        if name is None or name[1].decode("utf-8", "replace") != field:
            continue
        file_name = _PART_FILE.search(head)
        content = content[:-2] if content.endswith(b"\r\n") else content
        return (file_name[1].decode("utf-8", "replace") if file_name else ""), content
    return None


def _recognised(found: RecognisedUpd) -> dict:
    return {
        "supplier": found.supplier, "inn": found.inn, "upd_number": found.upd_number,
        "upd_date": found.upd_date.isoformat() if found.upd_date else None,
        "invoice_number": found.invoice_number,
        "invoice_date": found.invoice_date.isoformat() if found.invoice_date else None,
    }


def scan_answer(purchasing_factory, link: str, content: bytes, kind: str | None) -> dict:
    """What «Отсканировать УПД» answers for a stored scan: `{scan_file, recognised, lines, note}` (also the `result`
    of a done scanner job). Blocking: OCR may take seconds — call it off the event loop."""
    found, note = recognise_upd(content, kind)
    lines: list[dict] = []
    document = upd_document(
        purchasing_factory, number=found.upd_number, day=found.upd_date, inn=found.inn, seller=found.supplier,
    ) if found.upd_number else None
    if document is not None:
        basis = parse_upd_text(document["basis"])
        found = RecognisedUpd(
            supplier=document["supplier"] or found.supplier,
            inn=document["inn"] or found.inn,
            upd_number=document["upd_number"] or found.upd_number,
            upd_date=document["upd_date"] or found.upd_date,
            invoice_number=found.invoice_number or basis.invoice_number,
            invoice_date=found.invoice_date or basis.invoice_date,
        )
        lines = document["lines"]
        note = "Найден УПД в документах закупки — шапка и строки взяты из него"
    return {"scan_file": link, "recognised": _recognised(found), "lines": lines, "note": note}


def _scan(request: Request, file_name: str, content: bytes, principal: Principal, now: datetime, uow_factory) -> dict:
    link, _stored_kind = _checked(lambda: store_scan(uow_factory(), file_name, content, principal.actor, now))
    # Read what came (a picture straight to OCR), though a picture is stored as PDF.
    return scan_answer(_factory(request, "purchasing_uow_factory"), link, content, sniffed_type(content))


@router.post("/receipts/scan")
async def scan_receipt_document(
    request: Request,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """«Отсканировать УПД» from a file: the file is stored, its text layer read (no text — OCR); a УПД purchasing
    already holds under the same number (date ±3 days or the same seller) gives the header and the lines."""
    body = await request.body()
    if len(body) > SCAN_FILE_LIMIT + 64 * 1024:
        raise HTTPException(status_code=422, detail="Скан УПД больше 20 МБ")
    upload = multipart_file(request.headers.get("content-type", ""), body)
    if upload is None:
        raise HTTPException(status_code=422, detail="Файл не передан: нужен multipart с полем «file»")
    file_name, content = upload
    return await run_in_threadpool(_scan, request, file_name, content, principal, now, uow_factory)
