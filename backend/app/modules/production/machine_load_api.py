"""HTTP: загрузка станков по дням и проверка ручной постановки на занятый станок (правила — machine_load.py)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_overview_principal
from app.modules.references.domain import TechOperationTab

from .api import production_uow_factory, techcards_uow_factory
from .application import UnitOfWork, _shift
from .autoplan_api import _techcards, _units, references_uow_factory
from .autoplan_application import card_key
from .dispatch import local, localize
from .domain import ProductionStatus, ProductionTask, WorkShift
from .machine_load import (
    MachineStep,
    Occupancy,
    conflict_text,
    conflicts,
    machine_load,
    inventory_prefix,
    normalized_site,
    operation_occupancies,
    task_occupancies,
    task_steps,
    site_machine_load,
)


router = APIRouter(prefix="/api/production/machine-load", tags=["production"])

MAX_DAYS = 62


@dataclass(frozen=True)
class _Snapshot:
    tasks: dict[UUID, ProductionTask]
    steps: dict[UUID, tuple[MachineStep, ...]]  # по операции
    occupancies: tuple[Occupancy, ...]
    units: dict[str, int]
    names: dict[UUID, str]
    operator_sites: dict[UUID, str]
    site_units: dict[tuple[str, str], int]
    site_labels: dict[tuple[str, str], str]
    holidays: frozenset[date]
    shift: WorkShift
    warnings: tuple[str, ...]


def _site_equipment(factory: Callable[[], Any] | None) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
    """Count physical inventory numbers at their registered site, excluding resale markers (.X)."""
    if factory is None:
        return {}, {}
    with factory() as references:
        records = references.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=100_000)
    numbers: dict[tuple[str, str], set[str]] = {}
    labels: dict[tuple[str, str], str] = {}
    for record in records:
        fields = {" ".join(str(key).split()).casefold(): value for key, value in record.fields.items()}
        number = next((str(value or "") for key, value in fields.items() if "инв" in key or key == "inventorynumber"), "")
        number = re.sub(r"\s+", "", number).upper().replace("OБ", "ОБ")
        if not re.fullmatch(r"ОБ\.\d+(?:\.\d+)?", number):
            continue
        machine = inventory_prefix(number)
        site = normalized_site(fields.get("расположение"))
        key = (site, machine)
        numbers.setdefault(key, set()).add(number)
        equipment_type = (str(fields.get("тип") or "").strip() or str(fields.get("obtype") or "").strip()).casefold()
        if equipment_type in {"оборудование", "оборудование (пр)"}:
            name = str(fields.get("наименование") or fields.get("наменование") or "").strip()
            labels[key] = f"{machine} {name}".strip() if name else f"{machine} {record.display_text}".strip()
    return {key: len(values) for key, values in numbers.items()}, labels


def _snapshot(
    now: datetime,
    uow_factory: Callable[[], UnitOfWork],
    techcards_factory: Callable[[], Any] | None,
    references_factory: Callable[[], Any] | None,
) -> _Snapshot:
    """Живые задания (запущенные, не отданные площадке), станки их операций по выданной ЭТК и занятости плана."""
    cards, _ = _techcards(techcards_factory)
    units = _units(references_factory)
    site_units, site_labels = _site_equipment(references_factory)
    with uow_factory() as uow:
        tasks = [localize(task, now) for task in uow.production.with_status(ProductionStatus.RUNNING) if not task.handed_to]
        holidays = uow.calendar.holidays()
        shift = _shift(uow.calendar)
        operators = uow.operators.list()
        names = {operator.id: operator.name for operator in operators}
        operator_sites = {operator.id: operator.site for operator in operators}
    steps: dict[UUID, tuple[MachineStep, ...]] = {}
    occupancies: list[Occupancy] = []
    warnings: list[str] = []
    for task in sorted(tasks, key=lambda item: item.number):
        card = cards.get(card_key(task.project, task.item))
        own, task_warnings = task_steps(task, card.document if card is not None else None)
        steps.update(own)
        occupancies.extend(task_occupancies(task, own, holidays, shift))
        if any(operation.shares for operation in task.operations):
            warnings.extend(task_warnings)
    for machine in sorted({item.machine for item in occupancies}):
        if machine not in units:
            warnings.append(f"Станок {machine} не найден в БД.ОБ — считаю, что он один")
    return _Snapshot(
        {task.id: task for task in tasks}, steps, tuple(occupancies), units, names, operator_sites, site_units, site_labels, holidays, shift,
        tuple(dict.fromkeys(warnings)),
    )


def _item(item: Occupancy, names: dict[UUID, str]) -> dict:
    return {
        "task_id": str(item.task_id), "task_number": item.task_number, "project": item.project, "item": item.item,
        "operation_id": str(item.operation_id), "position": item.operation_position, "stage": item.operation_stage,
        "etk_position": item.etk_position, "etk_stage": item.etk_stage,
        "operator_names": [names[operator_id] for operator_id in item.operator_ids if operator_id in names],
        "start": item.start.isoformat(), "finish": item.finish.isoformat(),
    }


@router.get("")
def load(
    date_from: date,
    date_to: date,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="Конец периода раньше начала")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"Период загрузки станков — не больше {MAX_DAYS} дней")
    snapshot = _snapshot(now, uow_factory, techcards_factory, references_factory)
    days = [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]
    rows = machine_load(snapshot.occupancies, snapshot.units, days, snapshot.holidays, snapshot.shift, now.tzinfo)
    sites = site_machine_load(
        snapshot.occupancies, snapshot.site_units, snapshot.operator_sites, snapshot.site_labels,
        days, snapshot.holidays, snapshot.shift, now.tzinfo,
    )
    def row_payload(row) -> dict:
        return {
            "machine": row.machine, "label": row.label, "units": row.units, "known": row.known,
            "days": [{
                "day": day.day.isoformat(), "busy_seconds": day.busy_seconds, "capacity_seconds": day.capacity_seconds,
                "percent": day.percent, "peak": day.peak, "over": row.over(day),
            } for day in row.days],
            "items": [_item(item, snapshot.names) for item in row.items],
        }
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "machines": [row_payload(row) for row in rows],
        "sites": [{"site": site, "machines": [row_payload(row) for row in local_rows]} for site, local_rows in sites.items()],
        "warnings": list(snapshot.warnings),
    }


class CheckPayload(BaseModel):
    task_id: UUID
    operation_id: UUID
    # Кому ставят: его прежняя доля этой операции не считается занятостью (её переставляют).
    operator_id: UUID | None = None
    # Штук изделий доли; None — всё задание.
    quantity: int | None = Field(default=None, gt=0)
    start: datetime
    finish: datetime


@router.post("/check")
def check(
    payload: CheckPayload,
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    techcards_factory: Annotated[Callable[[], Any] | None, Depends(techcards_uow_factory)],
    references_factory: Annotated[Callable[[], Any] | None, Depends(references_uow_factory)],
) -> dict:
    """Предупреждение для ручной постановки: станки операции в окне уже заняты всеми единицами. Ничего не запрещает."""
    start, finish = local(payload.start, now), local(payload.finish, now)
    if finish <= start:
        raise HTTPException(status_code=422, detail="Конец окна раньше начала")
    snapshot = _snapshot(now, uow_factory, techcards_factory, references_factory)
    task = snapshot.tasks.get(payload.task_id)
    operation = next((item for item in task.operations if item.id == payload.operation_id), None) if task else None
    steps = snapshot.steps.get(payload.operation_id)
    if task is None or operation is None or not steps:
        return {"conflicts": []}
    candidate = operation_occupancies(
        task, operation, steps, [((payload.operator_id,) if payload.operator_id else (), payload.quantity, start, finish)],
        snapshot.holidays, snapshot.shift,
    )
    # Прежнее место этой же работы — не занятость: доля того же монтажника или вся операция (один или бригада).
    others = [
        item for item in snapshot.occupancies
        if item.operation_id != operation.id
        or not (len(item.operator_ids) != 1 or payload.operator_id is None or payload.operator_id in item.operator_ids)
    ]
    return {"conflicts": [{
        "machine": item.occupancy.machine, "label": item.occupancy.machine_label, "units": item.units,
        "start": item.start.isoformat(), "finish": item.finish.isoformat(),
        "text": conflict_text(item, snapshot.names),
        "busy": [_item(other, snapshot.names) for other in item.busy],
    } for item in conflicts(candidate, others, snapshot.units)]}
