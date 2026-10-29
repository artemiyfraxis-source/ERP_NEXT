"""HTTP: «Готово к запуску» — КБ с выданной ЭТК и набранным комплектом без запущенного тиража, запуск в один клик
(правила — `app/modules/production/launch_ready.py`). Читает техкарты, заявки на комплект и готовую продукцию,
поэтому живёт в workflows: межмодульная связка только здесь."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.production.api import closed_kbs, production_uow_factory, techcards_uow_factory
from app.modules.production.application import UnitOfWork
from app.modules.production.autoplan_api import _handle, _techcards
from app.modules.production.board import unlaunched_cards
from app.modules.production.domain import ProductionStatus
from app.modules.production.launch_ready import (
    MAX_BATCH,
    LaunchItem,
    LaunchReadyCommand,
    launch_ready,
    planned_operations,
    ready_rows,
    suggest_site,
)
from app.modules.production.plan import (
    kit_key_from_purpose,
    kit_stage_key,
    normalize_project_number,
    summarize_journal,
)
from app.modules.production.workload import ZERO, done_of
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal
from app.workflows.finished_goods import read_delivered
from app.workflows.production_plan import kit_stage_facts, project_deadline

router = APIRouter(prefix="/api/production/launch-ready", tags=["production"])

# Заявка на комплект от «Укомплектован» и дальше — тот же набор, что факт «kit» лестницы (`kit_stage_facts`);
# порядок — для подписи «Комплект»: показывается самый дальний статус заявок КБ.
KIT_ORDER = (
    FulfillmentStatus.COMPLETE, FulfillmentStatus.READY, FulfillmentStatus.ISSUED,
    FulfillmentStatus.RETURNED, FulfillmentStatus.DONE,
)


def _factory(request: Request, name: str) -> Callable[[], Any] | None:
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


def _fulfillment(request: Request) -> Callable[[], Any] | None:
    return _factory(request, "fulfillment_uow_factory")


def _shipping(request: Request) -> Callable[[], Any] | None:
    return _factory(request, "shipping_uow_factory")


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _number(value: Decimal) -> float | int:
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def _kits(requests: list) -> dict[str, str]:
    """kit_stage_key → статус заявки на комплект для КБ с набранным комплектом (факт «kit» `kit_stage_facts`)."""
    facts, _marks = kit_stage_facts(cards=(), tasks=(), fulfillment=requests, goods=(), marks={})
    picked = facts["kit"]
    best: dict[str, int] = {}
    for request in requests:
        key = kit_key_from_purpose(request.purpose)
        if key in picked and request.status in KIT_ORDER:
            best[key] = max(best.get(key, 0), KIT_ORDER.index(request.status))
    return {key: KIT_ORDER[best.get(key, 0)].value for key in picked}


@router.get("")
def launch_ready_list(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    fulfillment_factory: Annotated[Callable[[], Any] | None, Depends(_fulfillment)],
    shipping_factory: Annotated[Callable[[], Any] | None, Depends(_shipping)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    """КБ, готовые к запуску, с тиражом, площадкой, сроком и операциями, которые создаст запуск. Ничего не пишет."""
    cards, ready = _techcards(techcards_factory, closed)
    requests: list = []
    if fulfillment_factory is not None:
        with fulfillment_factory() as fulfillment:
            requests = list(fulfillment.fulfillment.list(limit=10_000))
    goods: list = []
    if shipping_factory is not None:
        with shipping_factory() as shipping:
            goods = list(shipping.goods.list(limit=1_000_000))
    with uow_factory() as uow:
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        open_cards = unlaunched_cards(uow, stored, ready, cards)
        tasks = [task for task in uow.production.list(limit=10_000) if task.status is not ProductionStatus.CANCELLED]
        delivered = read_delivered(uow, tasks)
        snapshots = {normalize_project_number(item.project): item for item in uow.plan_projects.list()}
        journal = summarize_journal(uow.plan_events.list())
        site_of = {operator.id: operator.site for operator in uow.operators.list()}

    goods_by_key = {kit_stage_key(good.project, good.item_index): good for good in goods}
    by_key: dict[str, list] = {}
    by_project: dict[str, list] = {}
    for task in tasks:
        by_key.setdefault(kit_stage_key(task.project, task.item), []).append(task)
        by_project.setdefault(normalize_project_number(task.project), []).append(task)
    cards_by_project: dict[str, list] = {}
    for card in ready:
        cards_by_project.setdefault(normalize_project_number(card.project), []).append(card)

    done, sites, deadlines = {}, {}, {}
    for card in open_cards:
        good, shop = goods_by_key.get(card.key), delivered.get(card.key)
        done[card.key] = done_of(shop.delivered if shop else ZERO, good.accepted if good else ZERO, good.sent if good else ZERO)
        project = normalize_project_number(card.project)
        sites[card.key] = suggest_site(by_key.get(card.key, ()), by_project.get(project, ()), site_of)
        deadlines[card.key] = project_deadline(journal.get(project), snapshots.get(project), cards_by_project.get(project, ()))
    summary = ready_rows(open_cards, _kits(requests), done, sites, deadlines)

    rows = []
    for row in summary.rows:
        card = row.card
        operations = planned_operations(card, row.quantity)
        rows.append({
            "techcard_id": str(card.techcard_id), "techcard_version": card.version,
            "techcard_revision": card.revision if card.revision >= 1 else None,
            "project": card.project, "index": card.index, "name": card.name, "customer": card.customer,
            "run": card.quantity, "launched": card.launched, "remainder": card.remainder,
            "done": _number(row.done.quantity), "done_source": row.done.source,
            "quantity": row.quantity, "site": row.site, "site_note": row.site_note, "kit": row.kit,
            "deadline": _iso(row.deadline), "task_deadline": _iso(card.deadline),
            "warnings": list(card.route.warnings),
            "operations": [{
                "position": operation.position, "code": operation.code, "stage": operation.stage,
                "description": operation.description,
                "seconds_per_unit": float(operation.seconds_per_unit), "setup_seconds": float(operation.setup_seconds),
                "per_unit": _number(factor),
                "etk_ops": [{"position": item.position, "code": item.code, "stage": item.stage} for item in operation.etk_ops],
            } for operation, factor in operations],
        })
    return {"items": rows, "waiting_kit": summary.waiting_kit, "no_run": list(summary.no_run)}


class LaunchItemPayload(BaseModel):
    # Строка списка, которую видел мастер: перевыпустили техкарту или запустили по КБ ещё — отказ по этой строке.
    techcard_id: UUID
    techcard_version: int = Field(ge=1)
    launched: int = Field(ge=0)
    # Тираж задания; null — весь остаток тиража КБ.
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)


class LaunchPayload(BaseModel):
    items: list[LaunchItemPayload] = Field(min_length=1, max_length=MAX_BATCH)


@router.post("/launch")
def launch_selected(
    payload: LaunchPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    """«Запустить» / «Запустить выбранные»: задание по каждой КБ тем же запуском, что «Создать задание» с доски."""
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База техкарт не настроена")
    cards, ready = _techcards(techcards_factory, closed)
    done = _handle(lambda: launch_ready(LaunchReadyCommand(
        items=tuple(LaunchItem(item.techcard_id, item.techcard_version, item.launched, item.quantity) for item in payload.items),
        actor=principal.actor, now=now,
    ), uow_factory, cards, ready))
    return {
        "created": [{
            "id": str(task.id), "number": task.number, "project": task.project, "item": task.item,
            "quantity": task.quantity, "techcard_id": str(task.techcard_id) if task.techcard_id else None,
        } for task in done.created],
        "refused": [{"techcard_id": str(item.item.techcard_id), "reason": item.reason} for item in done.refused],
    }
