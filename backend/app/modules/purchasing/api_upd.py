"""«Реестр УПД»: /api/purchasing/upd."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.shared.stored_files import decode_upload, file_response

from . import upd_registry as repo
from .deps import purchasing_uow_factory
from .upd_checks import STATUSES

router = APIRouter(prefix="/api/purchasing/upd", tags=["purchasing"])

Uow = Annotated[Callable, Depends(purchasing_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Editor = Annotated[Principal, Depends(procurement_edit_principal)]
# A Drive file id or «edo-<sha1>»: letters, digits, «_» and «-» only.
DocumentId = Annotated[str, Path(pattern=r"^[\w-]{1,128}$")]

_BUYER = re.compile(r"^(?:ours|other|unknown|[0-9]{10}|[0-9]{12})$")
# 20 MB of ZIP as base64 (the ceiling of edo_xml.LIMITS), plus the JSON around it.
_BASE64_LIMIT = (20 * 1024 * 1024 * 4) // 3 + 8


@contextmanager
def _errors():
    try:
        yield
    except repo.UpdError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from None


class HeaderIn(BaseModel):
    number: str = ""
    date: str | None = None
    seller_name: str = ""
    seller_inn: str = ""
    seller_kpp: str = ""
    buyer_inn: str = ""
    total_net: int | None = None
    total_vat: int | None = None
    total_gross: int | None = None


class LineIn(BaseModel):
    seq: int
    name: str = ""
    article: str = ""
    unit: str = ""
    # Decimals as text («250.5»); numbers are taken too. Checked in upd_registry.clean_lines with words for the buyer.
    quantity: str | int | float | None = None
    price: str | int | float | None = None
    net: int | None = None
    vat_rate: str = ""
    vat: int | None = None
    gross: int | None = None
    is_service: bool = False


class ManualIn(BaseModel):
    header: HeaderIn
    lines: list[LineIn] = Field(max_length=2000)
    # What the card was opened on: a newer parse or another saved version refuses the save.
    parser_updated: str
    manual_saved_at: str | None = None


class VerifiedIn(BaseModel):
    verified: bool


class SupplierIn(BaseModel):
    supplier_id: UUID | None = None


class ProjectIn(BaseModel):
    project_reference: str | None = Field(default=None, max_length=255)


class UploadFile(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=_BASE64_LIMIT)


class EdoImportIn(BaseModel):
    files: list[UploadFile] = Field(min_length=1, max_length=20)
    dry_run: bool = True


class SellersImportIn(BaseModel):
    dry_run: bool = True
    inns: list[str] | None = Field(default=None, max_length=1000)


@router.get("")
def list_documents(
    _: Viewer, make_uow: Uow, q: Annotated[str, Query(max_length=100)] = "", status: str | None = None,
    origin: str | None = None, supplier_id: UUID | None = None, no_supplier: bool = False,
    date_from: date | None = None, date_to: date | None = None, buyer: str | None = None, review: bool = False,
    duplicates: bool = False, page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail="Неизвестный статус документа")
    if origin is not None and origin not in ("edo", "google"):
        raise HTTPException(status_code=422, detail="Источник: edo или google")
    if buyer is not None and not _BUYER.match(buyer):
        raise HTTPException(status_code=422, detail="Покупатель: ours, other, unknown или ИНН")
    wanted = repo.ListFilter(
        q=q, status=status, origin=origin, supplier_id=supplier_id, no_supplier=no_supplier, date_from=date_from,
        date_to=date_to, buyer=buyer, review=review, duplicates=duplicates, page=page,
    )
    uow = make_uow()
    with uow:
        return repo.list_documents(uow.session, wanted)


@router.get("/sellers")
def sellers(_: Viewer, make_uow: Uow) -> dict:
    """«Продавцы без поставщика»: sellers of documents in force with a valid INN that the directory does not have."""
    uow = make_uow()
    with uow:
        return repo.sellers_without_supplier(uow.session)


@router.post("/sellers/import")
def import_sellers(payload: SellersImportIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    """«Завести поставщиков»: the dry run reports what apply would do and writes nothing."""
    uow = make_uow()
    with uow, _errors():
        result = repo.import_sellers(uow, payload.inns, principal.actor, now)
        if payload.dry_run:
            uow.session.rollback()
        else:
            uow.commit()
        return {**result, "dry_run": payload.dry_run}


@router.post("/import-edo")
def import_edo(payload: EdoImportIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    """«Загрузить из ЭДО»: XML files or Diadoc ZIPs; the dry run reports what apply would do and writes nothing."""
    uploads: list[tuple[str, bytes]] = []
    for item in payload.files:
        content = decode_upload(item.content_base64, f"Файл «{item.file_name}» повреждён")
        if not content:
            raise HTTPException(status_code=422, detail=f"Файл «{item.file_name}» пустой")
        uploads.append((item.file_name, content))
    uow = make_uow()
    with uow, _errors():
        result = repo.import_edo(uow, uploads, principal.actor, now)
        if payload.dry_run:
            uow.session.rollback()
        else:
            uow.commit()
        return {**result, "dry_run": payload.dry_run}


@router.get("/{document_id}")
def document(document_id: DocumentId, _: Viewer, make_uow: Uow) -> dict:
    uow = make_uow()
    with uow, _errors():
        return repo.document_detail(uow.session, document_id)


@router.get("/{document_id}/file")
def document_file(document_id: DocumentId, _: Viewer, make_uow: Uow) -> Response:
    """The original: a PDF opens in the tab, an EDO XML downloads (stored_files serving policy)."""
    uow = make_uow()
    with uow, _errors():
        content, name = repo.document_file(uow.session, document_id)
    return file_response(content, name)


def _write(make_uow: Callable, document_id: str, change: Callable[[Any], Any]) -> dict:
    uow = make_uow()
    with uow, _errors():
        change(uow)
        uow.commit()
        return repo.document_detail(uow.session, document_id)


@router.put("/{document_id}/manual")
def save_manual(document_id: DocumentId, payload: ManualIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    return _write(make_uow, document_id, lambda uow: repo.save_manual(
        uow, document_id, payload.header.model_dump(), [line.model_dump() for line in payload.lines],
        payload.parser_updated, payload.manual_saved_at, principal.actor, now,
    ))


@router.delete("/{document_id}/manual")
def drop_manual(document_id: DocumentId, principal: Editor, make_uow: Uow, now: Now) -> dict:
    """«Вернуть версию разборщика»: the buyer's header and lines are dropped."""
    return _write(make_uow, document_id, lambda uow: repo.drop_manual(uow, document_id, principal.actor, now))


@router.put("/{document_id}/verified")
def set_verified(document_id: DocumentId, payload: VerifiedIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    return _write(make_uow, document_id, lambda uow: repo.set_verified(
        uow, document_id, payload.verified, principal.actor, now,
    ))


@router.put("/{document_id}/supplier")
def set_supplier(document_id: DocumentId, payload: SupplierIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    """The supplier chosen by hand; null hands the document back to the link by the seller's INN."""
    return _write(make_uow, document_id, lambda uow: repo.set_supplier(
        uow, document_id, payload.supplier_id, principal.actor, now,
    ))


@router.put("/{document_id}/project")
def set_project(document_id: DocumentId, payload: ProjectIn, principal: Editor, make_uow: Uow, now: Now) -> dict:
    return _write(make_uow, document_id, lambda uow: repo.set_project(
        uow, document_id, payload.project_reference, principal.actor, now,
    ))
