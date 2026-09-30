"""Pure production-task and working-calendar rules ported from ProdTasks.gs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from math import ceil
from uuid import UUID, uuid4


class ProductionValidationError(ValueError):
    pass


class ProductionStatus(str, Enum):
    RUNNING = "Запущено"
    CANCELLED = "Отменено"


@dataclass(frozen=True)
class BusyInterval:
    label: str
    start: datetime
    finish: datetime


@dataclass(frozen=True)
class PlanResult:
    start: datetime | None
    finish: datetime | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WorkShift:
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    start: time = time(9, 0)
    finish: time = time(17, 30)
    breaks: tuple[tuple[time, time], ...] = ((time(13, 0), time(13, 30)),)
    # Чистое рабочее время в день (владелец, 30.09: смена 9:00–17:30, обед 30 минут, ещё час на туалет и перекуры).
    effective_seconds: int = 7 * 60 * 60

    def __post_init__(self) -> None:
        if not self.weekdays or any(day < 0 or day > 6 for day in self.weekdays):
            raise ProductionValidationError("Нужно выбрать хотя бы один рабочий день")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ProductionValidationError("Рабочий день указан дважды")
        if self.start >= self.finish:
            raise ProductionValidationError("Конец смены должен быть позже начала")
        if self.effective_seconds <= 0 or self.effective_seconds > 24 * 60 * 60:
            raise ProductionValidationError("Эффективные часы должны быть больше нуля")
        for start, finish in self.breaks:
            if start >= finish or start < self.start or finish > self.finish:
                raise ProductionValidationError("Перерыв должен находиться внутри смены")


DEFAULT_SHIFT = WorkShift()


@dataclass(frozen=True)
class OperatorSkill:
    operator_id: UUID
    operation_code: str
    allowed: bool
    stars: int
    coefficient: Decimal

    def __post_init__(self) -> None:
        if not self.operation_code.strip():
            raise ProductionValidationError("Код операции обязателен")
        if self.stars < 0 or self.stars > 5:
            raise ProductionValidationError("Звёзды должны быть целым числом от 0 до 5")
        if not self.coefficient.is_finite() or self.coefficient <= 0 or self.coefficient > 100:
            raise ProductionValidationError("Коэффициент должен быть больше нуля")


@dataclass(frozen=True)
class ProductionOperation:
    id: UUID
    position: int
    code: str
    stage: str
    description: str
    seconds_per_unit: Decimal
    setup_seconds: Decimal
    norm_seconds: int
    operator_ids: tuple[UUID, ...] = ()
    planned_start: datetime | None = None
    planned_finish: datetime | None = None
    assigned_by: str | None = None

    @classmethod
    def create(
        cls,
        *,
        position: int,
        code: str,
        stage: str,
        description: str,
        seconds_per_unit: Decimal,
        setup_seconds: Decimal,
        quantity: int,
    ) -> ProductionOperation:
        if position <= 0:
            raise ProductionValidationError("Номер этапа должен быть больше нуля")
        if not str(stage or "").strip():
            raise ProductionValidationError("Название этапа обязательно")
        unit = Decimal(seconds_per_unit)
        setup = Decimal(setup_seconds)
        if not unit.is_finite() or not setup.is_finite() or unit < 0 or setup < 0:
            raise ProductionValidationError("Нормативы операции не могут быть отрицательными")
        return cls(
            id=uuid4(),
            position=position,
            code=str(code or "").strip(),
            stage=str(stage).strip(),
            description=str(description or "").strip(),
            seconds_per_unit=unit,
            setup_seconds=setup,
            norm_seconds=ceil(setup + unit * quantity),
        )

    def assign(
        self,
        operator_ids: tuple[UUID, ...],
        plan: PlanResult,
        assigned_by: str,
    ) -> ProductionOperation:
        if len(set(operator_ids)) != len(operator_ids):
            raise ProductionValidationError("Оператор выбран дважды")
        if operator_ids and (plan.start is None or plan.finish is None):
            raise ProductionValidationError("Для назначения нужен плановый интервал")
        if not operator_ids and (plan.start is not None or plan.finish is not None):
            raise ProductionValidationError("Без операторов плановый интервал должен быть пустым")
        return replace(
            self,
            operator_ids=operator_ids,
            planned_start=plan.start,
            planned_finish=plan.finish,
            assigned_by=str(assigned_by or "").strip() or None,
        )


@dataclass(frozen=True)
class ProductionTask:
    id: UUID
    number: str
    source_idempotency_key: str
    source_fingerprint: str
    version: int
    created_at: datetime
    project: str
    item: str
    name: str
    quantity: int
    fulfillment_request_id: UUID | None
    deadline: date | None
    status: ProductionStatus
    launched_by: str
    operations: tuple[ProductionOperation, ...]

    @classmethod
    def create(
        cls,
        *,
        number: str,
        source_idempotency_key: str,
        source_fingerprint: str,
        project: str,
        item: str,
        name: str,
        quantity: int,
        fulfillment_request_id: UUID | None,
        deadline: date | None,
        launched_by: str,
        operations: tuple[ProductionOperation, ...],
        created_at: datetime,
    ) -> ProductionTask:
        if quantity <= 0:
            raise ProductionValidationError("Количество должно быть целым числом больше нуля")
        if not operations:
            raise ProductionValidationError(f"Техкарта «{item}» не выдана — запускать нечего")
        if len({operation.position for operation in operations}) != len(operations):
            raise ProductionValidationError("Номера этапов маршрута должны быть уникальны")
        required = (number, source_idempotency_key, source_fingerprint, project, item, launched_by)
        if any(not str(value or "").strip() for value in required):
            raise ProductionValidationError("Номер, проект, изделие и инициатор обязательны")
        return cls(
            id=uuid4(),
            number=str(number).strip(),
            source_idempotency_key=str(source_idempotency_key).strip(),
            source_fingerprint=str(source_fingerprint).strip(),
            version=1,
            created_at=created_at,
            project=str(project).strip(),
            item=str(item).strip(),
            name=str(name or "").strip(),
            quantity=quantity,
            fulfillment_request_id=fulfillment_request_id,
            deadline=deadline,
            status=ProductionStatus.RUNNING,
            launched_by=str(launched_by).strip(),
            operations=tuple(sorted(operations, key=lambda operation: operation.position)),
        )

    def operation(self, operation_id: UUID) -> ProductionOperation:
        operation = next((item for item in self.operations if item.id == operation_id), None)
        if operation is None:
            raise ProductionValidationError("Операция задания не найдена")
        return operation

    def assign(
        self,
        operation_id: UUID,
        operator_ids: tuple[UUID, ...],
        plan: PlanResult,
        assigned_by: str,
    ) -> ProductionTask:
        if self.status is ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {self.number} отменено — назначать нельзя")
        operation = self.operation(operation_id)
        assigned = operation.assign(operator_ids, plan, assigned_by)
        return replace(
            self,
            operations=tuple(assigned if item.id == operation_id else item for item in self.operations),
            version=self.version + 1,
        )

    def apply_schedule(
        self,
        assignments: tuple[tuple[UUID, tuple[UUID, ...], datetime, datetime], ...],
        assigned_by: str,
    ) -> ProductionTask:
        if self.status is ProductionStatus.CANCELLED:
            raise ProductionValidationError(f"Задание {self.number} отменено — планировать нельзя")
        by_operation = {item[0]: item[1:] for item in assignments}
        if len(by_operation) != len(assignments) or set(by_operation) != {item.id for item in self.operations}:
            raise ProductionValidationError("Автоплан должен содержать все операции задания")
        operations = tuple(
            operation.assign(
                by_operation[operation.id][0],
                PlanResult(by_operation[operation.id][1], by_operation[operation.id][2], ()),
                assigned_by,
            )
            for operation in self.operations
        )
        return replace(self, operations=operations, version=self.version + 1)

    def set_cancelled(self, cancelled: bool) -> ProductionTask:
        target = ProductionStatus.CANCELLED if cancelled else ProductionStatus.RUNNING
        if self.status is target:
            raise ProductionValidationError(
                f"Задание {self.number} уже в статусе «{target.value}»"
            )
        return replace(self, status=target, version=self.version + 1)


def operation_duration_seconds(
    operation: ProductionOperation,
    quantity: int,
    operators: int | Decimal = 1,
    *,
    operator_rate: Decimal | None = None,
) -> int:
    # prodOpDurationSec_: rate is the sum of the chosen operators' coefficients; only a non-positive rate falls back to 1.
    rate = Decimal(operator_rate if operator_rate is not None else operators)
    rate = rate if rate > 0 else Decimal(1)
    return ceil(operation.setup_seconds + operation.seconds_per_unit * quantity / rate)


@dataclass(frozen=True)
class SkillCheck:
    checked: bool
    allowed: bool
    stars: int
    coefficient: Decimal


def skill_of(
    skills: dict[str, dict[UUID, OperatorSkill]],
    operation_code: str,
    operator_id: UUID,
) -> SkillCheck:
    """prodSkillOf_: no row for the code yet — not checked; a row exists — no allowed row means no admission."""
    by_code = skills.get(str(operation_code or ""))
    if not by_code:
        return SkillCheck(False, True, 0, Decimal(1))
    skill = by_code.get(operator_id)
    if skill is None or not skill.allowed:
        return SkillCheck(True, False, skill.stars if skill else 0, Decimal(1))
    return SkillCheck(True, True, skill.stars, skill.coefficient)


def _day_spans(
    moment: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> tuple[tuple[datetime, datetime], ...]:
    if moment.weekday() not in shift.weekdays or moment.date() in holidays:
        return ()
    minute_spans = [(shift.start, shift.finish)]
    for break_start, break_finish in sorted(shift.breaks):
        split: list[tuple[time, time]] = []
        for start, finish in minute_spans:
            if break_finish <= start or break_start >= finish:
                split.append((start, finish))
                continue
            if start < break_start:
                split.append((start, min(finish, break_start)))
            if break_finish < finish:
                split.append((max(start, break_finish), finish))
        minute_spans = split
    result: list[tuple[datetime, datetime]] = []
    for start, finish in minute_spans:
        span_start = datetime.combine(moment.date(), start, moment.tzinfo)
        span_finish = datetime.combine(moment.date(), finish, moment.tzinfo)
        if span_finish > span_start:
            result.append((span_start, span_finish))
    return tuple(result)


def work_rate(shift: WorkShift = DEFAULT_SHIFT) -> Decimal:
    """Доля чистой работы в смене без обеда: мелкие перерывы (туалет, перекуры) размазаны по всему дню,
    поэтому норма 7 ч при 8 ч смены без обеда занимает весь день 9:00–17:30, а не заканчивается в 16:30."""
    available = 0
    spans = [(shift.start, shift.finish)]
    for break_start, break_finish in sorted(shift.breaks):
        split: list[tuple[time, time]] = []
        for start, finish in spans:
            if break_finish <= start or break_start >= finish:
                split.append((start, finish))
                continue
            if start < break_start:
                split.append((start, min(finish, break_start)))
            if break_finish < finish:
                split.append((max(start, break_finish), finish))
        spans = split
    for start, finish in spans:
        available += (finish.hour * 3600 + finish.minute * 60) - (start.hour * 3600 + start.minute * 60)
    if available <= 0:
        return Decimal(1)
    return min(Decimal(1), Decimal(shift.effective_seconds) / Decimal(available))


def next_working_moment(
    moment: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    current = moment
    for _ in range(400):
        for start, finish in _day_spans(current, holidays, shift):
            if current < finish:
                return max(current, start)
        current = datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo)
    raise ProductionValidationError("Рабочий момент не найден в пределах календаря")


def add_work_seconds(
    moment: datetime,
    seconds: int,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    current = next_working_moment(moment, holidays, shift)
    rate = work_rate(shift)
    # Норматив — чистые секунды работы; на часах он растягивается на долю перерывов внутри смены.
    remaining = Decimal(max(0, seconds))
    if rate < 1:
        # Растянутое время округляем до минуты вверх, чтобы в плане не было секунд.
        remaining = Decimal(ceil(remaining / rate / 60) * 60)
    if not remaining:
        return current
    for _ in range(400):
        for start, finish in _day_spans(current, holidays, shift):
            if finish <= current:
                continue
            segment_start = max(current, start)
            available = int((finish - segment_start).total_seconds())
            if remaining <= available:
                return segment_start + timedelta(seconds=int(remaining))
            remaining -= available
        current = next_working_moment(
            datetime.combine(current.date() + timedelta(days=1), time.min, current.tzinfo),
            holidays,
            shift,
        )
    raise ProductionValidationError("Норматив не помещается в пределы календаря")


def plan_operation(
    *,
    operation: ProductionOperation,
    quantity: int,
    operator_ids: tuple[UUID, ...],
    requested_start: datetime | None,
    earliest_start: datetime,
    busy: dict[UUID, tuple[BusyInterval, ...]],
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
    operator_rate: Decimal | None = None,
    skills: dict[str, dict[UUID, OperatorSkill]] | None = None,
    names: dict[UUID, str] | None = None,
) -> PlanResult:
    if not operator_ids:
        return PlanResult(None, None, ())
    checks = [skill_of(skills or {}, operation.code, operator_id) for operator_id in operator_ids]
    if operator_rate is None and skills is not None:
        operator_rate = sum((item.coefficient for item in checks), Decimal(0))
    duration = operation_duration_seconds(
        operation, quantity, len(operator_ids), operator_rate=operator_rate,
    )
    start = next_working_moment(requested_start or earliest_start, holidays, shift)
    if requested_start is None:
        for _ in range(50):
            finish = add_work_seconds(start, duration, holidays, shift)
            collisions = [
                interval.finish
                for operator_id in operator_ids
                for interval in busy.get(operator_id, ())
                if interval.start < finish and start < interval.finish
            ]
            if not collisions:
                break
            start = next_working_moment(min(collisions), holidays, shift)
    finish = add_work_seconds(start, duration, holidays, shift)
    warnings: list[str] = []
    if start < earliest_start:
        warnings.append("Старт раньше окончания предыдущего этапа")
    for operator_id, check in zip(operator_ids, checks):
        name = (names or {}).get(operator_id, "Оператор")
        if not check.allowed:
            warnings.append(f"{name}: нет допуска к операции {operation.code}")
        for interval in busy.get(operator_id, ()):
            if interval.start < finish and start < interval.finish:
                warnings.append(
                    f"{name} занят(а): {interval.label}, "
                    f"{interval.start:%d.%m.%Y %H:%M}–{interval.finish:%d.%m.%Y %H:%M}"
                )
    return PlanResult(start, finish, tuple(warnings))
