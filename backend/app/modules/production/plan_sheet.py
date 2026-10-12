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


# Owner 12.10: «Статус» листа сквозной — всегда этап проекта (как на экране «Проекты») + блокер, ручной отметки нет.
# Старые отметки `status` в журнале остаются историей; STAGE_SHOP_STATUS — только чтобы сверить их с этапом (plan_status_drift).
STAGE_SHOP_STATUS = {
    "КБ набрана": "К ЗАПУСК",
    "Распланировано": "РАСПЛАН",
    "В работе": "В РАБОТЕ",
    "ОТК": "ОТК",
    "Принято складом": "ГОТОВО",
    "Упаковано": "УПАК",
    "Отгружено": "ОТГРУЖЕН",
}
SHOP_HOLD = frozenset(("ХОЛД", "СТОП", "ПАУЗА"))
# Работа цеха закончена: изделие принято на склад ГП и дальше (ОТК ещё у цеха — строка активная).
SHOP_DONE_STAGES = frozenset(("Принято складом", "Упаковано", "Отгружено"))
# Производство закончено (ОТК и дальше): готовность 100.
SHOP_PRODUCED_STAGES = frozenset(("ОТК", *SHOP_DONE_STAGES))
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
    """Blocker of the journal + the ERP stage name, exactly as the projects screen shows them."""
    return ", ".join(part for part in (blocker, stage) if part)


def sheet_group(stage: str, blocker: str = "", terminal: bool = False) -> str:
    """Rows on hold go on top, finished ones (Принято складом, Упаковано, Отгружено) to the bottom, refused
    projects last."""
    if blocker in SHOP_HOLD:
        return "hold"
    if terminal:
        return "closed"
    if not blocker and stage in SHOP_DONE_STAGES:
        return "done"
    return "active"


def ready_percent(
    stage: str, tasks: Iterable[ProductionTask], processed: Mapping[UUID, Decimal],
) -> str:
    """Output against the task quantity, weighted by the operation norm; production finished (ОТК and on) is 100."""
    if stage in SHOP_PRODUCED_STAGES:
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
            share = min(Decimal(processed.get(operation.id, 0)) / max(task.operation_units(operation.id), Decimal(1)), Decimal(1))
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
    """Auto value of every sheet column next to the manual mark that overrides it (the status has no mark)."""
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
        name: SheetCell(marks[name], value, True) if name in marks and name != "status" else SheetCell(value, value, False)
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
    "CALENDAR_AFTER_DAYS", "CALENDAR_BEFORE_DAYS", "SHEET_GROUPS", "SHOP_DONE_STAGES", "SHOP_HOLD", "SHOP_PRODUCED_STAGES",
    "SHOP_STATUSES",
    "STAGE_SHOP_STATUS", "SheetCalendar", "SheetCell", "calendar_span", "ready_percent", "sheet_calendar",
    "sheet_cells", "sheet_group", "shop_status_auto",
]
