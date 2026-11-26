"""HTTP of the generic machine operation queue (T4). Rules: machine_queue.py; the ETK reading is the cutting queue's.

`GET /api/production/machine-queue?equipment_id=<uuid>` -> `{equipment_id, inventory, version, rows}`; a record that is
no physical machine -> 422, no БД.ОБ reference -> 503."""

from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.shared.http_auth import Principal, production_view_principal
from . import machine_queue
from .api import production_uow_factory, techcards_uow_factory
from .application import UnitOfWork
from .autoplan_api import references_uow_factory
from .domain import ProductionValidationError

router = APIRouter(prefix="/api/production/machine-queue", tags=["production"])


def _resolve(references_factory, equipment_id):
    from . import cutting_api  # the queue shares its ETK and БД.ОБ readers; imported late (cutting_api includes this router)

    catalog = cutting_api._catalog(references_factory)
    try:
        return catalog, cutting_api._unit(references_factory, catalog, equipment_id)
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error


def _rows(candidates, techcards_factory, unit, catalog):
    from . import cutting_api

    keys = {(item["task"].project, item["task"].item, item["task"].techcard_revision) for item in candidates}
    return machine_queue.queue_rows(candidates, cutting_api._documents(techcards_factory, keys), unit, catalog)


def equipment_rows(uow, equipment_id, principal, techcards_factory, references_factory):
    """The queue rows read on the caller's open production UoW. A caller inside `with factory()` must not open a second
    one: its session waits forever for the resource lock the first one holds (prod 23.11)."""
    catalog, unit = _resolve(references_factory, equipment_id)
    return _rows(machine_queue.collect(uow, principal.actor, unit), techcards_factory, unit, catalog)


@router.get("")
def queue(equipment_id: UUID, principal: Annotated[Principal, Depends(production_view_principal)],
          factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
          techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
          references_factory: Annotated[Callable | None, Depends(references_uow_factory)]):
    catalog, unit = _resolve(references_factory, equipment_id)
    with factory() as uow:
        candidates = machine_queue.collect(uow, principal.actor, unit)
    rows = _rows(candidates, techcards_factory, unit, catalog)
    return {"equipment_id": str(equipment_id), "inventory": unit.number,
            "version": machine_queue.queue_version(rows), "rows": rows}
