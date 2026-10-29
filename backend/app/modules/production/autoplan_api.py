"""HTTP: автоплан площадки ЦАР — что войдёт в расчёт, предпросмотр и применение выбранного мастером
(режимы «Весь цех» и «По проекту», период — от сейчас до горизонта работы)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal
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

from .api import closed_kbs, production_uow_factory, techcards_uow_factory
from .application import ConcurrentProductionUpdate, UnitOfWork
from .autoplan import equipment_units
from .autoplan_application import (
    ApplyAutoplanCommand,
    AutoplanCommand,
    AutoplanForbidden,
    AutoplanMode,
    CardSnapshot,
    StaleAutoplan,
    apply_autoplan,
    autoplan_context,
    card_key,
    describe,
    equipment_numbers,
    preview_autoplan,
)
from .domain import ProductionValidationError
from .launchable import ReadyCard, ready_cards


router = APIRouter(prefix="/api/production/autoplan", tags=["production"])


class PreviewPayload(BaseModel):
    # «Весь цех» — вся работа на всех монтажников площадки; «По проекту» — работа проекта на выбранных людей.
    mode: Literal["shop", "project"] = "shop"
    # Площадка, чьих людей ставить: ЦАР / ЖУК; пусто — обе (мастер площадки — всегда своя).
    site: str = Field(default="", max_length=40)
    project: str = Field(default="", max_length=200)
    people: list[UUID] = Field(default_factory=list, max_length=200)
    # Снятые галочки («Весь цех» и КБ проекта в «По проекту»): id заданий и id техкарт КБ без задания.
    exclude: list[UUID] = Field(default_factory=list, max_length=2000)
    # «До даты» вручную; не задано — план до горизонта работы (самый поздний срок или сколько нужно, до 8 недель).
    date_to: date | None = None
    # Тиражи новых заданий по id техкарты КБ (окно проекта на доске); нет КБ — весь её тираж.
    runs: dict[UUID, Annotated[int, Field(ge=1, le=1_000_000)]] = Field(default_factory=dict, max_length=2000)


class ApplyItem(BaseModel):
    task_id: UUID
    operation_id: UUID
    operator_id: UUID


class ApplyPayload(PreviewPayload):
    # Режим, проект, люди, исключения и «до даты» — те же, что в предпросмотре: они входят в отпечаток.
    items: list[ApplyItem] = Field(min_length=1, max_length=5000)
    fingerprint: str = Field(min_length=16, max_length=128)


def _command(payload: PreviewPayload, principal: Principal, now: datetime) -> AutoplanCommand:
    return AutoplanCommand(
        actor=principal.actor, now=now, mode=AutoplanMode(payload.mode), project=payload.project.strip(),
        people=tuple(dict.fromkeys(payload.people)), exclude=frozenset(payload.exclude), date_to=payload.date_to,
        runs=tuple(sorted(payload.runs.items(), key=lambda item: str(item[0]))), site=payload.site.strip(),
    )


def references_uow_factory(request: Request) -> Callable[[], Any] | None:
    factory = getattr(request.app.state, "references_uow_factory", None)
    return factory if callable(factory) else None


def _techcards(factory: Callable[[], TechcardsUnitOfWork] | None, closed: frozenset[str] = frozenset()) -> tuple[dict[tuple[str, str], CardSnapshot], list[ReadyCard]]:
    """Текущая выданная техкарта каждой позиции (проект, индекс; у нескольких — последняя выданная) и КБ к запуску —
    тот же список, что «Создать задание» на доске мастера (/api/production/launchable)."""
    if factory is None:
        return {}, []
    with factory() as techcards:
        rows = [card for card in techcards.techcards.list() if card.status is TechcardStatus.ISSUED]
        kitsets = tuple(techcards.kitsets.list()) if hasattr(techcards, "kitsets") else ()
    result: dict[tuple[str, str], CardSnapshot] = {}
    rows.sort(key=lambda item: (item.issued_at or item.updated_at, item.updated_at, str(item.id)))
    for card in rows:
        result[card_key(card.project, card.item_index)] = CardSnapshot(card.id, card.version, card.document)
    return result, ready_cards(rows, kitsets, closed)


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


@router.post("/context")
def context(
    payload: PreviewPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    cards, ready = _techcards(techcards_factory, closed)
    return _handle(lambda: autoplan_context(_command(payload, principal, now), uow_factory(), cards, ready))


@router.post("/preview")
def preview(
    payload: PreviewPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    (cards, ready), units = _techcards(techcards_factory, closed), _units(references_factory)
    return _handle(lambda: describe(preview_autoplan(_command(payload, principal, now), uow_factory(), cards, units, ready)))


@router.post("/apply")
def apply(
    payload: ApplyPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], TechcardsUnitOfWork] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
    closed: Annotated[frozenset[str], Depends(closed_kbs)],
) -> dict:
    (cards, ready), units = _techcards(techcards_factory, closed), _units(references_factory)

    def execute() -> dict:
        applied = apply_autoplan(ApplyAutoplanCommand(
            plan=_command(payload, principal, now),
            items=tuple((item.task_id, item.operation_id, item.operator_id) for item in payload.items),
            fingerprint=payload.fingerprint,
        ), uow_factory(), cards, units, ready)
        return {
            "applied": applied.operations,
            "tasks": [{"id": str(task.id), "number": task.number, "version": task.version} for task in applied.tasks],
            "created": [{"id": str(task.id), "number": task.number} for task in applied.created],
        }

    return _handle(execute)
