"""Базовый план и отклонения «план / факт» (идея из PRODUCTION_PLANNER Василия — «первый применённый план хранится
в baseline», перенесено с разрешения автора; в ERP — по кнопке «Зафиксировать базовый план»).

Базовый план — неизменяемый снимок производственного плана на момент фиксации: по каждому запущенному заданию с
плановыми сроками — плановое начало, плановый конец и тираж. Следующая фиксация пишет новый снимок с номером на
единицу больше; сравнение идёт с последним.

Отклонение задания против снимка считается по факту ERP:
- начато позже — первое «Начать» по заданию (журнал рабочего времени, без отменённых начал; для работ до журнала —
  начало сданной выработки) позже планового начала; не начато, а плановое начало прошло, — тоже опоздание (до «сейчас»);
- закончено позже — задание «Выполнено», последняя сдача (выработка финального этапа или «Сдано N шт» площадки)
  позже планового конца; не выполнено, а плановый конец прошёл, — опоздание до «сейчас»;
- недодано — у выполненного, отменённого или просроченного задания сдано меньше штук, чем в снимке.
Опоздание меньше допуска (`TOLERANCE`) отклонением не считается: план идёт по минутам смены, факт — по нажатиям."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Mapping
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from .domain import ProductionStatus, ProductionTask, ProductionValidationError

MOSCOW = ZoneInfo("Europe/Moscow")
# Опоздание до часа — не отклонение.
TOLERANCE = timedelta(hours=1)


def moment(value: datetime) -> datetime:
    """Момент для сравнения: SQLite отдаёт время без пояса (это московское время записи), PostgreSQL — с поясом."""
    return value.replace(tzinfo=MOSCOW) if value.tzinfo is None or value.utcoffset() is None else value


@dataclass(frozen=True)
class BaselineLine:
    task_id: UUID
    task_number: str
    project: str
    item: str
    planned_start: datetime
    planned_finish: datetime
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ProductionValidationError("Тираж задания в базовом плане должен быть больше нуля")
        if moment(self.planned_finish) < moment(self.planned_start):
            raise ProductionValidationError(f"Задание {self.task_number}: плановый конец раньше начала")
        if not str(self.task_number or "").strip():
            raise ProductionValidationError("У строки базового плана нужен номер задания")


@dataclass(frozen=True)
class PlanBaseline:
    id: UUID
    number: int
    created_at: datetime
    created_by_id: UUID
    created_by: str
    # Запущенные задания без плановых сроков на момент фиксации: сравнивать их не с чем, в снимок они не входят.
    skipped: int
    lines: tuple[BaselineLine, ...]

    def __post_init__(self) -> None:
        if self.number < 1 or self.skipped < 0:
            raise ProductionValidationError("Номер базового плана — от 1, пропущенных заданий не меньше нуля")
        if not str(self.created_by or "").strip():
            raise ProductionValidationError("У базового плана нужен автор")
        if len({line.task_id for line in self.lines}) != len(self.lines):
            raise ProductionValidationError("Задание в базовом плане дважды")


def task_window(task: ProductionTask) -> tuple[datetime, datetime] | None:
    """Плановое окно задания: от самого раннего планового начала операций до самого позднего конца."""
    starts = [operation.planned_start for operation in task.operations if operation.planned_start is not None]
    finishes = [operation.planned_finish for operation in task.operations if operation.planned_finish is not None]
    if not starts or not finishes:
        return None
    return min(starts, key=moment), max(finishes, key=moment)


def take_baseline(
    tasks: Iterable[ProductionTask], *, number: int, now: datetime, actor_id: UUID, actor_name: str,
) -> PlanBaseline:
    """Снимок плана: запущенные задания с плановыми сроками, по номеру задания."""
    lines: list[BaselineLine] = []
    skipped = 0
    for task in tasks:
        if task.status is not ProductionStatus.RUNNING:
            continue
        window = task_window(task)
        if window is None:
            skipped += 1
            continue
        lines.append(BaselineLine(
            task_id=task.id, task_number=task.number, project=task.project, item=task.item,
            planned_start=window[0], planned_finish=window[1], quantity=task.quantity,
        ))
    if not lines:
        raise ProductionValidationError(
            "Фиксировать нечего: у запущенных заданий нет плановых сроков — сначала назначьте операции"
        )
    return PlanBaseline(
        id=uuid4(), number=number, created_at=now, created_by_id=actor_id, created_by=str(actor_name).strip(),
        skipped=skipped, lines=tuple(sorted(lines, key=lambda line: (line.task_number, str(line.task_id)))),
    )


@dataclass(frozen=True)
class TaskFact:
    """Факт задания на сейчас: статус, первое начало, последняя сдача (у выполненного) и сдано штук изделия."""
    status: ProductionStatus
    started_at: datetime | None
    finished_at: datetime | None
    done_quantity: Decimal


@dataclass(frozen=True)
class Deviation:
    line: BaselineLine
    fact: TaskFact | None
    # Опоздание: положительное — позже плана; None — сравнивать не с чем (ещё не наступило).
    start_delay: timedelta | None
    finish_delay: timedelta | None
    # Недодано штук против снимка (0 — нет).
    shortfall: Decimal

    @property
    def late_start(self) -> bool:
        return self.start_delay is not None and self.start_delay > TOLERANCE

    @property
    def late_finish(self) -> bool:
        return self.finish_delay is not None and self.finish_delay > TOLERANCE

    @property
    def short(self) -> bool:
        return self.shortfall > 0

    @property
    def deviates(self) -> bool:
        return self.late_start or self.late_finish or self.short

    @property
    def started(self) -> bool:
        return self.fact is not None and self.fact.started_at is not None

    @property
    def finished(self) -> bool:
        return self.fact is not None and self.fact.status is ProductionStatus.DONE


def deviation(line: BaselineLine, fact: TaskFact | None, now: datetime) -> Deviation:
    current = moment(now)
    start, finish = moment(line.planned_start), moment(line.planned_finish)
    if fact is None:
        # Задание удалено вместе с проектом (строка снимка уходит с ним) — сюда не доходит; на всякий случай — без факта.
        return Deviation(line, None, None, None, Decimal(0))
    running = fact.status is ProductionStatus.RUNNING
    if fact.started_at is not None:
        start_delay = moment(fact.started_at) - start
    elif running and current > start:
        start_delay = current - start
    else:
        start_delay = None
    if fact.status is ProductionStatus.DONE and fact.finished_at is not None:
        finish_delay = moment(fact.finished_at) - finish
    elif running and current > finish:
        finish_delay = current - finish
    else:
        finish_delay = None
    closed = fact.status is not ProductionStatus.RUNNING or current > finish
    shortfall = max(Decimal(line.quantity) - fact.done_quantity, Decimal(0)) if closed else Decimal(0)
    return Deviation(line, fact, start_delay, finish_delay, shortfall)


def deviations(baseline: PlanBaseline, facts: Mapping[UUID, TaskFact], now: datetime) -> list[Deviation]:
    return [deviation(line, facts.get(line.task_id), now) for line in baseline.lines]


def first_start(starts: Iterable[tuple[UUID, datetime]], cancelled: Iterable[UUID]) -> datetime | None:
    """Первое «Начать» без отменённых начал: starts — (сессия, момент), cancelled — сессии с «Отменить начало»."""
    dropped = set(cancelled)
    moments = [at for session_id, at in starts if session_id not in dropped]
    return min(moments, key=moment) if moments else None
