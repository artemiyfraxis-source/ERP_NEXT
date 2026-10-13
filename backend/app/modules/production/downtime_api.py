"""HTTP: простои с причинами — «Простой» / «Закончить простой» в «Заданиях» и у мастера, открытые простои и отчёт
за период (правила — downtime.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, production_overview_principal, require_section, section_allowed

from .api import _performer, _period, production_uow_factory
from .application import UnitOfWork
from .domain import ProductionStatus, ProductionValidationError
from .launchable import stage_name
from .downtime import COMMENT_LIMIT, REASON_NAMES, REPORT_KEYS, Downtime, DowntimeConflict, DowntimeReason, moscow, report
from .downtime_infrastructure import SqlAlchemyDowntimeRepository


router = APIRouter(prefix="/api/production/downtime", tags=["production"])

# Отметить и закончить простой: монтажник («Задания», правка) или мастер («Планирование», правка).
downtime_record_principal = require_section(
    ("myops", "production"), "edit", detail="Отмечать простой может монтажник в «Заданиях» или мастер.",
)
downtime_view_principal = require_section(("myops", "production"), "view", detail="Раздел «Задания» вам не открыт.")
OPEN_CONFLICT = "У задания уже открыт простой — сначала закончите его"


class StartPayload(BaseModel):
    task_id: UUID
    operation_id: UUID | None = None
    reason: DowntimeReason
    comment: str = Field(default="", max_length=COMMENT_LIMIT)
    # Мастер: задание стоит целиком, не простой одного монтажника.
    whole_task: bool = False


def _master(principal: Principal) -> bool:
    return section_allowed(principal, ("production",), "edit")


def _result(item: Downtime, now: datetime) -> dict:
    end = item.ended_at if item.ended_at is not None else now
    return {
        "id": str(item.id), "task_id": str(item.task_id) if item.task_id else None,
        "operation_id": str(item.operation_id) if item.operation_id else None,
        "operator_id": str(item.operator_id) if item.operator_id else None, "operator_name": item.operator_name,
        "whole_task": item.operator_id is None,
        "reason": item.reason.value, "reason_name": item.reason_name, "comment": item.comment,
        "started_at": moscow(item.started_at).isoformat(), "started_by_name": item.started_by_name,
        "ended_at": moscow(item.ended_at).isoformat() if item.ended_at is not None else None,
        "ended_by_name": item.ended_by_name, "open": item.open,
        "seconds": max(0, int((moscow(end) - moscow(item.started_at)).total_seconds())),
        "project": item.project, "task_number": item.task_number, "item": item.item, "stage": stage_name(item.stage),
    }


def _audit(uow, event_type: str, item: Downtime, principal: Principal, now: datetime) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=item.task_id or item.id, event_type=event_type, actor=principal.actor, occurred_at=now,
        details={"downtime_id": str(item.id), "reason": item.reason.value, "comment": item.comment,
                 "operator_id": str(item.operator_id) if item.operator_id else None, "task_number": item.task_number},
    ))


@router.get("/reasons")
def reasons(_principal: Annotated[Principal, Depends(downtime_view_principal)]) -> dict:
    return {"reasons": [{"key": reason.value, "name": name} for reason, name in REASON_NAMES.items()]}


@router.get("/open")
def open_downtimes(
    _principal: Annotated[Principal, Depends(downtime_view_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
) -> dict:
    """Открытые простои цеха (их немного): карточка задания показывает свой и кнопку «Закончить простой»."""
    with uow_factory() as uow:
        rows = SqlAlchemyDowntimeRepository(uow.session).open_all()
    return {"downtimes": [_result(item, now) for item in rows]}


@router.post("")
def start_downtime(
    payload: StartPayload,
    principal: Annotated[Principal, Depends(downtime_record_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    on_behalf_of: UUID | None = None,
) -> dict:
    master = _master(principal)
    if payload.whole_task and not master:
        raise HTTPException(status_code=403, detail="Простой всего задания отмечает мастер")
    performer = None if payload.whole_task else (_performer(uow_factory, principal, on_behalf_of) or principal.actor)
    with uow_factory() as uow:
        task = uow.production.get(payload.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задание не найдено")
        if task.status is not ProductionStatus.RUNNING:
            raise HTTPException(status_code=422, detail=f"Задание {task.status.value.lower()} — простой не отмечается")
        operation = None
        if payload.operation_id is not None:
            operation = next((item for item in task.operations if item.id == payload.operation_id), None)
            if operation is None:
                raise HTTPException(status_code=422, detail="Операция не из этого задания")
        if performer is not None and not master:
            assigned = performer.id in task.team or any(performer.id in item.operator_ids for item in task.operations)
            if not assigned:
                raise HTTPException(status_code=403, detail="Простой отмечают по своему заданию")
        repository = SqlAlchemyDowntimeRepository(uow.session)
        if repository.open_for_task(task.id) is not None:
            raise HTTPException(status_code=409, detail=OPEN_CONFLICT)
        try:
            item = Downtime(
                id=uuid4(), task_id=task.id, operation_id=operation.id if operation else None,
                operator_id=performer.id if performer else None, operator_name=performer.name if performer else "",
                reason=payload.reason, comment=payload.comment, started_at=now,
                started_by_id=principal.actor.id, started_by_name=principal.actor.name,
                project=task.project, task_number=task.number, item=task.item,
                stage=(stage_name(operation.stage) or f"этап {operation.position}") if operation else "",
            )
        except ProductionValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            repository.add(item)
        except IntegrityError as error:  # второе «Простой» по тому же заданию в ту же секунду
            raise HTTPException(status_code=409, detail=OPEN_CONFLICT) from error
        _audit(uow, "production.downtime_started", item, principal, now)
        uow.commit()
    return {"downtime": _result(item, now)}


@router.post("/{downtime_id}/stop")
def stop_downtime(
    downtime_id: UUID,
    principal: Annotated[Principal, Depends(downtime_record_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    on_behalf_of: UUID | None = None,
) -> dict:
    performer = _performer(uow_factory, principal, on_behalf_of) or principal.actor
    with uow_factory() as uow:
        repository = SqlAlchemyDowntimeRepository(uow.session)
        item = repository.get(downtime_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Простой не найден")
        if not _master(principal) and item.operator_id != performer.id:
            raise HTTPException(status_code=403, detail="Закончить чужой простой может мастер")
        try:
            closed = item.close(now, principal.actor.id, principal.actor.name)
            repository.close(closed)
        except DowntimeConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ProductionValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        _audit(uow, "production.downtime_ended", closed, principal, now)
        uow.commit()
    return {"downtime": _result(closed, now)}


@router.get("/report")
def downtime_report(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    date_from: date,
    date_to: date,
    by: str = "reason",
) -> dict:
    """Простои за дни периода (по Москве) по причинам или по сотрудникам, рядом — работа по журналу времени."""
    if by not in REPORT_KEYS:
        raise HTTPException(status_code=422, detail="Отчёт группируется по причине или по сотруднику")
    since, until = _period(date_from, date_to)
    with uow_factory() as uow:
        items = SqlAlchemyDowntimeRepository(uow.session).overlapping(since, until)
        work = uow.work_log.summary("operator", since=since, until=until)
    rows = report(items, by=by, since=since, until=until, now=now)
    work_by_operator = {str(row["key"]): int(row["actual_seconds"]) for row in work}
    return {
        "by": by, "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "rows": [{
            "key": row.key, "label": row.label, "count": row.count, "seconds": row.seconds, "open": row.open,
            "work_seconds": work_by_operator.get(row.key) if by == "operator" else None,
        } for row in rows],
        "total_seconds": sum(row.seconds for row in rows), "total_count": sum(row.count for row in rows),
        "work_seconds": sum(work_by_operator.values()),
        "entries": [{**_result(item, now), "period_seconds": item.seconds_within(since, until, now)} for item in items],
    }
