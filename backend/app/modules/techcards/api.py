"""Ручки генератора: описание сборки → маршрут.

Два входа вместо одного флага «режим»: у жгута и коаксиала разные поля.
У жгута разъём описывается числом контактов, у коаксиала — типом посадки
пина и крышкой; у жгута несколько проводов, у коаксиала один кабель с
экраном. Свести их в одну схему значило бы получить набор полей,
половина которых всегда пустая, и валидацию «если режим коакс, то
обязательно…» вместо типов, которые проверяются сами.
"""

from collections.abc import Callable, Sequence
import base64
import binascii
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.file_types import sniffed_type
from app.shared.stored_files import decode_upload, file_response
from app.shared.http_auth import (
    TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, refuse_api_tokens, tcgen_principal, techcards_edit_principal, techcards_view_principal, tctemplates_admin_principal,
    tctemplates_edit_principal, tctemplates_view_principal, section_allowed,
)
from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab
from app.workflows.etk_lots import IssuedKitLots
from app.workflows.new_operation import NewOperationUnitOfWork, create_operation
from app.workflows.techcard_build import with_technology
from app.workflows.warehouse_article import infer_article_identity

from .application import (
    ChangeProjectTechcardCommand,
    AutoBuildProjectTechcardCommand,
    CreateProjectTechcardCommand,
    EditOperationRecordCommand,
    EditTechcardOperationCommand,
    RebuildProjectTechcardCommand,
    OperationLibraryEditedError,
    SaveTechcardRouteCommand,
    ReplaceOperationLibraryCommand,
    UnitOfWork,
    create_project_techcard,
    auto_build_project_techcard,
    edit_operation_record,
    new_operation,
    discard_project_techcard_draft,
    edit_project_operation,
    issue_project_techcard,
    rebuild_project_techcard,
    replace_operation_library,
    save_project_route,
)

from .domain.coax import КоаксСборка
from .domain.coax import Сторона as КоаксСторона
from .domain.coax import построить_коакс
from .domain.harness import Провод, Разъём, Сборка, построить
from .etk_read import IssuedLots, card_views
from .project_cards import OperationSketch, ProjectTechcard, TechcardRevision, TechcardValidationError
from .project_cards import operation_key as etk_operation_key
from .operation_cards import OperationCardTemplate
from .grapher_import import GrapherComponent, GrapherImportError, read_export
from .harness import SheetRow
from .harness.rules import to_decimal
from .connector_housings import attach_housings, connector_catalog, housing_of
from .connector_series import RECORD_ID_KEY, connector_series, find_in_series
from .assembly_schema import generator_to_assembly
from .scheme_corrections import list_scheme_corrections
from .assembly_drafts import AssemblyDraftError, AssemblyDraftNotFound, draft_for_card, finalise_draft
from .generator_drafts import (
    GeneratorDraft,
    GeneratorDraftConflict,
    GeneratorDraftTooLarge,
    SaveGeneratorDraftCommand,
    delete_generator_draft,
    get_generator_draft,
    save_generator_draft,
)
from .etk_builder import attach_route, propose_route
from .etk_parts import attach_parts
from .template_sheets import TemplateDraftConflict, TemplateSheetNotFound
from .template_sheet_service import (
    delete_template_sheet,
    draft_result,
    drop_template_draft,
    edit_template_draft,
    get_template_draft,
    open_template_draft,
    save_template_draft,
    template_sheet_catalog,
)

router = APIRouter(prefix="/api", tags=["Генератор"])

# Потолки на входе. Не ради «валидации вообще»: длина в миллиметрах
# больше километра или сотня проводов в одной сборке — это опечатка, и
# поймать её здесь дешевле, чем печатать карту с такой длиной.
MAX_ЧИСЛО = 1_000_000.0
MAX_ПРОВОДОВ = 200


class ProjectTechcardPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    item_index: str = Field(min_length=1, max_length=255)
    product_name: str = Field(min_length=1, max_length=512)
    source_calculation: str = Field(default="", max_length=512)
    deadline: date | None = None


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class RebuildTechcardPayload(VersionPayload):
    document: dict[str, Any]


class AutoBuildTechcardPayload(VersionPayload):
    config: dict[str, Any]
    # «Ручная сборка» opened from an agent's draft: the build finalises it (final config + revision, corpus example).
    draft_id: UUID | None = None
    # «Поправить вручную»: the generator («Типовая») config the manual one came from. The server rebuilds the automatic
    # scheme from it to log the master's corrections (techcards.scheme_corrected); it is never taken as the scheme.
    generator_origin: dict[str, Any] | None = None


class EditOperationPayload(VersionPayload):
    operation: dict[str, Any]
    # «Сохранить также в шаблон» — правка уходит и в шаблон операции (нужна правка «Шаблонов техкарт»).
    to_template: bool = False


