"""Native Production Plan API composed from module-owned facts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.modules.fulfillment.application import UnitOfWork as FulfillmentUnitOfWork
from app.modules.procurement.application import UnitOfWork as ProcurementUnitOfWork
from app.modules.requests.application import UnitOfWork as RequestsUnitOfWork
from app.modules.shipping.application import UnitOfWork as ShippingUnitOfWork
from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal
from app.workflows import project_kitset as stage_flow
from app.workflows.production_plan import build_production_plan

from .application import UnitOfWork
from .domain import ProductionValidationError
from .plan import (
    KIT_STAGES,
    PLAN_BLOCKERS,
    PLAN_MARK_ARRIVAL,
    PLAN_MARK_SUPPLIED,
    PROJECT_TERMINALS,
    SHOP_STATUSES,
    TC_REVIEW_VALUES,
    PlanEvent,
    PlanProjectSnapshot,
    normalize_project_number,
)
from .plan_sheet import calendar_span
from .plan_application import (
    AddPlanEventCommand,
    ReplacePlanProjectsCommand,
    StageChangeConflict,
    add_plan_event,
    replace_plan_projects,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/production/plan", tags=["production-plan"])


class PlanProjectPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    customer: str = Field(default="", max_length=512)
    opened_on: date | None = None
    deadline: date | None = None
    status: str = Field(default="", max_length=64)
    terminal: bool = False


class PlanProjectsSnapshotPayload(BaseModel):
    projects: list[PlanProjectPayload] = Field(max_length=10_000)


class PlanEventPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=32)
    blocker: str = Field(default="", max_length=32)
    text: str = Field(default="", max_length=5000)
    deadline: date | None = None
    item_index: str = Field(default="", max_length=64)
    value: str = Field(default="", max_length=64)
    field: str = Field(default="", max_length=32)
    # «Этап КБ» назад — причина обязательна (годится и `text`).
    reason: str = Field(default="", max_length=2000)
    # «Этап КБ»: `preview_token` из `/stage-preview` — изменилось закрываемое с тех пор → 409.
    preview_token: str = Field(default="", max_length=64)


def _factory(request: Request, name: str, *, required: bool = False):
    factory = getattr(request.app.state, name, None)
    if callable(factory):
        return factory
    if required:
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return None


def production_factory(request: Request) -> Callable[[], UnitOfWork]:
    return _factory(request, "production_uow_factory", required=True)


def techcards_factory(request: Request) -> Callable[[], TechcardsUnitOfWork] | None:
    return _factory(request, "techcards_uow_factory")


def fulfillment_factory(request: Request) -> Callable[[], FulfillmentUnitOfWork] | None:
    return _factory(request, "fulfillment_uow_factory")


def shipping_factory(request: Request) -> Callable[[], ShippingUnitOfWork] | None:
    return _factory(request, "shipping_uow_factory")


def requests_factory(request: Request) -> Callable[[], RequestsUnitOfWork] | None:
    return _factory(request, "uow_factory")


def procurement_factory(request: Request) -> Callable[[], ProcurementUnitOfWork] | None:
    return _factory(request, "procurement_uow_factory")


def _read(factory, reader, default=()):
    if factory is None:
        return default
    with factory() as uow:
        return reader(uow)


def _safe_read(name: str, factory, reader, default=()):
    """ProdPlan.gs prodPlanData_ `safe`: a failing source leaves its column empty, the plan still loads."""
    try:
        return _read(factory, reader, default)
    except Exception:  # noqa: BLE001 - any source failure degrades to an empty column, as in SUPPLYMAP
        logger.warning("production plan source %s failed", name, exc_info=True)
        return default


def _event_result(event: PlanEvent) -> dict[str, Any]:
    return {
        "id": str(event.id), "project": event.project,
        "occurred_at": event.occurred_at.isoformat(), "who": event.who,
        "kind": event.kind, "blocker": event.blocker, "text": event.text,
        "deadline": event.deadline.isoformat() if event.deadline else None,
        "item_index": event.item_index, "value": event.value, "field": event.field,
        "from_value": event.from_value, "reason": event.reason,
    }


def _processed(outputs) -> dict[UUID, Decimal]:
    """prodOpState_ input: good + scrap per operation, from active (not superseded) outputs."""
    result: dict[UUID, Decimal] = {}
    for output in outputs:
        result[output.operation_id] = result.get(output.operation_id, Decimal(0)) + (
            output.good_quantity + output.defect_quantity
        )
    return result


@router.get("")
def production_plan(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    today: Annotated[date, Depends(moscow_today)],
    production_uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
    techcards_uow_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_factory)],
    fulfillment_uow_factory: Annotated[Callable[[], FulfillmentUnitOfWork] | None, Depends(fulfillment_factory)],
    shipping_uow_factory: Annotated[Callable[[], ShippingUnitOfWork] | None, Depends(shipping_factory)],
    requests_uow_factory: Annotated[Callable[[], RequestsUnitOfWork] | None, Depends(requests_factory)],
    procurement_uow_factory: Annotated[Callable[[], ProcurementUnitOfWork] | None, Depends(procurement_factory)],
) -> dict:
    # The registry is the plan itself (erpProjectsList_ is outside `safe` in SUPPLYMAP); every other
    # source degrades to an empty column on failure.
    with production_uow_factory() as uow:
        projects = tuple(uow.plan_projects.list())
    tasks = _safe_read("tasks", production_uow_factory, lambda uow: tuple(uow.production.list(limit=10_000)))
    events = _safe_read("journal", production_uow_factory, lambda uow: tuple(uow.plan_events.list()))
    cards = _safe_read("techcards", techcards_uow_factory, lambda uow: tuple(uow.techcards.list()))
    kitsets = _safe_read(
        "kitsets", techcards_uow_factory,
        lambda uow: tuple(uow.kitsets.list()) if hasattr(uow, "kitsets") else (),
    )
    fulfillment = _safe_read("fulfillment", fulfillment_uow_factory, lambda uow: tuple(uow.fulfillment.list(limit=10_000)))
    goods = _safe_read("goods", shipping_uow_factory, lambda uow: tuple(uow.goods.list(limit=10_000)))
    requests = _safe_read("requests", requests_uow_factory, lambda uow: tuple(uow.requests.recent(10_000)))
    procurement = _safe_read("procurement", procurement_uow_factory, lambda uow: tuple(uow.procurement.list(limit=10_000)))
    # ОТК лестницы: коробки последней операции в очереди ОТК или проверенные ОТК.
    boxes = _safe_read("boxes", production_uow_factory, lambda uow: tuple(uow.boxes.list(limit=100_000)))
    inspected = _safe_read("qc", production_uow_factory, lambda uow: frozenset(uow.qc.inspected_box_ids()), frozenset())
    worked = _safe_read("work", production_uow_factory, lambda uow: frozenset(uow.production.worked([task.id for task in tasks])), frozenset())
    running = tuple(task.id for task in tasks)
    outputs = _safe_read("outputs", production_uow_factory, lambda uow: tuple(uow.outputs.active_for_tasks(running)))
    rows = build_production_plan(
        cards=cards, tasks=tasks, fulfillment=fulfillment, goods=goods,
        requests=requests, procurement=procurement, projects=projects,
        events=events, today=today, kitsets=kitsets, processed=_processed(outputs),
        boxes=boxes, inspected=inspected, worked=worked,
    )
    first_day, last_day = calendar_span(today)
    return {
        "blockers": list(PLAN_BLOCKERS), "stages": list(KIT_STAGES), "terminals": list(PROJECT_TERMINALS),
        "review_values": list(TC_REVIEW_VALUES),
        "shop_statuses": list(SHOP_STATUSES), "supplied_values": list(PLAN_MARK_SUPPLIED),
        "arrival_values": list(PLAN_MARK_ARRIVAL),
        "today": today.isoformat(), "calendar_from": first_day.isoformat(), "calendar_to": last_day.isoformat(),
        "rows": [{
            "project": row.project, "customer": row.customer,
            "opened_on": row.opened_on.isoformat() if row.opened_on else None,
            "deadline": row.deadline.isoformat() if row.deadline else None,
            "days_left": row.days_left, "stage": row.stage.status, "terminal": row.terminal,
            "stage_manual": row.stage.manual,
            "kits": [{
                "item_index": item.item_index, "status": item.status, "manual": item.manual,
                "tc": item.tc, "kit": item.kit, "skipped": list(item.skipped),
                "review": None if item.review is None else {
                    "value": item.review.value, "text": item.review.text,
                    "who": item.review.who,
                    "occurred_at": item.review.occurred_at.isoformat() if item.review.occurred_at else None,
                },
            } for item in row.stage.kits],
            "supply": None if row.supply is None else {
                "total": row.supply.total, "stock": row.supply.stock,
                "transit": row.supply.transit, "buying": row.supply.buying,
                "request": row.supply.request,
                "customer_supplied": row.supply.customer_supplied,
                "customer_supplied_stock": row.supply.customer_supplied_stock,
                "eta": row.supply.eta,
            },
            "plan": None if row.plan is None else {
                "tasks": row.plan.tasks, "assigned": row.plan.assigned,
                "total": row.plan.total,
                "start": row.plan.start.isoformat() if row.plan.start else None,
                "finish": row.plan.finish.isoformat() if row.plan.finish else None,
            },
            "blocker": None if row.journal.blocker is None else {
                "type": row.journal.blocker.type, "text": row.journal.blocker.text,
                "since": row.journal.blocker.since.isoformat(), "who": row.journal.blocker.who,
            },
            "last": None if row.journal.last is None else {
                "occurred_at": row.journal.last.occurred_at.isoformat(),
                "who": row.journal.last.who, "text": row.journal.last.text,
            },
            "events": [_event_result(event) for event in row.journal.events[:30]],
            "group": row.group,
            "sheet": {
                name: {"value": cell.value, "auto": cell.auto, "manual": cell.manual}
                for name, cell in (row.sheet or {}).items()
            },
            "calendar": {
                "work": [day.isoformat() for day in row.calendar.work],
                "starts": {day.isoformat(): quantity for day, quantity in row.calendar.starts},
                "notes": {day.isoformat(): text for day, text in row.calendar.notes},
            },
        } for row in rows],
    }


@router.put("/projects-snapshot")
def save_project_snapshot(
    payload: PlanProjectsSnapshotPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
) -> dict:
    try:
        projects = replace_plan_projects(ReplacePlanProjectsCommand(
            projects=tuple(PlanProjectSnapshot(
                project=item.project, customer=item.customer,
                opened_on=item.opened_on, deadline=item.deadline,
                status=item.status, terminal=item.terminal,
            ) for item in payload.projects),
            actor=principal.actor, now=now,
        ), uow_factory())
    except (ProductionValidationError, IntegrityError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"projects": len(projects)}


def _known_projects(uow_factory, techcards_uow_factory) -> list[str]:
    with uow_factory() as uow:
        known_projects = [item.project for item in uow.plan_projects.list()]
    if techcards_uow_factory is not None:
        known_projects.extend(_read(
            techcards_uow_factory,
            lambda uow: [item.project for item in uow.techcards.list()],
        ))
    return known_projects


def _stage_state(project: str, kb: str, uow_factory, techcards_uow_factory, fulfillment_uow_factory, shipping_uow_factory):
    if normalize_project_number(project) not in {
        normalize_project_number(item) for item in _known_projects(uow_factory, techcards_uow_factory)
    }:
        raise ProductionValidationError("Проект не найден в реестре.")
    return stage_flow.load_project_stage_state(
        project, production=uow_factory, techcards=techcards_uow_factory,
        fulfillment=fulfillment_uow_factory, shipping=shipping_uow_factory, kb=kb,
    )


@router.get("/stage-preview")
def stage_preview(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
    techcards_uow_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_factory)],
    fulfillment_uow_factory: Annotated[Callable[[], FulfillmentUnitOfWork] | None, Depends(fulfillment_factory)],
    shipping_uow_factory: Annotated[Callable[[], ShippingUnitOfWork] | None, Depends(shipping_factory)],
    project: Annotated[str, Query(min_length=1, max_length=255)],
    value: Annotated[str, Query(min_length=1, max_length=64)],
    kb: Annotated[str, Query(max_length=64)] = "",
) -> dict:
    """Предпросмотр ручной смены этапа с «Производственного плана» — тот же ответ, что у карточки проекта: по КБ что
    закроется (`close`) и что останется с причиной (`keep`), итоги, `preview_token` для записи."""
    try:
        state = _stage_state(project, kb, uow_factory, techcards_uow_factory, fulfillment_uow_factory, shipping_uow_factory)
        return stage_flow.preview_project_stage(state, value, kb)
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/events", status_code=201)
def save_plan_event(
    payload: PlanEventPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
    techcards_uow_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_factory)],
    fulfillment_uow_factory: Annotated[Callable[[], FulfillmentUnitOfWork] | None, Depends(fulfillment_factory)],
    shipping_uow_factory: Annotated[Callable[[], ShippingUnitOfWork] | None, Depends(shipping_factory)],
) -> dict:
    if payload.kind == "stage":
        # Ручной этап — через единую смену этапа (история «было → стало», причина назад, одна транзакция).
        try:
            state = _stage_state(
                payload.project, payload.item_index, uow_factory, techcards_uow_factory,
                fulfillment_uow_factory, shipping_uow_factory,
            )
            change = stage_flow.change_project_stage(stage_flow.ChangeProjectStageCommand(
                project=payload.project, value=payload.value, kb=payload.item_index,
                reason=payload.reason or payload.text, actor=principal.actor, now=now,
                preview_token=payload.preview_token,
            ), state, uow_factory)
        except StageChangeConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ProductionValidationError, IntegrityError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "event": _event_result(change.events[-1]) if change.events else None,
            "events": [_event_result(event) for event in change.events], "change": change.payload(),
        }
    known_projects = _known_projects(uow_factory, techcards_uow_factory)
    try:
        event = add_plan_event(AddPlanEventCommand(
            project=payload.project, kind=payload.kind, blocker=payload.blocker,
            text=payload.text, deadline=payload.deadline,
            item_index=payload.item_index, value=payload.value, field=payload.field,
            known_projects=tuple(known_projects), actor=principal.actor, now=now,
        ), uow_factory())
    except (ProductionValidationError, IntegrityError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"event": _event_result(event)}
