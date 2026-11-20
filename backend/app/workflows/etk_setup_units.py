"""Read side of «ЭТК показывает станок наладки»: the unit of a READY setup task for a tooling row of an ETK.

An overlay over the issued ETK, no rebuild: the setup task's snapshot holds the issued-operation ref (the ETK position),
its БД.ОБ unit is resolved to the CURRENT inventory number at read time.

- `GET /api/production/my/tasks/{task_id}/operations/{operation_id}/etk/units` - latest READY setup per ETK position of
  that operation, same access as the ETK route of the operation.
- `GET /api/techcards/projects/{card_id}/setup-units` - READY setups of the card whose production task is still
  running, every distinct unit per position; access of the techcards screen (`techcards` view).
"""

from collections.abc import Callable
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.modules.equipment_setup.domain import SetupStatus
from app.modules.equipment_setup.infrastructure import SetupTaskRow
from app.modules.production.api import _etk_task_access, production_uow_factory
from app.modules.production.domain import ProductionStatus
from app.modules.production.infrastructure import ProductionTaskRow
from app.shared.http_auth import Principal, production_view_principal, techcards_view_principal

from .production_resource_context import machine_units

router = APIRouter(tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]


def _ready(session, *, operation_id: UUID | None = None, card_id: UUID | None = None, task_id: UUID | None = None):
    """READY setup rows with their issued-operation ref; the ref lives only in the JSON snapshot."""
    rows = session.scalars(select(SetupTaskRow).where(SetupTaskRow.status == SetupStatus.READY.value))
    for row in rows:
        ref = (row.snapshot or {}).get("operation") or {}
        try:
            if operation_id is not None and UUID(ref["production_operation_id"]) != operation_id:
                continue
            if task_id is not None and UUID(ref["production_task_id"]) != task_id:
                continue
            if card_id is not None and UUID(ref["card_id"]) != card_id:
                continue
            yield row, int(ref["etk_position"]), UUID(ref["production_task_id"])
        except (KeyError, ValueError, TypeError):
            continue  # a snapshot without a usable ref can't point at an ETK row


def _iso(value) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _view(session, found: list) -> list[dict]:
    """Items `(setup row, position, production task id)` to contract rows; a unit gone from the catalog is skipped."""
    if not found:
        return []
    units = {unit.id: unit for unit in machine_units(session)}
    numbers = dict(session.execute(select(ProductionTaskRow.id, ProductionTaskRow.number).where(
        ProductionTaskRow.id.in_({task for _, _, task in found}))).all())
    result = []
    for row, position, task in found:
        unit = units.get(row.machine_id)
        if unit is None:
            continue
        result.append({"etk_position": position, "unit_id": str(unit.id), "inventory_number": unit.inventory_number,
                       "model": unit.model or None, "name": unit.name or None, "ready_at": _iso(row.updated_at),
                       "task_number": numbers.get(task)})
    return result


@router.get("/api/production/my/tasks/{task_id}/operations/{operation_id}/etk/units")
def operation_etk_units(task_id: UUID, operation_id: UUID,
                        principal: Annotated[Principal, Depends(production_view_principal)], factory: Factory) -> dict:
    _etk_task_access(task_id, operation_id, principal, factory)
    with factory() as uow:
        latest: dict[int, tuple] = {}
        for item in _ready(uow.session, operation_id=operation_id, task_id=task_id):
            position = item[1]
            if position not in latest or item[0].updated_at > latest[position][0].updated_at:
                latest[position] = item
        rows = _view(uow.session, [latest[position] for position in sorted(latest)])
    return {"units": rows}


@router.get("/api/techcards/projects/{card_id}/setup-units", dependencies=[Depends(techcards_view_principal)])
def project_setup_units(card_id: UUID, factory: Factory) -> dict:
    with factory() as uow:
        session = uow.session
        found = list(_ready(session, card_id=card_id))
        open_tasks = set(session.scalars(select(ProductionTaskRow.id).where(
            ProductionTaskRow.id.in_({task for _, _, task in found}),
            ProductionTaskRow.status == ProductionStatus.RUNNING.value))) if found else set()
        latest: dict[tuple[int, UUID], tuple] = {}
        for item in found:
            key = (item[1], item[0].machine_id)
            if item[2] in open_tasks and (key not in latest or item[0].updated_at > latest[key][0].updated_at):
                latest[key] = item
        rows = _view(session, list(latest.values()))
    rows.sort(key=lambda item: (item["etk_position"], item["inventory_number"], item["unit_id"]))
    return {"units": rows}
