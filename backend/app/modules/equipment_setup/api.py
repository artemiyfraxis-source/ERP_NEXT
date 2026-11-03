"""Setup command HTTP contract. Deliberately not mounted before production resource integration."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.modules.production.resource_claims import ResourceConflict

from .application import AssignSetupCommand, ChangeSetupCommand, ConcurrentSetupUpdate, SetupAction, SetupPermissionError, assign_setup_task, change_setup_task
from .domain import IssuedOperationRef, RecipeVersionRef
from .infrastructure import task_payload
from .recipe_application import PublishRecipeCommand, RecordTrialCommand, publish_recipe, record_trial
from .recipes import RecipeField
from .queries import list_executors, list_machines, list_recipes, list_tasks, list_trials, read_recipe, read_task


router = APIRouter(prefix="/api/production/equipment-setup", tags=["production"])


def setup_uow_factory(request: Request):
    factory = getattr(request.app.state, "equipment_setup_uow_factory", None)
    if factory is None:
        raise HTTPException(503, "Общий учёт занятости производства и наладки ещё не подключён")
    return factory


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationPayload(Payload):
    card_id: UUID
    card_revision: int = Field(strict=True, ge=1)
    production_task_id: UUID
    production_operation_id: UUID
    etk_position: int = Field(strict=True, ge=1)


class RecipePayload(Payload):
    recipe_id: UUID
    version: int = Field(strict=True, ge=1)


class AssignPayload(Payload):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(strict=True, ge=0, le=0)
    operation: OperationPayload
    machine_id: str = Field(min_length=1, max_length=128)
    technician_id: UUID
    recipe: RecipePayload
    planned_start: datetime
    planned_finish: datetime


class ChangePayload(Payload):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(strict=True, ge=1)
    action: SetupAction
    reason: str = Field(default="", max_length=2000)
    machine_available: bool | None = Field(default=None, strict=True)


class RecipeFieldPayload(Payload):
    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=500)
    unit: str = Field(default="", max_length=40)


class PublishRecipePayload(Payload):
    expected_version: int = Field(strict=True, ge=0)
    machine_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    geometry: list[RecipeFieldPayload] = Field(default_factory=list, max_length=100)
    panel_settings: list[RecipeFieldPayload] = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=2000)


class TrialPayload(Payload):
    expected_version: int = Field(strict=True, ge=1)
    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=160)


def _perform(action, serialize=task_payload):
    try:
        return serialize(action())
    except SetupPermissionError as error:
        raise HTTPException(403, str(error)) from error
    except (ConcurrentSetupUpdate, ResourceConflict) as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except InvalidOperation as error:
        raise HTTPException(422, "Нужно конечное десятичное число") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/tasks")
def assign(payload: AssignPayload, principal: Annotated[Principal, Depends(authenticated_principal)],
           factory: Annotated[Callable, Depends(setup_uow_factory)], now: Annotated[datetime, Depends(moscow_now)]):
    def run():
        command = AssignSetupCommand(payload.idempotency_key, payload.expected_version,
            IssuedOperationRef(**payload.operation.model_dump()), payload.machine_id, payload.technician_id,
            RecipeVersionRef(**payload.recipe.model_dump()), payload.planned_start, payload.planned_finish)
        return assign_setup_task(command, factory(), principal=principal, now=now)
    return _perform(run)


@router.post("/tasks/{task_id}/transition")
def transition(task_id: UUID, payload: ChangePayload, principal: Annotated[Principal, Depends(authenticated_principal)],
               factory: Annotated[Callable, Depends(setup_uow_factory)], now: Annotated[datetime, Depends(moscow_now)]):
    def run():
        command = ChangeSetupCommand(payload.idempotency_key, payload.expected_version, task_id,
            payload.action, payload.reason, payload.machine_available)
        return change_setup_task(command, factory(), principal=principal, now=now)
    return _perform(run)


@router.post("/recipes/{recipe_id}/versions")
def publish(recipe_id: UUID, payload: PublishRecipePayload, principal: Annotated[Principal, Depends(authenticated_principal)],
            factory: Annotated[Callable, Depends(setup_uow_factory)], now: Annotated[datetime, Depends(moscow_now)]):
    def run():
        command = PublishRecipeCommand(recipe_id, payload.expected_version, payload.machine_id, payload.title,
            tuple(RecipeField(**item.model_dump()) for item in payload.geometry),
            tuple(RecipeField(**item.model_dump()) for item in payload.panel_settings), payload.source)
        return publish_recipe(command, factory(), principal=principal, now=now)
    return _perform(run)


def _trial_result(trial):
    result = task_payload(trial)
    result["value"] = str(trial.value)
    return result


@router.put("/tasks/{task_id}/trials/{measurement_id}")
def trial(task_id: UUID, measurement_id: UUID, payload: TrialPayload,
          principal: Annotated[Principal, Depends(authenticated_principal)],
          factory: Annotated[Callable, Depends(setup_uow_factory)], now: Annotated[datetime, Depends(moscow_now)]):
    def run():
        command = RecordTrialCommand(measurement_id, task_id, payload.expected_version, payload.label, Decimal(payload.value), payload.unit)
        return record_trial(command, factory(), principal=principal, now=now)
    return _perform(run, _trial_result)


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, principal: Annotated[Principal, Depends(authenticated_principal)],
             factory: Annotated[Callable, Depends(setup_uow_factory)]):
    return _perform(lambda: read_task(task_id, factory(), principal=principal))


@router.get("/tasks")
def get_tasks(principal: Annotated[Principal, Depends(authenticated_principal)],
              factory: Annotated[Callable, Depends(setup_uow_factory)],
              limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    return _perform(lambda: list_tasks(factory(), principal=principal, limit=limit, offset=offset),
        lambda items: {"items": [task_payload(item) for item in items], "limit": limit, "offset": offset})


def _page_result(limit, offset, serialize=task_payload):
    return lambda items: {"items": [serialize(item) for item in items], "limit": limit, "offset": offset}


@router.get("/machines")
def get_machines(principal: Annotated[Principal, Depends(authenticated_principal)],
                 factory: Annotated[Callable, Depends(setup_uow_factory)],
                 limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    return _perform(lambda: list_machines(factory(), principal=principal, limit=limit, offset=offset), _page_result(limit, offset))


@router.get("/assignment-context")
def get_assignment_context(card_id: UUID, card_revision: Annotated[int, Query(ge=1)],
                           production_task_id: UUID, production_operation_id: UUID, etk_position: Annotated[int, Query(ge=1)],
                           principal: Annotated[Principal, Depends(authenticated_principal)],
                           factory: Annotated[Callable, Depends(setup_uow_factory)],
                           limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    def run():
        operation = IssuedOperationRef(card_id, card_revision, production_task_id, production_operation_id, etk_position)
        return list_machines(factory(), principal=principal, limit=limit, offset=offset, operation=operation)
    return _perform(run, _page_result(limit, offset))


@router.get("/executors")
def get_executors(machine_id: Annotated[str, Query(min_length=1, max_length=128)],
                  principal: Annotated[Principal, Depends(authenticated_principal)],
                  factory: Annotated[Callable, Depends(setup_uow_factory)],
                  limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    return _perform(lambda: list_executors(machine_id, factory(), principal=principal, limit=limit, offset=offset), _page_result(limit, offset))


@router.get("/recipes")
def get_recipes(machine_id: Annotated[str, Query(min_length=1, max_length=128)],
                principal: Annotated[Principal, Depends(authenticated_principal)],
                factory: Annotated[Callable, Depends(setup_uow_factory)],
                limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    return _perform(lambda: list_recipes(machine_id, factory(), principal=principal, limit=limit, offset=offset), _page_result(limit, offset))


@router.get("/recipes/{recipe_id}/versions/{version}")
def get_recipe(recipe_id: UUID, version: int,
               principal: Annotated[Principal, Depends(authenticated_principal)],
               factory: Annotated[Callable, Depends(setup_uow_factory)]):
    return _perform(lambda: read_recipe(recipe_id, version, factory(), principal=principal))


@router.get("/tasks/{task_id}/trials")
def get_trials(task_id: UUID, principal: Annotated[Principal, Depends(authenticated_principal)],
               factory: Annotated[Callable, Depends(setup_uow_factory)],
               limit: Annotated[int, Query(ge=1, le=200)] = 100, offset: Annotated[int, Query(ge=0)] = 0):
    return _perform(lambda: list_trials(task_id, factory(), principal=principal, limit=limit, offset=offset),
        _page_result(limit, offset, _trial_result))
