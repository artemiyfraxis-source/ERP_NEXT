"""«Норма и факт» (владелец, 29.10.2026, идея №2 «Ресурсного планирования»): сколько на самом деле занимает операция
против нормы ЭТК — по шаблонам операций и по монтажникам. Только отчёт: нормы и компетенции сам не меняет, предлагает.

Факт — работа одного монтажника над одной операцией задания за период: сумма его сдач (время сессий без пауз, сданные
годные + брак штуки, норма на эти штуки). Отношение факт / норма: 1,00 — ровно по норме, 1,40 — на 40 % дольше.

Правила:
- в медиану идут факты с нормой, штуками и временем (> 0); без нормы — «нет нормы», в сводку не идут;
- «мало данных» — меньше MIN_FACTS фактов: медиана показывается, предложения нет;
- шаблон «норма занижена» — медиана > SLOW и даже нижняя четверть > 1 (три четверти фактов дольше нормы); «завышена» —
  медиана < FAST и верхняя четверть < 1. Предложение — штучное время × медиану;
- монтажник: его факт делится на медиану шаблона (если по шаблону данных хватает), чтобы неверная норма не
  записывалась ему в скорость; коэффициент скорости «Компетенций» = 1 / медиана (план делит норму на коэффициент).
  Предлагается, если данных хватает и он отличается от 1 хотя бы на 0,10."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Iterable, Sequence
from uuid import UUID

MIN_FACTS = 5
SLOW = 1.25
FAST = 0.8
COEFFICIENT_STEP = 0.05
COEFFICIENT_MIN, COEFFICIENT_MAX = 0.3, 3.0


@dataclass(frozen=True)
class Submission:
    """Одна сдача: из журнала рабочего времени («Сдать») или, до журнала (0105), из сдачи сессии."""
    session_id: UUID
    task_id: UUID
    task_number: str
    project: str
    item: str
    operation_id: UUID
    position: int
    operation_code: str
    stage: str
    operator_id: UUID
    operator_name: str
    started_at: datetime
    finished_at: datetime
    actual_seconds: int
    quantity: Decimal
    seconds_per_unit: Decimal
    norm_seconds: int


@dataclass(frozen=True)
class Fact:
    task_id: UUID
    task_number: str
    project: str
    item: str
    operation_id: UUID
    position: int
    template: str  # ключ шаблона: код операции, без кода — название этапа
    operation_code: str
    stage: str
    operator_id: UUID
    operator_name: str
    started_at: datetime
    finished_at: datetime
    submissions: int
    actual_seconds: int
    quantity: Decimal
    norm_seconds: int
    seconds_per_unit: Decimal

    @property
    def counted(self) -> bool:
        return self.norm_seconds > 0 and self.quantity > 0 and self.actual_seconds > 0

    @property
    def ratio(self) -> float | None:
        return self.actual_seconds / self.norm_seconds if self.counted else None

    @property
    def actual_per_piece(self) -> float | None:
        return self.actual_seconds / float(self.quantity) if self.quantity > 0 else None

    @property
    def norm_per_piece(self) -> float | None:
        return self.norm_seconds / float(self.quantity) if self.quantity > 0 and self.norm_seconds > 0 else None


def template_key(operation_code: str, stage: str) -> str:
    code = str(operation_code or "").strip()
    return code or str(stage or "").strip()


def facts(submissions: Iterable[Submission]) -> list[Fact]:
    """Сдачи → факты: одна операция задания × один монтажник."""
    groups: dict[tuple[UUID, UUID], list[Submission]] = defaultdict(list)
    for item in submissions:
        groups[(item.operation_id, item.operator_id)].append(item)
    result: list[Fact] = []
    for items in groups.values():
        items.sort(key=lambda item: item.finished_at)
        last = items[-1]
        result.append(Fact(
            task_id=last.task_id, task_number=last.task_number, project=last.project, item=last.item,
            operation_id=last.operation_id, position=last.position,
            template=template_key(last.operation_code, last.stage), operation_code=last.operation_code, stage=last.stage,
            operator_id=last.operator_id, operator_name=last.operator_name,
            started_at=min(item.started_at for item in items), finished_at=last.finished_at,
            submissions=len(items),
            actual_seconds=sum(item.actual_seconds for item in items),
            quantity=sum((item.quantity for item in items), Decimal(0)),
            norm_seconds=sum(item.norm_seconds for item in items),
            seconds_per_unit=last.seconds_per_unit,
        ))
    result.sort(key=lambda fact: (fact.finished_at, fact.task_number, fact.position))
    return result


def quartiles(values: Sequence[float]) -> tuple[float, float]:
    """Нижняя и верхняя четверти (линейная интерполяция, как «КВАРТИЛЬ» в таблицах)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("пусто")

    def at(share: float) -> float:
        position = share * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return at(0.25), at(0.75)


@dataclass(frozen=True)
class Spread:
    facts: int  # фактов в сводке (с нормой)
    no_norm: int
    median: float | None
    low: float | None
    high: float | None
    actual_seconds: int
    norm_seconds: int

    @property
    def enough(self) -> bool:
        return self.facts >= MIN_FACTS

    @property
    def verdict(self) -> str:
        """«медленнее» / «быстрее» / «в норме» по медиане; без фактов — «»."""
        if self.median is None:
            return ""
        return "медленнее" if self.median > SLOW else "быстрее" if self.median < FAST else "в норме"


