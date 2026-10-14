"""Первая поставка: что сделать первым, чтобы отгрузить часть проекта как можно раньше.

Идея — «фронт Парето» первой партии из планировщика Василия (PRODUCTION_PLANNER, коммит 6d7bfea: варианты с разной
передаточной партией, из них — недоминируемые по «сколько изделий» и «когда»), переписана под ERP: в проекте не одно
изделие, а несколько КБ с тиражами, поэтому вариант первой поставки — сколько штук каждой КБ сделать первыми.

Варианты:
- «Комплекты»: одинаковая доля тиража каждой КБ (10 %, 25 %, 50 %; не меньше одной штуки);
- «Целые КБ»: несколько КБ целиком — сначала те, что в полном плане кончаются раньше;
- весь проект (100 %) — полная отгрузка.
Каждый вариант считается тем же прямым автопланом (autoplan.plan: монтажники, допуски, занятость, станки, наладка) —
только на штуки варианта; остальное в проекте в этот расчёт не идёт. Готово к — конец последней операции варианта
(операции, назначенные вручную, держат своё окно). «Сколько» — доля работы штук проекта по нормам (без подготовки;
нет норм — доля штук): штуки разных КБ несравнимы, работа — сравнима (решено агентом).
Остаются только недоминируемые варианты: нет другого, который готов не позже и везёт не меньше (хоть в чём-то лучше).
Ничего не пишет."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Iterable, Sequence
from uuid import UUID

from .autoplan import AutoplanInput, plan

FRACTIONS = (Decimal("0.10"), Decimal("0.25"), Decimal("0.50"))
MAX_ITEM_VARIANTS = 6
MAX_SHOWN = 5


@dataclass(frozen=True)
class Lot:
    task_id: UUID
    quantity: int
    total: int


@dataclass(frozen=True)
class DeliveryVariant:
    kind: str  # "share" — комплекты, "items" — целые КБ, "full" — весь проект
    lots: tuple[Lot, ...]
    ready: datetime
    share: Decimal  # доля работы штук проекта, 0..1


def _labour(data: AutoplanInput, task, quantity: int) -> Decimal:
    """Работа штук задания на quantity изделий по свободным операциям, в секундах нормы (без подготовки: наладка не
    везётся заказчику, иначе 10 % тиража выглядели бы половиной проекта)."""
    return sum((
        operation.seconds_per_unit * task.operation_units(operation.id, quantity)
        for operation in task.operations if operation.id in data.free
    ), Decimal(0))


def _run(data: AutoplanInput, lots: Sequence[Lot]) -> tuple[datetime | None, dict[UUID, datetime]]:
    """Прямой автоплан только по штукам варианта: когда готово всё и каждое задание. None — что-то не помещается."""
    chosen = {lot.task_id: lot for lot in lots}
    tasks = tuple(
        replace(task, quantity=chosen[task.id].quantity) if task.id in chosen else task for task in data.tasks
    )
    included = {operation.id for task in data.tasks if task.id in chosen for operation in task.operations}
    # Остальная работа проекта в этот расчёт не идёт: её операции не расставляются и никого не занимают.
    free = frozenset(item for item in data.free if item in included)
    result = plan(replace(data, tasks=tasks, free=free))
    finishes: dict[UUID, datetime] = {}
    for item in result.placements:
        finishes[item.task_id] = max(finishes.get(item.task_id, item.finish), item.finish)
    for task in tasks:
        for operation in task.operations if task.id in chosen else ():
            if operation.id not in free and operation.planned_finish is not None and operation.planned_finish > data.start:
                finishes[task.id] = max(finishes.get(task.id, operation.planned_finish), operation.planned_finish)
    if result.unplaced:
        return None, finishes
    return max(finishes.values(), default=data.start), finishes


def pareto(variants: Iterable[DeliveryVariant]) -> tuple[DeliveryVariant, ...]:
    """Недоминируемые: по времени готовности, у каждого следующего — больше работы проекта."""
    result: list[DeliveryVariant] = []
    for variant in sorted(variants, key=lambda item: (item.ready, -item.share, item.kind)):
        if result and variant.share <= result[-1].share:
            continue
        result.append(variant)
    return tuple(result)


def _spread(variants: Sequence[DeliveryVariant], limit: int) -> tuple[DeliveryVariant, ...]:
    """Не больше limit вариантов: первый, последний (весь проект) и равномерно между ними."""
    if len(variants) <= limit:
        return tuple(variants)
    picks = sorted({round(index * (len(variants) - 1) / (limit - 1)) for index in range(limit)})
    return tuple(variants[index] for index in picks)


def first_delivery_variants(data: AutoplanInput) -> tuple[DeliveryVariant, ...]:
    """Варианты первой поставки по заданиям со свободными операциями (см. описание модуля)."""
    project = [task for task in data.tasks if any(operation.id in data.free for operation in task.operations)]
    if not project:
        return ()
    # Нет норм на штуку — доля по штукам изделий.
    by_work = sum((_labour(data, task, task.quantity) for task in project), Decimal(0)) > 0

    def measure(task, quantity: int) -> Decimal:
        return _labour(data, task, quantity) if by_work else Decimal(quantity)

    total = sum((measure(task, task.quantity) for task in project), Decimal(0))

    by_id = {task.id: task for task in project}
    seen: set[tuple[tuple[UUID, int], ...]] = set()
    finishes: dict[UUID, datetime] = {}

    def variant(kind: str, lots: Sequence[Lot]) -> DeliveryVariant | None:
        signature = tuple(sorted((lot.task_id, lot.quantity) for lot in lots))
        if signature in seen:
            return None
        seen.add(signature)
        ready, own = _run(data, lots)
        if kind == "full":
            finishes.update(own)
        if ready is None:
            return None
        work = sum((measure(by_id[lot.task_id], lot.quantity) for lot in lots), Decimal(0))
        return DeliveryVariant(kind, tuple(lots), ready, min(Decimal(1), work / total))

    candidates = [variant("full", [Lot(task.id, task.quantity, task.quantity) for task in project])]
    for fraction in FRACTIONS:
        lots = [Lot(task.id, min(task.quantity, max(1, ceil(task.quantity * fraction))), task.quantity) for task in project]
        if any(lot.quantity < lot.total for lot in lots):
            candidates.append(variant("share", lots))
    if len(project) > 1:
        # Целые КБ — в порядке окончания в полном плане (не поместилась — в конце), при равенстве — по номеру задания.
        latest = datetime.max.replace(tzinfo=data.start.tzinfo)
        ordered = sorted(project, key=lambda task: (finishes.get(task.id) or latest, task.number))
        sizes = sorted({max(1, round(index * (len(project) - 1) / MAX_ITEM_VARIANTS)) for index in range(1, MAX_ITEM_VARIANTS + 1)})
        for size in sizes:
            candidates.append(variant("items", [Lot(task.id, task.quantity, task.quantity) for task in ordered[:size]]))
    return _spread(pareto(item for item in candidates if item is not None), MAX_SHOWN)
