"""HTTP of the harness drawing of a position (TECHMAP-GRAPHER embedded with the author's permission, see
THIRD_PARTY_NOTICES.md). Routes only: the rules live in `harness/` — the document (`schema`), what may be stored
(`validate`), the use cases with the revision history (`application`; tables in `harness_infrastructure`),
«Ручная сборка» from the drawing (`assembly`), its documents — connection table, cut list, specification
(`documents`) — and the `.techmap-project.zip` exchange (`exchange`).

The concurrency token is the integer `revision` of the drawing (each save is n+1, migration 0090; the history keeps
thinned rows — autosaves of one person within 10 minutes share a row, see harness.application). For one
release a write also takes the old `version` (the hash of the stored bytes, or the revision as text — what this
release hands out under that name), so a browser tab opened before the update still saves."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, projects_edit_principal, projects_view_principal
from app.shared.stored_files import decode_upload, file_response

from .api import optional_references_uow_factory, techcards_uow_factory
from .harness import application as harness, documents as harness_documents
from .harness.assembly import assembly_from_harness
from .harness.exchange import design_from_export, export_zip
from .harness.schema import Expected, HarnessError, decode, normalized
from .harness.snapshot import parse_design
from .harness.validate import ReferenceBook
from .harness_catalog import reference_book
from .harness_catalog_api import reference_tables
from .kitsets import document_json, item_id

router = APIRouter(prefix="/api/techcards/kitsets", tags=["Проекты"])

Factory = Callable[[], Any]
References = Callable[[], ReferenceBook] | None


@contextmanager
def _refusals() -> Iterator[None]:
    """Use-case refusals → HTTP: not found 404, a stale or racing write 409, a drawing that must not be stored 422."""
    try:
        yield
    except harness.HarnessNotFound as error:
        detail = {"message": str(error), "gone": True} if error.gone else str(error)
        raise HTTPException(status_code=404, detail=detail) from error
    except harness.HarnessConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except harness.HarnessInvalid as error:
        raise HTTPException(status_code=422, detail={"message": str(error), "errors": error.errors}) from error
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def references_loader(
    factory: Annotated[Callable[[], ReferencesUnitOfWork] | None, Depends(optional_references_uow_factory)],
) -> References:
    """The ERP books, read only when a write brings in a reference to look up."""
    if factory is None:
        return None
    return lambda: reference_book(reference_tables(factory)[1])


class SavePayload(BaseModel):
    design: dict[str, Any]
    # The revision the edit was made on; None (and no `version`) — the drawing is new (no harness КД at this position).
    revision: int | None = Field(default=None, ge=0)
    # Deprecated, one release: the token before revisions.
    version: str | None = Field(default=None, max_length=64)
    # «Сохранить версию»: this save is a history row of its own that no later autosave folds into.
    checkpoint: bool = False


class CheckpointPayload(BaseModel):
    # The revision on screen; None — fix whatever is stored.
    revision: int | None = Field(default=None, ge=0)


class DerivePayload(BaseModel):
    # The drawing as it is in the editor (unsaved); None — the saved one.
    design: dict[str, Any] | None = None


class UnlinkPayload(BaseModel):
    # The revision on screen; None — unlink whatever is stored (the card does not hold a revision).
    revision: int | None = Field(default=None, ge=0)
    version: str | None = Field(default=None, max_length=64)


class RelinkPayload(BaseModel):
    document_id: UUID


class RestoreRevisionPayload(BaseModel):
    # The revision on screen; None — restore over whatever is stored.
    revision: int | None = Field(default=None, ge=0)


class ImportPayload(BaseModel):
    file_name: str = Field(min_length=1, max_length=400)
    content_b64: str = Field(min_length=1, max_length=42_000_000)
    revision: int | None = Field(default=None, ge=0)
    version: str | None = Field(default=None, max_length=64)


def _saved(result: harness.Saved, created: bool = True) -> dict[str, Any]:
    body = harness.view(result.drawing, result.warnings)
    return {**body, "created": result.created} if created else body


@router.get("/{project}/harness/{index}")
def get_harness(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """Чертёж жгута позиции: документ КД, сам чертёж, номер версии для сохранения. Нет — `document: null`."""
    with _refusals():
        with factory() as uow:
            kitset, index = harness.position(uow, project, index)
            found = harness.current(uow, kitset.project, index)
        return harness.view(found)


@router.put("/{project}/harness/{index}")
def save_harness(
    project: str, index: str, payload: SavePayload,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    references: Annotated[References, Depends(references_loader)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Сохранить чертёж новой версией. Версия — та, что пришла с чтением; чужое сохранение после неё — 409. Чертёж,
    который нельзя хранить (оборванные концы, повторные id, ссылка мимо справочников…), — 422 со списком причин.
    Чертежа ещё нет — создаётся (версия не нужна)."""
    with _refusals():
        return _saved(harness.save(
            factory, project, index, payload.design, Expected.of(payload.revision, payload.version),
            actor=principal.actor, now=now, references=references, checkpoint=payload.checkpoint,
        ))