class RoutePayload(VersionPayload):
    route: dict[str, Any]


class RecordStepPayload(BaseModel):
    kind: str = Field(default="", max_length=32)
    text: str = Field(default="", max_length=10_000)
    tolerance: str = Field(default="", max_length=1_000)


class RecordToolPayload(BaseModel):
    section: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=1_000)
    inventory_number: str = Field(default="", max_length=255)
    program: str = Field(default="", max_length=255)
    parameters: str = Field(default="", max_length=1_000)
    norm: str = Field(default="", max_length=255)
    unit: str = Field(default="", max_length=64)


class OperationRecordPayload(BaseModel):
    """SUPPLYMAP tcCardEditForm_ → portalTcRecordSave: name, safety (without the form heading), defects, steps,
    tooling. Empty rows are dropped server-side (tcRecordNormalize_)."""

    name: str = Field(default="", max_length=512)
    safety: str = Field(default="", max_length=10_000)
    defects: str = Field(default="", max_length=20_000)
    revision: str = Field(default="", max_length=255)
    steps: list[RecordStepPayload] = Field(default_factory=list, max_length=200)
    tooling: list[RecordToolPayload] = Field(default_factory=list, max_length=200)


class NewOperationPayload(BaseModel):
    """«Новая операция»: `code` — «Номер» (БД.ОП) and the card's code; `copy_of` — the code of an operation to copy
    (steps, tooling, defects, safety, norms); none — a blank card. Lengths are checked with Russian messages inside."""

    code: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=1_000)
    copy_of: str | None = Field(default=None, max_length=255)
    category: str = Field(default="", max_length=255)


class OperationLibraryPayload(BaseModel):
    sourceCommit: str = Field(min_length=40, max_length=40)
    templates: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    force: bool = False


class TemplateDraftOpenPayload(BaseModel):
    """portalTcTplDraft(templateId, title): templateId задан → черновик правки, иначе новый."""

    template_id: str = Field(default="", max_length=255)
    title: str = Field(default="", max_length=400)


class TemplateDraftRowsPayload(BaseModel):
    rows: list[list[str | None]] = Field(max_length=400)


class TemplateDraftSavePayload(BaseModel):
    title: str = Field(default="", max_length=400)
    category: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=5_000)


class GrapherComponentPayload(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    article: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=512)


class AssemblyFromGeneratorPayload(BaseModel):
    config: dict[str, Any]


class GrapherSheetRowPayload(BaseModel):
    """A row of «Данные из листа»: the drawing is checked against it."""
    id: str = Field(min_length=1, max_length=255)
    type: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=512)
    article: str = Field(default="", max_length=512)
    kd: str = Field(default="", max_length=512)
    qty: str | float | None = None


class GrapherImportPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=42_000_000)
    components: list[GrapherComponentPayload] = Field(default_factory=list, max_length=5000)
    sheet: list[GrapherSheetRowPayload] = Field(default_factory=list, max_length=5000)


def _generator_gate(*scopes: str):
    """«Генератор техкарт» for a person (the generator's own gate decides), or an API token with one of `scopes` —
    authenticated_principal admits no other token here (FastAPI security scopes, see app.shared.http_auth)."""

    def gate(
        principal: Annotated[Principal, Security(
            authenticated_principal, scopes=[TOKEN_SCOPE_PREFIX + scope for scope in scopes],
        )],
    ) -> Principal:
        return principal if principal.service else tcgen_principal(principal)

    gate.token_scopes = frozenset(scopes)  # type: ignore[attr-defined]  (the contract test reads it)
    return gate


# «Чертёж → схема сборки»: the assembly drafts API (assembly_api.py) takes an `assembly` token; the conversion of a
# generator config into «Ручная сборка» is pure computation and serves both the generator and the drafting agent.
assembly_principal = _generator_gate("assembly")
generator_or_assembly_principal = _generator_gate("tcgen", "assembly")
# The generator's draft on the server is a person's own scratch pad: the generator's gate, and no token at all (a token
# acts for nobody, so it has no draft).
generator_person_principal = _generator_gate()


def techcards_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "techcards_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def optional_references_uow_factory(request: Request) -> Callable[[], ReferencesUnitOfWork] | None:
    factory = getattr(request.app.state, "references_uow_factory", None)
    return factory if callable(factory) else None


