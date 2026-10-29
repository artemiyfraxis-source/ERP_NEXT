"""HTTP: «Загрузка» — загрузка цеха и прогноз готовности по выданным ЭТК, одним запросом, только чтение (workload.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_today
from app.shared.http_auth import Principal, production_overview_principal
from app.workflows.finished_goods import read_delivered
from app.workflows.production_plan import project_deadline

from .api import closed_kbs, production_uow_factory, techcards_uow_factory
from .application import UnitOfWork, _shift
from .domain import ProductionStatus
from .launchable import ready_cards
from .plan import kit_stage_key, normalize_project_number, summarize_journal
from .workload import (
    HORIZON_DAYS,
    KINDS,
    SHOP,
    SITES,
    UNASSIGNED,
    ZERO,
    AbsenceSpan,
    KbLoad,
    ProjectLoad,
    Staff,
    bottlenecks,
    capacity_days,
    demand,
    done_of,
    forecast,
    kb_site,
    plan_order,
    unit_hours,
    weeks,
)


router = APIRouter(prefix="/api/production/workload", tags=["production"])

WEEKS_SHOWN = 8


def _hours(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _shipping(request: Request) -> Callable[[], Any] | None:
    factory = getattr(request.app.state, "shipping_uow_factory", None)
    return factory if callable(factory) else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


@router.get("")
def workload(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    today: Annotated[date, Depends(moscow_today)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    shipping_factory: Annotated[Callable[[], Any] | None, Depends(_shipping)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    cards: list = []
    kitsets: tuple = ()
    if techcards_factory is not None:
        with techcards_factory() as techcards:
            cards = [card for card in techcards.techcards.list() if card.status is TechcardStatus.ISSUED]
            kitsets = tuple(techcards.kitsets.list()) if hasattr(techcards, "kitsets") else ()
    goods: list = []
    if shipping_factory is not None:
        with shipping_factory() as shipping:
            goods = list(shipping.goods.list(limit=1_000_000))
    with uow_factory() as uow:
        tasks = [task for task in uow.production.list(limit=10_000) if task.status is not ProductionStatus.CANCELLED]
        delivered = read_delivered(uow, tasks)
        snapshots = {normalize_project_number(item.project): item for item in uow.plan_projects.list()}
        journal = summarize_journal(uow.plan_events.list())
        holidays = uow.calendar.holidays()
        absences = tuple(AbsenceSpan(item.operator_id, item.from_date, item.to_date) for item in uow.calendar.absences())
        shift = _shift(uow.calendar)
        operators = list(uow.operators.list())

    site_of = {operator.id: operator.site for operator in operators}
    staff = [Staff(operator.id, operator.site) for operator in operators if operator.active and operator.shop_worker]
    goods_by_key = {kit_stage_key(good.project, good.item_index): good for good in goods}
    tasks_by_key: dict[str, list] = {}
    for task in tasks:
        tasks_by_key.setdefault(kit_stage_key(task.project, task.item), []).append(task)

    # Одна ЭТК на КБ: при двух выданных — последняя по ревизии.
    current: dict[str, Any] = {}
    for card in ready_cards(cards, kitsets, closed):
        if card.key not in current or card.revision >= current[card.key].revision:
            current[card.key] = card
    cards_by_project: dict[str, list] = {}
    for card in cards:
        cards_by_project.setdefault(normalize_project_number(card.project), []).append(card)

    grouped: dict[str, list[KbLoad]] = {}
    customers: dict[str, str] = {}
    no_run: list[str] = []
    for key, card in sorted(current.items()):
        project = normalize_project_number(card.project) or card.project.strip()
        good = goods_by_key.get(key)
        shop = delivered.get(key)
        done = done_of(shop.delivered if shop else ZERO, good.accepted if good else ZERO, good.sent if good else ZERO)
        own = tasks_by_key.get(key, [])
        site, note = kb_site(
            (task.handed_to for task in own),
            (site_of.get(operator_id, "") for task in own if not task.handed_to for operation in task.operations
             for operator_id in {*operation.operator_ids, *(share.operator_id for share in operation.shares)}),
        )
        kb = KbLoad(project, card.index, card.name, card.quantity, done, site, unit_hours(card.route.steps), note)
        if card.quantity is None:
            no_run.append(f"{project} {card.index}")
        elif kb.hours <= 0:
            continue
        grouped.setdefault(project, []).append(kb)
        customers.setdefault(project, card.customer or (good.customer if good else ""))

    projects = [
        ProjectLoad(
            project=project,
            customer=(snapshots[project].customer if project in snapshots else "") or customers.get(project, ""),
            deadline=project_deadline(journal.get(project), snapshots.get(project), cards_by_project.get(project, ())),
            opened_on=snapshots[project].opened_on if project in snapshots else None,
            kbs=tuple(kbs),
        )
        for project, kbs in grouped.items()
    ]
    days = capacity_days(today, HORIZON_DAYS, shift, holidays, staff, absences)
    plan = forecast(projects, days)
    week_rows = weeks(days, plan, demand(projects, days), WEEKS_SHOWN)
    order = {item.project: index for index, item in enumerate(plan_order(projects))}
    people = {site: sum(1 for person in staff if (person.site if person.site in SITES else SITES[0]) == site) for site in SITES}

    def kinds_of(values: dict[str, Decimal]) -> dict[str, float]:
        return {kind: _hours(values[kind]) for kind in KINDS if values.get(kind, ZERO) > 0}

    rows = []
    for item in projects:
        result = plan.forecasts.get(item.project)
        by_kind: dict[str, Decimal] = {}
        for kb in item.kbs:
            for kind, hours in kb.by_kind.items():
                by_kind[kind] = by_kind.get(kind, ZERO) + hours
        rows.append({
            "project": item.project, "customer": item.customer, "deadline": _iso(item.deadline),
            "hours": _hours(item.hours),
            "by_site": {site: _hours(hours) for site, hours in item.by_site.items()},
            "by_kind": kinds_of(by_kind),
            "ready": _iso(result.ready) if result else None,
            "status": result.status if result else "нет часов",
            "slack_days": result.slack_days if result else None,
            "order": order.get(item.project, len(order)),
            "kbs": [{
                "index": kb.index, "name": kb.name, "run": kb.run,
                "done": float(kb.done.quantity), "done_source": kb.done.source,
                "remaining": float(kb.remaining), "hours": _hours(kb.hours),
                "unit_hours": round(float(sum(kb.per_unit.values(), ZERO)), 3),
                "site": kb.site, "site_note": kb.site_note, "by_kind": kinds_of(kb.by_kind),
            } for kb in sorted(item.kbs, key=lambda kb: kb.index)],
        })
    rows.sort(key=lambda row: (row["order"], row["project"]))
    return {
        "today": today.isoformat(),
        "horizon_to": days[-1].day.isoformat() if days else today.isoformat(),
        "shift_hours": _hours(Decimal(shift.effective_seconds) / Decimal(3600)),
        "sites": list(SITES), "unassigned": UNASSIGNED, "shop": SHOP, "kinds": list(KINDS),
        "people": people,
        "projects": rows,
        "weeks": [{
            "start": week.start.isoformat(), "end": week.end.isoformat(),
            "sites": {site: {
                "capacity": _hours(week.capacity.get(site, ZERO)),
                "demand": _hours(sum(week.demand.get(site, {}).values(), ZERO)),
                "planned": _hours(week.planned.get(site, ZERO)),
                "kinds": kinds_of(dict(week.demand.get(site, {}))),
            } for site in (*SITES, UNASSIGNED, SHOP)},
        } for week in week_rows],
        "bottlenecks": [{
            "site": item.site, "start": item.start.isoformat(), "demand": _hours(item.demand),
            "capacity": _hours(item.capacity), "kinds": [[kind, _hours(hours)] for kind, hours in item.kinds],
        } for item in bottlenecks(week_rows)],
        "no_run": no_run,
    }