@router.post("/{project}/harness/{index}/checkpoint")
def checkpoint_harness(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    payload: CheckpointPayload | None = None,
) -> dict:
    """«Сохранить версию»: текущая версия остаётся в истории как есть — следующие автосохранения начнут новую строку.
    Версия с экрана, если передана, должна быть текущей."""
    expected = Expected(revision=payload.revision) if payload is not None else Expected()
    with _refusals():
        held = harness.checkpoint(factory, project, index, expected, actor=principal.actor, now=now)
    return {"revision": held.revision if held else None, "row": harness.revision_json(held) if held else None}


@router.post("/{project}/harness/{index}/derive")
def derive_harness(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
    payload: DerivePayload | None = None,
) -> dict:
    """«Ручная сборка» из чертежа (переданного или сохранённого): блоки по маршруту КД, операции с ключом КД.
    Ничего не сохраняется — экран сливает её со схемой мастера (`rederive`)."""
    unsaved = payload.design if payload is not None else None
    with _refusals():
        with factory() as uow:
            kitset, index = harness.position(uow, project, index)
            assembly = kitset.assembly(index)
            found = None if unsaved is not None else harness.current(uow, kitset.project, index)
        if unsaved is not None:
            content, warnings = normalized(unsaved)
        elif found is None:
            raise harness.HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        else:
            content, warnings = decode(found.content), []
        design, read_warnings = parse_design(content)
        config, notes = assembly_from_harness(
            design, index=index, name=assembly.name if assembly else "", part_qty=assembly.run if assembly else 1,
        )
    return {"build_config": config, "warnings": list(dict.fromkeys([*warnings, *read_warnings, *notes]))}


