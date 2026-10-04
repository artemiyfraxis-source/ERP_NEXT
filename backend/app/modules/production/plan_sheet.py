"""The shop sheet «ЗАГРУЗ» as a view of the production plan: one row per project, sheet columns, day calendar.

Owner 04.10: «тут вот такое представление я хочу». SUPPLYMAP has no such screen (ProdPlan.gs only syncs the
sheet once); every column is computed from ERP facts where they exist, and a plan mark («Отметка плана»)
overrides it by hand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from .domain import ProductionStatus, ProductionTask
from .plan import (
    KIT_STAGES,
    PLAN_DAY_FIELD_PREFIX,
    SHOP_STATUSES,
    ProjectStage,
    SupplySummary,
    _date,
)


# ProdPlan.gs PLAN_SHOP_STAGE_ read backwards: the ERP stage is shown as the sheet status it maps from.
STAGE_SHOP_STATUS = {
    "КБ набрана": "К ЗАПУСК",
    "В работе": "В РАБОТЕ",
    "КБ на складе": "ГОТОВО",
    "Отгружено": "ОТГРУЖЕН",
}
SHOP_HOLD = frozenset(("ХОЛД", "СТОП", "ПАУЗА"))
SHOP_DONE = frozenset(("ОТК", "НА ОТГР", "ГОТОВО", "ОТГРУЖЕН"))
SHEET_GROUPS = ("hold", "active", "done", "closed")
CALENDAR_BEFORE_DAYS = 7
CALENDAR_AFTER_DAYS = 60


@dataclass(frozen=True)
class SheetCell:
    value: str
    auto: str = ""
    manual: bool = False


@dataclass(frozen=True)
class SheetCalendar:
    work: tuple[date, ...] = ()
    starts: tuple[tuple[date, int], ...] = ()
    notes: tuple[tuple[date, str], ...] = ()


def shop_status_auto(stage: str, blocker: str = "") -> str:
    """Blocker of the journal + the sheet status of the ERP stage; a stage with no sheet twin stays as it is."""
    parts = [blocker] if blocker else []
    stage_status = STAGE_SHOP_STATUS.get(stage, stage)
    if stage_status and stage_status not in parts:
        parts.append(stage_status)
    return ", ".join(parts)


def sheet_group(status: str, terminal: bool = False) -> str:
    """Rows on hold go on top, finished ones (ОТК … ОТГРУЖЕН) to the bottom, refused projects last."""
    statuses = [item.strip() for item in status.split(",") if item.strip()]
    if any(item in SHOP_HOLD for item in statuses):
        return "hold"
    if terminal:
        return "closed"
    if statuses and all(item in SHOP_DONE for item in statuses):
        return "done"
    return "active"


def ready_percent(
    stage: str, tasks: Iterable[ProductionTask], processed: Mapping[UUID, Decimal],
) -> str:
    """Output against the task quantity, weighted by the operation norm; a KB on stock is 100."""
    if stage in ("КБ на складе", "Отгружено"):
        return "100"
    total = Decimal(0)
    done = Decimal(0)
    seen = False
    for task in tasks:
        if task.status is not ProductionStatus.RUNNING:
            continue
        for operation in task.operations:
            seen = True
            weight = Decimal(max(operation.norm_seconds, 1))
            share = min(Decimal(processed.get(operation.id, 0)) / max(task.quantity, 1), Decimal(1))
            total += weight
            done += weight * share
    if not seen:
        return ""
    return str(int((done * 100 / total).to_integral_value(ROUND_HALF_UP))) if total else "0"


def _flag(value: bool) -> str:
    return "да" if value else "нет"


def sheet_cells(
    *,
    stage: ProjectStage,
    blocker: str,
    supply: SupplySummary | None,
    assigned: int,
    ready: str,
    marks: Mapping[str, str],
) -> dict[str, SheetCell]:
    """Auto value of every sheet column next to the manual mark that overrides it."""
    kits = stage.kits
    reached = lambda status: KIT_STAGES.index(status) if status in KIT_STAGES else -1  # noqa: E731
    picked = KIT_STAGES.index("КБ набрана")
    if supply is None or not supply.customer_supplied:
        supplied = "НЕТ"
    elif supply.customer_supplied >= supply.total:
        supplied = "ДА"
    else:
        supplied = "Ч/Т"
    in_stock = bool(supply and supply.total and supply.stock >= supply.total)
    if supply is None or not supply.total:
        arrival = ""
    elif in_stock:
        arrival = "Склад"
    else:
        eta = _date(supply.eta)
        arrival = eta.isoformat() if eta else "Ждем"
    auto = {
        "comment": "",
        "status": shop_status_auto(stage.status, blocker),
        "ready": ready,
        "in_plan": _flag(assigned > 0),
        "customer_supplied": supplied,
        "kit_checked": "нет",
        "kit_present": _flag(in_stock),
        # Решено агентом — проверить: ЗКП читаем как «на проект есть заявки» (смысл колонки владелец не назвал).
        "zkp": _flag(bool(supply and supply.total)),
        "kit_arrival": arrival,
        "tc_ready": _flag(bool(kits) and all(item.tc for item in kits)),
        "test_sb": "нет",
        "kit_picked": _flag(bool(kits) and all(item.kit or reached(item.status) >= picked for item in kits)),
    }
    return {
        name: SheetCell(marks[name], value, True) if name in marks else SheetCell(value, value, False)
        for name, value in auto.items()
    }


def _operation_windows(task: ProductionTask) -> Iterable[tuple[datetime, datetime]]:
    for operation in task.operations:
        windows = [
            (share.planned_start, share.planned_finish) for share in operation.shares
            if share.planned_start and share.planned_finish
        ]
        if not windows and operation.planned_start and operation.planned_finish:
            windows = [(operation.planned_start, operation.planned_finish)]
        yield from windows


def sheet_calendar(
    tasks: Iterable[ProductionTask], marks: Mapping[str, str], first: date, last: date,
) -> SheetCalendar:
    """Planned work days of the project's running tasks, task quantity on its first day, day notes."""
    work: set[date] = set()
    starts: dict[date, int] = {}
    for task in tasks:
        if task.status is not ProductionStatus.RUNNING:
            continue
        task_first: date | None = None
        for start, finish in _operation_windows(task):
            # A window ending exactly at midnight does not touch the next day.
            day, end = start.date(), (finish - timedelta(microseconds=1)).date()
            task_first = day if task_first is None else min(task_first, day)
            while day <= end:
                if first <= day <= last:
                    work.add(day)
                day += timedelta(days=1)
        if task_first is not None and first <= task_first <= last:
            starts[task_first] = starts.get(task_first, 0) + task.quantity
    notes = []
    for name, text in marks.items():
        if not name.startswith(PLAN_DAY_FIELD_PREFIX):
            continue
        try:
            day = date.fromisoformat(name[len(PLAN_DAY_FIELD_PREFIX):])
        except ValueError:
            continue
        if first <= day <= last:
            notes.append((day, text))
    return SheetCalendar(tuple(sorted(work)), tuple(sorted(starts.items())), tuple(sorted(notes)))


def calendar_span(today: date) -> tuple[date, date]:
    return today - timedelta(days=CALENDAR_BEFORE_DAYS), today + timedelta(days=CALENDAR_AFTER_DAYS)


__all__ = [
    "CALENDAR_AFTER_DAYS", "CALENDAR_BEFORE_DAYS", "SHEET_GROUPS", "SHOP_DONE", "SHOP_HOLD", "SHOP_STATUSES",
    "STAGE_SHOP_STATUS", "SheetCalendar", "SheetCell", "calendar_span", "ready_percent", "sheet_calendar",
    "sheet_cells", "sheet_group", "shop_status_auto",
]
