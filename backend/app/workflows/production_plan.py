"""Compose the native production plan from module-owned snapshots and facts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping
from uuid import UUID

from app.modules.fulfillment.domain import FulfillmentRequest, FulfillmentStatus
from app.modules.procurement.domain import ProcurementLine
from app.modules.production.domain import ProductionStatus, ProductionTask
from app.modules.production.plan import (
    PlanEvent,
    KitMark,
    KitReview,
    PlanProjectSnapshot,
    project_terminal,
    ProjectJournal,
    ProjectStage,
    MANUAL_TECHCARD_PROJECTS,
    SupplyPosition,
    SupplySummary,
    days_left,
    kit_key_from_purpose,
    kit_stage_key,
    normalize_project_number,
    project_from_purpose,
    project_stage,
    summarize_journal,
    summarize_kit_marks,
    summarize_plan_marks,
    summarize_supply,
    supply_bucket,
)
from app.modules.production.plan_sheet import (
    SheetCalendar,
    SheetCell,
    calendar_span,
    ready_percent,
    sheet_calendar,
    sheet_cells,
    sheet_group,
)
from app.modules.requests.domain import Request
from app.modules.shipping.domain import FinishedGood
from app.modules.techcards.kitsets import Kitset
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus


logger = logging.getLogger(__name__)


def _safe(name: str, compute: Callable[[], dict], default: dict | None = None) -> dict:
    """ProdPlan.gs prodPlanData_ `safe`: a column that fails to compute is left empty."""
    try:
        return compute()
    except Exception:  # noqa: BLE001 - one broken column must not take the whole plan down
        logger.warning("production plan column %s failed", name, exc_info=True)
        return {} if default is None else default


def _utc_timestamp(value: datetime) -> float:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).timestamp()


@dataclass(frozen=True)
class ProjectTaskPlan:
    tasks: int
    assigned: int
    total: int
    start: datetime | None
    finish: datetime | None


@dataclass(frozen=True)
class ProductionPlanRow:
    project: str
    customer: str
    opened_on: date | None
    deadline: date | None
    days_left: int | None
    stage: ProjectStage
    supply: SupplySummary | None
    plan: ProjectTaskPlan | None
    journal: ProjectJournal
    terminal: bool = False
    # Лист «ЗАГРУЗ»: колонки (авто + ручная отметка), группа строки, календарь по дням.
    sheet: Mapping[str, SheetCell] | None = None
    group: str = "active"
    calendar: SheetCalendar = SheetCalendar()


def _project_for_request(request: Request) -> str:
    return normalize_project_number(request.project_reference) if request.project_reference else project_from_purpose(request.purpose)


def _date_text(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _supply(
    requests: Iterable[Request], procurement: Iterable[ProcurementLine],
) -> dict[str, SupplySummary]:
    # A request has one procurement line plus a `-Д` line per paid shortfall; each is its own position.
    procurement_by_request: dict[UUID, list[ProcurementLine]] = {}
    for line in procurement:
        procurement_by_request.setdefault(line.source_request_id, []).append(line)
    positions = []
    for request in requests:
        project = _project_for_request(request)
        customer_supplied = bool(request.link and "давальческ" in request.link.casefold())
        for line in procurement_by_request.get(request.id) or [None]:
            status = line.status.value if line else request.status.value
            # ProdPlan.gs planSupplyPositions_: ОЖИД. or ПЛАН. ДАТА of the Закупки row, else the request's.
            eta = (line.expected_arrival or line.planned_date) if line else None
            eta = eta or request.planned_date
            positions.append(SupplyPosition(
                project=project,
                bucket=supply_bucket(status, line is not None),
                eta=_date_text(eta),
                customer_supplied=customer_supplied,
            ))
    return summarize_supply(positions)


def _task_plan(tasks: Iterable[ProductionTask]) -> dict[str, ProjectTaskPlan]:
    grouped: dict[str, list[ProductionTask]] = {}
    for task in tasks:
        if task.status is ProductionStatus.RUNNING:
            grouped.setdefault(normalize_project_number(task.project), []).append(task)
    result = {}
    for project, project_tasks in grouped.items():
        operations = [operation for task in project_tasks for operation in task.operations]
        assigned = [operation for operation in operations if operation.operator_ids]
        starts = [operation.planned_start for operation in assigned if operation.planned_start]
        finishes = [operation.planned_finish for operation in assigned if operation.planned_finish]
        result[project] = ProjectTaskPlan(
            tasks=len(project_tasks), assigned=len(assigned), total=len(operations),
            start=min(starts) if starts else None,
            finish=max(finishes) if finishes else None,
        )
    return result


def kit_stage_facts(
    *,
    cards: Iterable[ProjectTechcard],
    tasks: Iterable[ProductionTask],
    fulfillment: Iterable[FulfillmentRequest],
    goods: Iterable[FinishedGood],
    marks: dict[str, KitMark],
) -> tuple[dict[str, set[str]], dict[str, KitMark]]:
    """`kitStageFacts_`: which КБ has an issued techcard, a picked kit, a running task, stock, shipment.

    Shared by the production plan and the project kitset list (SUPPLYMAP portalKitsetList).
    """
    marks = dict(marks)
    facts: dict[str, set[str]] = {
        "tc": set(), "tc_project": set(), "kit": set(), "work": set(),
        "stock": set(), "shipped": set(),
    }
    facts["tc_project"].update(MANUAL_TECHCARD_PROJECTS)
    for card in cards:
        if card.status is TechcardStatus.ISSUED:
            key = kit_stage_key(card.project, card.item_index)
            facts["tc"].add(key)
            current = marks.get(key, KitMark())
            if current.review is None or (
                current.review.occurred_at is not None
                and _utc_timestamp(current.review.occurred_at) < _utc_timestamp(card.updated_at)
            ):
                marks[key] = KitMark(
                    stage=current.stage,
                    review=KitReview("не проверена", "ТК выдана", "генератор", card.updated_at),
                )
    picked = {
        FulfillmentStatus.COMPLETE, FulfillmentStatus.READY, FulfillmentStatus.ISSUED,
        FulfillmentStatus.RETURNED, FulfillmentStatus.DONE,
    }
    for request in fulfillment:
        if request.status in picked:
            key = kit_key_from_purpose(request.purpose)
            if key:
                facts["kit"].add(key)
    for task in tasks:
        if task.status is ProductionStatus.RUNNING:
            facts["work"].add(kit_stage_key(task.project, task.item))
    for good in goods:
        key = kit_stage_key(good.project, good.item_index)
        need = good.ordered or good.accepted
        if good.accepted > 0 and good.accepted >= need:
            facts["stock"].add(key)
        # Этап «Отгружено» — по уехавшему (`sent`), не по отобранному: упакованное ещё на складе (решено агентом 30.09).
        if good.sent > 0 and good.sent >= need:
            facts["shipped"].add(key)

    return facts, marks


def build_production_plan(
    *,
    cards: Iterable[ProjectTechcard],
    tasks: Iterable[ProductionTask],
    fulfillment: Iterable[FulfillmentRequest],
    goods: Iterable[FinishedGood],
    requests: Iterable[Request],
    procurement: Iterable[ProcurementLine],
    projects: Iterable[PlanProjectSnapshot],
    events: Iterable[PlanEvent],
    today: date,
    kitsets: Iterable[Kitset] = (),
    processed: Mapping[UUID, Decimal] | None = None,
) -> tuple[ProductionPlanRow, ...]:
    cards = tuple(cards)
    tasks = tuple(tasks)
    fulfillment = tuple(fulfillment)
    goods = tuple(goods)
    events = tuple(events)
    project_snapshots = {normalize_project_number(item.project): item for item in projects}
    journal = summarize_journal(events)
    marks = summarize_kit_marks(events)
    plan_marks = summarize_plan_marks(events)
    processed = processed or {}
    first_day, last_day = calendar_span(today)
    tasks_by_project: dict[str, list[ProductionTask]] = {}
    for task in tasks:
        tasks_by_project.setdefault(normalize_project_number(task.project), []).append(task)
    supply = _safe("supply", lambda: _supply(requests, procurement))
    task_plan = _safe("tasks", lambda: _task_plan(tasks))

    cards_by_project: dict[str, list[ProjectTechcard]] = {}
    for card in cards:
        cards_by_project.setdefault(normalize_project_number(card.project), []).append(card)

    facts, marks = kit_stage_facts(
        cards=cards, tasks=tasks, fulfillment=fulfillment, goods=goods, marks=marks,
    )

    # КБ проекта — из карточки набора (tcKitsetSyncErp_ → «Позиции проектов») и из карточек техкарт.
    kit_indexes: dict[str, list[str]] = {}
    for kitset in kitsets:
        kit_indexes.setdefault(normalize_project_number(kitset.project), []).extend(
            assembly.index for assembly in kitset.assemblies
        )

    rows = []
    # ProdPlan.gs prodPlanData_: только проект реестра «Проекты», у которого есть состав (КБ) или записи
    # журнала; голая строка реестра и карточка ТК без строки реестра в план не попадают.
    for project in sorted(project_snapshots):
        if not (kit_indexes.get(project) or cards_by_project.get(project) or journal.get(project)):
            continue
        project_cards = cards_by_project.get(project, [])
        item_indexes = list(dict.fromkeys(
            [*kit_indexes.get(project, []), *(card.item_index for card in project_cards)]
        ))
        snapshot = project_snapshots.get(project)
        project_journal = journal.get(project, ProjectJournal(None, None, ()))
        card_deadlines = [card.deadline for card in project_cards if card.deadline]
        deadline = (
            project_journal.deadline
            or (snapshot.deadline if snapshot else None)
            or (min(card_deadlines) if card_deadlines else None)
        )
        project_goods = [good for good in goods if normalize_project_number(good.project) == project]
        customer = (snapshot.customer if snapshot else "") or next(
            (good.customer for good in project_goods if good.customer), "",
        )
        opened_on = (
            snapshot.opened_on if snapshot else min(card.created_at.date() for card in project_cards)
        )
        terminal = project_terminal(snapshot, project_journal)
        stage = project_stage(
            project,
            item_indexes,
            facts,
            terminal,
            marks,
        )
        project_tasks = tasks_by_project.get(project, [])
        project_marks = plan_marks.get(project, {})
        project_plan = task_plan.get(project)
        is_terminal = bool(terminal) or (project_journal.status is None and bool(snapshot and snapshot.terminal))
        cells = _safe("sheet", lambda: sheet_cells(
            stage=stage,
            blocker=project_journal.blocker.type if project_journal.blocker else "",
            supply=supply.get(project),
            assigned=project_plan.assigned if project_plan else 0,
            ready=ready_percent(stage.status, project_tasks, processed),
            marks=project_marks,
        ))
        rows.append(ProductionPlanRow(
            project=project, customer=customer, opened_on=opened_on,
            deadline=deadline, days_left=days_left(deadline, today),
            stage=stage, supply=supply.get(project), plan=project_plan,
            journal=project_journal,
            terminal=is_terminal,
            sheet=cells,
            group=sheet_group(cells["status"].value if "status" in cells else "", is_terminal),
            calendar=_safe(
                "calendar", lambda: sheet_calendar(project_tasks, project_marks, first_day, last_day),
                SheetCalendar(),
            ),
        ))
    return tuple(sorted(rows, key=lambda row: (
        row.journal.blocker is None,
        row.deadline is None,
        row.deadline or date.max,
        row.project,
    )))
