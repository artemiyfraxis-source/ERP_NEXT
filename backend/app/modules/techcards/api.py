"""Ручки генератора: описание сборки → маршрут.

Два входа вместо одного флага «режим»: у жгута и коаксиала разные поля.
У жгута разъём описывается числом контактов, у коаксиала — типом посадки
пина и крышкой; у жгута несколько проводов, у коаксиала один кабель с
экраном. Свести их в одну схему значило бы получить набор полей,
половина которых всегда пустая, и валидацию «если режим коакс, то
обязательно…» вместо типов, которые проверяются сами.
"""

from collections.abc import Callable
import base64
import binascii
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal, tcgen_principal, techcards_edit_principal, techcards_view_principal, tctemplates_admin_principal,
    tctemplates_view_principal,
)
from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab, gost_awg_map
from app.modules.references.technology import TECH_SNAPSHOT_SCHEMA_VERSION, TerminalRecord
from app.workflows.techcard_build import enrich_terminal_norms

from .application import (
    ChangeProjectTechcardCommand,
    AutoBuildProjectTechcardCommand,
    CreateProjectTechcardCommand,
    EditOperationRecordCommand,
    EditTechcardOperationCommand,
    RebuildProjectTechcardCommand,
    ReplaceOperationLibraryCommand,
    UnitOfWork,
    create_project_techcard,
    auto_build_project_techcard,
    edit_operation_record,
    discard_project_techcard_draft,
    edit_project_operation,
    issue_project_techcard,
    rebuild_project_techcard,
    replace_operation_library,
)

from .domain.cut_lengths import CalcRow, CalcWire, cut_lengths_from_calc
from .domain.coax import КоаксСборка
from .domain.coax import Сторона as КоаксСторона
from .domain.coax import построить_коакс
from .domain.harness import Провод, Разъём, Сборка, построить
from .project_cards import ProjectTechcard, TechcardRevision, TechcardValidationError
from .operation_cards import OperationCardTemplate
from .grapher_import import GrapherComponent, GrapherImportError, read_export
from .connector_housings import attach_housings

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


class CutLengthWirePayload(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    art: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=512)


class CutLengthCalcPayload(BaseModel):
    code: str = Field(default="", max_length=255)
    comp: str = Field(default="", max_length=512)
    req: str = Field(default="", max_length=2000)
    l: str = Field(default="", max_length=64)


class CutLengthsPayload(BaseModel):
    wires: list[CutLengthWirePayload] = Field(max_length=500)
    calc_ops: list[CutLengthCalcPayload] = Field(max_length=2000)


class RebuildTechcardPayload(VersionPayload):
    document: dict[str, Any]


class AutoBuildTechcardPayload(VersionPayload):
    config: dict[str, Any]


class EditOperationPayload(VersionPayload):
    operation: dict[str, Any]


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


class OperationLibraryPayload(BaseModel):
    sourceCommit: str = Field(min_length=40, max_length=40)
    templates: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class GrapherComponentPayload(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    article: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=512)


class GrapherImportPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=42_000_000)
    components: list[GrapherComponentPayload] = Field(default_factory=list, max_length=5000)


def techcards_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "techcards_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def optional_references_uow_factory(request: Request) -> Callable[[], ReferencesUnitOfWork] | None:
    factory = getattr(request.app.state, "references_uow_factory", None)
    return factory if callable(factory) else None


def _run(operation):
    try:
        return operation()
    except TechcardValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена") from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Карточка проекта изменена конкурентно") from error


def _card_result(card: ProjectTechcard) -> dict:
    return {
        "id": str(card.id),
        "version": card.version,
        "project": card.project,
        "item_index": card.item_index,
        "product_name": card.product_name,
        "source_calculation": card.source_calculation,
        "deadline": card.deadline.isoformat() if card.deadline else None,
        "status": card.status.value,
        "revision": card.revision,
        "document": attach_housings(card.document),
        "manual_operation_keys": list(card.manual_operation_keys),
        "created_at": card.created_at.isoformat(),
        "updated_at": card.updated_at.isoformat(),
        "issued_at": card.issued_at.isoformat() if card.issued_at else None,
    }


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
        ))
    except (binascii.Error, GrapherImportError) as error:
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
    ), uow_factory()))
    return {
        "templates": result.templates,
        "source_commit": payload.sourceCommit.lower(),
        "kept_edited": list(result.kept_edited),
    }