@router.get("/{project}/harness/{index}/export")
def export_harness(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> Response:
    """Чертёж как `.techmap-project.zip` — открыть в TECHMAP-GRAPHER."""
    with _refusals():
        with factory() as uow:
            kitset, index = harness.position(uow, project, index)
            assembly = kitset.assembly(index)
            found = harness.current(uow, kitset.project, index)
        if found is None:
            raise harness.HarnessNotFound(f"У сборки {index} нет чертежа жгута")
        product = item_id(kitset.project, index)
        archive = export_zip(
            decode(found.content), project=kitset.project, index=index, product=product,
            name=assembly.name if assembly else "", quantity=assembly.run if assembly else 1,
            harness_id=str(found.document.id), created=found.document.uploaded_at, updated=found.document.uploaded_at,
        )
    return file_response(archive, "application/zip", f"{product}.techmap-project.zip")


@router.post("/{project}/harness/{index}/import")
def import_harness(
    project: str, index: str, payload: ImportPayload,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    references: Annotated[References, Depends(references_loader)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Чертёж из экспорта TECHMAP-GRAPHER (`.techmap-project.zip`) — новой версией чертежа позиции (с той же проверкой
    версии и содержимого, что и сохранение; ссылки мимо справочников ERP — только предупреждения). Дальше правда —
    копия в ERP."""
    content = decode_upload(payload.content_b64.split(",", 1)[-1], "Файл передан не в base64")
    with _refusals():
        design = design_from_export(content, payload.file_name, index=index, product=item_id(project, index))
        return _saved(harness.save(
            factory, project, index, design, Expected.of(payload.revision, payload.version),
            actor=principal.actor, now=now, references=references, kind="imported",
            event="techcards.kitset_harness_imported",
        ))


@router.post("/{project}/harness/{index}/unlink")
def unlink_harness(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    payload: UnlinkPayload | None = None,
) -> dict:
    """«Отвязать» чертёж от позиции: строка КД, сам чертёж и его история остаются (отвязанный, с «Вернуть» в
    карточке), позиция его больше не читает — ни редактор, ни «Предложить из КД». Версия с экрана, если передана,
    должна совпасть."""
    expected = Expected.of(payload.revision, payload.version) if payload is not None else Expected()
    with _refusals():
        document = harness.unlink(factory, project, index, expected, actor=principal.actor, now=now)
    return {"document": document_json(document)}


@router.post("/{project}/harness/{index}/restore")
def relink_harness(
    project: str, index: str, payload: RelinkPayload,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """«Вернуть» отвязанный чертёж позиции (с его историей). У позиции уже есть чертёж — 409: два чертежа у одной
    позиции не бывает, сначала отвязывают тот."""
    with _refusals():
        try:
            linked = harness.relink(factory, project, index, payload.document_id, actor=principal.actor, now=now)
        except IntegrityError as error:  # another «Вернуть» linked a drawing first (migration 0089)
            raise harness.HarnessConflict(harness.BUSY.format(index=str(index or "").strip().upper())) from error
        return harness.view(linked)


@router.get("/{project}/harness/{index}/revisions")
def harness_revisions(
    project: str, index: str,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
    limit: Annotated[int, Query(ge=1, le=harness.HISTORY_LIMIT)] = 200,
) -> dict:
    """«История версий» чертежа: кто и когда сохранил, размер, как появилась (сохранение, загрузка из
    TECHMAP-GRAPHER, восстановление), новые сверху."""
    with _refusals():
        drawing, items, total = harness.history(factory, project, index, limit=limit)
    return {"document_id": str(drawing.document.id), "current": drawing.revision, "total": total,
            "revisions": [harness.revision_json(item) for item in items]}


@router.get("/{project}/harness/{index}/revisions/{number}")
def harness_revision(
    project: str, index: str, number: int,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """Одна версия чертежа целиком — как она была сохранена."""
    with _refusals():
        item, design = harness.revision(factory, project, index, number)
    return {**harness.revision_json(item), "design": design}


@router.post("/{project}/harness/{index}/revisions/{number}/restore")
def restore_harness_revision(
    project: str, index: str, number: int,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    references: Annotated[References, Depends(references_loader)],
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    payload: RestoreRevisionPayload | None = None,
) -> dict:
    """Восстановить версию N: она становится чертежом новой версией (история не меняется). Версия с экрана, если
    передана, должна быть текущей."""
    expected = Expected(revision=payload.revision) if payload is not None else Expected()
    with _refusals():
        return _saved(harness.restore_revision(
            factory, project, index, number, expected, actor=principal.actor, now=now, references=references,
        ), created=False)


# ── Documents of the drawing: connection table, cut list, specification (harness/documents.py) ─────────────────────

DocumentKind = Literal["connections", "cut", "bom"]
Harnesses = Annotated[int, Query(ge=1, le=100_000, description="На сколько жгутов считать расход")]


@router.get("/{project}/harness/{index}/documents")
def harness_documents_saved(
    project: str, index: str, kind: DocumentKind,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
    quantity: Harnesses = 1, format: Literal["json", "xlsx"] = "json",
) -> Any:
    """Документ сохранённого чертежа: «Таблица соединений», «Карта резки» или «Спецификация» на `quantity` жгутов;
    `format=xlsx` — файл для Excel (как «Excel» таблиц ERP). Считается каждый раз, ничего не хранится."""
    with _refusals():
        product, content = harness.saved_design(factory, project, index)
    if format != "xlsx":
        return harness_documents.documents(kind, content, quantity)
    title, headers, rows = harness_documents.table(kind, content, quantity)
    return file_response(harness_documents.excel_text(headers, rows), "application/vnd.ms-excel",
                         f"{product} — {title}.xls")


@router.post("/{project}/harness/{index}/documents")
def harness_documents_live(
    project: str, index: str, payload: DerivePayload, kind: DocumentKind,
    factory: Annotated[Factory, Depends(techcards_uow_factory)],
    _principal: Annotated[Principal, Depends(projects_view_principal)],
    quantity: Harnesses = 1,
) -> dict:
    """Тот же документ по чертежу из редактора (ещё не сохранённому) — окна редактора видят правку сразу, не дожидаясь
    автосохранения. Без чертежа в запросе — сохранённый. Ничего не сохраняется."""
    with _refusals():
        if payload.design is None:
            content = harness.saved_design(factory, project, index)[1]
        else:
            harness.product_of(factory, project, index)
            content = normalized(payload.design)[0]
    return harness_documents.documents(kind, content, quantity)
