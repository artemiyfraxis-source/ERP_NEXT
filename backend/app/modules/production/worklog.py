"""Журнал рабочего времени (владелец, 12.10.2026: «логирование времени по завершению, чтобы через пару месяцев
сделать сводку и сравнить с расчётными»).

Каждое «Начать / Пауза / Продолжить / Сдать / Отменить начало» пишет строку, которая больше не меняется: кто делал
(исполнитель — монтажник, чьё это время), кто нажал (тот же монтажник или мастер, работающий за него), когда,
и копия норм операции на этот момент — штучное время, подготовка и плановая норма доли. Обновление норм в задании
историю не переписывает. Строка «Сдать» несёт сданные штуки, фактическое время сессии (сумма интервалов работы,
паузы не считаются) и норму на сданные штуки."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from math import ceil
from typing import Iterable
from uuid import UUID

from .domain import ProductionValidationError


class WorkEvent(str, Enum):
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    FINISH = "finish"
    CANCEL = "cancel"


# Сводка «факт / норма» группируется по одному из этих ключей.
SUMMARY_KEYS = ("operation_code", "operator", "project")


@dataclass(frozen=True)
class WorkTimeEntry:
    id: UUID
    idempotency_key: str
    session_id: UUID
    sequence: int
    event: WorkEvent
    occurred_at: datetime
    task_id: UUID
    operation_id: UUID
    operator_id: UUID
    operator_name: str
    actor_id: UUID
    actor_name: str
    project: str
    item: str
    task_number: str
    operation_code: str
    stage: str
    position: int
    seconds_per_unit: Decimal
    setup_seconds: Decimal
    planned_norm_seconds: int
    good_quantity: Decimal | None = None
    defect_quantity: Decimal | None = None
    running_seconds: int | None = None
    earned_norm_seconds: int | None = None

    def __post_init__(self) -> None:
        if not str(self.idempotency_key or "").strip():
            raise ProductionValidationError("У записи журнала времени нужен ключ повтора")
        if self.sequence < 0 or self.planned_norm_seconds < 0:
            raise ProductionValidationError("Номер записи и норма не могут быть отрицательными")
        if self.seconds_per_unit < 0 or self.setup_seconds < 0:
            raise ProductionValidationError("Нормативы операции не могут быть отрицательными")
        finish = (self.good_quantity, self.defect_quantity, self.running_seconds, self.earned_norm_seconds)
        if self.event is WorkEvent.FINISH:
            if any(value is None for value in finish):
                raise ProductionValidationError("Сдача пишется со штуками, фактом и нормой")
            if self.good_quantity < 0 or self.defect_quantity < 0 or self.running_seconds < 0 or self.earned_norm_seconds < 0:
                raise ProductionValidationError("Штуки, факт и норма сдачи не могут быть отрицательными")
        elif any(value is not None for value in finish):
            raise ProductionValidationError("Штуки и факт пишутся только при сдаче")

    @property
    def on_behalf(self) -> bool:
        """Нажал не сам исполнитель (мастер работал за монтажника)."""
        return self.actor_id != self.operator_id


def earned_norm_seconds(seconds_per_unit: Decimal, setup_seconds: Decimal, quantity: Decimal, *, first: bool) -> int:
    """Норма на сданные штуки: штучное время × штуки; подготовка — один раз, в первой сдаче исполнителя по операции."""
    return ceil(Decimal(seconds_per_unit) * Decimal(quantity) + (Decimal(setup_seconds) if first else Decimal(0)))


def percent(actual_seconds: int, norm_seconds: int) -> int | None:
    """Факт в процентах нормы (109 — на 9 % дольше нормы); без нормы — None."""
    return round(actual_seconds * 100 / norm_seconds) if norm_seconds > 0 else None


@dataclass(frozen=True)
class TimeFact:
    """«Факт / норма» исполнителя по операции: сумма сдач."""
    actual_seconds: int
    norm_seconds: int
    finishes: int

    @property
    def percent(self) -> int | None:
        return percent(self.actual_seconds, self.norm_seconds)


def time_fact(entries: Iterable[WorkTimeEntry]) -> TimeFact | None:
    finishes = [entry for entry in entries if entry.event is WorkEvent.FINISH]
    if not finishes:
        return None
    return TimeFact(
        actual_seconds=sum(entry.running_seconds or 0 for entry in finishes),
        norm_seconds=sum(entry.earned_norm_seconds or 0 for entry in finishes),
        finishes=len(finishes),
    )
