"""Загрузка станков (владелец, 17.10.2026: «в планировании надо учитывать загрузку станка»).

Станок — тип по префиксу инвентарного номера «ОБ.098» из первой строки «Оборудование» операции выданной ЭТК (тот же,
что в ключе наладки автоплана); единиц станка — разных инвентарных номеров с этим префиксом в БД.ОБ (нет в БД.ОБ —
одна).

Занятость считается при чтении из плана, ничего не хранится. Доля операции задания (у бригады и у неразделённой
операции — вся операция) занимает станок каждой своей операцией ЭТК со станком: подготовка шага + машинное
время tMachine × штуки (без экономии наладки автоплана); подготовка в плане с 18.10 — 0 (launchable). Нет машинного
времени — штучное время операции: за ручным прессом станок занят, пока на нём работает монтажник. Нет ни того, ни
другого и нет подготовки — станок не занят.
Операции ПФ идут подряд в порядке ЭТК с начала окна доли; не влезают в окно — все сжимаются в одной пропорции, чтобы
остаться внутри него.

Загрузка дня = занятые секунды смены / (единиц × секунд смены дня). Пик — сколько занятостей станка идёт одновременно;
пик больше единиц — станок перегружен в этот день, даже если процент меньше 100. Ручная постановка проверяется тем же
расчётом: если в её окне станок уже занят всеми единицами — предупреждение «ОБ.098 занят …», не запрет."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from math import ceil
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from .domain import (
    EtkOperation,
    ProductionOperation,
    ProductionTask,
    WorkShift,
    add_work_seconds,
    next_working_moment,
)
from .launchable import _seconds, route_from_document, stage_name
from .scheduling import work_seconds_between

EQUIPMENT_SECTION = "Оборудование"
_INVENTORY = re.compile(r"([ОO]Б)\s*\.\s*(\d+)", re.IGNORECASE)


def inventory_prefix(value: object) -> str:
    """«ОБ.027.2» / «ОБ.027.X» / «ОБ.027» → «ОБ.027»: тип станка, общий для всех его единиц (и ключ наладки
    автоплана, и станок загрузки)."""
    match = _INVENTORY.search(str(value or ""))
    return f"ОБ.{match.group(2)}" if match else ""


@dataclass(frozen=True)
class MachineStep:
    """Операция ЭТК, которая занимает станок: на сколько — подготовка + секунды на штуку."""
    position: int  # номер операции в ЭТК
    stage: str
    machine: str  # «ОБ.098»
    machine_label: str  # «ОБ.098 Пресс»
    setup_seconds: Decimal
    unit_seconds: Decimal  # машинное время на штуку; нет — штучное время операции


@dataclass(frozen=True)
class Occupancy:
    """Станок занят операцией ЭТК задания в окне [start, finish)."""
    machine: str
    machine_label: str
    task_id: UUID
    task_number: str
    project: str
    item: str
    operation_id: UUID
    operation_position: int
    operation_stage: str
    etk_position: int
    etk_stage: str
    operator_ids: tuple[UUID, ...]
    start: datetime
    finish: datetime


@dataclass(frozen=True)
class MachineDay:
    day: date
    busy_seconds: int
    capacity_seconds: int  # единиц × секунд смены дня; выходной — 0
    peak: int  # больше всего занятостей одновременно

    @property
    def percent(self) -> int | None:
        if self.capacity_seconds <= 0:
            return None
        return round(self.busy_seconds * 100 / self.capacity_seconds)


@dataclass(frozen=True)
class MachineRow:
    machine: str
    label: str
    units: int
    known: bool  # станок есть в БД.ОБ
    days: tuple[MachineDay, ...]
    items: tuple[Occupancy, ...]  # занятости, которые задевают период

    def over(self, day: MachineDay) -> bool:
        """Перегруз дня: занятых секунд больше, чем у всех единиц, или одновременно занятостей больше единиц."""
        return day.peak > self.units or (day.capacity_seconds > 0 and day.busy_seconds > day.capacity_seconds)


@dataclass(frozen=True)
class Conflict:
    """Новая занятость встаёт туда, где станок уже занят всеми единицами."""
    occupancy: Occupancy
    units: int
    busy: tuple[Occupancy, ...]  # чужие занятости, что пересекаются с окном

    @property
    def start(self) -> datetime:
        return max(self.occupancy.start, min(item.start for item in self.busy))

    @property
    def finish(self) -> datetime:
        return min(self.occupancy.finish, max(item.finish for item in self.busy))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split())


def _machine(built: Mapping[str, Any]) -> tuple[str, str]:
    for tool in _mapping(built.get("card")).get("tooling") or []:
        tool = _mapping(tool)
        prefix = inventory_prefix(tool.get("inventory_number"))
        if _text(tool.get("section")) == EQUIPMENT_SECTION and prefix:
            return prefix, f"{prefix} {_text(tool.get('name'))}".strip()
    return "", ""


def task_steps(task: ProductionTask, document: Mapping[str, Any] | None) -> tuple[dict[UUID, tuple[MachineStep, ...]], tuple[str, ...]]:
    """Станки операций задания по выданной ЭТК: операция задания — её операции ЭТК (у ПФ — по порядку ПФ). Код,
    который разошёлся с техкартой, — станки этой операции не учитываются и предупреждение."""
    if not isinstance(document, Mapping):
        return {}, (f"{task.number}: выданная техкарта {task.project} {task.item} не найдена — станки не учтены",)
    route = route_from_document(document)
    built = [_mapping(item) for item in document.get("operations") or []]
    config_ops = [_mapping(item) for item in _mapping(document.get("build_config")).get("ops") or []]
    result: dict[UUID, tuple[MachineStep, ...]] = {}
    warnings: list[str] = []
    for operation in task.operations:
        parts = operation.etk_ops or (EtkOperation(operation.position, operation.code, operation.stage),)
        steps: list[MachineStep] = []
        for part in parts:
            index = part.position - 1
            step = route[index] if 0 <= index < len(route) else None
            if step is None or step.code != part.code:
                warnings.append(
                    f"{task.number} · этап {operation.position}: код {part.code or '—'} не совпадает с техкартой — "
                    "станки этапа не учтены"
                )
                steps = []
                break
            etk = built[index] if index < len(built) else {}
            machine, label = _machine(etk)
            if not machine:
                continue
            config_op = config_ops[index] if index < len(config_ops) else {}
            times = _mapping(etk.get("times"))
            machine_seconds = _seconds(config_op.get("tMachine") or times.get("machine_seconds"))
            steps.append(MachineStep(
                part.position, stage_name(part.stage) or step.stage, machine, label, step.setup_seconds,
                machine_seconds if machine_seconds > 0 else step.seconds_per_unit,
            ))
        if steps:
            result[operation.id] = tuple(steps)
    return result, tuple(warnings)


def lay_out(
    steps: Sequence[MachineStep], start: datetime, finish: datetime, units: Decimal,
    holidays: frozenset[date], shift: WorkShift,
) -> list[tuple[MachineStep, datetime, datetime]]:
    """Занятости операций ЭТК подряд с начала окна; не влезают — сжаты в одной пропорции до размера окна."""
    durations = [ceil(max(Decimal(0), step.setup_seconds + step.unit_seconds * units)) for step in steps]
    total = sum(durations)
    window = work_seconds_between(start, finish, holidays, shift)
    if total <= 0 or window <= 0:
        return []
    if total > window:
        durations = [duration * window // total for duration in durations]
    result: list[tuple[MachineStep, datetime, datetime]] = []
    cursor = start
    for step, seconds in zip(steps, durations):
        if seconds <= 0 or cursor >= finish:
            continue
        begin = min(next_working_moment(cursor, holidays, shift), finish)
        end = min(add_work_seconds(begin, seconds, holidays, shift), finish)
        if end > begin:
            result.append((step, begin, end))
        cursor = end
    return result


def operation_occupancies(
    task: ProductionTask, operation: ProductionOperation, steps: Sequence[MachineStep],
    windows: Iterable[tuple[tuple[UUID, ...], int | None, datetime, datetime]],
    holidays: frozenset[date], shift: WorkShift,
) -> list[Occupancy]:
    """Занятости операции по окнам (исполнители, штук изделий или None — всё задание, начало, конец)."""
    result: list[Occupancy] = []
    for operator_ids, quantity, start, finish in windows:
        units = task.operation_units(operation.id, quantity)
        for step, begin, end in lay_out(steps, start, finish, units, holidays, shift):
            result.append(Occupancy(
                step.machine, step.machine_label, task.id, task.number, task.project, task.item, operation.id,
                operation.position, stage_name(operation.stage) or operation.stage, step.position, step.stage,
                tuple(operator_ids), begin, end,
            ))
    return result


def planned_windows(operation: ProductionOperation) -> list[tuple[tuple[UUID, ...], int | None, datetime, datetime]]:
    """Окна плана операции: у раздробленной — каждая доля со своими штуками; иначе (один или бригада) — вся
    операция в общем окне."""
    shares = [share for share in operation.shares if share.planned_start is not None and share.planned_finish is not None]
    if not shares:
        return []
    if operation.split:
        return [((share.operator_id,), share.quantity, share.planned_start, share.planned_finish) for share in shares]
    return [(
        tuple(share.operator_id for share in shares), None,
        min(share.planned_start for share in shares), max(share.planned_finish for share in shares),
    )]


def task_occupancies(
    task: ProductionTask, steps: Mapping[UUID, Sequence[MachineStep]], holidays: frozenset[date], shift: WorkShift,
) -> list[Occupancy]:
    result: list[Occupancy] = []
    for operation in task.operations:
        own = steps.get(operation.id)
        if own:
            result.extend(operation_occupancies(task, operation, own, planned_windows(operation), holidays, shift))
    return result


def peak(intervals: Iterable[tuple[datetime, datetime]], start: datetime, finish: datetime) -> int:
    """Больше всего интервалов одновременно внутри [start, finish)."""
    events: list[tuple[datetime, int]] = []
    for item_start, item_finish in intervals:
        lower, upper = max(item_start, start), min(item_finish, finish)
        if upper > lower:
            events += [(lower, 1), (upper, -1)]
    current = best = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        current += delta
        best = max(best, current)
    return best


def _machine_order(machine: str) -> tuple[int, str]:
    digits = re.sub(r"\D", "", machine)
    return (int(digits) if digits else 10**9, machine)


def machine_load(
    occupancies: Sequence[Occupancy], units: Mapping[str, int], days: Sequence[date],
    holidays: frozenset[date], shift: WorkShift, tz: tzinfo | None,
) -> tuple[MachineRow, ...]:
    """Загрузка станков по дням периода: строка на каждый станок, который занимает хоть одна операция плана."""
    by_machine: dict[str, list[Occupancy]] = {}
    for item in occupancies:
        by_machine.setdefault(item.machine, []).append(item)
    bounds = [(day, datetime.combine(day, time.min, tz), datetime.combine(day + timedelta(days=1), time.min, tz)) for day in days]
    period = (bounds[0][1], bounds[-1][2]) if bounds else None
    rows: list[MachineRow] = []
    for machine in sorted(by_machine, key=_machine_order):
        items = sorted(by_machine[machine], key=lambda item: (item.start, item.finish, item.task_number))
        count = max(1, int(units.get(machine, 0) or 0))
        machine_days: list[MachineDay] = []
        for day, lower, upper in bounds:
            capacity = work_seconds_between(lower, upper, holidays, shift) * count
            busy = sum(
                work_seconds_between(max(item.start, lower), min(item.finish, upper), holidays, shift)
                for item in items if item.start < upper and item.finish > lower
            )
            machine_days.append(MachineDay(day, busy, capacity, peak(((item.start, item.finish) for item in items), lower, upper)))
        shown = tuple(item for item in items if period is not None and item.start < period[1] and item.finish > period[0])
        rows.append(MachineRow(
            machine, next((item.machine_label for item in items if item.machine_label), machine), count,
            machine in units, tuple(machine_days), shown,
        ))
    return tuple(rows)


def conflicts(
    candidate: Sequence[Occupancy], existing: Sequence[Occupancy], units: Mapping[str, int],
) -> tuple[Conflict, ...]:
    """Где новая занятость упирается в станок, уже занятый всеми единицами."""
    result: list[Conflict] = []
    for item in candidate:
        count = max(1, int(units.get(item.machine, 0) or 0))
        overlapping = tuple(sorted(
            (other for other in existing
             if other.machine == item.machine and other.start < item.finish and other.finish > item.start),
            key=lambda other: (other.start, other.finish, other.task_number),
        ))
        if overlapping and peak(((other.start, other.finish) for other in overlapping), item.start, item.finish) >= count:
            result.append(Conflict(item, count, overlapping))
    return tuple(result)


def _moment(value: datetime) -> str:
    return f"{value:%d.%m %H:%M}"


def window_text(start: datetime, finish: datetime) -> str:
    """«17.10 10:00–12:30»; через день — «17.10 16:00 – 20.10 10:00»."""
    if start.date() == finish.date():
        return f"{_moment(start)}–{finish:%H:%M}"
    return f"{_moment(start)} – {_moment(finish)}"


def occupancy_text(item: Occupancy, names: Mapping[UUID, str]) -> str:
    who = ", ".join(names.get(operator_id, "") for operator_id in item.operator_ids if names.get(operator_id))
    etk = f", оп. ЭТК {item.etk_position} «{item.etk_stage}»" if item.etk_stage and item.etk_stage != item.operation_stage else ""
    return (
        f"{item.task_number} · {item.item}, этап {item.operation_position} «{item.operation_stage}»{etk}"
        f" {window_text(item.start, item.finish)}{f' ({who})' if who else ''}"
    )


def conflict_text(conflict: Conflict, names: Mapping[UUID, str]) -> str:
    """«ОБ.098 занят 17.10 10:00–12:30: ПЗ-0012 · КБ1, этап 2 «Опрессовка» … (Анна)»."""
    taken = f" (заняты все {conflict.units} ед.)" if conflict.units > 1 else ""
    return (
        f"{conflict.occupancy.machine} занят{taken} {window_text(conflict.start, conflict.finish)}: "
        + "; ".join(occupancy_text(item, names) for item in conflict.busy)
    )
