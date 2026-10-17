"""«Чертёж → схема сборки» (owner 05.10.2026): the API an agent drafts assemblies through.

Claude Code (an API token with the scope `assembly`) and later a server-side agent upload the drawing of a project
position into its КД, check a draft «Ручная сборка» config with the real builder (`dry_run`), store it as a draft and
read the finished examples (draft, final, lesson) back as a corpus. A person with the «Генератор техкарт» section does
the same from a cookie session. Nothing here writes a project techcard: the master builds the card from «Ручная
сборка» naming the draft (`draft_id` on POST /api/techcards/projects/{id}/build, a cookie session only), and that
build finalises the draft."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal
from app.shared.stored_files import decode_upload
from app.workflows import project_kitset as flow

# An API token with the scope `assembly`, or a person whose «Генератор техкарт» is open.
from .api import assembly_principal, techcards_uow_factory
from .application import UnitOfWork
from .assembly_drafts import (
    AssemblyDraft,
    AssemblyDraftError,
    AssemblyDraftNotFound,
    SubmitDraftCommand,
    corpus,
    get_draft,
    list_drafts,
    set_lesson,
    submit_draft,
)
from .drawing_merge import DrawingMergeError, merge_pdfs
from .kitsets import KitsetNotFound, KitsetValidationError

router = APIRouter(prefix="/api/techcards/assembly", tags=["Схемы сборки"])

MAX_DRAWING_BYTES = 20 * 1024 * 1024
# A drawing is a PDF or a picture of the sheet; the extension names the type and the first bytes must agree.
DRAWING_TYPES = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}


class DrawingPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=64)
    file_name: str = Field(min_length=1, max_length=400)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class DrawingPart(BaseModel):
    file_name: str = Field(min_length=1, max_length=400)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class MergedDrawingPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=64)
    # Имя сшитого файла; пусто — «<первый файл без расширения>+<сколько ещё>.pdf».
    file_name: str = Field(default="", max_length=400)
    files: list[DrawingPart] = Field(min_length=2, max_length=20)


class DraftPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=64)
    drawing_document_id: UUID | None = None
    source: str = Field(min_length=1, max_length=32)
    build_config: dict[str, Any]
    note: str = Field(default="", max_length=20_000)


class LessonPayload(BaseModel):
    lesson: str = Field(max_length=20_000)


def _run(operation):
    try:
        return operation()
    except (AssemblyDraftNotFound, KitsetNotFound) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (AssemblyDraftError, KitsetValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _document_url(project: str, document_id: UUID | None) -> str | None:
    if document_id is None:
        return None
    return f"/api/techcards/kitsets/{quote(project, safe='')}/documents/{document_id}"


def _draft_json(draft: AssemblyDraft, *, full: bool = True) -> dict:
    result = {
        "id": str(draft.id), "project": draft.project, "item_index": draft.item_index,
        "drawing_document_id": str(draft.drawing_document_id) if draft.drawing_document_id else None,
        "drawing_url": _document_url(draft.project, draft.drawing_document_id),
        "source": draft.source, "author": draft.author, "status": draft.status,
        "final_revision": draft.final_revision, "lesson": draft.lesson, "note": draft.note,
        "created_at": draft.created_at.isoformat(), "updated_at": draft.updated_at.isoformat(),
    }
    if full:
        result.update(draft_config=draft.draft_config, final_config=draft.final_config)
    return result


def _drawing_type(file_name: str, content: bytes) -> str:
    suffix = "." + file_name.rsplit(".", 1)[-1].strip().lower() if "." in file_name else ""
    known = DRAWING_TYPES.get(suffix)
    if known is None:
        raise HTTPException(status_code=422, detail="Чертёж принимается только в PDF, JPG или PNG")
    content_type, signatures = known
    if not content.startswith(signatures):
        raise HTTPException(status_code=422, detail=f"Файл «{file_name}» не похож на {suffix[1:].upper()}")
    return content_type


@router.post("/drawings", status_code=201)
def upload_drawing(
    payload: DrawingPayload,
    principal: Annotated[Principal, Depends(assembly_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """Чертёж позиции — в КД её сборки (как «КД» в карточке проекта): PDF, JPG или PNG до 20 МБ."""
    content = decode_upload(payload.content_base64.split(",", 1)[-1], "Файл передан не в base64")
    if not content:
        raise HTTPException(status_code=422, detail="Файл не передан")
    if len(content) > MAX_DRAWING_BYTES:
        raise HTTPException(status_code=422, detail="Чертёж больше 20 МБ")
    content_type = _drawing_type(payload.file_name.strip(), content)
    document = _run(lambda: flow.attach_document(
        payload.project, payload.item_index, payload.file_name, content_type, content, principal.actor, now,
        uow_factory,
    ))
    return {
        "id": str(document.id), "project": document.project, "item_index": document.item_index,
        "name": document.file_name, "size": document.size, "content_type": document.content_type,
        "url": _document_url(document.project, document.id),
    }


def _merged_name(first: str, others: int) -> str:
    stem = first.strip().rsplit(".", 1)[0] if "." in first else first.strip()
    return f"{stem}+{others}.pdf"


@router.post("/drawings/merge", status_code=201)
def upload_merged_drawing(
    payload: MergedDrawingPayload,
    principal: Annotated[Principal, Depends(assembly_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """Несколько PDF в заданном порядке — один файл КД позиции (как одиночный чертёж). Всё или ничего: пока
    любая часть не прочитана, в КД ничего не попадает."""
    parts: list[tuple[str, bytes]] = []
    for part in payload.files:
        name = part.file_name.strip()
        content = decode_upload(part.content_base64.split(",", 1)[-1], f"Файл «{name}» передан не в base64")
        if not content:
            raise HTTPException(status_code=422, detail=f"Файл «{name}» пуст")
        if len(content) > MAX_DRAWING_BYTES:
            raise HTTPException(status_code=422, detail=f"Файл «{name}» больше 20 МБ")
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=422, detail=f"Сшиваются только PDF: «{name}» — другой формат")
        parts.append((name, content))
    try:
        merged = merge_pdfs(parts)
    except DrawingMergeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if len(merged.content) > MAX_DRAWING_BYTES:
        raise HTTPException(status_code=422, detail="Сшитый чертёж больше 20 МБ")
    file_name = payload.file_name.strip() or _merged_name(parts[0][0], len(parts) - 1)
    if not file_name.lower().endswith(".pdf"):
        file_name += ".pdf"
    parts_note = "; ".join(f"{name} — {count} стр." for name, count in merged.pages)
    document = _run(lambda: flow.attach_document(
        payload.project, payload.item_index, file_name, "application/pdf", merged.content, principal.actor, now,
        uow_factory, audit_extra={"parts": parts_note, "pages": str(sum(count for _, count in merged.pages))},
    ))
    return {
        "id": str(document.id), "project": document.project, "item_index": document.item_index,
        "name": document.file_name, "size": document.size, "content_type": document.content_type,
        "url": _document_url(document.project, document.id),
        "pages": [{"file_name": name, "pages": count} for name, count in merged.pages],
    }


@router.post("/drafts")
def submit_assembly_draft(
    payload: DraftPayload,
    response: Response,
    principal: Annotated[Principal, Depends(assembly_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    dry_run: bool = Query(default=False),
) -> dict:
    """Проверить черновик «Ручной сборки» настоящим сборщиком ЭТК; без dry_run — сохранить его у позиции.
    Техкарту проекта не трогает."""
    draft, checked = _run(lambda: submit_draft(SubmitDraftCommand(
        project=payload.project, item_index=payload.item_index, drawing_document_id=payload.drawing_document_id,
        source=payload.source.strip(), build_config=payload.build_config, note=payload.note, dry_run=dry_run,
        actor=principal.actor, now=now,
    ), uow_factory))
    if draft is not None:
        response.status_code = 201
    return {
        "dry_run": dry_run, "draft": _draft_json(draft) if draft else None,
        "warnings": list(checked.warnings), "operations": checked.operations, "route": checked.route,
    }


@router.get("/drafts", dependencies=[Depends(assembly_principal)])
def assembly_drafts(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    project: str = Query(default="", max_length=255),
    item_index: str = Query(default="", max_length=64),
) -> dict:
    return {"items": [_draft_json(draft, full=False) for draft in list_drafts(project, item_index, uow_factory)]}


@router.get("/drafts/{draft_id}", dependencies=[Depends(assembly_principal)])
def assembly_draft(
    draft_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return _draft_json(_run(lambda: get_draft(draft_id, uow_factory)))


@router.put("/drafts/{draft_id}/lesson")
def assembly_draft_lesson(
    draft_id: UUID,
    payload: LessonPayload,
    principal: Annotated[Principal, Depends(assembly_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """«Что поправил и почему» — к паре черновик/итог."""
    return _draft_json(_run(lambda: set_lesson(draft_id, payload.lesson, principal.actor, now, uow_factory)))


@router.get("/corpus", dependencies=[Depends(assembly_principal)])
def assembly_corpus(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    project: str = Query(default="", max_length=255),
    q: str = Query(default="", max_length=255),
) -> dict:
    """Образцы корпуса — собранные черновики (черновик, итог, урок), новые первыми, не больше 50."""
    return {"items": [_draft_json(draft) for draft in corpus(project, q, uow_factory)]}
