"""Простои с причинами (владелец, 13.10.2026). Идея — «Причины и длительность свободных интервалов» из
PRODUCTION_PLANNER Василия (src/domain/idle.ts, перенос с разрешения автора): у него простои выводятся из расчётного
плана, в ERP их отмечает цех — монтажник или мастер.

Простой — остановка работы по заданию: монтажник по своей операции (или мастер за него) либо мастер по заданию
целиком. Причина — из короткого списка; у «Другое» обязателен комментарий. Начало пишется нажатием «Простой»,
конец — «Закончить простой»; у задания открыт не больше одного простоя (база держит частичным UNIQUE).

Отчёт за период суммирует длительность простоев по причинам или по сотрудникам: в период входит только часть
простоя, попавшая в его дни; открытый простой считается до «сейчас». Рядом — работа из журнала рабочего времени
(migration 0105) за тот же период: какую долю времени цех стоял."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Iterable
from uuid import UUID

from app.shared.clock import MOSCOW

from .domain import ProductionValidationError


class DowntimeReason(str, Enum):
    NO_MATERIAL = "no_material"
    NO_DRAWING = "no_drawing"
    EQUIPMENT = "equipment"
    WAITING_QC = "waiting_qc"
    OTHER = "other"


REASON_NAMES: dict[DowntimeReason, str] = {
    DowntimeReason.NO_MATERIAL: "Нет материала",
    DowntimeReason.NO_DRAWING: "Нет чертежа / ТК",
    DowntimeReason.EQUIPMENT: "Оборудование",
    DowntimeReason.WAITING_QC: "Ждёт ОТК",
    DowntimeReason.OTHER: "Другое",
}

COMMENT_LIMIT = 500
# Отчёт группируется по причине или по сотруднику; простой задания целиком — строка «Задание целиком».
REPORT_KEYS = ("reason", "operator")
WHOLE_TASK = "Задание целиком"


class DowntimeConflict(Exception):
    """Простой уже открыт или уже закончен другим нажатием."""


def moscow(value: datetime) -> datetime:
    """Момент по Москве: SQLite отдаёт время без пояса (это московское время стены), PostgreSQL — с поясом."""
    return value.replace(tzinfo=MOSCOW) if value.tzinfo is None or value.utcoffset() is None else value.astimezone(MOSCOW)


@dataclass(frozen=True)
class Downtime:
    id: UUID
    task_id: UUID | None
    operation_id: UUID | None
    # Чей простой: монтажник; None — задание стоит целиком (отметил мастер).
    operator_id: UUID | None
    operator_name: str
    reason: DowntimeReason
    comment: str
    started_at: datetime
    started_by_id: UUID
    started_by_name: str
    # Копия задания на момент начала: отчёт не зависит от того, что задание потом переделали или удалили.
    project: str
    task_number: str
    item: str
    stage: str = ""
    ended_at: datetime | None = None
    ended_by_id: UUID | None = None
    ended_by_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "comment", str(self.comment or "").strip())
        if not isinstance(self.reason, DowntimeReason):
            raise ProductionValidationError("Причина простоя — из списка")
        if len(self.comment) > COMMENT_LIMIT:
            raise ProductionValidationError(f"Комментарий к простою — не длиннее {COMMENT_LIMIT} символов")
        if self.reason is DowntimeReason.OTHER and not self.comment:
            raise ProductionValidationError("У причины «Другое» напишите, что случилось")
        if (self.ended_at is None) != (self.ended_by_id is None):
            raise ProductionValidationError("Конец простоя пишется вместе с тем, кто его закончил")
        if self.ended_at is not None and moscow(self.ended_at) < moscow(self.started_at):
            raise ProductionValidationError("Конец простоя раньше начала")

    @property
    def open(self) -> bool:
        return self.ended_at is None

    @property
    def reason_name(self) -> str:
        return REASON_NAMES[self.reason]

    def close(self, at: datetime, by_id: UUID, by_name: str) -> "Downtime":
        if not self.open:
            raise DowntimeConflict("Простой уже закончен")
        if moscow(at) < moscow(self.started_at):
            raise ProductionValidationError("Конец простоя раньше начала")
        return replace(self, ended_at=at, ended_by_id=by_id, ended_by_name=by_name)

    def seconds_within(self, since: datetime, until: datetime, now: datetime) -> int:
        """Сколько секунд простоя попало в [since, until); открытый — до «сейчас»."""
        start = max(moscow(self.started_at), moscow(since))
        end = min(moscow(self.ended_at) if self.ended_at is not None else moscow(now), moscow(until))
        return max(0, int((end - start).total_seconds()))


@dataclass(frozen=True)
class ReportRow:
    key: str
    label: str
    count: int
    seconds: int
    open: int


def report(
    downtimes: Iterable[Downtime], *, by: str, since: datetime, until: datetime, now: datetime,
) -> list[ReportRow]:
    """Сумма простоев за период: по причинам — в порядке списка причин (без пустых), по сотрудникам — по убыванию."""
    if by not in REPORT_KEYS:
        raise ProductionValidationError("Отчёт по простоям группируется по причине или по сотруднику")
    sums: dict[str, list] = {}
    for item in downtimes:
        seconds = item.seconds_within(since, until, now)
        if seconds <= 0:
            continue
        if by == "reason":
            key, label = item.reason.value, item.reason_name
        else:
            key, label = (str(item.operator_id), item.operator_name) if item.operator_id else ("task", WHOLE_TASK)
        row = sums.setdefault(key, [label, 0, 0, 0])
        row[1] += 1
        row[2] += seconds
        row[3] += int(item.open)
    rows = [ReportRow(key, label, count, seconds, opened) for key, (label, count, seconds, opened) in sums.items()]
    if by == "reason":
        order = {reason.value: position for position, reason in enumerate(DowntimeReason)}
        return sorted(rows, key=lambda row: order[row.key])
    return sorted(rows, key=lambda row: (-row.seconds, row.label))
