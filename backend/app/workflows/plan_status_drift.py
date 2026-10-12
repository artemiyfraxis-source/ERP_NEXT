"""Сверка старых ручных статусов «Загрузки цеха» с выводимым: `python -m app.workflows.plan_status_drift`.

Владелец 12.10: «Статус» листа сквозной — этап проекта + блокер, ручной отметки нет. Старые записи «Отметка плана»
с полем `status` остаются в журнале историей и при чтении игнорируются. Команда ничего не пишет: перечисляет
проекты, у которых последняя ручная отметка статуса расходится с выводимым статусом (проект, отметка, выводимый,
кто и когда поставил), чтобы владелец увидел это до релиза.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.modules.production.plan import (
    PLAN_KINDS,
    PLAN_STAGE_AUTO,
    PlanEvent,
    normalize_project_number,
    normalize_shop_statuses,
)
from app.modules.production.plan_sheet import STAGE_SHOP_STATUS, shop_status_auto
from app.workflows.production_plan import ProductionPlanRow, build_production_plan


@dataclass(frozen=True)
class StatusDrift:
    project: str
    mark: str
    derived: str
    who: str
    occurred_at: datetime


def _status_set(value: str) -> set[str]:
    return {part.strip().casefold() for part in value.split(",") if part.strip()}


def status_drift(rows: Iterable[ProductionPlanRow], events: Iterable[PlanEvent]) -> list[StatusDrift]:
    """Projects whose last manual status mark (still in the journal) differs from stage + blocker."""
    last: dict[str, PlanEvent] = {}
    for event in events:
        if event.kind == PLAN_KINDS["mark"] and event.field == "status":
            last[normalize_project_number(event.project)] = event
    result = []
    for row in rows:
        event = last.get(row.project)
        if event is None or event.value in ("", PLAN_STAGE_AUTO):
            continue  # no mark, or the last one cleared it
        blocker = row.journal.blocker.type if row.journal.blocker else ""
        derived = shop_status_auto(row.stage.status, blocker)
        # The mark was typed in the old sheet words (В РАБОТЕ …): compare in that vocabulary.
        legacy = {blocker, STAGE_SHOP_STATUS.get(row.stage.status, row.stage.status)} - {""}
        mark = normalize_shop_statuses(event.value)
        if _status_set(mark) != _status_set(", ".join(legacy)):
            result.append(StatusDrift(row.project, mark, derived, event.who, event.occurred_at))
    return sorted(result, key=lambda item: item.project)


def format_drift(items: Sequence[StatusDrift]) -> str:
    if not items:
        return "Расхождений нет: ручных статусов, отличных от этапа проекта, нет."
    lines = [f"Расхождений: {len(items)}. Проект: ручной статус | выводимый (этап + блокер) | кто | когда"]
    lines.extend(
        f"{item.project}: {item.mark} | {item.derived} | {item.who or '—'} | {item.occurred_at:%d.%m.%Y %H:%M}"
        for item in items
    )
    lines.append("Проверка без записи: журнал не менялся.")
    return "\n".join(lines)


def load_and_compare(state, today: date) -> list[StatusDrift]:
    def read(name: str, reader, default=()):
        factory = getattr(state, name, None)
        if not callable(factory):
            return default
        with factory() as uow:
            return reader(uow)

    projects = read("production_uow_factory", lambda uow: tuple(uow.plan_projects.list()))
    events = read("production_uow_factory", lambda uow: tuple(uow.plan_events.list()))
    tasks = read("production_uow_factory", lambda uow: tuple(uow.production.list(limit=10_000)))
    cards = read("techcards_uow_factory", lambda uow: tuple(uow.techcards.list()))
    kitsets = read("techcards_uow_factory", lambda uow: tuple(uow.kitsets.list()) if hasattr(uow, "kitsets") else ())
    fulfillment = read("fulfillment_uow_factory", lambda uow: tuple(uow.fulfillment.list(limit=10_000)))
    goods = read("shipping_uow_factory", lambda uow: tuple(uow.goods.list(limit=10_000)))
    requests = read("uow_factory", lambda uow: tuple(uow.requests.recent(10_000)))
    procurement = read("procurement_uow_factory", lambda uow: tuple(uow.procurement.list(limit=10_000)))
    boxes = read("production_uow_factory", lambda uow: tuple(uow.boxes.list(limit=100_000)))
    inspected = read("production_uow_factory", lambda uow: frozenset(uow.qc.inspected_box_ids()), frozenset())
    rows = build_production_plan(
        cards=cards, tasks=tasks, fulfillment=fulfillment, goods=goods, requests=requests,
        procurement=procurement, projects=projects, events=events, today=today, kitsets=kitsets,
        boxes=boxes, inspected=inspected,
    )
    return status_drift(rows, events)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Показать проекты, где последняя ручная отметка статуса загрузки цеха расходится с этапом проекта. "
                    "Ничего не пишет.",
    ).parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from types import SimpleNamespace

    from app.database import configure_database
    from app.shared.clock import moscow_now

    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, url)
    try:
        print(format_drift(load_and_compare(holder.state, moscow_now().date())))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