def spread(items: Sequence[Fact], ratios: Sequence[float] | None = None) -> Spread:
    counted = [fact for fact in items if fact.counted]
    values = list(ratios) if ratios is not None else [fact.ratio for fact in counted]
    low, high = quartiles(values) if values else (None, None)
    return Spread(
        facts=len(values), no_norm=len(items) - len(counted),
        median=median(values) if values else None, low=low, high=high,
        actual_seconds=sum(fact.actual_seconds for fact in items),
        norm_seconds=sum(fact.norm_seconds for fact in counted),
    )


@dataclass(frozen=True)
class TemplateRow:
    template: str
    operation_code: str
    stage: str
    spread: Spread
    seconds_per_unit: float | None  # медиана штучного времени по фактам
    suggestion: str  # «» — предлагать нечего
    suggested_seconds_per_unit: float | None


def _seconds_text(value: float) -> str:
    seconds = int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if seconds < 60:
        return f"{seconds} с"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} мин {rest} с" if rest else f"{minutes} мин"


def _ratio_text(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def stable_slow(value: Spread) -> bool:
    return value.enough and value.median is not None and value.median > SLOW and (value.low or 0) > 1


def stable_fast(value: Spread) -> bool:
    return value.enough and value.median is not None and value.median < FAST and (value.high or 0) < 1


def by_template(items: Sequence[Fact]) -> list[TemplateRow]:
    groups: dict[str, list[Fact]] = defaultdict(list)
    for fact in items:
        groups[fact.template].append(fact)
    rows: list[TemplateRow] = []
    for template, group in groups.items():
        value = spread(group)
        units = [float(fact.seconds_per_unit) for fact in group if fact.counted and fact.seconds_per_unit > 0]
        unit = median(units) if units else None
        suggestion, suggested = "", None
        if (stable_slow(value) or stable_fast(value)) and value.median is not None:
            word = "занижена" if value.median > 1 else "завышена"
            if unit:
                suggested = unit * value.median
                suggestion = (f"Норма {word}: факт ×{_ratio_text(value.median)} — предложение: штучное время "
                              f"{_seconds_text(suggested)} вместо {_seconds_text(unit)}")
            else:
                suggestion = f"Норма {word}: факт ×{_ratio_text(value.median)} от нормы"
        last = max(group, key=lambda fact: fact.finished_at)
        rows.append(TemplateRow(template, last.operation_code, last.stage, value, unit, suggestion, suggested))
    rows.sort(key=lambda row: (-row.spread.facts, row.stage, row.template))
    return rows


def template_medians(rows: Iterable[TemplateRow]) -> dict[str, float]:
    """Медианы шаблонов, по которым данных хватает: ими монтажник очищается от ошибки нормы."""
    return {row.template: row.spread.median for row in rows if row.spread.enough and row.spread.median}


def relative_ratio(fact: Fact, medians: dict[str, float]) -> float | None:
    """Отношение монтажника к обычному для шаблона: факт / норма ÷ медиана шаблона (данных мало — к норме)."""
    if fact.ratio is None:
        return None
    return fact.ratio / medians.get(fact.template, 1.0)


@dataclass(frozen=True)
class OperatorRow:
    operator_id: UUID
    operator_name: str
    spread: Spread  # сырые факт / норма
    relative: float | None  # медиана относительно шаблонов
    coefficient: float | None  # предлагаемый коэффициент скорости; None — не предлагать
    suggestion: str


def _coefficient(relative: float) -> float:
    raw = min(max(1 / relative, COEFFICIENT_MIN), COEFFICIENT_MAX)
    return round(round(raw / COEFFICIENT_STEP) * COEFFICIENT_STEP, 2)


def by_operator(items: Sequence[Fact], templates: Sequence[TemplateRow]) -> list[OperatorRow]:
    medians = template_medians(templates)
    groups: dict[UUID, list[Fact]] = defaultdict(list)
    for fact in items:
        groups[fact.operator_id].append(fact)
    rows: list[OperatorRow] = []
    for operator_id, group in groups.items():
        value = spread(group)
        relatives = [ratio for ratio in (relative_ratio(fact, medians) for fact in group) if ratio is not None]
        relative = median(relatives) if relatives else None
        coefficient, suggestion = None, ""
        if relative and value.enough:
            proposed = _coefficient(relative)
            if abs(proposed - 1) >= COEFFICIENT_STEP * 2 - 1e-9:
                coefficient = proposed
                word = "медленнее" if proposed < 1 else "быстрее"
                suggestion = f"Работает {word} нормы — коэффициент скорости в «Компетенциях»: {_ratio_text(proposed)}"
        name = max(group, key=lambda fact: fact.finished_at).operator_name
        rows.append(OperatorRow(operator_id, name, value, relative, coefficient, suggestion))
    rows.sort(key=lambda row: (-row.spread.facts, row.operator_name))
    return rows
