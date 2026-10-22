"""HTTP: доска мастера — будущие задания КБ с выданной техкартой, группы оборудования операций и групповое назначение
(правила — board.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal
from app.shared.project_number import normalize_project_number

from .api import _box_result, _number, _result, production_uow_factory, techcards_uow_factory
from .application import UnitOfWork
from .autoplan_api import _handle, _techcards, references_uow_factory
from .board import (
    BoardAssignCommand,
    BoardItem,
    BoardOutputCommand,
    Equipment,
    assign_board,
    card_key,
    equipment_of,
    future_tasks,
    operation_machines,
    record_board_output,
    unlaunched_cards,
)
from .domain import ProductionStatus, ProductionTask
from .machine_load import conflict_text, conflicts, operation_occupancies
from .machine_load_api import _snapshot


router = APIRouter(prefix="/api/production/board", tags=["production"])


class BoardItemPayload(BaseModel):
    # Настоящее задание — task_id + task_version; будущее (КБ без задания) — techcard_id + techcard_version.
    task_id: UUID | None = None
    task_version: int | None = Field(default=None, ge=1)
    techcard_id: UUID | None = None
    techcard_version: int | None = Field(default=None, ge=1)
    # Операция — по id или номеру этапа.
    operation_id: UUID | None = None
    position: int | None = Field(default=None, ge=1)
    # Штук изделий; null — вся операция.
    quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    # Тираж нового задания (только у КБ без задания); null — весь тираж КБ.
    run: int | None = Field(default=None, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _reference(self) -> "BoardItemPayload":
        if self.task_id is not None:
            if self.task_version is None:
                raise ValueError("Укажите версию задания")
        elif self.techcard_id is None or self.techcard_version is None:
            raise ValueError("Укажите задание (task_id и task_version) или КБ (techcard_id и techcard_version)")
        if self.operation_id is None and self.position is None:
            raise ValueError("Укажите операцию: operation_id или position")
        return self


class BoardOutputPayload(BaseModel):
    # КБ без задания, которую видел мастер, и этап маршрута.
    techcard_id: UUID
    techcard_version: int = Field(ge=1)
    position: int = Field(ge=1)
    good_quantity: int = Field(gt=0, le=1_000_000)
    # Тираж нового задания; null — весь тираж КБ.
    run: int | None = Field(default=None, ge=1, le=1_000_000)
    fill_inputs: bool = False


class AssignPayload(BaseModel):
    operator_id: UUID
    requested_start: datetime | None = None
    items: list[BoardItemPayload] = Field(min_length=1, max_length=500)


def _equipment(item: Equipment) -> dict:
    return {"key": item.key, "label": item.label, "limited": item.limited}


def _references(factory: Callable[[], Any] | None) -> dict[str, str]:
    """«Машина» БД.ОП по коду операции."""
    if factory is None:
        return {}
    with factory() as references:
        records = references.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000)
    return operation_machines([record.fields for record in records])


def _categories(factory: Callable[[], Any] | None, codes: set[str]) -> dict[str, str]:
    """Категория карточки операции из библиотеки карточек по коду (первая карточка кода выигрывает)."""
    if factory is None or not codes:
        return {}
    with factory() as techcards:
        library = getattr(techcards, "operation_cards", None)
        templates = library.matching((), sorted(codes)) if library is not None else ()
    result: dict[str, str] = {}
    for template in templates:
        if template.code and template.category and template.code not in result:
            result[template.code] = template.category
    return result


def _codes(task: ProductionTask) -> set[str]:
    return {
        part.code for operation in task.operations
        for part in (operation.etk_ops or ()) if part.code
    } | {operation.code for operation in task.operations if operation.code}


def _with_equipment(body: dict, task: ProductionTask, groups: dict[UUID, Equipment]) -> dict:
    for row, operation in zip(body["operations"], task.operations):
        row["equipment"] = _equipment(groups[operation.id])
    return body


@router.get("")
def board(
    principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
    project: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    """Запущенные задания (не отданные площадке) и будущие задания КБ с выданной техкартой без задания — из выданной
    сейчас ревизии; у каждой операции группа оборудования. КБ без тиража или с маршрутом, который не собрался, —
    в skipped с причиной. project — номер проекта; пусто — все."""
    cards, ready = _techcards(techcards_factory)
    wanted = normalize_project_number(project) if project.strip() else ""

    def mine(value: str) -> bool:
        return not wanted or normalize_project_number(value) == wanted

    with uow_factory() as uow:
        stored = tuple(uow.production.with_status(ProductionStatus.RUNNING))
        open_cards = [card for card in unlaunched_cards(uow, stored, ready, cards) if mine(card.project)]
    # Будущее задание строит тот же запуск, что и первое назначение; кто запустит — не важно для показа.
    news, skipped = future_tasks(open_cards, principal.actor, now)
    tasks = sorted(
        (task for task in stored if not task.handed_to and mine(task.project)),
        key=lambda task: (task.deadline is None, task.deadline, task.number),
    )
    machines = _references(references_factory)
    codes = set().union(*(_codes(task) for task in (*tasks, *(item.task for item in news.values()))))
    categories = _categories(techcards_factory, codes)

    def document(task: ProductionTask):
        card = cards.get(card_key(task.project, task.item))
        return card.document if card is not None else None

    future = []
    for item in sorted(news.values(), key=lambda value: (value.card.deadline is None, value.card.deadline, value.card.project, value.card.index)):
        body = _result(item.task)
        body.update({
            "pending": True, "techcard_id": str(item.card.techcard_id), "techcard_version": item.card.version,
            "techcard_revision": item.card.revision if item.card.revision >= 1 else None,
            "customer": item.card.customer, "run": item.card.run, "warnings": list(item.card.route.warnings),
        })
        future.append(_with_equipment(body, item.task, equipment_of(item.task, item.card.document, machines, categories)))
    return {
        "project": project.strip(),
        "tasks": [
            _with_equipment({**_result(task), "pending": False}, task, equipment_of(task, document(task), machines, categories))
            for task in tasks
        ],
        "future": future,
        "skipped": [{
            "techcard_id": str(item.card.techcard_id), "techcard_version": item.card.version,
            "project": item.card.project, "index": item.card.index, "name": item.card.name, "reason": item.reason,
        } for item in skipped],
    }


@router.post("/assign")
def assign(
    payload: AssignPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Группа операций одному монтажнику одной транзакцией: недостающие задания создаются из выданной сейчас ревизии,
    операции встают подряд по сроку → КБ → этапу. Устаревшая доска (версия задания или техкарты) — 409."""
    if techcards_factory is None and any(item.task_id is None for item in payload.items):
        raise HTTPException(status_code=503, detail="База техкарт не настроена")
    cards, ready = _techcards(techcards_factory)
    command = BoardAssignCommand(
        operator_id=payload.operator_id, requested_start=payload.requested_start,
        items=tuple(BoardItem(
            task_id=item.task_id, task_version=item.task_version, techcard_id=item.techcard_id,
            techcard_version=item.techcard_version, operation_id=item.operation_id, position=item.position,
            quantity=item.quantity, run=item.run,
        ) for item in payload.items),
        actor=principal.actor, now=now,
    )
    done = _handle(lambda: assign_board(command, uow_factory(), cards, ready))
    numbers = {task.id: task.number for task in done.tasks}
    warnings = [*done.warnings, *_machine_warnings(done, payload.operator_id, now, uow_factory, techcards_factory, references_factory)]
    return {
        "tasks": [_result(task) for task in done.tasks],
        "created": [{
            "id": str(task.id), "number": task.number, "techcard_id": str(task.techcard_id),
            "techcard_revision": task.techcard_revision,
        } for task in done.created],
        "placed": [{
            "task_id": str(item.task_id), "task_number": numbers.get(item.task_id, ""),
            "operation_id": str(item.operation_id), "position": item.position, "quantity": item.quantity,
            "start": item.start.isoformat(), "finish": item.finish.isoformat(),
        } for item in done.placed],
        "refused": [{
            "task_id": str(item.item.task_id) if item.item.task_id else None,
            "techcard_id": str(item.item.techcard_id) if item.item.techcard_id else None,
            "operation_id": str(item.item.operation_id) if item.item.operation_id else None,
            "position": item.item.position, "reason": item.reason,
        } for item in done.refused],
        "warnings": list(dict.fromkeys(warnings)),
    }


