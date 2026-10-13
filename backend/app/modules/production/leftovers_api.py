"""HTTP: остатки полуфабрикатов (leftovers.py) — только чтение, ничего не двигает."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends

from app.shared.http_auth import Principal, production_overview_principal

from .api import production_uow_factory
from .application import UnitOfWork
from .leftovers import Leftover, leftovers

router = APIRouter(prefix="/api/production/leftovers", tags=["production"])

_BOX_LIMIT = 100_000


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _result(item: Leftover) -> dict:
    return {
        "task_id": str(item.task_id), "task_number": item.task_number, "task_status": item.task_status.value,
        "project": item.project, "item": item.item,
        "operation_id": str(item.operation_id), "position": item.position, "code": item.code, "stage": item.stage,
        "produced": _number(item.produced), "consumed": _number(item.consumed),
        "in_boxes": _number(item.in_boxes), "needed": _number(item.needed), "surplus": _number(item.surplus),
        "boxes": [{
            "id": str(box.id), "number": box.number, "status": box.status.value, "available": _number(box.available),
        } for box in item.boxes],
    }


@router.get("")
def list_leftovers(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        every = uow.boxes.list(limit=_BOX_LIMIT)
        task_ids = tuple(dict.fromkeys(box.task_id for box in every if box.available > 0))
        boxes = [box for box in every if box.task_id in set(task_ids)]
        tasks = [task for task in (uow.production.get(task_id) for task_id in task_ids) if task is not None]
        outputs = list(uow.outputs.active_for_tasks(task_ids))
    return {"leftovers": [_result(item) for item in leftovers(tasks, boxes, outputs)]}