def etk_part_identifier(
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> Callable[[str], str] | None:
    """Name of an ETK component by the warehouse article rules (`stock05ArtInfo_` over БД.ТЕР and БД.КОАКС).
    The records are read once per request and only when a document has a component row."""
    if references_factory is None:
        return None
    records: tuple = ()
    loaded = False

    def identify(article: str) -> str:
        nonlocal records, loaded
        if not loaded:
            with references_factory() as uow:
                records = tuple(
                    record
                    for tab in (TechOperationTab.TERMINAL, TechOperationTab.COAX)
                    for record in uow.tech_operations.list(tab=tab, query="", limit=10_000)
                )
            loaded = True
        info = infer_article_identity(article, records)
        return info.name if info else ""

    return identify


def _run(operation):
    try:
        return operation()
    except OperationLibraryEditedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except TechcardValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена") from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Карточка проекта изменена конкурентно") from error


def etk_issued_lots(request: Request) -> IssuedLots | None:
    """Lots handed to the shop, read through the Fulfillment port; None when that module is not wired (tests)."""
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    return IssuedKitLots(factory) if callable(factory) else None


class EtkCards:
    """Project cards as the screens read them: parts by the warehouse article rules and each component's ГРН by the
    current lot bindings of the card's project + КБ (issued lots, else the «Состав» lot, else the snapshot), read once
    for all the cards of the request."""

    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], identify: Callable[[str], str] | None, issued: IssuedLots | None,
    ):
        self.uow_factory, self.identify, self.issued = uow_factory, identify, issued

    def many(self, cards: Sequence[ProjectTechcard]) -> list[dict]:
        if not cards:
            return []
        with self.uow_factory() as uow:
            return card_views(uow, cards, self.identify, self.issued)

    def one(self, card: ProjectTechcard) -> dict:
        return self.many([card])[0]


def etk_cards(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    identify: Annotated[Callable[[str], str] | None, Depends(etk_part_identifier)],
    issued: Annotated[IssuedLots | None, Depends(etk_issued_lots)],
) -> EtkCards:
    return EtkCards(uow_factory, identify, issued)


def _revision_result(revision: TechcardRevision) -> dict:
    return {
        "id": str(revision.id),
        "revision": revision.revision,
        "reason": revision.reason,
        "actor_name": revision.actor_name,
        "occurred_at": revision.occurred_at.isoformat(),
        "document": revision.document,
    }


def _operation_norms(references_factory: Callable[[], ReferencesUnitOfWork] | None) -> dict[str, dict[str, str]] | None:
    """БД.ОП by operation code → time norms (SUPPLYMAP tcRecordsForPortal_: «Время Операции», «Время подготовки,
    сек»; the first row of a code wins). None — the references snapshot is not loaded, so «нет норм» is unknown."""
    if references_factory is None:
        return None
    with references_factory() as references_uow:
        if references_uow.tech_operations.metadata() is None:
            return None
        records = references_uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    norms: dict[str, dict[str, str]] = {}
    for record in records:
        code = str(record.fields.get("opNumber") or "").strip()
        if code and code not in norms:
            norms[code] = {
                "operation_seconds": str(record.fields.get("tOp") or ""),
                "setup_seconds": str(record.fields.get("tPrep") or ""),
            }
    return norms


def _operation_card_result(
    card: OperationCardTemplate, *, detail: bool = False, norms: dict[str, dict[str, str]] | None = None,
) -> dict:
    controls = sum(1 for step in card.steps if step.kind == "Контроль")
    result = {
        "id": card.source_id,
        "code": card.code,
        "side": card.side,
        "title": card.title,
        "name": card.name,
        "category": card.category,
        "steps": len(card.steps),
        "controls": controls,
        "tooling": len(card.tooling),
        "first_step": card.steps[0].text if card.steps else "",
        "step_text": " ".join(step.text for step in card.steps),
        "norms": None if norms is None else norms.get(card.code),
        "norms_known": norms is not None,
        "changed_at": card.changed_at.isoformat() if card.changed_at else None,
        "changed_by": card.changed_by,
    }
    if detail:
        result["document"] = card.document()
        result["raw_rows"] = [list(row) for row in card.raw_rows]
    return result