@router.post("/manual-output")
def manual_output(
    payload: BoardOutputPayload,
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
) -> dict:
    """«Уже сделано» по КБ без задания: задание создаётся из выданной сейчас ревизии (как первым назначением) и в нём
    же записываются готовые штуки этапа — одной транзакцией. Устаревшая доска — 409."""
    if techcards_factory is None:
        raise HTTPException(status_code=503, detail="База техкарт не настроена")
    cards, ready = _techcards(techcards_factory)
    done = _handle(lambda: record_board_output(BoardOutputCommand(
        techcard_id=payload.techcard_id, techcard_version=payload.techcard_version, position=payload.position,
        good_quantity=payload.good_quantity, actor=principal.actor, now=now, run=payload.run,
        fill_inputs=payload.fill_inputs,
    ), uow_factory(), cards, ready))
    box = done.box
    warnings = [f"Создано задание {done.task.number}. Записано {_number(box.quantity)} шт на этап {box.operation_position}, коробка {box.code or box.number}."]
    if done.filled:
        warnings.append("Отбиты готовыми: " + ", ".join(
            f"этап {item.operation_position} — {_number(item.quantity)} шт"
            for item in sorted(done.filled, key=lambda item: item.operation_position)
        ) + ".")
    operation = next(item for item in done.task.operations if item.position == payload.position)
    return {
        "task": _result(done.task), "operation_id": str(operation.id), "box": _box_result(box),
        "output_id": str(done.output.id), "filled": [_box_result(item) for item in done.filled], "warnings": warnings,
    }


def _machine_warnings(done, operator_id: UUID, now: datetime, uow_factory, techcards_factory, references_factory) -> list[str]:
    """Станок операции в её новом окне уже занят всеми единицами — предупреждение, как у ручной постановки (не запрет)."""
    snapshot = _snapshot(now, uow_factory, techcards_factory, references_factory)
    result: list[str] = []
    for item in done.placed:
        task = snapshot.tasks.get(item.task_id)
        steps = snapshot.steps.get(item.operation_id)
        if task is None or not steps:
            continue
        operation = task.operation(item.operation_id)
        share = operation.share_of(operator_id)
        if share is None or share.planned_start is None:
            continue
        candidate = operation_occupancies(
            task, operation, steps, [((share.operator_id,), share.quantity, share.planned_start, share.planned_finish)],
            snapshot.holidays, snapshot.shift,
        )
        others = [
            other for other in snapshot.occupancies
            if other.operation_id != operation.id or share.operator_id not in other.operator_ids
        ]
        result.extend(conflict_text(conflict, snapshot.names) for conflict in conflicts(candidate, others, snapshot.units))
    return result
