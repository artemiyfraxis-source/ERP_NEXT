"""«Проекты» — карточки наборов по проектам (SUPPLYMAP erptechmap: portalKitset*, portalKd*, portalKitMark,
portalIssueFromKitset). Composed like the production plan API: this router reads other modules' units of work
through app.state and hands cross-module steps to `app.workflows.project_kitset`."""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.fulfillment.application import free_for_project, project_reserve_by_lot
from app.modules.fulfillment.domain import FulfillmentValidationError
from app.modules.production.domain import ProductionValidationError
from app.modules.requests.application import CalcSheetError
from app.modules.requests.domain import ValidationError as RequestValidationError, unit_for_type
from app.modules.production.plan import (
    KIT_STAGES, PLAN_BLOCKERS, PROJECT_TERMINALS, TC_REVIEW_VALUES, normalize_project_number, summarize_journal,
)
from app.modules.production.plan_application import AddPlanEventCommand, add_plan_event
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, kitset_tiles_principal, projects_edit_principal, projects_view_principal
from app.shared.stored_files import file_response
from app.workflows import project_kitset as flow
from app.workflows import project_track as track_flow

from .application import CreateProjectTechcardCommand, create_project_techcard
from .kitsets import (
    Kitset,
    KitsetNotFound,
    KitsetValidationError,
    item_id,
    sort_kitsets,
)
from .project_cards import TechcardValidationError


router = APIRouter(prefix="/api/techcards/kitsets", tags=["Проекты"])


def _factory(request: Request, name: str, *, required: bool = False):
    factory = getattr(request.app.state, name, None)
    if callable(factory):
        return factory
    if required:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return None


def techcards_factory(request: Request):
    return _factory(request, "techcards_uow_factory", required=True)


def production_factory(request: Request):
    return _factory(request, "production_uow_factory")


def shipping_factory(request: Request):
    return _factory(request, "shipping_uow_factory")


def fulfillment_factory(request: Request):
    return _factory(request, "fulfillment_uow_factory")


def requests_factory(request: Request):
    return _factory(request, "uow_factory")


def operations_factory(request: Request):
    return _factory(request, "operations_uow_factory")


def procurement_factory(request: Request):
    return _factory(request, "procurement_uow_factory")


def receiving_factory(request: Request):
    return _factory(request, "receiving_uow_factory")


def _name_queue(request: Request):
    """tcKitNamesEnqueue_: очередь фонового добора наименований (app.state.kitset_name_queue)."""
    queue = getattr(request.app.state, "kitset_name_queue", None)
    return queue.enqueue if queue is not None else None


def _read(factory, reader, default=()):
    if factory is None:
        return default
    with factory() as uow:
        return reader(uow)


def _run(operation):
    try:
        return operation()
    except KitsetNotFound as error:
        # «Не найдена» — не сбой: плитка висела на открытом экране после сноса. gone → клиент вернётся к списку.
        raise HTTPException(status_code=404, detail={"message": str(error), "gone": True}) from error
    except (KitsetValidationError, TechcardValidationError, ProductionValidationError, FulfillmentValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Карточка проекта изменена конкурентно — обновите") from error


def _number(value: Decimal) -> str:
    return flow.kit_number_payload(value)


def _facts(request: Request) -> flow.KitsetFacts:
    return flow.kitset_facts(
        techcards_factory(request), production_factory(request),
        fulfillment_factory(request), shipping_factory(request),
    )


def _kb_stage(stage) -> dict[str, Any]:
    return {
        "index": stage.item_index, "status": stage.status, "manual": stage.manual,
        "tc": stage.tc, "kit": stage.kit,
        "review": None if stage.review is None else {
            "value": stage.review.value, "text": stage.review.text, "who": stage.review.who,
        },
    }


@router.get("")
def kitsets(
    request: Request,
    _principal: Annotated[Principal, Depends(kitset_tiles_principal)],
    sort: Annotated[str, Query(max_length=16)] = "upd",
) -> dict:
    """`portalKitsetList`: one tile per project with the computed status, КБ stages and the active blocker."""
    with techcards_factory(request)() as uow:
        items = list(uow.kitsets.list())
    stages = flow.kitset_stages(items, _facts(request))
    statuses = flow.kitset_statuses()
    rows = []
    for kitset in items:
        stage, blocker = stages[kitset.project]
        rows.append({
            "project": kitset.project, "customer": kitset.customer, "calc_source": kitset.calc_source,
            "updated_at": kitset.updated_at, "updated_by": kitset.updated_by,
            "assemblies": len(kitset.assemblies), "lines": kitset.lines,
            "kbs": [assembly.index for assembly in kitset.assemblies],
            "status": stage.status, "kb_stages": [_kb_stage(item) for item in stage.kits], "blocker": blocker,
        })
    rows = sort_kitsets(rows, sort, statuses)
    for row in rows:
        row["updated_at"] = row["updated_at"].isoformat()
    return {
        "items": rows, "statuses": statuses, "stages": list(KIT_STAGES),
        "terminals": list(PROJECT_TERMINALS), "blockers": list(PLAN_BLOCKERS),
    }


class ShipPayload(BaseModel):
    shipping_point: str = Field(default="", max_length=2000)
    contact: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=128)


