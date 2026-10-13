"""План от даты отгрузки (обратный расчёт): когда начать работу проекта, чтобы успеть к отгрузке, и где не успеваем.

Идея — из планировщика Василия (PRODUCTION_PLANNER, коммит 6d7bfea, режим «вытягивания»), переписана под автоплан ERP:
та же входная картина (autoplan.AutoplanInput — задания, свободные операции, монтажники ЦАР с допусками, их занятость
и отсутствия, календарь), но операции ставятся не «как можно раньше», а «как можно позже».

Расчёт — зеркало жадного автоплана. Срок — конец смены в день отгрузки. Операции берутся от конца схемы задания к
началу: операция готова, когда расставлены все, кто берёт её полуфабрикат. Её «закончить к» — срок или начало самого
раннего потребителя (назначенный вручную потребитель тоже держит своё начало). Операция встаёт на монтажника с
допуском, у которого она начнётся позже всех: окно ищется назад от «закончить к» в обход его занятости и отсутствий.
Порядок готовых: позднее «закончить к» раньше, потом номер ПЗ и этап — от больших. Всё детерминировано.

Упрощения (решено агентом): полная подготовка по карте у каждой операции (без экономии наладки), станки не
ограничивают, вход отдаётся всем количеством (передаточные партии не учитываются) — обратный план осторожнее
прямого, поэтому «начать не позже» не обещает лишнего. Занятость в прошлом не мешает: операция, которой не хватило
времени, встаёт раньше «сейчас», и на сколько раньше — столько рабочего времени и не хватает. Ничего не пишет."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from .autoplan import AUTOPLAN_SITE, AutoplanInput, Unplaced
from .domain import ProductionOperation, ProductionValidationError, clock_between, clock_seconds, rewind_clock
from .launchable import stage_name
from .scheduling import _allowed, _rate, network_order

_SLOT_ATTEMPTS = 400


@dataclass(frozen=True)
class LatestSlot:
    """Самое позднее окно операции, при котором проект успевает к отгрузке."""
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    start: datetime
    finish: datetime

    def late(self, now: datetime) -> bool:
        return self.start < now


@dataclass(frozen=True)
class TaskWindow:
    """Задание в обратном плане: начать не позже / закончить к; запас (минус — нехватка) в секундах смены."""
    task_id: UUID
    start: datetime
    finish: datetime
    slack_seconds: int


@dataclass(frozen=True)
class BackwardResult:
    due: datetime
    start: datetime  # «сейчас» расчёта
    slots: tuple[LatestSlot, ...]
    unplaced: tuple[Unplaced, ...]
    warnings: tuple[str, ...]
    tasks: tuple[TaskWindow, ...]

    @property
    def fits(self) -> bool:
        return not self.unplaced and all(not slot.late(self.start) for slot in self.slots)

    @property
    def start_by(self) -> datetime | None:
        return min((slot.start for slot in self.slots), default=None)

    @property
    def slack_seconds(self) -> int:
        """Запас всего проекта: у самого раннего «начать не позже»; минус — столько рабочего времени не хватает."""
        return min((item.slack_seconds for item in self.tasks), default=0)


def due_moment(ship_date: date, tzinfo, holidays: frozenset[date], shift) -> datetime:
    """Конец последней смены не позже дня отгрузки: изделие готово к концу дня отгрузки, как дедлайн автоплана."""
    return _latest_moment(datetime.combine(ship_date + timedelta(days=1), time.min, tzinfo), holidays, shift)


def _latest_moment(moment: datetime, holidays: frozenset[date], shift) -> datetime:
    """Последний рабочий момент не позже moment (конец смены, если moment вне смены)."""
    return rewind_clock(moment, 1, holidays, shift) + timedelta(seconds=1)


def slack_between(start: datetime, latest_start: datetime, holidays: frozenset[date], shift) -> int:
    """Секунды смены от «сейчас» до «начать не позже»; начать надо было раньше — со знаком минус."""
    if latest_start >= start:
        return clock_between(start, latest_start, holidays, shift)
    return -clock_between(latest_start, start, holidays, shift)


class _Backward:
    def __init__(self, data: AutoplanInput, due: datetime):
        self.data = data
        self.due = due
        self.task_of = {operation.id: task for task in data.tasks for operation in task.operations}
        self.ops = {operation.id: operation for task in data.tasks for operation in task.operations}
        self.consumers: dict[UUID, list[UUID]] = {}
        for task in data.tasks:
            for operation in task.operations:
                for edge in operation.inputs:
                    self.consumers.setdefault(edge.input_operation_id, []).append(operation.id)
        self.topo = {
            operation.id: index for task in data.tasks for index, operation in enumerate(network_order(task.operations))
        }
        # Прошлое уже не занято: мешает только занятость после начала расчёта.
        self.busy: dict[UUID, list[tuple[datetime, datetime]]] = {
            operator_id: [
                (item.start, item.finish) for item in data.busy.get(operator_id, ())
                if item.finish > item.start and item.finish > data.start
            ] for operator_id in data.team
        }
        self.slots: dict[UUID, LatestSlot] = {}
        self.unplaced: dict[UUID, str] = {}

    def bound(self, operation_id: UUID) -> datetime:
        """«Закончить к»: срок или начало самого раннего потребителя (расставленного или назначенного вручную)."""
        limit = self.due
        for consumer in self.consumers.get(operation_id, ()):
            if consumer in self.slots:
                limit = min(limit, self.slots[consumer].start)
            elif consumer not in self.data.free and self.ops[consumer].planned_start is not None:
                limit = min(limit, self.ops[consumer].planned_start)
        return limit

    def order(self, operation_id: UUID) -> tuple:
        task = self.task_of[operation_id]
        # Сортировка по возрастанию: позднее «закончить к», больший номер и этап — раньше.
        return (-self.bound(operation_id).timestamp(), _desc(task.number), -self.topo[operation_id])

    def ready(self, pending: set[UUID]) -> list[UUID]:
        return sorted(
            (item for item in pending if not any(consumer in pending for consumer in self.consumers.get(item, ()))),
            key=self.order,
        )

    def slot(self, operation: ProductionOperation, operator_id: UUID, bound: datetime) -> tuple[datetime, datetime] | None:
        data = self.data
        task = self.task_of[operation.id]
        rate = _rate(data.skills, operation.skill_codes, (operator_id,))
        net = ceil(operation.setup_seconds + operation.seconds_per_unit * task.operation_units(operation.id) / (rate if rate > 0 else Decimal(1)))
        clock = clock_seconds(net, data.shift)
        finish = _latest_moment(bound, data.holidays, data.shift)
        for _ in range(_SLOT_ATTEMPTS):
            start = rewind_clock(finish, clock, data.holidays, data.shift)
            overlapping = [busy_start for busy_start, busy_finish in self.busy.get(operator_id, ()) if busy_start < finish and busy_finish > start]
            if not overlapping:
                return start, finish
            finish = _latest_moment(max(overlapping), data.holidays, data.shift)
        return None

    def place(self, operation_id: UUID) -> None:
        operation = self.ops[operation_id]
        bound = self.bound(operation_id)
        best: tuple[tuple, UUID, datetime, datetime] | None = None
        allowed = False
        for index, operator_id in enumerate(self.data.team):
            if not _allowed(self.data.skills, operation.skill_codes, operator_id):
                continue
            allowed = True
            try:
                window = self.slot(operation, operator_id, bound)
            except ProductionValidationError:
                window = None
            if window is None:
                continue
            rank = (-window[0].timestamp(), -window[1].timestamp(), index)
            if best is None or rank < best[0]:
                best = (rank, operator_id, *window)
        if best is None:
            self.unplaced[operation_id] = (
                "Не помещается в календарь даже с опозданием" if allowed else
                f"Нет монтажников {AUTOPLAN_SITE} с допуском к операции {operation.code or stage_name(operation.stage)} — заполните компетенции"
            )
            return
        _, operator_id, start, finish = best
        self.busy.setdefault(operator_id, []).append((start, finish))
        self.slots[operation_id] = LatestSlot(self.task_of[operation_id].id, operation_id, operator_id, start, finish)

    def run(self) -> None:
        pending = {operation_id for operation_id in self.data.free if operation_id in self.ops}
        while pending:
            ready = self.ready(pending)
            if not ready:
                for operation_id in pending:
                    self.unplaced[operation_id] = "Схема этапов задания замкнута в цикл"
                return
            current = ready[0]
            pending.discard(current)
            self.place(current)


def _desc(number: str) -> tuple:
    """Номер ПЗ «от больших»: цифры с минусом, текст — как есть (порядок стабильный)."""
    digits = "".join(char for char in number if char.isdigit())
    return (-int(digits) if digits else 0, number)


def backward_plan(data: AutoplanInput, ship_date: date) -> BackwardResult:
    """Расставить свободные операции как можно позже к дате отгрузки (см. описание модуля)."""
    due = due_moment(ship_date, data.start.tzinfo, data.holidays, data.shift)
    planner = _Backward(data, due)
    planner.run()
    warnings: list[str] = []
    for task in data.tasks:
        if not any(operation.id in data.free for operation in task.operations):
            continue
        for operation in task.operations:
            if operation.id not in data.free and operation.planned_finish is not None and operation.planned_finish > due:
                warnings.append(
                    f"{task.number} · этап {operation.position} «{stage_name(operation.stage)}» назначен с окончанием "
                    f"{operation.planned_finish:%d.%m %H:%M} — позже даты отгрузки"
                )
    windows: list[TaskWindow] = []
    for task in {planner.task_of[item].id: planner.task_of[item] for item in planner.slots}.values():
        own = [slot for slot in planner.slots.values() if slot.task_id == task.id]
        start = min(slot.start for slot in own)
        windows.append(TaskWindow(
            task.id, start, max(slot.finish for slot in own),
            slack_between(data.start, start, data.holidays, data.shift),
        ))
    windows.sort(key=lambda item: (item.start, item.finish, str(item.task_id)))
    slots = tuple(sorted(planner.slots.values(), key=lambda slot: (slot.start, slot.finish, str(slot.operator_id))))
    unplaced = tuple(
        Unplaced(planner.task_of[operation_id].id, operation_id, reason)
        for operation_id, reason in sorted(planner.unplaced.items(), key=lambda pair: (
            planner.task_of[pair[0]].number, planner.ops[pair[0]].position,
        ))
    )
    return BackwardResult(due, data.start, slots, unplaced, tuple(warnings), tuple(windows))