@router.put("/techcards/templates/{source_id}")
def edit_operation_card_template(
    source_id: str,
    payload: OperationRecordPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
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


@router.get("/techcards/projects", dependencies=[Depends(techcards_view_principal)])
def project_techcards(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        cards = uow.techcards.list()
    return {"items": [_card_result(card) for card in cards]}


@router.post("/techcards/projects", status_code=201)
def add_project_techcard(
    payload: ProjectTechcardPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
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
    return _card_result(card)


@router.get("/techcards/projects/{card_id}", dependencies=[Depends(techcards_view_principal)])
def project_techcard(
    card_id: UUID,
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        card = uow.techcards.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Карточка проекта не найдена")
    return _card_result(card)


@router.put("/techcards/projects/{card_id}/etk")
def rebuild_project_etk(
    card_id: UUID,
    payload: RebuildTechcardPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    card = _run(lambda: rebuild_project_techcard(RebuildProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        document=payload.document,
    ), uow_factory()))
    return _card_result(card)


@router.post("/techcards/projects/{card_id}/build")
def auto_build_project_etk(
    card_id: UUID,
    payload: AutoBuildTechcardPayload,
    principal: Annotated[Principal, Depends(tcgen_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
    references_factory: Annotated[
        Callable[[], ReferencesUnitOfWork] | None,
        Depends(optional_references_uow_factory),
    ],
) -> dict:
    config = payload.config
    technology_source = None
    if references_factory is not None:
        with references_factory() as references_uow:
            metadata = references_uow.tech_operations.metadata()
            records = references_uow.tech_operations.list(
                tab=TechOperationTab.TERMINAL, query="", limit=10_000,
            ) if metadata and metadata["schema_version"] == TECH_SNAPSHOT_SCHEMA_VERSION else ()
            gost_awg = gost_awg_map(references_uow.references.list())
        if metadata and metadata["schema_version"] == TECH_SNAPSHOT_SCHEMA_VERSION:
            config = enrich_terminal_norms(
                config,
                (TerminalRecord.from_fields(record.fields) for record in records),
                gost_awg,
            )
            technology_source = {
                "schema_version": metadata["schema_version"],
                "source_updated_at": (
                    metadata["source_updated_at"].isoformat()
                    if metadata["source_updated_at"] else None
                ),
                "imported_at": metadata["imported_at"].isoformat(),
            }
    card = _run(lambda: auto_build_project_techcard(AutoBuildProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        config=config,
        actor=principal.actor,
        now=now,
        technology_source=technology_source,
    ), uow_factory()))
    return _card_result(card)


@router.post("/techcards/projects/{card_id}/issue")
def issue_project_etk(
    card_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    card = _run(lambda: issue_project_techcard(ChangeProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return _card_result(card)


@router.delete("/techcards/projects/{card_id}/etk")
def discard_project_etk_draft(
    card_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    """«Удалить созданные» генератора: убрать собранный, ещё не выданный черновик ЭТК."""
    card = _run(lambda: discard_project_techcard_draft(ChangeProjectTechcardCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
    ), uow_factory()))
    return _card_result(card)


@router.post("/techcards/cut-lengths")
def calc_cut_lengths(
    payload: CutLengthsPayload,
    _principal: Annotated[Principal, Depends(techcards_edit_principal)],
) -> dict:
    """Длины резки проводов из строк расчёта КБn (SUPPLYMAP calcCutLengths_, TcGenInline.html:2276)."""
    lengths = cut_lengths_from_calc(
        [CalcWire(id=item.id, art=item.art, name=item.name) for item in payload.wires],
        [CalcRow(code=item.code, comp=item.comp, req=item.req, l_m=item.l) for item in payload.calc_ops],
    )
    return {"lengths": lengths}


@router.put("/techcards/projects/{card_id}/operations/{operation_key}")
def edit_project_etk_operation(
    card_id: UUID,
    operation_key: str,
    payload: EditOperationPayload,
    principal: Annotated[Principal, Depends(techcards_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(techcards_uow_factory)],
) -> dict:
    card = _run(lambda: edit_project_operation(EditTechcardOperationCommand(
        card_id=card_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        operation_key=operation_key,
        operation=payload.operation,
    ), uow_factory()))
    return _card_result(card)


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
    pin_type: Literal["пайка", "внутри корпуса"] = "пайка"
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