class RequestsPayload(BaseModel):
    """«Сразу создать заявки на закупку»: общие поля пачки «Оформить заявку → Из расчёта»."""

    purpose: str = Field(min_length=1, max_length=128)
    delivery_point: str = Field(min_length=1, max_length=512)
    procurement_deadline: date


class LoadPayload(BaseModel):
    """Расчёт — ссылкой на Google Таблицу, как в SUPPLYMAP (`portalKitsetLoad(token, url, ship, opts)`)."""

    url: str = Field(min_length=1, max_length=2048)
    number: str = Field(default="", max_length=255)
    deadline: date | None = None
    ship: ShipPayload | None = None
    requests: RequestsPayload | None = None


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Файл передан не в base64") from error


_CALC_SHEET_STATUS = {"config": 503, "access": 422, "unsupported": 422, "upstream": 502}


def _calc(payload: LoadPayload, request: Request):
    try:
        return flow.read_calc_by_link(payload.url, lambda: flow.calc_sheets_reader(request.app.state))
    except CalcSheetError as error:
        raise HTTPException(status_code=_CALC_SHEET_STATUS.get(error.kind, 502), detail=str(error)) from error


@router.post("/load")
def load(
    payload: LoadPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKitsetLoad`: create a project from the calculation, or refresh it («Обновить из расчёта»)."""
    data = _run(lambda: _calc(payload, request))
    result = _run(lambda: flow.load_kitset(flow.LoadKitsetCommand(
        data=data,
        number=payload.number, deadline=payload.deadline,
        ship=payload.ship.model_dump() if payload.ship else {},
        actor=principal.actor, now=now,
    ), techcards_factory(request), production_factory(request), shipping_factory(request),
        _name_queue(request)))
    requests, requests_error = _project_requests(payload, request, principal, result.project, now)
    return {"res": {
        "project": result.project, "assemblies": result.assemblies, "lines": result.lines,
        "erp": result.erp, "erp_error": result.erp_error, "ship": result.ship, "ship_error": result.ship_error,
        "ship_to_error": result.ship_to_error, "deadline_error": result.deadline_error,
        "requests": requests, "requests_error": requests_error,
    }}


def _project_requests(
    payload: LoadPayload, request: Request, principal: Principal, project: str, now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    """Заявки не роняют заведение проекта: сбой — в `requests_error`, как прочие побочные шаги."""
    options = payload.requests
    if options is None:
        return None, ""
    if principal.permissions.get("newrequest") != "edit":
        return None, "раздел «Оформить заявку» вам не открыт"
    factory = requests_factory(request)
    if factory is None:
        return None, "база заявок не настроена"
    try:
        created = flow.create_kitset_requests(flow.KitsetRequestsCommand(
            project=project, url=payload.url, purpose=options.purpose, delivery_point=options.delivery_point,
            procurement_deadline=options.procurement_deadline, actor=principal.actor, now=now,
        ), factory, lambda: flow.requests_calc_reader(request.app.state))
    except (ValueError, CalcSheetError) as error:
        return None, str(error)
    except Exception:  # noqa: BLE001 — проект уже заведён, заявку оформят кнопкой
        logging.exception("kitset requests:%s", project)
        return None, "заявки не созданы — оформи кнопкой «Оформить заявку из расчёта»"
    return {"created": created.created, "existing": created.existing, "skipped": list(created.skipped)}, ""


@router.get("/lots")
def lots(
    request: Request,
    _principal: Annotated[Principal, Depends(projects_edit_principal)],
    q: Annotated[str, Query(max_length=255)] = "",
    project: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """The warehouse picker of the spec (stockPick_): the whole stock with a search, not only «matching names» —
    a matched list misses when acceptance named the goods differently from the КД."""
    factory = fulfillment_factory(request)
    if factory is None:
        return {"lots": []}
    with factory() as uow:
        found = uow.warehouse.list(limit=300, query=q, include_zero=False)
        held = project_reserve_by_lot(uow, found, project.strip())  # the project's «Со склада» reserve is its own
    return {"lots": [{
        "lot_id": str(lot.id), "warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package,
        "article": lot.article, "name": lot.name, "unit": lot.unit,
        "storage_location": lot.storage_location, "available": _number(free_for_project(lot, held)),
        **({"reserved": _number(held[lot.id])} if held.get(lot.id) else {}),
    } for lot in found if free_for_project(lot, held) > 0]}


def _kitset(request: Request, project: str) -> Kitset:
    with techcards_factory(request)() as uow:
        kitset = uow.kitsets.get(project)
    if kitset is None:
        raise HTTPException(status_code=404, detail={"message": f"Карточка набора «{project}» не найдена", "gone": True})
    return kitset


def _card(request: Request, kitset: Kitset) -> dict:
    facts = _facts(request)
    stage, blocker = flow.kitset_stages([kitset], facts)[kitset.project]
    stage_of = {item.item_index: item for item in stage.kits}
    cards = flow.cards_state(kitset.project, facts.cards)
    canon = normalize_project_number(kitset.project)
    journal = summarize_journal(event for event in facts.events if normalize_project_number(event.project) == canon).get(canon)
    active = journal.blocker if journal is not None else None
    snapshot = next((item for item in facts.projects if normalize_project_number(item.project) == canon), None)
    with techcards_factory(request)() as uow:
        documents = uow.kitsets.documents(kitset.project)
    return {
        "project": kitset.project, "customer": kitset.customer, "calc_source": kitset.calc_source,
        "updated_at": kitset.updated_at.isoformat(), "updated_by": kitset.updated_by, "version": kitset.version,
        "status": stage.status, "blocker": blocker,
        "blocker_info": None if active is None else {
            "type": active.type, "text": active.text, "since": active.since.isoformat(), "who": active.who,
        },
        "deadline": ((journal.deadline if journal is not None else None) or (snapshot.deadline if snapshot else None)),
        "journal": [{
            "id": str(event.id), "occurred_at": event.occurred_at.isoformat(), "who": event.who, "kind": event.kind,
            "blocker": event.blocker, "text": event.text, "item_index": event.item_index, "value": event.value,
        } for event in (journal.events[:30] if journal is not None else ())],
        "stages": list(KIT_STAGES), "review_values": list(TC_REVIEW_VALUES),
        "terminals": list(PROJECT_TERMINALS), "blockers": list(PLAN_BLOCKERS),
        "assemblies": [{
            "index": assembly.index, "name": assembly.name, "run": _number(assembly.run),
            "item": item_id(kitset.project, assembly.index),
            "stage": _kb_stage(stage_of[assembly.index]) if assembly.index in stage_of else None,
            "techcard": cards.get(assembly.index),
            "kd": [_document_json(document) for document in documents if document.item_index == assembly.index],
            "components": [{
                "art": component.article, "type": component.type, "name": component.name, "link": component.link,
                "per": _number(component.per_unit), "scrap": _number(component.scrap_pct),
                "batch": _number(component.batch(assembly.run)), "unit": unit_for_type(component.type),
                "lot": None if component.lot is None else {
                    "lot_id": str(component.lot.lot_id) if component.lot.lot_id else None,
                    "warehouse": component.lot.warehouse, "grn": component.lot.grn,
                    "package": component.lot.package,
                },
            } for component in assembly.components],
        } for assembly in kitset.assemblies],
    }


@router.get("/{project}")
def kitset(
    project: str,
    request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """`portalKitsetGet`: шапка + сборки со своим составом, этап КБ, техкарта, КД."""
    return _card(request, _kitset(request, project))


class LinePayload(BaseModel):
    index: str = Field(min_length=1, max_length=64)
    art: str = Field(min_length=1, max_length=255)
    per: str = Field(default="0", max_length=32)
    scrap: str = Field(default="0", max_length=32)
    name: str | None = Field(default=None, max_length=1024)
    link: str | None = Field(default=None, max_length=2000)
    lot: dict[str, Any] | None = None


class LinesPayload(BaseModel):
    edits: list[LinePayload] = Field(min_length=1, max_length=500)


@router.put("/{project}/lines")
def save_lines(
    project: str,
    payload: LinesPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKitsetSave`: autosave of ONE spec row — an edit of one cell must not overwrite twenty others."""

    def save():
        with techcards_factory(request)() as uow:
            current = uow.kitsets.get(project)
            if current is None:
                raise KitsetNotFound(f"Карточка набора «{project}» не найдена")
            edits = []
            for item in payload.edits:
                edit = item.model_dump(exclude_unset=True)
                if "lot" in edit and edit["lot"] is None:
                    edit["lot"] = {}
                edits.append(edit)
            updated, count = current.with_edits(edits, who=principal.actor.name, now=now)
            uow.kitsets.save(updated)
            uow.commit()
            return {"project": updated.project, "updated": count}

    return {"res": _run(save)}


@router.get("/{project}/supply")
def supply(
    project: str,
    request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """`portalKitsetSupply`: что по позициям закуплено, что едет, что есть на складе и чего не хватает."""
    kitset = _kitset(request, project)
    lines, _requests = flow.project_supply(
        kitset, requests_factory(request), procurement_factory(request), fulfillment_factory(request),
    )
    return {"project": kitset.project, "lines": [{
        "art": line.article, "name": line.name, "kbs": list(line.kbs), "need": _number(line.need),
        "unit": unit_for_type(line.item_type),
        "free": _number(line.free), "ordered": _number(line.ordered), "kitted": _number(line.kitted),
        "short": _number(line.short),
        "grn": line.grn, "stage": line.stage, "received": line.received,
        "rows": [{"where": row.where, "status": row.status, "source_id": row.source_id} for row in line.rows],
    } for line in lines]}


class OrderMissingPayload(BaseModel):
    """«Заказать недостающее»: выбранные позиции (артикул КД), пусто — все, где не хватает."""

    articles: list[str] = Field(default_factory=list, max_length=500)
    deadline: date | None = None
    delivery_point: str = Field(default="", max_length=512)


@router.post("/{project}/order-missing")
def order_missing(
    project: str,
    payload: OrderMissingPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Заявки на закупку недостающего: Нужно − Свободно − В закупке по каждой позиции; повтор не дублирует."""
    if principal.permissions.get("newrequest") != "edit":
        raise HTTPException(status_code=403, detail="Раздел «Оформить заявку» вам не открыт")
    factory = requests_factory(request)
    if factory is None:
        raise HTTPException(status_code=503, detail="База заявок не настроена")
    command = flow.OrderMissingCommand(
        project=project, articles=tuple(str(item) for item in payload.articles), actor=principal.actor, now=now,
        deadline=payload.deadline, delivery_point=payload.delivery_point,
    )

    def run():
        try:
            return flow.order_missing(
                command, techcards_factory(request), factory, procurement_uow=procurement_factory(request),
                fulfillment_uow=fulfillment_factory(request), production_uow=production_factory(request),
            )
        except RequestValidationError as error:
            raise KitsetValidationError(str(error)) from error

    result = _run(run)
    return {"created": result.created, "skipped": result.skipped, "message": result.message}


@router.get("/{project}/track")
def track(
    project: str,
    request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """«Трек проекта» (ARCH-TRACK-3): всё, что по проекту создано в модулях, по времени; без даты — в конце.
    Только чтение через порты модулей; модуль без фабрики пропускается. Задачи арбитра — раздел «Задачи»."""
    kitset = _kitset(request, project)
    entries = track_flow.project_track(
        kitset.project, techcards=techcards_factory(request), requests=requests_factory(request),
        procurement=procurement_factory(request), receiving=receiving_factory(request),
        fulfillment=fulfillment_factory(request), production=production_factory(request),
        shipping=shipping_factory(request), tasks=operations_factory(request),
    )
    return {"project": kitset.project, "entries": [entry.payload() for entry in entries]}


class IssuePayload(BaseModel):
    deadline: date | None = None


@router.post("/{project}/issue")
def issue(
    project: str,
    payload: IssuePayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalIssueFromKitset`: по заявке на комплектацию на каждую сборку с составом."""
    factory = fulfillment_factory(request)
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    kitset = _kitset(request, project)
    result = _run(lambda: flow.issue_from_kitset(kitset, payload.deadline, principal.actor, now, factory))
    return {"created": result.created, "skipped": result.skipped, "message": result.message}


@router.delete("/{project}")
def delete(
    project: str,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKitsetDelete`: проект уходит целиком во всех разделах сразу, с хвостами без движения."""
    return {"res": _run(lambda: flow.delete_kitset(
        project, principal.actor, now,
        techcards_factory(request), production_factory(request), shipping_factory(request),
        fulfillment_uow=fulfillment_factory(request), requests_uow=requests_factory(request),
        procurement_uow=procurement_factory(request),
    ))}


class MarkPayload(BaseModel):
    kb: str = Field(default="", max_length=64)
    kind: str = Field(min_length=1, max_length=16)
    value: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=5000)


@router.post("/{project}/marks")
def mark(
    project: str,
    payload: MarkPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKitMark`: ручной этап КБ и проверка ТК — в журнал проекта, как в «Производственном плане»."""
    if payload.kind not in {"stage", "tcReview"}:
        raise HTTPException(status_code=422, detail="Неизвестный вид отметки.")
    factory = production_factory(request)
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    kitset = _kitset(request, project)
    event = _run(lambda: add_plan_event(AddPlanEventCommand(
        project=kitset.project, kind=payload.kind, blocker="", text=payload.text, deadline=None,
        known_projects=(kitset.project,), actor=principal.actor, now=now,
        item_index=payload.kb, value=payload.value,
    ), factory()))
    return {"msg": event.text or event.value, "event": {"kind": event.kind, "value": event.value, "item_index": event.item_index}}


class StatusPayload(BaseModel):
    action: str = Field(min_length=1, max_length=16)
    value: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=5000)


@router.post("/{project}/status")
def change_status(
    project: str,
    payload: StatusPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Статус проекта в один клик: этап (на все КБ) / «авто» / Отказ·Слился·Отменён, блокер или его снятие."""
    factory = production_factory(request)
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    kitset = _kitset(request, project)
    events = _run(lambda: flow.change_project_status(flow.ChangeProjectStatusCommand(
        project=kitset.project, item_indexes=tuple(assembly.index for assembly in kitset.assemblies),
        action=payload.action, value=payload.value, text=payload.text, actor=principal.actor, now=now,
    ), factory))
    return {"events": len(events), "card": _card(request, kitset)}


class JournalPayload(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    text: str = Field(default="", max_length=5000)
    deadline: date | None = None


@router.post("/{project}/journal")
def journal_note(
    project: str,
    payload: JournalPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Комментарий или дедлайн в журнал проекта прямо из карточки (раньше — только нижняя панель плана)."""
    if payload.kind not in {"note", "deadline"}:
        raise HTTPException(status_code=422, detail="Неизвестный вид записи.")
    factory = production_factory(request)
    if factory is None:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    kitset = _kitset(request, project)
    _run(lambda: add_plan_event(AddPlanEventCommand(
        project=kitset.project, kind=payload.kind, blocker="", text=payload.text, deadline=payload.deadline,
        known_projects=(kitset.project,), actor=principal.actor, now=now,
    ), factory()))
    return {"card": _card(request, kitset)}


class DocumentPayload(BaseModel):
    index: str = Field(min_length=1, max_length=64)
    file_name: str = Field(min_length=1, max_length=400)
    content_type: str = Field(default="", max_length=255)
    content_b64: str = Field(min_length=1)


# The harness drawing (TECHMAP-GRAPHER design document, schemaVersion 1) kept as a КД row of its position and
# edited in ERP (harness_api.py): one row per position, saved over itself.
HARNESS_CONTENT_TYPE = "application/vnd.fraxis.harness+json"


def document_kind(document) -> str:
    """«harness» — the harness drawing edited in ERP; «file» — an attached file."""
    return "harness" if (document.content_type or "").split(";")[0].strip() == HARNESS_CONTENT_TYPE else "file"


def _document_json(document) -> dict:
    """A row of the «КД» register as `tcKdByAssembly_` hands it to the card. `stored` — the bytes are in ERP (the
    card links the download); otherwise `url` is the archived Drive link of a row from before the move, read-only.
    `kind` — «harness» for the harness drawing (opened in the drawing editor), «file» otherwise."""
    return {
        "id": str(document.id), "index": document.item_index, "name": document.file_name,
        "size": document.size, "stored": document.stored, "url": document.url or None,
        "uploaded_at": document.uploaded_at.isoformat(), "who": document.uploaded_by,
        "kind": document_kind(document),
    }


@router.post("/{project}/documents", status_code=201)
def add_document(
    project: str,
    payload: DocumentPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKdAdd`: вложить файл к сборке — файл хранится в базе ERP, строка — в реестре КД."""
    content = _decode(payload.content_b64)
    document = _run(lambda: flow.attach_document(
        project, payload.index, payload.file_name, payload.content_type, content,
        principal.actor, now, techcards_factory(request),
    ))
    return {"res": _document_json(document)}


@router.get("/{project}/documents/{document_id}")
def document(
    project: str,
    document_id: UUID,
    request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> Response:
    """Файл КД из базы ERP. У строки из времён Диска байтов нет — 404, карточка ведёт на архивную ссылку."""
    kitset = _kitset(request, project)
    with techcards_factory(request)() as uow:
        found = uow.kitsets.document_content(kitset.project, document_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Файл КД не найден")
    meta, content = found
    return file_response(content, meta.content_type or "application/octet-stream", meta.file_name)


@router.delete("/{project}/documents/{document_id}")
def delete_document(
    project: str,
    document_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """`portalKdDelete`: отвязать вложение — строка реестра и файл уходят из ERP."""
    deleted = _run(lambda: flow.detach_document(
        project, document_id, principal.actor, now, techcards_factory(request),
    ))
    return {"res": {"deleted": deleted}}


@router.get("/{project}/bom/{index}")
def bom(
    project: str,
    index: str,
    request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """`tcLoadBomFromKit`: состав сборки для генератора — из сохранённой карточки, с правками оператора.
    Аналог по КД → оригинал к применению (kitUseArt_): генератор ищет контакт по артикулу к применению.
    `calc_ops` — строки листа КБn расчёта (`tcCalcOps_`): длины резки и лужение сметчика. Хранятся в карточке с
    заведения / «Обновить из расчёта»; у старой карточки лист читается один раз и запоминается."""
    kitset = _kitset(request, project)
    assembly = kitset.assembly(index)
    if assembly is None:
        raise HTTPException(status_code=404, detail=f"В наборе «{kitset.project}» нет сборки {index}")
    return flow.generator_bom(kitset, assembly, flow.assembly_calc_ops(
        kitset, assembly, lambda: flow.calc_sheets_reader(request.app.state), techcards_factory(request),
    ))


class TechcardPayload(BaseModel):
    deadline: date | None = None


@router.post("/{project}/techcards/{index}")
def open_techcard(
    project: str,
    index: str,
    payload: TechcardPayload,
    request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """«Генератор» сборки: карточка техкарты изделия проекта+КБ, заводится при первом открытии."""
    kitset = _kitset(request, project)
    assembly = kitset.assembly(index)
    if assembly is None:
        raise HTTPException(status_code=404, detail=f"В наборе «{kitset.project}» нет сборки {index}")
    factory = techcards_factory(request)
    with factory() as uow:
        card = uow.techcards.by_key(kitset.project, assembly.index)
    if card is None:
        card = _run(lambda: create_project_techcard(CreateProjectTechcardCommand(
            project=kitset.project, item_index=assembly.index,
            product_name=assembly.name or item_id(kitset.project, assembly.index),
            source_calculation=kitset.calc_source, deadline=payload.deadline,
            actor=principal.actor, now=now,
        ), factory()))
    return {"card_id": str(card.id)}
