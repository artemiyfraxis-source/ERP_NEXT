"""Остатки полуфабрикатов (идея — PRODUCTION_PLANNER Василия: остаток полуфабриката = выпуск − расход по
журналу движения; перенесено в устройство ERP).

Остаток выводится из учёта коробок, ничего не хранится и не двигается: годные штуки операции лежат в её
коробках (ProductionBox.available — сдано минус взято следующими этапами, резерв и отбраковка). Не финальная
операция задания отдаёт свой полуфабрикат операциям-потребителям по схеме этапов; потребителю ещё нужно
(его штуки по заданию − уже сделано годных и брака) × штук входа на одну. Всё, что лежит в коробках сверх
этой потребности, — остаток: никакая операция задания его уже не возьмёт. У выполненного или отменённого
задания потребности нет — всё, что лежит в коробках не финальной операции, остаток.

Коробки финальной операции — изделия (их путь — ОТК и склад ГП), в остатки они не входят.

Подсказка автоплану («из остатков») — на экране: строка предложения с той же КБ проекта, этапом и операцией,
что у остатка другого задания. Коробку другого задания операция взять не может (input_error), поэтому
подсказка — только видимость: мастер решает сам, складские остатки и количества заданий не меняются."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Sequence
from uuid import UUID

from .domain import ProductionStatus, ProductionTask
from .launchable import stage_name
from .traceability import BoxStatus, ProductionBox, ProductionOutput

# Штуки в этих коробках физически лежат у цеха: сданы монтажником, подтверждены мастером или возвращены на
# исправление. «Израсходована» — пустая.
_HELD = frozenset({BoxStatus.OPEN, BoxStatus.OPERATOR_CLOSED, BoxStatus.CONFIRMED, BoxStatus.RETURNED})


@dataclass(frozen=True)
class LeftoverBox:
    id: UUID
    number: str | None
    status: BoxStatus
    available: Decimal


@dataclass(frozen=True)
class Leftover:
    """Остаток полуфабриката одной операции задания."""
    task_id: UUID
    task_number: str
    task_status: ProductionStatus
    project: str
    item: str
    operation_id: UUID
    position: int
    code: str
    stage: str  # русское название этапа
    produced: Decimal  # годных сдано в коробки операции
    consumed: Decimal  # взято следующими этапами
    in_boxes: Decimal  # лежит в коробках сейчас
    needed: Decimal  # ещё возьмут потребители этого задания
    boxes: tuple[LeftoverBox, ...]

    @property
    def surplus(self) -> Decimal:
        return max(Decimal(0), self.in_boxes - self.needed)


def _processed(outputs: Iterable[ProductionOutput]) -> dict[UUID, Decimal]:
    """Сделано по операции: годные + брак действующих сдач (исправленные сдачи не считаются)."""
    result: dict[UUID, Decimal] = {}
    for output in outputs:
        result[output.operation_id] = result.get(output.operation_id, Decimal(0)) + output.good_quantity + output.defect_quantity
    return result


def needed(task: ProductionTask, operation_id: UUID, processed: Mapping[UUID, Decimal]) -> Decimal:
    """Сколько штук полуфабриката операции ещё возьмут её потребители в этом задании. Задание не в работе — 0."""
    if task.status is not ProductionStatus.RUNNING:
        return Decimal(0)
    total = Decimal(0)
    for consumer in task.operations:
        for edge in consumer.inputs:
            if edge.input_operation_id != operation_id:
                continue
            left = task.operation_units(consumer.id) - processed.get(consumer.id, Decimal(0))
            total += max(Decimal(0), left) * edge.per_unit
    return total


def task_leftovers(
    task: ProductionTask,
    boxes: Sequence[ProductionBox],
    outputs: Sequence[ProductionOutput],
) -> tuple[Leftover, ...]:
    """Остатки полуфабрикатов задания по его коробкам и сдачам: только операции, где остаток больше нуля."""
    final_id = task.final_operation.id
    processed = _processed(item for item in outputs if item.task_id == task.id)
    own = [box for box in boxes if box.task_id == task.id]
    result: list[Leftover] = []
    for operation in task.operations:
        if operation.id == final_id:
            continue
        mine = [box for box in own if box.operation_id == operation.id]
        held = [box for box in mine if box.status in _HELD and box.available > 0]
        in_boxes = sum((box.available for box in held), Decimal(0))
        if in_boxes <= 0:
            continue
        leftover = Leftover(
            task_id=task.id, task_number=task.number, task_status=task.status, project=task.project, item=task.item,
            operation_id=operation.id, position=operation.position, code=operation.code,
            stage=stage_name(operation.stage) or operation.stage,
            produced=sum((box.quantity for box in mine), Decimal(0)),
            consumed=sum((box.consumed for box in mine), Decimal(0)),
            in_boxes=in_boxes, needed=needed(task, operation.id, processed),
            boxes=tuple(
                LeftoverBox(box.id, box.number, box.status, box.available)
                for box in sorted(held, key=lambda value: (value.created_at, str(value.number or "")))
            ),
        )
        if leftover.surplus > 0:
            result.append(leftover)
    return tuple(result)


def leftovers(
    tasks: Iterable[ProductionTask],
    boxes: Sequence[ProductionBox],
    outputs: Sequence[ProductionOutput],
) -> tuple[Leftover, ...]:
    """Остатки полуфабрикатов всех заданий: по изделию, этапу, номеру задания."""
    rows = [item for task in tasks for item in task_leftovers(task, boxes, outputs)]
    return tuple(sorted(rows, key=lambda item: (item.item.casefold(), item.position, item.task_number)))
