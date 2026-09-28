"""Native Production Plan API composed from module-owned facts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.modules.fulfillment.application import UnitOfWork as FulfillmentUnitOfWork
from app.modules.procurement.application import UnitOfWork as ProcurementUnitOfWork
from app.modules.requests.application import UnitOfWork as RequestsUnitOfWork
from app.modules.shipping.application import UnitOfWork as ShippingUnitOfWork
from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal
from app.workflows.production_plan import build_production_plan

from .application import UnitOfWork
from .domain import ProductionValidationError
from .plan import KIT_STAGES, PLAN_BLOCKERS, TC_REVIEW_VALUES, PlanEvent, PlanProjectSnapshot
from .plan_application import (
    AddPlanEventCommand,
    ReplacePlanProjectsCommand,
    add_plan_event,
    replace_plan_projects,
)


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


def _event_result(event: PlanEvent) -> dict[str, Any]:
    return {
        "id": str(event.id), "project": event.project,
        "occurred_at": event.occurred_at.isoformat(), "who": event.who,
        "kind": event.kind, "blocker": event.blocker, "text": event.text,
        "deadline": event.deadline.isoformat() if event.deadline else None,
        "item_index": event.item_index, "value": event.value,
    }


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
    with production_uow_factory() as uow:
        tasks = tuple(uow.production.list(limit=10_000))
        projects = tuple(uow.plan_projects.list())
        events = tuple(uow.plan_events.list())
    cards = _read(techcards_uow_factory, lambda uow: tuple(uow.techcards.list()))
    fulfillment = _read(fulfillment_uow_factory, lambda uow: tuple(uow.fulfillment.list(limit=10_000)))
    goods = _read(shipping_uow_factory, lambda uow: tuple(uow.goods.list(limit=10_000)))
    requests = _read(requests_uow_factory, lambda uow: tuple(uow.requests.recent(10_000)))
    procurement = _read(procurement_uow_factory, lambda uow: tuple(uow.procurement.list(limit=10_000)))
    rows = build_production_plan(
        cards=cards, tasks=tasks, fulfillment=fulfillment, goods=goods,
        requests=requests, procurement=procurement, projects=projects,
        events=events, today=today,
    )
    return {
        "blockers": list(PLAN_BLOCKERS), "stages": list(KIT_STAGES),
        "review_values": list(TC_REVIEW_VALUES),
        "rows": [{
            "project": row.project, "customer": row.customer,
            "opened_on": row.opened_on.isoformat() if row.opened_on else None,
            "deadline": row.deadline.isoformat() if row.deadline else None,
            "days_left": row.days_left, "stage": row.stage.status,
            "stage_manual": row.stage.manual,
            "kits": [{
                "item_index": item.item_index, "status": item.status, "manual": item.manual,
                "tc": item.tc, "kit": item.kit,
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


@router.post("/events", status_code=201)
def save_plan_event(
    payload: PlanEventPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
    techcards_uow_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_factory)],
) -> dict:
    with uow_factory() as uow:
        known_projects = [item.project for item in uow.plan_projects.list()]
    if techcards_uow_factory is not None:
        known_projects.extend(_read(
            techcards_uow_factory,
            lambda uow: [item.project for item in uow.techcards.list()],
        ))
    try:
        event = add_plan_event(AddPlanEventCommand(
            project=payload.project, kind=payload.kind, blocker=payload.blocker,
            text=payload.text, deadline=payload.deadline,
            item_index=payload.item_index, value=payload.value,
            known_projects=tuple(known_projects), actor=principal.actor, now=now,
        ), uow_factory())
    except (ProductionValidationError, IntegrityError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"event": _event_result(event)}
