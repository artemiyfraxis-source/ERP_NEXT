"""«Базовый план» производственного плана: зафиксировать снимок и показать отклонения факта от последнего снимка."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, production_edit_principal, production_overview_principal

from .application import UnitOfWork
from .baseline import TOLERANCE, Deviation, PlanBaseline, deviations, take_baseline, task_window
from .baseline_infrastructure import SqlAlchemyBaselineRepository
from .domain import ProductionStatus, ProductionValidationError


router = APIRouter(prefix="/api/production/plan/baseline", tags=["production-plan"])


def production_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "production_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _minutes(delay: timedelta | None) -> int | None:
    return None if delay is None else int(delay.total_seconds() // 60)


def _number(value: Decimal) -> str:
    text = format(value.normalize(), "f") if value == value.to_integral_value() else format(value, "f").rstrip("0")
    return text.rstrip(".") or "0"


def _row(item: Deviation) -> dict[str, Any]:
    line, fact = item.line, item.fact
    return {
        "task_id": str(line.task_id), "task_number": line.task_number, "project": line.project, "item": line.item,
        "quantity": line.quantity,
        "planned_start": line.planned_start.isoformat(), "planned_finish": line.planned_finish.isoformat(),
        "status": fact.status.value if fact else None,
        "started_at": fact.started_at.isoformat() if fact and fact.started_at else None,
        "finished_at": fact.finished_at.isoformat() if fact and item.finished and fact.finished_at else None,
        "done_quantity": _number(fact.done_quantity) if fact else "0",
        "start_delay_minutes": _minutes(item.start_delay), "finish_delay_minutes": _minutes(item.finish_delay),
        "late_start": item.late_start, "late_finish": item.late_finish,
        "shortfall": _number(item.shortfall), "short": item.short,
        "started": item.started, "finished": item.finished,
    }


def _result(uow, repository: SqlAlchemyBaselineRepository, now: datetime) -> dict[str, Any]:
    baseline = repository.latest()
    result: dict[str, Any] = {"tolerance_minutes": int(TOLERANCE.total_seconds() // 60), "baseline": None, "rows": []}
    if baseline is None:
        return result
    tasks = [task for task in (uow.production.get(line.task_id) for line in baseline.lines) if task is not None]
    items = deviations(baseline, repository.facts(tasks), now)
    in_baseline = {line.task_id for line in baseline.lines}
    # Запущено после фиксации (или получило сроки позже): в снимке их нет — показываем счётчиком.
    outside = sum(
        1 for task in uow.production.with_status(ProductionStatus.RUNNING)
        if task.id not in in_baseline and task_window(task) is not None
    )
    result["baseline"] = _header(baseline, outside)
    result["rows"] = [_row(item) for item in items]
    result["summary"] = {
        "total": len(items),
        "late_start": sum(item.late_start for item in items),
        "late_finish": sum(item.late_finish for item in items),
        "short": sum(item.short for item in items),
        "on_plan": sum(not item.deviates for item in items),
    }
    return result


def _header(baseline: PlanBaseline, outside: int) -> dict[str, Any]:
    return {
        "id": str(baseline.id), "number": baseline.number, "created_at": baseline.created_at.isoformat(),
        "created_by": baseline.created_by, "skipped": baseline.skipped, "tasks": len(baseline.lines),
        "outside": outside,
    }


@router.get("")
def baseline_deviations(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
) -> dict:
    with uow_factory() as uow:
        return _result(uow, SqlAlchemyBaselineRepository(uow.session), now)


@router.post("", status_code=201)
def fix_baseline(
    principal: Annotated[Principal, Depends(production_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_factory)],
) -> dict:
    try:
        with uow_factory() as uow:
            repository = SqlAlchemyBaselineRepository(uow.session)
            baseline = take_baseline(
                uow.production.with_status(ProductionStatus.RUNNING), number=repository.next_number(), now=now,
                actor_id=principal.actor.id, actor_name=principal.actor.name,
            )
            repository.add(baseline)
            uow.commit()
    except ProductionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(
            status_code=409, detail="Базовый план только что зафиксировали — обновите страницу",
        ) from error
    with uow_factory() as uow:
        return _result(uow, SqlAlchemyBaselineRepository(uow.session), now)