@router.post("/techcards/grapher/import")
def import_grapher_export(
    payload: GrapherImportPayload,
    _principal: Annotated[Principal, Depends(tcgen_principal)],
) -> dict:
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
        return read_export(content, payload.filename, tuple(
            GrapherComponent(item.id, item.article, item.name) for item in payload.components
        ), tuple(
            SheetRow(item.id, item.type, item.name, item.article, item.kd, to_decimal(item.qty))
            for item in payload.sheet
        ))
    except (binascii.Error, GrapherImportError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/techcards/assembly/from-generator", dependencies=[Depends(generator_or_assembly_principal)])
def assembly_from_generator(payload: AssemblyFromGeneratorPayload) -> dict:
    """Генераторная сборка как ручная («Ручная сборка»: блоки с операциями) — открыть и править в редакторе схемы.
    Операции генератора идут со своими полями как есть; ничего не сохраняется."""
    try:
        return generator_to_assembly(payload.config)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/techcards/templates", dependencies=[Depends(tctemplates_view_principal)])
def operation_card_templates(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    with uow_factory() as uow:
        cards = uow.operation_cards.list()
        metadata = uow.operation_cards.metadata()
    norms = _operation_norms(references_factory)
    return {
        "metadata": None if metadata is None else {
            **metadata,
            "imported_at": metadata["imported_at"].isoformat(),
        },
        "items": [_operation_card_result(card, norms=norms) for card in cards],
    }


@router.get("/techcards/templates/{source_id}", dependencies=[Depends(tctemplates_view_principal)])
def operation_card_template(
    source_id: str,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    with uow_factory() as uow:
        card = uow.operation_cards.get(source_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка операции не найдена")
    return _operation_card_result(card, detail=True, norms=_operation_norms(references_factory))


def _sheet_run(operation):
    try:
        return _run(operation)
    except TemplateSheetNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TemplateDraftConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# ── Шаблоны-листы БД.ТК (SUPPLYMAP portalTcTplList/Draft/Save/DropDraft/Delete) ──

@router.get("/techcards/template-sheets", dependencies=[Depends(tctemplates_view_principal)])
def template_sheets(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return template_sheet_catalog(uow_factory())


@router.post("/techcards/template-sheets/drafts", status_code=201)
def open_template_sheet_draft(
    payload: TemplateDraftOpenPayload,
    principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    draft = _sheet_run(lambda: open_template_draft(
        uow_factory(), template_id=payload.template_id, title=payload.title, actor=principal.actor, now=now,
    ))
    return draft_result(draft, with_rows=True)


@router.get("/techcards/template-sheets/drafts/{draft_id}", dependencies=[Depends(tctemplates_view_principal)])
def template_sheet_draft(
    draft_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return draft_result(_sheet_run(lambda: get_template_draft(uow_factory(), draft_id)), with_rows=True)


@router.put("/techcards/template-sheets/drafts/{draft_id}")
def edit_template_sheet_draft(
    draft_id: UUID,
    payload: TemplateDraftRowsPayload,
    _principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    draft = _sheet_run(lambda: edit_template_draft(uow_factory(), draft_id, rows=payload.rows, now=now))
    return draft_result(draft, with_rows=True)


@router.post("/techcards/template-sheets/drafts/{draft_id}/save")
def save_template_sheet_draft(
    draft_id: UUID,
    payload: TemplateDraftSavePayload,
    principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return _sheet_run(lambda: save_template_draft(
        uow_factory(), draft_id, title=payload.title, category=payload.category,
        description=payload.description, actor=principal.actor, now=now,
    ))


@router.delete("/techcards/template-sheets/drafts/{draft_id}")
def drop_template_sheet_draft(
    draft_id: UUID,
    _principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return _sheet_run(lambda: drop_template_draft(uow_factory(), draft_id))


@router.delete("/techcards/template-sheets/{template_id}")
def delete_template_sheet_endpoint(
    template_id: str,
    principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    return _sheet_run(lambda: delete_template_sheet(uow_factory(), template_id, actor=principal.actor, now=now))


@router.put("/techcards/templates/supplymap-library")
def import_operation_card_templates(
    payload: OperationLibraryPayload,
    principal: Annotated[Principal, Depends(tctemplates_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    result = _run(lambda: replace_operation_library(ReplaceOperationLibraryCommand(
        source_commit=payload.sourceCommit,
        templates=tuple(payload.templates),
        actor=principal.actor,
        now=now,
        force=payload.force,
    ), uow_factory()))
    return {
        "templates": result.templates,
        "source_commit": payload.sourceCommit.lower(),
        "overwritten_edited": list(result.overwritten_edited),
    }


@router.put("/techcards/templates/{source_id}")
def edit_operation_card_template(
    source_id: str,
    payload: OperationRecordPayload,
    principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    try:
        card = _run(lambda: edit_operation_record(EditOperationRecordCommand(
            source_id=source_id,
            record=payload.model_dump(),
            actor=principal.actor,
            now=now,
        ), uow_factory()))
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail="Карточка операции не найдена") from error
        raise
    return _operation_card_result(card, detail=True, norms=_operation_norms(references_factory))


@router.post("/techcards/operations", status_code=201)
def create_operation_endpoint(
    payload: NewOperationPayload,
    principal: Annotated[Principal, Depends(tctemplates_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    """A new operation: its БД.ОП row and its card template in one transaction (both edit rights needed — the
    operation row is «Справочник», the card «Шаблоны техкарт»; no API token — the template gate refuses tokens).
    A taken code — 422."""
    if not section_allowed(principal, ("erpref",), "edit"):
        raise HTTPException(status_code=403, detail="Раздел «Справочник» открыт вам только на просмотр.")
    created = _run(lambda: create_operation(
        new_operation(payload.code, payload.name, payload.copy_of or "", payload.category),
        principal.actor, now, NewOperationUnitOfWork(uow_factory()),
    ))
    norms = _operation_norms(references_factory)
    record = created.record
    return {
        "code": record.fields.get("opNumber", ""),
        "name": record.fields.get("opName", ""),
        "record": {"id": str(record.id), "tab": record.tab.value, "display_text": record.display_text,
                   "fields": record.fields},
        "templates": [_operation_card_result(card, detail=True, norms=norms) for card in created.templates],
    }


@router.get("/techcards/projects", dependencies=[Depends(techcards_view_principal)])
def project_techcards(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    with uow_factory() as uow:
        cards = uow.techcards.list()
    return {"items": etk.many(cards)}


@router.post("/techcards/projects", status_code=201)
def add_project_techcard(
    payload: ProjectTechcardPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    card = _run(lambda: create_project_techcard(CreateProjectTechcardCommand(
        project=payload.project,
        item_index=payload.item_index,
        product_name=payload.product_name,
        source_calculation=payload.source_calculation,
        deadline=payload.deadline,
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return etk.one(card)


@router.get("/techcards/projects/{card_id}", dependencies=[Depends(techcards_view_principal)])
def project_techcard(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    with uow_factory() as uow:
        card = uow.techcards.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
    return etk.one(card)


@router.put("/techcards/projects/{card_id}/etk")
def rebuild_project_etk(
    card_id: UUID,
    payload: RebuildTechcardPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    card = _run(lambda: rebuild_project_techcard(RebuildProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        document=payload.document,
    ), uow_factory()))
    return etk.one(card)


# A `tcgen` API token reads and routes, it never writes a project techcard (an issued card would get a new revision):
# the agent flow writes assembly drafts only. Refused before the body is read.
@router.post(
    "/techcards/projects/{card_id}/build",
    dependencies=[Depends(refuse_api_tokens("Токен API не собирает техкарты проектов."))],
)
def auto_build_project_etk(
    card_id: UUID,
    payload: AutoBuildTechcardPayload,
    principal: Annotated[Principal, Depends(tcgen_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    if payload.draft_id is not None:
        _draft_run(lambda: draft_for_card(payload.draft_id, card_id, uow_factory))
    config, technology_source = with_technology(payload.config, references_factory)
    card = _run(lambda: auto_build_project_techcard(AutoBuildProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        config=config,
        inputs=payload.config,
        actor=principal.actor,
        now=now,
        technology_source=technology_source,
        generator_origin=payload.generator_origin,
    ), uow_factory()))
    if payload.draft_id is not None:
        # The config as the master built it (before the reference norms are filled in) — the pair to the draft.
        _draft_run(lambda: finalise_draft(
            payload.draft_id, payload.config, card.revision, principal.actor, now, uow_factory,
        ))
    return etk.one(card)


@router.get("/techcards/scheme-corrections", dependencies=[Depends(tcgen_principal)])
def scheme_corrections(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    since: date | None = None,
    project: str = "",
) -> dict:
    """Журнал правок «Схемы полуфабрикатов» (techcards.scheme_corrected), новые сверху; токен `tcgen` читает."""
    with uow_factory() as uow:
        items = list_scheme_corrections(uow.session, since=since, project=project.strip())
    return {"items": items}


def _draft_run(operation):
    try:
        return operation()
    except AssemblyDraftNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AssemblyDraftError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/techcards/projects/{card_id}/issue")
def issue_project_etk(
    card_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    card = _run(lambda: issue_project_techcard(ChangeProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return etk.one(card)


@router.delete("/techcards/projects/{card_id}/etk")
def discard_project_etk_draft(
    card_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    """«Удалить созданные» генератора: убрать собранный, ещё не выданный черновик ЭТК."""
    card = _run(lambda: discard_project_techcard_draft(ChangeProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return etk.one(card)


@router.put("/techcards/projects/{card_id}/operations/{operation_key}")
def edit_project_etk_operation(
    card_id: UUID,
    operation_key: str,
    payload: EditOperationPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    if payload.to_template and not section_allowed(principal, ("erptctpl",), "edit"):
        raise HTTPException(status_code=403, detail="Сохранить правку в шаблон: раздел «Шаблоны техкарт» открыт вам только на просмотр.")
    card = _run(lambda: edit_project_operation(EditTechcardOperationCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        operation_key=operation_key,
        operation=payload.operation,
        to_template=payload.to_template,
    ), uow_factory()))
    return etk.one(card)


@router.put("/techcards/projects/{card_id}/route")
def save_project_etk_route(
    card_id: UUID,
    payload: RoutePayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    etk: Annotated[EtkCards, Depends(etk_cards)],
) -> dict:
    """«Сохранить ПФ»: операции ЭТК, разложенные технологом по полуфабрикатам (блоки схемы, порядок операций внутри
    блока — порядок работы). Схема проверяется целиком (422 «Схема полуфабрикатов: …»); выданная ЭТК — новой ревизией."""
    card = _run(lambda: save_project_route(SaveTechcardRouteCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        route=payload.route,
    ), uow_factory()))
    return etk.one(card)


@router.post("/techcards/projects/{card_id}/route/propose", dependencies=[Depends(techcards_edit_principal)])
def propose_project_etk_route(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """«Предложить разбивку на ПФ»: схема ПФ по правилу генератора (ручная сборка — её блоки) для правки технологом.
    Ничего не сохраняет: сохраняет «Сохранить ПФ» (PUT …/route). `same` — ЭТК уже разбита так."""
    with uow_factory() as uow:
        card = uow.techcards.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
    proposal = propose_route(card.document)
    if proposal is None:
        raise HTTPException(status_code=422, detail="Электронная техкарта ещё не собрана")
    return proposal


@router.get("/techcards/projects/{card_id}/revisions", dependencies=[Depends(techcards_view_principal)])
def project_etk_revisions(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        if uow.techcards.get(card_id) is None:
            raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
        revisions = uow.techcards.revisions(card_id)
    return {"items": [_revision_result(revision) for revision in revisions]}


# ── эскиз операции: своя картинка технолога ─────────────────────────────────

SKETCH_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_SKETCH_BYTES = 10 * 1024 * 1024


class SketchPayload(BaseModel):
    file_name: str = Field(default="", max_length=512)
    content_type: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=14_000_000)


def _sketch_result(sketch: OperationSketch) -> dict:
    return {
        "operation_key": sketch.operation_key, "file_name": sketch.file_name, "size": sketch.size,
        "uploaded_by": sketch.uploaded_by, "uploaded_at": sketch.uploaded_at.isoformat(),
    }


def _operation_keys(card: ProjectTechcard) -> set[str]:
    keys = set()
    for item in (card.document or {}).get("operations") or []:
        try:
            keys.add(etk_operation_key(item))
        except TechcardValidationError:
            continue
    return keys


def _card_or_404(uow, card_id: UUID) -> ProjectTechcard:
    card = uow.techcards.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
    return card


@router.get("/techcards/projects/{card_id}/sketches", dependencies=[Depends(techcards_view_principal)])
def operation_sketches(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        _card_or_404(uow, card_id)
        sketches = uow.techcards.sketches(card_id)
    return {"items": [_sketch_result(sketch) for sketch in sketches]}


@router.get("/techcards/projects/{card_id}/sketches/{operation_key:path}", dependencies=[Depends(techcards_view_principal)])
def operation_sketch(
    card_id: UUID,
    operation_key: str,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> Response:
    with uow_factory() as uow:
        found = uow.techcards.sketch_content(card_id, operation_key)
    if found is None:
        raise HTTPException(status_code=404, detail="Эскиз не найден")
    sketch, content = found
    return file_response(content, sketch.file_name)


@router.put("/techcards/projects/{card_id}/sketches/{operation_key:path}")
def put_operation_sketch(
    card_id: UUID,
    operation_key: str,
    payload: SketchPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """Картинка эскиза к операции ЭТК — хранится в базе ERP, одна на операцию (новая заменяет старую)."""
    content_type = payload.content_type.strip().lower()
    if content_type not in SKETCH_TYPES:
        raise HTTPException(status_code=422, detail="Эскиз — картинка PNG, JPG, WEBP или GIF")
    content = decode_upload(payload.content_base64.split(",", 1)[-1], "Картинка передана не в base64")
    if not content:
        raise HTTPException(status_code=422, detail="Картинка пустая")
    if len(content) > MAX_SKETCH_BYTES:
        raise HTTPException(status_code=422, detail="Картинка эскиза больше 10 МБ")
    # The bytes decide, not the declared type: a page or an SVG named «.png» is not a sketch (audit F04).
    content_type = sniffed_type(content) or ""
    if content_type not in SKETCH_TYPES:
        raise HTTPException(status_code=422, detail="Файл не похож на картинку PNG, JPG, WEBP или GIF")
    with uow_factory() as uow:
        card = _card_or_404(uow, card_id)
        if operation_key not in _operation_keys(card):
            raise HTTPException(status_code=404, detail="Операция не найдена в ЭТК")
        sketch = OperationSketch(
            operation_key=operation_key,
            file_name=payload.file_name.strip() or f"эскиз{SKETCH_TYPES[content_type]}",
            content_type=content_type, size=len(content), uploaded_by=principal.actor.name, uploaded_at=now,
        )
        uow.techcards.put_sketch(card_id, sketch, content)
        uow.commit()
    return _sketch_result(sketch)


@router.delete("/techcards/projects/{card_id}/sketches/{operation_key:path}")
def delete_operation_sketch(
    card_id: UUID,
    operation_key: str,
    _principal: Annotated[Principal, Depends(techcards_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        _card_or_404(uow, card_id)
        deleted = uow.techcards.delete_sketch(card_id, operation_key)
        uow.commit()
    return {"deleted": deleted}


@router.get("/techcards/connector-families", dependencies=[Depends(tctemplates_view_principal)])
def connector_families(article: str = "") -> dict:
    """База разъёмов: семейства корпусов с рисунком по образцу артикула — чтобы видеть, что умеет схема.
    `article` — проверить свой артикул: какой корпус нарисует ЭТК (или None — серия не распознана)."""
    return {"items": connector_catalog(), "match": housing_of(article[:255]) if article.strip() else None,
            "series_count": len(connector_series())}


@router.get("/techcards/connector-series", dependencies=[Depends(tctemplates_view_principal)])
def connector_series_view(
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
    article: str = "",
) -> dict:
    """«База данных → Разъёмы» по сериям: все корпуса серии (по таблицам каталога производителя), вилки на плату,
    совместимые терминалы БД.ТЕР и диапазон провода. `article` — найти серию и корпус своего артикула."""
    records: list[dict] = []
    coax: list[dict] = []
    if references_factory is not None:
        with references_factory() as uow:
            records = [{**record.fields, RECORD_ID_KEY: str(record.id)}
                       for record in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=10_000)]
            coax = [record.fields for record in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=10_000)]
    series = connector_series(records, coax)
    return {"series": series, "match": find_in_series(article[:255], series) if article.strip() else None}


# ── жгут ────────────────────────────────────────────────────────────────────


class ВходПровода(BaseModel):
    name: str = Field(default="", max_length=512)
    length_mm: float = Field(default=0.0, ge=0, le=MAX_ЧИСЛО)
    qty: float = Field(default=1.0, gt=0, le=MAX_ЧИСЛО)
    # Разделка: снять изоляцию с концов под опрессовку или пайку.
    strip: bool = True


class ВходРазъёма(BaseModel):
    name: str = Field(default="", max_length=512)
    # Сколько контактов заводится: по нему считается норма опрессовки.
    contacts: float = Field(default=1.0, gt=0, le=MAX_ЧИСЛО)
    # Пайка вместо опрессовки — другая операция и другое время.
    solder: bool = False


class ЖгутIn(BaseModel):
    name: str = Field(default="", max_length=512)
    wires: list[ВходПровода] = Field(min_length=1, max_length=MAX_ПРОВОДОВ)
    side_a: ВходРазъёма | None = None
    side_b: ВходРазъёма | None = None
    heatshrink: bool = False
    twist: bool = False
    marking: bool = False


# ── коаксиал ────────────────────────────────────────────────────────────────


class ВходСтороны(BaseModel):
    article: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=512)
    # «пайка» — прямой разъём, корпус монтируется отдельной операцией.
    # «внутри корпуса» — угловой (R/A): пин паяется уже в корпусе.
    #
    # Перечисление, а не строка: от типа посадки зависит и наличие
    # монтажа корпуса, и момент, когда надевается термоусадка. Опечатка
    # здесь дала бы правдоподобный, но неверный маршрут.
    # «цанга» — цанговый центральный контакт: не паяется, садится при монтаже корпуса.
    pin_type: Literal["пайка", "внутри корпуса", "цанга"] = "пайка"
    cover: Literal["", "обжим", "винт"] = ""


class КоаксIn(BaseModel):
    name: str = Field(default="", max_length=512)
    cable_article: str = Field(default="", max_length=512)
    cable_name: str = Field(default="", max_length=512)
    cable_length_mm: float = Field(default=0.0, ge=0, le=MAX_ЧИСЛО)
    side_a: ВходСтороны | None = None
    side_b: ВходСтороны | None = None
    shield_type: Literal["обжим", "пайка"] = "обжим"
    tut_article: str = Field(default="", max_length=512)
    tut_name: str = Field(default="", max_length=512)
    tut_length_mm: float = Field(default=20.0, ge=0, le=MAX_ЧИСЛО)
    strip_free_end: bool = False


# ── выдача ──────────────────────────────────────────────────────────────────


def _выдать(шаги, предупреждения: list[str]) -> dict:
    """Маршрут в вид, пригодный для показа и печати.

    `depends_on` отдаётся наружу намеренно: по нему видно, что можно
    делать параллельно. Без него маршрут выглядит строго последовательным,
    и мастер планирует смену хуже, чем мог бы.
    """
    return {
        "steps": [
            {
                "position": ш.position,
                "code": ш.code,
                "name": ш.name,
                "component": ш.component,
                "semi_product": ш.semi_product,
                "count": ш.count,
                "length_m": ш.length_m,
                "requirement": ш.requirement,
                "depends_on": ш.depends_on,
            }
            for ш in шаги
        ],
        "warnings": предупреждения,
    }


@router.post("/route/harness", dependencies=[Depends(tcgen_principal)])
def route_harness(body: ЖгутIn) -> dict:
    """Маршрут обычной кабельной сборки."""
    сборка = Сборка(
        name=body.name,
        wires=[Провод(**w.model_dump()) for w in body.wires],
        side_a=Разъём(**body.side_a.model_dump()) if body.side_a else None,
        side_b=Разъём(**body.side_b.model_dump()) if body.side_b else None,
        heatshrink=body.heatshrink,
        twist=body.twist,
        marking=body.marking,
    )
    return _выдать(*построить(сборка))


@router.post("/route/coax", dependencies=[Depends(tcgen_principal)])
def route_coax(body: КоаксIn) -> dict:
    """Маршрут коаксиальной сборки."""
    сборка = КоаксСборка(
        cable_article=body.cable_article,
        cable_name=body.cable_name,
        cable_length_mm=body.cable_length_mm,
        side_a=КоаксСторона(**body.side_a.model_dump()) if body.side_a else None,
        side_b=КоаксСторона(**body.side_b.model_dump()) if body.side_b else None,
        shield_type=body.shield_type,
        tut_article=body.tut_article,
        tut_name=body.tut_name,
        tut_length_mm=body.tut_length_mm,
        strip_free_end=body.strip_free_end,
    )
    return _выдать(*построить_коакс(сборка))


# ── Черновик генератора ТК на сервере (владелец 11.10.2026) ─────────────────────────────────────────────────────────
# The unfinished generator form of the signed-in person for one position (КБ): read, save over a version, delete. Only
# one's own draft — the person is the session's, never a parameter. No audit event per save (a scratch pad).


class GeneratorDraftBody(BaseModel):
    payload: dict[str, Any]
    build_fingerprint: str = Field(default="", max_length=64)
    # The version the browser saved over; 0 — it has none yet (the first save creates the draft).
    base_version: int = Field(ge=0)


def _generator_position(project: str, item_index: str) -> tuple[str, str]:
    project, item_index = project.strip(), item_index.strip()
    if not project or not item_index:
        raise HTTPException(status_code=422, detail="Укажите проект и КБ черновика.")
    return project, item_index


def _generator_draft_json(draft: GeneratorDraft) -> dict:
    return {
        "project": draft.project, "item_index": draft.item_index, "payload": draft.payload,
        "build_fingerprint": draft.build_fingerprint, "version": draft.version,
        "created_at": draft.created_at.isoformat(), "updated_at": draft.updated_at.isoformat(),
    }


@router.get("/techcards/generator-draft")
def read_generator_draft(
    principal: Annotated[Principal, Depends(generator_person_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    project: Annotated[str, Query(max_length=255)] = "",
    item_index: Annotated[str, Query(max_length=64)] = "",
) -> dict:
    project, item_index = _generator_position(project, item_index)
    draft = get_generator_draft(principal.actor.id, project, item_index, uow_factory)
    return {"draft": None if draft is None else _generator_draft_json(draft)}


@router.put("/techcards/generator-draft")
def write_generator_draft(
    payload: GeneratorDraftBody,
    principal: Annotated[Principal, Depends(generator_person_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    project: Annotated[str, Query(max_length=255)] = "",
    item_index: Annotated[str, Query(max_length=64)] = "",
) -> dict:
    project, item_index = _generator_position(project, item_index)
    try:
        draft = save_generator_draft(SaveGeneratorDraftCommand(
            user_id=principal.actor.id, project=project, item_index=item_index, payload=payload.payload,
            build_fingerprint=payload.build_fingerprint, base_version=payload.base_version, now=now,
        ), uow_factory)
    except GeneratorDraftTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except GeneratorDraftConflict as error:
        current = error.current
        raise HTTPException(status_code=409, detail={
            "message": str(error), "code": "generator_draft_conflict", "version": current.version,
            "updated_at": current.updated_at.isoformat() if current.version else None,
        }) from error
    return _generator_draft_json(draft)


@router.delete("/techcards/generator-draft")
def remove_generator_draft(
    principal: Annotated[Principal, Depends(generator_person_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    project: Annotated[str, Query(max_length=255)] = "",
    item_index: Annotated[str, Query(max_length=64)] = "",
) -> dict:
    project, item_index = _generator_position(project, item_index)
    return {"deleted": delete_generator_draft(principal.actor.id, project, item_index, uow_factory)}
