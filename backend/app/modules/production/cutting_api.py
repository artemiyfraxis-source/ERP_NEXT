"""Grouped cutting controls. Row work/ETK URLs remain the ordinary authorized production endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.clock import moscow_now
from app.shared.http_auth import (Principal, production_edit_principal, production_overview_principal,
                                  production_view_principal, production_view_work_principal)
from .api import (MachineBindingPayload, production_uow_factory, techcards_uow_factory, cutting_current_cards,
                  cutting_refresh_cards, mark_stale_etk, _performer, _replay_key, MaterialInputPayload,
                  issued_card_for_item, pinned_card)
from . import cutting_queue
from .application import ConcurrentProductionUpdate, UnitOfWork
from .cutting_application import assign_group, check_access, describe_group, split_group, revalidate_group, refresh_group_etk
from .domain import ProductionValidationError
from .resource_assignments import MachineBinding
from .resource_claims import ResourceConflict
from .cutting_execution import execute_group
from .downtime import DowntimeReason, COMMENT_LIMIT

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
           factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
           techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    with factory() as uow:
        result = []
        for group in uow.cutting.list():
            try:
                check_access(group, uow, principal.actor)
            except PermissionError:
                continue
            result.append(describe_group(group, uow))
    return {"groups": mark_stale_etk(result, techcards_factory)}


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
                 factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
                 techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    with factory() as uow:
        group = uow.cutting.get(group_id)
        if group is None:
            raise HTTPException(404, "Группа не найдена")
        _handle(lambda: check_access(group, uow, principal.actor))
        view = describe_group(group, uow)
    return {"group": mark_stale_etk([view], techcards_factory)[0]}


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


@router.post("/{group_id}/refresh-etk")
def refresh_etk(group_id: UUID, payload: VersionPayload,
                principal: Annotated[Principal, Depends(production_edit_principal)],
                now: Annotated[datetime, Depends(moscow_now)],
                factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
                techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    cards = cutting_refresh_cards(group_id, factory, techcards_factory)
    return {"group": _handle(lambda: refresh_group_etk(group_id, payload.version, cards, principal.actor, now, factory()))}


class GroupOutputPayload(BaseModel):
    member_id: UUID
    good_quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    defect_quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=6)
    defect_character: str = Field(default="", max_length=255)
    defect_description: str = Field(default="", max_length=2000)
    materials: list[MaterialInputPayload] = Field(default_factory=list, max_length=100)


class GroupSubmitPayload(VersionPayload):
    rows: list[GroupOutputPayload] = Field(min_length=1, max_length=1000)


class GroupDowntimePayload(VersionPayload):
    reason: DowntimeReason
    comment: str = Field(default="", max_length=COMMENT_LIMIT)


@router.post("/{group_id}/start")
def start_group_work(group_id: UUID, payload: VersionPayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    on_behalf_of: UUID | None = None):
    worker = _performer(factory, principal, on_behalf_of) or principal.actor
    current = cutting_current_cards(group_id, factory, techcards_factory)
    return _handle(lambda: execute_group(group_id, payload.model_dump(mode="json"), "start",
        _replay_key(idempotency_key), principal.actor, worker, now, factory(), current_cards=current))


def _work_action(group_id, payload, action, principal, now, factory, key, on_behalf_of):
    worker = _performer(factory, principal, on_behalf_of) or principal.actor
    return _handle(lambda: execute_group(group_id, payload.model_dump(mode="json"), action,
        _replay_key(key), principal.actor, worker, now, factory()))


@router.post("/{group_id}/submit")
def submit_group_work(group_id: UUID, payload: GroupSubmitPayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    on_behalf_of: UUID | None = None):
    return _work_action(group_id, payload, "submit", principal, now, factory, idempotency_key, on_behalf_of)


@router.post("/{group_id}/downtime/start")
def start_group_downtime(group_id: UUID, payload: GroupDowntimePayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    on_behalf_of: UUID | None = None):
    return _work_action(group_id, payload, "downtime/start", principal, now, factory, idempotency_key, on_behalf_of)


@router.post("/{group_id}/downtime/stop")
def stop_group_downtime(group_id: UUID, payload: VersionPayload,
    principal: Annotated[Principal, Depends(production_view_work_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    on_behalf_of: UUID | None = None):
    return _work_action(group_id, payload, "downtime/stop", principal, now, factory, idempotency_key, on_behalf_of)


# «Очередь резки» по станку (owner 20.11): computed from open cut work of all projects; see CUTTING_API.md.
queue_router = APIRouter(prefix="/api/production/cutting-queue", tags=["production"])


def _queue(principal, factory, techcards_factory, machine_id=None):
    with factory() as uow:
        keys = cutting_queue.needed_documents(uow, principal.actor)
    documents = {}
    if techcards_factory is not None and keys:
        with techcards_factory() as techcards:
            for key in keys:
                project, item, revision = key
                card = issued_card_for_item(techcards, project, item)
                documents[key] = pinned_card(techcards, card, revision)[0].document if card is not None else None
    with factory() as uow:
        return cutting_queue.queue_rows(uow, documents, principal.actor, machine_id)


@queue_router.get("/machines")
def queue_machines(principal: Annotated[Principal, Depends(production_view_principal)],
                   factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
                   techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    return {"machines": cutting_queue.machines(_queue(principal, factory, techcards_factory))}


@queue_router.get("")
def queue_rows(machine_id: Annotated[str, Query(min_length=1, max_length=64)],
               principal: Annotated[Principal, Depends(production_view_principal)],
               factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
               techcards_factory: Annotated[Callable | None, Depends(techcards_uow_factory)]):
    """`machine_id=none` — cuts whose card names no machine («Станок не указан»)."""
    machine = machine_id.strip()
    rows = _queue(principal, factory, techcards_factory, machine)
    return {"machine_id": None if machine == cutting_queue.NO_MACHINE else machine,
            "version": cutting_queue.queue_version(rows), "rows": rows}


class QueueDonePayload(BaseModel):
    task_id: UUID
    operation_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


@queue_router.post("/done")
def queue_done(payload: QueueDonePayload,
               principal: Annotated[Principal, Depends(production_view_work_principal)],
               now: Annotated[datetime, Depends(moscow_now)],
               factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
               idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
               on_behalf_of: UUID | None = None):
    worker = _performer(factory, principal, on_behalf_of) or principal.actor
    key = _replay_key(idempotency_key)
    if key is None:
        raise HTTPException(422, "Нужен ключ повтора")
    try:
        return cutting_queue.mark_done(payload.task_id, payload.operation_id, payload.quantity, key,
                                       principal.actor, worker, now, factory())
    except (ConcurrentProductionUpdate, ResourceConflict, IntegrityError, StaleDataError) as error:
        raise HTTPException(409, str(error) if isinstance(error, ConcurrentProductionUpdate) else
                            "Задание или ресурсы изменились — обновите очередь") from error
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, error.args[0] if error.args else "Не найдено") from error
    except ProductionValidationError as error:
        raise HTTPException(422, str(error)) from error


# One router for app.main: the group controls and the queue.
groups_router = router
router = APIRouter()
router.include_router(groups_router)
router.include_router(queue_router)
