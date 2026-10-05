"""HTTP: автоплан площадки ЦАР на период — предпросмотр и применение выбранного мастером."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.references.domain import TechOperationTab
from app.modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal

from .api import production_uow_factory, techcards_uow_factory
from .application import ConcurrentProductionUpdate, UnitOfWork
from .autoplan import equipment_units
from .autoplan_application import (
    ApplyAutoplanCommand,
    AutoplanCommand,
    AutoplanForbidden,
    CardSnapshot,
    StaleAutoplan,
    apply_autoplan,
    card_key,
    describe,
    equipment_numbers,
    preview_autoplan,
)
from .domain import ProductionValidationError


router = APIRouter(prefix="/api/production/autoplan", tags=["production"])


class PreviewPayload(BaseModel):
    date_from: date
    date_to: date
    task_ids: list[UUID] | None = Field(default=None, max_length=1000)
    replan_auto: bool = False


class ApplyItem(BaseModel):
    task_id: UUID
    operation_id: UUID
    operator_id: UUID


class ApplyPayload(PreviewPayload):
    # task_ids и replan_auto — те же, что в предпросмотре: они входят в отпечаток.
    items: list[ApplyItem] = Field(min_length=1, max_length=5000)
    fingerprint: str = Field(min_length=16, max_length=128)


def references_uow_factory(request: Request) -> Callable[[], Any] | None:
    factory = getattr(request.app.state, "references_uow_factory", None)
    return factory if callable(factory) else None


def _cards(factory: Callable[[], TechcardsUnitOfWork] | None) -> dict[tuple[str, str], CardSnapshot]:
    """Текущая выданная техкарта каждой позиции (проект, индекс); у нескольких — последняя выданная."""
    if factory is None:
        return {}
    with factory() as techcards:
        rows = [card for card in techcards.techcards.list() if card.status is TechcardStatus.ISSUED]
    result: dict[tuple[str, str], CardSnapshot] = {}
    for card in sorted(rows, key=lambda item: (item.issued_at or item.updated_at, item.updated_at, str(item.id))):
        result[card_key(card.project, card.item_index)] = CardSnapshot(card.id, card.version, card.document)
    return result


def _units(factory: Callable[[], Any] | None) -> dict[str, int]:
    """Единицы станков по префиксу инвентарного номера из БД.ОБ."""
    if factory is None:
        return {}
    with factory() as references:
        records = references.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=100_000)
    return equipment_units(equipment_numbers([record.fields for record in records]))


def _handle(operation):
    try:
        return operation()
    except AutoplanForbidden as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (StaleAutoplan, ConcurrentProductionUpdate) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error


@router.post("/preview")
def preview(
    payload: PreviewPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    cards, units = _cards(techcards_factory), _units(references_factory)
    return _handle(lambda: describe(preview_autoplan(AutoplanCommand(
        date_from=payload.date_from, date_to=payload.date_to,
        task_ids=tuple(payload.task_ids) if payload.task_ids is not None else None,
        replan_auto=payload.replan_auto, actor=principal.actor, now=now,
    ), uow_factory(), cards, units)))


@router.post("/apply")
def apply(
    payload: ApplyPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    cards, units = _cards(techcards_factory), _units(references_factory)

    def execute() -> dict:
        applied = apply_autoplan(ApplyAutoplanCommand(
            date_from=payload.date_from, date_to=payload.date_to,
            task_ids=tuple(payload.task_ids) if payload.task_ids is not None else None,
            replan_auto=payload.replan_auto,
            items=tuple((item.task_id, item.operation_id, item.operator_id) for item in payload.items),
            fingerprint=payload.fingerprint, actor=principal.actor, now=now,
        ), uow_factory(), cards, units)
        return {
            "applied": applied.operations,
            "tasks": [{"id": str(task.id), "number": task.number, "version": task.version} for task in applied.tasks],
        }

    return _handle(execute)
