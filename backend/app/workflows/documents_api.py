"""«Документы с ознакомлением» по HTTP (identity/documents.py): «Кадры» загружают PDF и видят лист ознакомления,
каждый сотрудник — свои документы и подпись «Ознакомлен».

Every signed-in employee reads their own list; who may upload, archive or see the sheet is the `hr` edit rule checked
by the use case (the same as the leave route). An API token never reaches these routes (`authenticated_principal`
refuses it). The PDF travels as base64 in JSON, as every other upload of the ERP (app/shared/stored_files.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.modules.identity.api import _client_key as client_key  # the login's counters: a signature is a login check
from app.modules.identity.application import AuthenticationRateLimited
from app.modules.identity.documents import (
    MAX_PDF_BYTES,
    DocumentConflict,
    DocumentError,
    DocumentForbidden,
    DocumentNotFound,
    acknowledge,
    create_document,
    delete_version,
    documents_overview,
    pending_count,
    set_archived,
    signature_sheet,
    upload_version,
    version_file,
)
from app.modules.identity.leave import LeaveError, LeaveForbidden
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.stored_files import decode_upload, file_response

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Room for a little more than the 20 МБ limit, so an oversize PDF reaches the use case and gets its Russian message.
BASE64_LIMIT = (MAX_PDF_BYTES * 4) // 3 + 64 * 1024
DAMAGED = "Файл повреждён — загрузите PDF ещё раз."

T = TypeVar("T")


class FileInput(BaseModel):
    file_name: str = Field(default="", max_length=1024)
    content_base64: str = Field(min_length=1, max_length=BASE64_LIMIT)


class DocumentInput(FileInput):
    title: str = Field(min_length=1, max_length=1024)
    kind: str = Field(min_length=1, max_length=32)
    scope: str = Field(min_length=1, max_length=16)
    scope_value: str = Field(default="", max_length=1024)


class ArchiveInput(BaseModel):
    archived: bool


class AckInput(BaseModel):
    secret: str = Field(min_length=1, max_length=128)


def _identity(request: Request) -> Callable[[], object]:
    factory = getattr(request.app.state, "identity_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="Identity ERP не настроен")
    return factory


def _run(action: Callable[[], T]) -> T:
    try:
        return action()
    except DocumentNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (DocumentForbidden, LeaveForbidden) as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except DocumentConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Документ уже изменился — обновите страницу.") from error
    except AuthenticationRateLimited as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except (DocumentError, LeaveError) as error:  # LeaveError: the PIN / password refused by the shared signer check
        raise HTTPException(status_code=422, detail=str(error)) from error


def _overview(request: Request, principal: Principal) -> dict:
    result = documents_overview(principal.actor.id, _identity(request)())
    return {"own": result.own, "journal": result.journal, "hr": result.hr}


@router.get("")
def overview(principal: Annotated[Principal, Depends(authenticated_principal)], request: Request) -> dict:
    """The person's documents in force (unsigned first) and, with «Кадры» edit, every document with its count."""
    return _overview(request, principal)


@router.get("/pending")
def pending(principal: Annotated[Principal, Depends(authenticated_principal)], request: Request) -> dict:
    """The badge: how many documents wait for this person's «Ознакомлен»."""
    return {"count": pending_count(principal.actor.id, _identity(request)())}


@router.post("", status_code=201)
def create(
    payload: DocumentInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    content = decode_upload(payload.content_base64, DAMAGED)
    document = _run(lambda: create_document(
        principal.actor, payload.title, payload.kind, payload.scope, payload.scope_value, payload.file_name, content,
        now, _identity(request)(),
    ))
    return _run(lambda: signature_sheet(document.id, principal.actor.id, _identity(request)()))


@router.get("/{document_id}/sheet")
def sheet(
    document_id: UUID,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    version_no: int | None = None,
) -> dict:
    """«Лист ознакомления» of a version (the current one by default): who signed, who has not."""
    return _run(lambda: signature_sheet(document_id, principal.actor.id, _identity(request)(), version_no))


@router.post("/{document_id}/versions", status_code=201)
def new_version(
    document_id: UUID,
    payload: FileInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    content = decode_upload(payload.content_base64, DAMAGED)
    _run(lambda: upload_version(document_id, principal.actor, payload.file_name, content, now, _identity(request)()))
    return _run(lambda: signature_sheet(document_id, principal.actor.id, _identity(request)()))


@router.post("/{document_id}/archive")
def archive(
    document_id: UUID,
    payload: ArchiveInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    _run(lambda: set_archived(document_id, principal.actor, payload.archived, now, _identity(request)()))
    return _run(lambda: signature_sheet(document_id, principal.actor.id, _identity(request)()))


@router.delete("/versions/{version_id}")
def remove_version(
    version_id: UUID,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """A version uploaded by mistake — the latest one, while nobody has signed it."""
    left = _run(lambda: delete_version(version_id, principal.actor, now, _identity(request)()))
    if left is None:
        return {"deleted_document": True, "document": None}
    return {"deleted_document": False,
            "document": _run(lambda: signature_sheet(left.id, principal.actor.id, _identity(request)()))}


@router.get("/versions/{version_id}/file")
def pdf(
    version_id: UUID,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
) -> Response:
    name, content = _run(lambda: version_file(version_id, principal.actor.id, _identity(request)()))
    return file_response(content, name)


@router.post("/versions/{version_id}/acknowledge")
def sign(
    version_id: UUID,
    payload: AckInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """«Ознакомлен»: the PIN or the password typed again (a mismatch counts as a failed login)."""
    _run(lambda: acknowledge(
        version_id, principal.actor, payload.secret, now, _identity(request)(), client_key=client_key(request),
    ))
    return _overview(request, principal)
