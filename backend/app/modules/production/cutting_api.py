"""Grouped cutting controls. Row work/ETK URLs remain the ordinary authorized production endpoints."""

from datetime import datetime
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal
from .api import MachineBindingPayload, production_uow_factory, techcards_uow_factory, cutting_current_cards
from .application import ConcurrentProductionUpdate, UnitOfWork
from .cutting_application import assign_group, check_access, describe_group, split_group, revalidate_group
from .domain import ProductionValidationError
from .resource_assignments import MachineBinding
from .resource_claims import ResourceConflict

router = APIRouter(prefix="/api/production/cutting-groups", tags=["production"])


def _handle(action):
    try:
        return action()
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error
    except (ConcurrentProductionUpdate, ResourceConflict, IntegrityError, StaleDataError) as error:
        raise HTTPException(409, "Группа или ресурсы изменились — обновите данные") from error


@router.get("")
def groups(principal: Annotated[Principal, Depends(production_overview_principal)],
           factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]):
    with factory() as uow:
        result = []
        for group in uow.cutting.list():
            try:
                check_access(group, uow, principal.actor)
            except PermissionError:
                continue
            result.append(describe_group(group, uow))
        return {"groups": result}


class ExistingGroupsPayload(BaseModel):
    project: str = Field(min_length=1, max_length=255)
    task_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=1000)
    dry_run: bool = True


@router.post("/from-existing")
def from_existing(payload: ExistingGroupsPayload,
                  principal: Annotated[Principal, Depends(production_edit_principal)],
                  now: Annotated[datetime, Depends(moscow_now)],
                  factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
                  techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    from app.workflows.cutting_group_existing import run

    if techcards_factory is None:
        raise HTTPException(503, "База техкарт не настроена")
    return _handle(lambda: run(payload.project, task_ids=payload.task_ids, dry_run=payload.dry_run,
        factory=factory, techcards_factory=techcards_factory, actor=principal.actor, now=now))


@router.get("/{group_id}")
def group_detail(group_id: UUID, principal: Annotated[Principal, Depends(production_overview_principal)],
                 factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]):
    with factory() as uow:
        group = uow.cutting.get(group_id)
        if group is None:
            raise HTTPException(404, "Группа не найдена")
        _handle(lambda: check_access(group, uow, principal.actor))
        return {"group": describe_group(group, uow)}


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class GroupAssignPayload(VersionPayload):
    operator_ids: list[UUID] = Field(min_length=1, max_length=100)
    requested_start: datetime | None = None
    machine_bindings: dict[UUID, list[MachineBindingPayload]] = Field(default_factory=dict, max_length=100)


class SplitPayload(VersionPayload):
    member_ids: list[UUID] = Field(min_length=1, max_length=100)


@router.post("/{group_id}/assign")
def assign(group_id: UUID, payload: GroupAssignPayload,
           principal: Annotated[Principal, Depends(production_edit_principal)],
           now: Annotated[datetime, Depends(moscow_now)],
           factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]):
    bindings = {identifier: tuple(MachineBinding(item.etk_position, item.machine_id) for item in items)
                for identifier, items in payload.machine_bindings.items()}
    return {"group": _handle(lambda: assign_group(group_id, payload.version, payload.operator_ids,
            payload.requested_start, bindings, principal.actor, now, factory()))}


@router.post("/{group_id}/split")
def split(group_id: UUID, payload: SplitPayload,
          principal: Annotated[Principal, Depends(production_edit_principal)],
          now: Annotated[datetime, Depends(moscow_now)],
          factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]):
    return {"group": _handle(lambda: split_group(group_id, payload.version, payload.member_ids,
                                                principal.actor, now, factory()))}


@router.post("/{group_id}/cancel")
def cancel(group_id: UUID, payload: VersionPayload,
           principal: Annotated[Principal, Depends(production_edit_principal)],
           now: Annotated[datetime, Depends(moscow_now)],
           factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)]):
    return {"group": _handle(lambda: split_group(group_id, payload.version, (),
                                                principal.actor, now, factory(), cancel=True))}


@router.post("/{group_id}/revalidate")
def revalidate(group_id: UUID, payload: VersionPayload,
               principal: Annotated[Principal, Depends(production_edit_principal)],
               now: Annotated[datetime, Depends(moscow_now)],
               factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
               techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    current = cutting_current_cards(group_id, factory, techcards_factory)
    return {"group": _handle(lambda: revalidate_group(group_id, payload.version, current,
                                                     principal.actor, now, factory()))}
