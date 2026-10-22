"""План от даты отгрузки и варианты первой поставки для проекта в автоплане (только просмотр, ничего не пишет).

Входная картина та же, что у предпросмотра автоплана «По проекту» (autoplan_application._prepare): задания проекта и
его КБ без задания, выбранные люди, их занятость и отсутствия, допуски, календарь, ключи наладки и станки БД.ОБ.
Правила — backward_plan.py и first_delivery.py."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Sequence
from uuid import UUID

from .application import UnitOfWork, _busy, _load_clock, _progress, _shift, _skills, _with_absences
from .autoplan import AutoplanInput, SetupKey, operation_keys
from .autoplan_application import (
    AutoplanCommand,
    AutoplanMode,
    CardSnapshot,
    _auto_movable,
    _horizon,
    _scope,
    card_key,
    machine_names,
)
from .backward_plan import BackwardResult, backward_plan
from .domain import ProductionTask, ProductionValidationError
from .first_delivery import DeliveryVariant, first_delivery_variants
from .launchable import ReadyCard, stage_name
from .machine_load import MachineStep, task_steps


@dataclass(frozen=True)
class ShipPlan:
    ship_date: date
    backward: BackwardResult
    variants: tuple[DeliveryVariant, ...]
    tasks: dict[UUID, ProductionTask]
    new_tasks: frozenset[UUID]
    names: dict[UUID, str]
    warnings: tuple[str, ...]
    # Конец работы проекта, уже назначенной вручную или начатой (не сделанной); None — такой нет.
    assigned_finish: datetime | None = None

    @property
    def fits(self) -> bool:
        return self.backward.fits and (self.assigned_finish is None or self.assigned_finish <= self.backward.due)


def _input(
    command: AutoplanCommand,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
    ready: Sequence[ReadyCard],
):
    """Вход расчёта — как в _prepare автоплана: свободное — не назначенное и прежний автоплан, ещё не начатый."""
    scope = _scope(command, uow, cards, ready)
    start, end = _horizon(command)
    now = command.now
    progress = _progress(uow, [task.id for task in scope.eligible])
    free: set[UUID] = set()
    movable: set[UUID] = set()
    done_ops: set[UUID] = set()
    for task in scope.eligible:
        for operation in task.operations:
            done = progress.by_operation.get(operation.id, Decimal(0))
            if done >= task.operation_units(operation.id):
                done_ops.add(operation.id)
                continue
            if not operation.shares:
                if done <= 0:
                    free.add(operation.id)
            elif _auto_movable(operation, done, start, end):
                free.add(operation.id)
                movable.add(operation.id)
    for item in scope.new_tasks.values():
        free.update(operation.id for operation in item.task.operations)
    tasks = tuple(
        replace(task, operations=tuple(
            operation.with_shares((), operation.assigned_by) if operation.id in movable else operation
            for operation in task.operations
        )) if any(operation.id in movable for operation in task.operations) else task
        for task in sorted(scope.tasks.values(), key=lambda value: (value.created_at, str(value.id)))
    ) + tuple(item.task for item in sorted(scope.new_tasks.values(), key=lambda value: (value.card.project, value.card.index)))
    team = tuple(item.id for item in scope.team_rows)
    busy = _with_absences(
        _busy(tasks, UUID(int=0), UUID(int=0), now, _load_clock(uow, now, tasks)), tuple(uow.calendar.absences()), team, now.tzinfo,
    )
    keys: dict[UUID, SetupKey] = {}
    steps: dict[UUID, tuple[MachineStep, ...]] = {}
    warnings = list(scope.warnings)
    for task in tasks:
        if not any(operation.id in free or operation.planned_start for operation in task.operations):
            continue
        card = cards.get(card_key(task.project, task.item))
        task_keys, _ = operation_keys(task, card.document if card is not None else None)
        keys.update(task_keys)
        steps.update(task_steps(task, card.document if card is not None else None)[0])
    data = AutoplanInput(
        tasks=tasks, free=frozenset(free), keys=keys, team=team, skills=_skills(uow), busy=busy,
        capacity={machine: max(1, int(units.get(machine, 0)) or 1) for machine in machine_names(keys, steps)},
        start=start, end=end, holidays=uow.calendar.holidays(), shift=_shift(uow.calendar), steps=steps,
    )
    return data, scope, warnings, frozenset(done_ops)


def ship_plan(
    command: AutoplanCommand,
    ship_date: date | None,
    uow: UnitOfWork,
    cards: Mapping[tuple[str, str], CardSnapshot],
    units: Mapping[str, int],
    ready: Sequence[ReadyCard] = (),
) -> ShipPlan:
    if command.mode is not AutoplanMode.PROJECT:
        raise ProductionValidationError("План от даты отгрузки считается по проекту")
    with uow:
        data, scope, warnings, done = _input(command, uow, cards, units, ready)
        assigned = [
            operation.planned_finish for task in scope.eligible for operation in task.operations
            if operation.id not in data.free and operation.id not in done and operation.planned_finish is not None
            and operation.planned_finish > data.start
        ]
        project_tasks = [task for task in data.tasks if any(operation.id in data.free for operation in task.operations)]
        if ship_date is None:
            ship_date = min((task.deadline for task in project_tasks if task.deadline is not None), default=None)
        if ship_date is None:
            raise ProductionValidationError("Укажите дату отгрузки")
        if ship_date < command.now.date():
            raise ProductionValidationError("Дата отгрузки уже прошла")
        return ShipPlan(
            ship_date=ship_date,
            backward=backward_plan(data, ship_date),
            variants=first_delivery_variants(data),
            tasks={task.id: task for task in data.tasks},
            new_tasks=frozenset(scope.new_tasks),
            names={item.id: item.name for item in scope.operators},
            warnings=tuple(warnings),
            assigned_finish=max(assigned, default=None),
        )


def describe_ship_plan(result: ShipPlan) -> dict:
    """Ответ для панели «От даты отгрузки» в автоплане."""
    tasks = result.tasks
    operations = {operation.id: operation for task in tasks.values() for operation in task.operations}
    backward = result.backward

    def task_row(task_id: UUID) -> dict:
        task = tasks[task_id]
        return {
            "task_id": str(task.id), "number": task.number, "new": task.id in result.new_tasks,
            "project": task.project, "item": task.item, "name": task.name, "quantity": task.quantity,
        }

    late = [slot for slot in backward.slots if slot.late(backward.start)]
    return {
        "ship_date": result.ship_date.isoformat(),
        "start": backward.start.isoformat(),
        "due": backward.due.isoformat(),
        "fits": result.fits,
        "assigned_finish": result.assigned_finish.isoformat() if result.assigned_finish else None,
        "start_by": backward.start_by.isoformat() if backward.start_by else None,
        "slack_seconds": backward.slack_seconds,
        "tasks": [{
            **task_row(item.task_id), "latest_start": item.start.isoformat(), "latest_finish": item.finish.isoformat(),
            "slack_seconds": item.slack_seconds,
        } for item in backward.tasks],
        "late": [{
            **task_row(slot.task_id), "position": operations[slot.operation_id].position,
            "stage": stage_name(operations[slot.operation_id].stage), "code": operations[slot.operation_id].code,
            "operator_name": result.names.get(slot.operator_id, ""),
            "latest_start": slot.start.isoformat(), "latest_finish": slot.finish.isoformat(),
        } for slot in late],
        "unplaced": [{
            **task_row(item.task_id), "position": operations[item.operation_id].position,
            "stage": stage_name(operations[item.operation_id].stage), "reason": item.reason,
        } for item in backward.unplaced],
        "warnings": list(dict.fromkeys((*result.warnings, *backward.warnings))),
        "first_delivery": [{
            "kind": variant.kind, "ready": variant.ready.isoformat(),
            "share": float(round(variant.share, 4)),
            "lots": [{**task_row(lot.task_id), "lot": lot.quantity, "total": lot.total} for lot in variant.lots],
        } for variant in result.variants],
    }
