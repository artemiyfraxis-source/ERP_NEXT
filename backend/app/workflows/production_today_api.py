"""HTTP: «Сегодня» мастера участка — кто что делает, план на сегодня, просроченное и что стоит и почему, одним запросом,
только чтение (правила — production/today.py). Читает производство, техкарты (выданные ЭТК, наборы), комплектацию
(набран ли комплект КБ) и «Загрузку» (проекты «не успевает»): межмодульная связка — здесь, в workflows."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.modules.fulfillment.domain import FulfillmentKind, FulfillmentStatus
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, production_overview_principal
from app.workflows.production_workload_api import _shipping, workload

from app.modules.production.api import closed_kbs, production_uow_factory, techcards_uow_factory
from app.modules.production.application import UnitOfWork, _progress, _shift, _skills
from app.modules.production.dispatch import local, localize
from app.modules.production.domain import ProductionStatus, ProductionTask, skill_of
from app.modules.production.downtime import Downtime, moscow
from app.modules.production.downtime_infrastructure import SqlAlchemyDowntimeRepository
from app.modules.production.launchable import operation_code, stage_name
from app.modules.production.plan import kit_key_from_purpose, kit_stage_key, normalize_project_number, summarize_journal
from app.modules.production.today import (
    FIX_LABELS,
    ShareLine,
    Stuck,
    blocked_shares,
    blocked_tasks,
    day_bounds,
    home_site,
    missing_etk,
    on_site,
    overdue_lines,
    overdue_tasks,
    plan_counts,
    planned_today,
    resolve_site,
    share_lines,
    stuck_order,
    task_site,
)
from app.modules.production.workload import SITES, UNASSIGNED


router = APIRouter(prefix="/api/production/today", tags=["production"])

PICKED = frozenset({
    FulfillmentStatus.COMPLETE, FulfillmentStatus.READY, FulfillmentStatus.ISSUED,
    FulfillmentStatus.RETURNED, FulfillmentStatus.DONE,
})


def _fulfillment(request: Request) -> Callable[[], Any] | None:
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    return factory if callable(factory) else None


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _operation(task: ProductionTask, operation) -> dict:
    return {
        "task_id": str(task.id), "task_number": task.number, "project": task.project, "item": task.item,
        "name": task.name, "operation_id": str(operation.id), "position": operation.position,
        "stage": stage_name(operation.stage), "code": operation_code(operation.code),
    }


def _line(line: ShareLine, names: dict[UUID, str], now: datetime) -> dict:
    return {
        **_operation(line.task, line.operation),
        "operator_id": str(line.operator_id), "operator_name": names.get(line.operator_id, ""), "crew": line.crew,
        "planned_start": _iso(line.planned_start), "planned_finish": _iso(line.planned_finish),
        "started_at": _iso(line.started_at), "finished_at": _iso(line.finished_at),
        "quantity": _number(line.quantity), "done": _number(line.done), "remaining": _number(line.remaining),
        "state": line.state, "late": not line.finished and line.planned_finish <= now,
    }


def _stuck(item: Stuck, names: dict[UUID, str]) -> dict:
    task, operation = item.task, item.operation
    return {
        "kind": item.kind, "project": item.project, "item": item.item, "reason": item.reason,
        "task_id": str(task.id) if task else None, "task_number": task.number if task else "",
        "operation_id": str(operation.id) if operation else None, "position": operation.position if operation else None,
        "stage": stage_name(operation.stage) if operation else "",
        "operator_name": names.get(item.operator_id, "") if item.operator_id else "",
        "since": _iso(item.since), "fix": item.fix, "fix_label": FIX_LABELS[item.fix],
    }


def _downtime_text(item: Downtime) -> str:
    return f"Простой: {item.reason_name}" + (f" — {item.comment}" if item.comment else "")


@router.get("")
def today_view(
    request: Request,
    principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    today: Annotated[date, Depends(moscow_today)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    shipping_factory: Annotated[Callable[[], Any] | None, Depends(_shipping)],
    fulfillment_factory: Annotated[Callable[[], Any] | None, Depends(_fulfillment)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
    site: Annotated[str, Query(max_length=40)] = "",
) -> dict:
    day_start, day_end = day_bounds(today, now)
    with uow_factory() as uow:
        viewer = uow.operators.get(principal.actor.id)
        operators = list(uow.operators.list())
        tasks = [localize(task, now) for task in uow.production.list(limit=10_000) if task.status is not ProductionStatus.CANCELLED]
        holidays = uow.calendar.holidays()
        shift = _shift(uow.calendar)
        absences = list(uow.calendar.absences())
        skills = _skills(uow)
        running = [task for task in tasks if task.status is ProductionStatus.RUNNING]
        progress = _progress(uow, [task.id for task in running])
        worked = uow.production.worked([task.id for task in running])
        delivered = uow.deliveries.totals([task.id for task in running if task.handed_to])
        downtimes = SqlAlchemyDowntimeRepository(uow.session).open_all()
        journal = summarize_journal(uow.plan_events.list())
        fitters = sorted(
            (item for item in operators if item.active and item.shop_worker),
            key=lambda item: item.name.casefold(),
        )
        sessions = {item.id: uow.work.current(item.id) for item in fitters}

    chosen, can_switch = resolve_site(viewer.site if viewer is not None else "", principal.full_access, site)
    names = {item.id: item.name for item in operators}
    site_of = {item.id: home_site(item.site) for item in operators}
    crew = [item for item in fitters if home_site(item.site) == chosen]
    crew_ids = {item.id for item in crew}
    by_id = {task.id: task for task in tasks}
    working_day = today.weekday() in shift.weekdays and today not in holidays
    away = {
        item.operator_id: item for item in absences
        if item.from_date <= today <= item.to_date and item.operator_id in crew_ids
    }
    site_tasks = [task for task in running if on_site(task, chosen, site_of)]
    open_by_operator = {item.operator_id: item for item in downtimes if item.operator_id is not None}

    working = {(item.operation_id, item.operator_id) for item in sessions.values() if item is not None}
    lines = share_lines(running, progress.by_operation, progress.by_operator, crew_ids, working)
    today_lines = planned_today(lines, day_start, day_end)

    # 1. Кто что делает сейчас.
    people = []
    for person in crew:
        session = sessions.get(person.id)
        task = by_id.get(session.task_id) if session is not None else None
        operation = next((item for item in task.operations if item.id == session.operation_id), None) if task else None
        mine = [line for line in today_lines if line.operator_id == person.id]
        planned, done = plan_counts(mine)
        upcoming = next((line for line in mine if not line.finished and not line.started), None)
        absence = away.get(person.id)
        downtime = open_by_operator.get(person.id)
        if session is not None and task is not None and operation is not None:
            state = "пауза" if session.status.value == "Пауза" else "работает"
        elif downtime is not None:
            state = "простой"
        elif absence is not None:
            state = "отсутствует"
        else:
            state = "свободен"
        people.append({
            "id": str(person.id), "name": person.name, "state": state,
            "work": {
                **_operation(task, operation), "since": _iso(local(session.started_at, now)),
                "paused_since": _iso(local(session.pause_started_at, now)),
            } if state in ("работает", "пауза") else None,
            "downtime": {
                "reason": _downtime_text(downtime), "since": moscow(downtime.started_at).isoformat(),
                "task_number": downtime.task_number, "project": downtime.project, "item": downtime.item,
                "task_id": str(downtime.task_id) if downtime.task_id else None,
            } if downtime is not None else None,
            "absence": {"kind": absence.kind, "from": absence.from_date.isoformat(), "to": absence.to_date.isoformat()}
            if absence is not None else None,
            "planned": planned, "done": done,
            "next": {**_operation(upcoming.task, upcoming.operation), "planned_start": _iso(upcoming.planned_start)}
            if upcoming is not None else None,
        })

    # 2. План на сегодня — как у ЦАР, так и у ЖУК (владелец 29.10); плюс задания, отданные участку целиком.
    handed = [{
        "task_id": str(task.id), "task_number": task.number, "project": task.project, "item": task.item,
        "name": task.name, "quantity": task.quantity, "delivered": delivered.get(task.id, 0),
        "remaining": max(task.quantity - delivered.get(task.id, 0), 0), "deadline": _iso(task.deadline),
    } for task in sorted(running, key=lambda item: (item.deadline is None, item.deadline or date.max, item.number))
        if task.handed_to == chosen]

    # 3. Просрочено.
    late_lines = overdue_lines(lines, now)
    late_tasks = [task for task in overdue_tasks(running, today) if on_site(task, chosen, site_of)]
    load = workload(principal, today, uow_factory, techcards_factory, shipping_factory, closed)
    late_projects = [
        {"project": row["project"], "customer": row["customer"], "deadline": row["deadline"], "ready": row["ready"],
         "slack_days": row["slack_days"], "hours": row["by_site"].get(chosen, 0) + row["by_site"].get(UNASSIGNED, 0)}
        for row in load["projects"]
        if row["status"] == "не успевает" and (row["by_site"].get(chosen, 0) > 0 or row["by_site"].get(UNASSIGNED, 0) > 0)
    ]

    # 4. Стоит и почему.
    cards: list = []
    kitsets: tuple = ()
    if techcards_factory is not None:
        with techcards_factory() as techcards:
            cards = list(techcards.techcards.list())
            kitsets = tuple(techcards.kitsets.list()) if hasattr(techcards, "kitsets") else ()
    issued = frozenset(kit_stage_key(card.project, card.item_index) for card in cards if card.status is TechcardStatus.ISSUED)
    picked: set[str] = set()
    kit_status: dict[str, tuple[str, str]] = {}
    if fulfillment_factory is not None:
        with fulfillment_factory() as fulfillment:
            requests = list(fulfillment.fulfillment.list(limit=10_000))
        for item in requests:
            key = kit_key_from_purpose(item.purpose)
            if not key or getattr(item, "kind", FulfillmentKind.MAIN) is not FulfillmentKind.MAIN:
                continue
            if item.status in PICKED:
                picked.add(key)
            elif item.status is not FulfillmentStatus.CANCELLED:
                kit_status[key] = (item.status.value, item.number)
    stuck: list[Stuck] = []
    for item in downtimes:
        task = by_id.get(item.task_id) if item.task_id else None
        if item.operator_id in crew_ids or (item.operator_id is None and task is not None and on_site(task, chosen, site_of)):
            operation = next((op for op in task.operations if op.id == item.operation_id), None) if task else None
            stuck.append(Stuck(
                "downtime", item.project, item.item, _downtime_text(item), "task", task, operation,
                item.operator_id, moscow(item.started_at),
            ))
    projects = {normalize_project_number(task.project) for task in site_tasks}
    for project in sorted(projects):
        blocker = journal.get(project).blocker if journal.get(project) else None
        if blocker is not None:
            stuck.append(Stuck(
                "blocker", project, "", f"Проект на {blocker.type}" + (f": {blocker.text}" if blocker.text else ""),
                "prodplan", since=local(blocker.since, now),
            ))
    stuck.extend(blocked_shares(
        lines, day_end, progress.by_operation,
        lambda codes, operator_id: skill_of(skills, codes, operator_id), names,
    ))
    stuck.extend(blocked_tasks(site_tasks, progress.by_operation, worked, issued, frozenset(picked), kit_status, kit_stage_key))
    launched = frozenset(kit_stage_key(task.project, task.item) for task in tasks)
    stuck.extend(missing_etk(
        ((normalize_project_number(kitset.project), assembly.index, assembly.name)
         for kitset in kitsets for assembly in kitset.assemblies),
        projects, issued, launched, closed, kit_stage_key,
    ))
    stuck.sort(key=stuck_order)

    return {
        "today": today.isoformat(), "now": now.isoformat(), "working_day": working_day,
        "shift": {"start": shift.start.strftime("%H:%M"), "finish": shift.finish.strftime("%H:%M")},
        "site": chosen, "sites": list(SITES), "can_switch": can_switch,
        "people": people,
        "plan": [_line(line, names, now) for line in today_lines],
        "handed": handed,
        "overdue": {
            "operations": [_line(line, names, now) for line in late_lines],
            "tasks": [{
                "task_id": str(task.id), "task_number": task.number, "project": task.project, "item": task.item,
                "name": task.name, "deadline": _iso(task.deadline), "days": (today - task.deadline).days,
                "site": task_site(task, site_of),
            } for task in late_tasks],
            "projects": late_projects,
        },
        "stuck": [_stuck(item, names) for item in stuck],
    }
