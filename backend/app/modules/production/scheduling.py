"""Pure automatic task scheduling ported from SUPPLYMAP ProdSchedule.gs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil
from uuid import UUID

from .domain import (
    BusyInterval,
    DEFAULT_SHIFT,
    OperatorSkill,
    ProductionOperation,
    ProductionTask,
    ProductionValidationError,
    WorkShift,
    _day_spans,
    work_rate,
    add_work_seconds,
    plan_operation,
)


@dataclass(frozen=True)
class ScheduledOperation:
    operation_id: UUID
    position: int
    operator_ids: tuple[UUID, ...]
    start: datetime
    finish: datetime
    automatic: bool


@dataclass(frozen=True)
class ScheduleVariant:
    transfer_batch: int
    operations: tuple[ScheduledOperation, ...]
    first_ready: datetime
    start: datetime
    finish: datetime
    idle_seconds: int
    warnings: tuple[str, ...]


def work_seconds_between(
    start: datetime,
    finish: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> int:
    if finish <= start:
        return 0
    total = 0
    current = start
    for _ in range(800):
        if current >= finish:
            break
        for span_start, span_finish in _day_spans(current, holidays, shift):
            lower = max(span_start, current)
            upper = min(span_finish, finish)
            if upper > lower:
                total += int((upper - lower).total_seconds())
        current = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), current.tzinfo)
    return int(total * work_rate(shift))


def batch_sizes(quantity: int) -> tuple[int, ...]:
    if quantity <= 0:
        raise ProductionValidationError("Количество должно быть больше нуля")
    minimum = max(1, ceil(quantity / 200))
    values = [
        quantity,
        ceil(quantity * Decimal("0.25")),
        ceil(quantity * Decimal("0.10")),
        ceil(quantity * Decimal("0.05")),
        ceil(quantity * Decimal("0.01")),
        minimum,
    ]
    return tuple(sorted({max(minimum, value) for value in values}, reverse=True))


def _skill(
    skills: dict[str, dict[UUID, OperatorSkill]],
    operation_code: str,
    operator_id: UUID,
) -> OperatorSkill | None:
    return skills.get(operation_code, {}).get(operator_id)


def _allowed(
    skills: dict[str, dict[UUID, OperatorSkill]],
    operation_code: str,
    operator_id: UUID,
) -> bool:
    # prodSkillOf_: while no row exists for the operation code the matrix is not filled yet and
    # admission is not checked; once one row exists, an operator without an allowed row is not admitted.
    by_code = skills.get(operation_code)
    if not by_code:
        return True
    skill = by_code.get(operator_id)
    return skill is not None and skill.allowed


def _rate(
    skills: dict[str, dict[UUID, OperatorSkill]],
    operation_code: str,
    operator_ids: tuple[UUID, ...],
) -> Decimal:
    return sum(
        (_skill(skills, operation_code, operator_id).coefficient
         if _skill(skills, operation_code, operator_id) is not None else Decimal(1))
        for operator_id in operator_ids
    )


def chain_start(
    previous: ProductionOperation,
    previous_plan: ScheduledOperation,
    previous_rate: Decimal,
    current: ProductionOperation,
    current_rate: Decimal,
    quantity: int,
    transfer_batch: int,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> datetime:
    batch = max(1, min(transfer_batch, quantity))
    batches = ceil(quantity / batch)
    last = quantity - (batches - 1) * batch
    previous_unit = previous.seconds_per_unit / max(Decimal(1), previous_rate)
    current_unit = current.seconds_per_unit / max(Decimal(1), current_rate)
    first_offset = previous.setup_seconds + batch * previous_unit - current.setup_seconds
    tail_offset = Decimal(work_seconds_between(
        previous_plan.start, previous_plan.finish, holidays, shift,
    )) - current.setup_seconds - (quantity - last) * current_unit
    offset = ceil(max(Decimal(0), first_offset, tail_offset))
    return add_work_seconds(previous_plan.start, offset, holidays, shift) if offset else previous_plan.start


def _merged(intervals: tuple[BusyInterval, ...]) -> tuple[tuple[datetime, datetime], ...]:
    ordered = sorted(
        ((item.start, item.finish) for item in intervals if item.finish > item.start),
        key=lambda item: item[0],
    )
    result: list[list[datetime]] = []
    for start, finish in ordered:
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], finish)
        else:
            result.append([start, finish])
    return tuple((item[0], item[1]) for item in result)


def idle_seconds(
    operator_ids: tuple[UUID, ...],
    busy: dict[UUID, tuple[BusyInterval, ...]],
    start: datetime,
    finish: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
) -> int:
    window = work_seconds_between(start, finish, holidays, shift)
    total = 0
    for operator_id in operator_ids:
        used = sum(
            work_seconds_between(max(span_start, start), min(span_finish, finish), holidays, shift)
            for span_start, span_finish in _merged(busy.get(operator_id, ()))
        )
        total += max(0, window - used)
    return total


def schedule_task(
    *,
    task: ProductionTask,
    transfer_batch: int,
    team: tuple[UUID, ...],
    skills: dict[str, dict[UUID, OperatorSkill]],
    busy: dict[UUID, tuple[BusyInterval, ...]],
    now: datetime,
    holidays: frozenset[date],
    shift: WorkShift = DEFAULT_SHIFT,
    max_crew: int = 1,
    replan: bool = False,
) -> ScheduleVariant:
    if task.status.value == "Отменено":
        raise ProductionValidationError(f"Задание {task.number} отменено — планировать нельзя")
    if not task.operations:
        raise ProductionValidationError("В задании нет операций")
    occupied = {operator_id: list(intervals) for operator_id, intervals in busy.items()}
    fixed_ids = {
        operation.id for operation in task.operations
        if not replan and operation.operator_ids and operation.planned_start and operation.planned_finish
    }
    for operation in task.operations:
        if operation.id not in fixed_ids:
            continue
        for operator_id in operation.operator_ids:
            occupied.setdefault(operator_id, []).append(BusyInterval(
                f"{task.number} · этап {operation.position}",
                operation.planned_start, operation.planned_finish,
            ))

    result: list[ScheduledOperation] = []
    warnings: list[str] = []
    previous_operation: ProductionOperation | None = None
    previous_plan: ScheduledOperation | None = None
    previous_rate = Decimal(1)
    crew_limit = max(1, min(5, max_crew))

    for operation in task.operations:
        if operation.id in fixed_ids:
            placed = ScheduledOperation(
                operation.id, operation.position, operation.operator_ids,
                operation.planned_start, operation.planned_finish, False,
            )
            current_rate = _rate(skills, operation.code, placed.operator_ids)
            if previous_operation and previous_plan:
                needed = chain_start(
                    previous_operation, previous_plan, previous_rate,
                    operation, current_rate, task.quantity, transfer_batch,
                    holidays, shift,
                )
                if placed.start < needed:
                    warnings.append(
                        f"Этап {operation.position} назначен раньше готовности деталей с этапа "
                        f"{previous_operation.position}"
                    )
        else:
            candidates = tuple(
                operator_id for operator_id in team
                if _allowed(skills, operation.code, operator_id)
            )
            if not candidates:
                raise ProductionValidationError(
                    f"Этап {operation.position} ({operation.code or operation.stage}): "
                    "нет допущенных операторов — заведите компетенции"
                )

            def fit(crew: tuple[UUID, ...]) -> ScheduledOperation:
                rate = _rate(skills, operation.code, crew)
                earliest = now
                if previous_operation and previous_plan:
                    earliest = max(earliest, chain_start(
                        previous_operation, previous_plan, previous_rate,
                        operation, rate, task.quantity, transfer_batch,
                        holidays, shift,
                    ))
                plan = plan_operation(
                    operation=operation, quantity=task.quantity, operator_ids=crew,
                    requested_start=None, earliest_start=earliest,
                    busy={key: tuple(value) for key, value in occupied.items()},
                    holidays=holidays, shift=shift, operator_rate=rate,
                )
                assert plan.start is not None and plan.finish is not None
                return ScheduledOperation(
                    operation.id, operation.position, crew, plan.start, plan.finish, True,
                )

            placed = min((fit((operator_id,)) for operator_id in candidates), key=lambda item: (item.finish, item.start))
            while len(placed.operator_ids) < crew_limit:
                grown = [
                    fit((*placed.operator_ids, operator_id)) for operator_id in candidates
                    if operator_id not in placed.operator_ids
                ]
                improving = [item for item in grown if item.finish < placed.finish]
                if not improving:
                    break
                placed = min(improving, key=lambda item: (item.finish, item.start))
            current_rate = _rate(skills, operation.code, placed.operator_ids)
            for operator_id in placed.operator_ids:
                occupied.setdefault(operator_id, []).append(BusyInterval(
                    f"{task.number} · этап {operation.position}", placed.start, placed.finish,
                ))
        result.append(placed)
        previous_operation, previous_plan, previous_rate = operation, placed, current_rate

    last_operation = task.operations[-1]
    last_plan = result[-1]
    first_duration = ceil(
        last_operation.setup_seconds
        + min(transfer_batch, task.quantity) * last_operation.seconds_per_unit / max(Decimal(1), previous_rate)
    )
    first_ready = min(
        last_plan.finish,
        add_work_seconds(last_plan.start, first_duration, holidays, shift),
    )
    start = min(item.start for item in result)
    finish = max(item.finish for item in result)
    if task.deadline and finish.date() > task.deadline:
        warnings.append("Готово позже дедлайна")
    operators = tuple(dict.fromkeys(
        operator_id for item in result for operator_id in item.operator_ids
    ))
    return ScheduleVariant(
        transfer_batch=max(1, min(transfer_batch, task.quantity)),
        operations=tuple(result), first_ready=first_ready, start=start, finish=finish,
        idle_seconds=idle_seconds(
            operators, {key: tuple(value) for key, value in occupied.items()},
            start, finish, holidays, shift,
        ),
        warnings=tuple(warnings),
    )


def schedule_variants(**kwargs) -> tuple[ScheduleVariant, ...]:
    task: ProductionTask = kwargs["task"]
    variants = [
        schedule_task(transfer_batch=batch, **kwargs)
        for batch in batch_sizes(task.quantity)
    ]
    kept = [
        variant for index, variant in enumerate(variants)
        if index == 0 or not any(
            other.first_ready <= variant.first_ready
            and other.finish <= variant.finish
            and (
                other.first_ready < variant.first_ready
                or other.finish < variant.finish
                or other.transfer_batch > variant.transfer_batch
            )
            for other_index, other in enumerate(variants) if other_index != index
        )
    ]
    return tuple(kept[:5])
