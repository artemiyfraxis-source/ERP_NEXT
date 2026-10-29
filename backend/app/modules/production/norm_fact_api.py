"""HTTP: отчёт «Норма и факт» за период (правила — norm_fact.py). Только чтение: нормы и компетенции не меняет."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.shared.clock import moscow_today
from app.shared.http_auth import Principal, production_overview_principal

from .api import _period, production_uow_factory
from .application import UnitOfWork
from .infrastructure import ProductionOperationRow, ProductionOutputRow, ProductionTaskRow, ProductionWorkSessionRow, ProductionWorkTimeLogRow
from .launchable import operation_code, stage_name
from .norm_fact import FAST, MIN_FACTS, SLOW, Fact, OperatorRow, Spread, Submission, TemplateRow, by_operator, by_template, facts
from .worklog import WorkEvent, earned_norm_seconds


router = APIRouter(prefix="/api/production/norm-fact", tags=["production"])

DEFAULT_DAYS = 60


def _logged(session, since: datetime, until: datetime) -> list[Submission]:
    """Сдачи из журнала рабочего времени: время сессии без пауз и норма на сданные штуки — как записаны при сдаче."""
    log, work = ProductionWorkTimeLogRow, ProductionWorkSessionRow
    rows = session.execute(
        select(log, work.started_at).join(work, work.id == log.session_id)
        .where(log.event == WorkEvent.FINISH.value, log.occurred_at >= since, log.occurred_at < until)
    ).all()
    return [Submission(
        session_id=row.session_id, task_id=row.task_id, task_number=row.task_number, project=row.project, item=row.item,
        operation_id=row.operation_id, position=row.position, operation_code=operation_code(row.operation_code),
        stage=stage_name(row.stage), operator_id=row.operator_id, operator_name=row.operator_name,
        started_at=started_at, finished_at=row.occurred_at, actual_seconds=int(row.running_seconds or 0),
        quantity=Decimal(row.good_quantity or 0) + Decimal(row.defect_quantity or 0),
        seconds_per_unit=Decimal(row.seconds_per_unit), norm_seconds=int(row.earned_norm_seconds or 0),
    ) for row, started_at in rows]


def _before_log(uow: UnitOfWork, since: datetime, until: datetime) -> list[Submission]:
    """Сдачи сессий, которых нет в журнале (сделаны до него, миграция 0105): время — от начала до сдачи без пауз,
    норма — по нормам операции задания (подготовка — в первой сдаче монтажника по операции). Ввод готовых мастером
    (без сессии) времени не несёт и в отчёт не идёт."""
    session = uow.session
    output, operation, task, log = ProductionOutputRow, ProductionOperationRow, ProductionTaskRow, ProductionWorkTimeLogRow
    logged = select(log.session_id).where(log.event == WorkEvent.FINISH.value, log.session_id == output.session_id)
    rows = session.execute(
        select(output, operation, task.number, task.project, task.item)
        .join(operation, operation.id == output.operation_id)
        .join(task, task.id == output.task_id)
        .where(
            output.session_id.is_not(None), ~logged.exists(),
            output.finished_at >= since, output.finished_at < until,
        )
    ).all()
    if not rows:
        return []
    pairs = {(row[0].operation_id, row[0].operator_id) for row in rows}
    first = {
        (operation_id, operator_id): started
        for operation_id, operator_id, started in session.execute(
            select(output.operation_id, output.operator_id, func.min(output.started_at))
            .where(output.session_id.is_not(None), output.operation_id.in_({pair[0] for pair in pairs}))
            .group_by(output.operation_id, output.operator_id)
        )
    }
    names = {}
    for operator_id in {pair[1] for pair in pairs}:
        operator = uow.operators.get(operator_id)
        names[operator_id] = operator.name if operator is not None else "—"
    result: list[Submission] = []
    for item, op, number, project, item_id in rows:
        name = names[item.operator_id]
        quantity = Decimal(item.good_quantity) + Decimal(item.defect_quantity)
        running = max(0, int((item.finished_at - item.started_at).total_seconds()) - int(item.pause_seconds or 0))
        result.append(Submission(
            session_id=item.session_id, task_id=item.task_id, task_number=number, project=project, item=item_id,
            operation_id=item.operation_id, position=op.position, operation_code=operation_code(op.code),
            stage=stage_name(op.stage), operator_id=item.operator_id, operator_name=name,
            started_at=item.started_at, finished_at=item.finished_at, actual_seconds=running, quantity=quantity,
            seconds_per_unit=Decimal(op.seconds_per_unit),
            norm_seconds=earned_norm_seconds(
                op.seconds_per_unit, op.setup_seconds, quantity,
                first=first.get((item.operation_id, item.operator_id)) == item.started_at,
            ),
        ))
    return result


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _spread(value: Spread) -> dict:
    return {
        "facts": value.facts, "no_norm": value.no_norm, "enough": value.enough, "verdict": value.verdict,
        "median": _round(value.median), "low": _round(value.low), "high": _round(value.high),
        "actual_seconds": value.actual_seconds, "norm_seconds": value.norm_seconds,
    }


def _template(row: TemplateRow) -> dict:
    return {
        "template": row.template, "operation_code": row.operation_code, "stage": row.stage, **_spread(row.spread),
        "seconds_per_unit": _round(row.seconds_per_unit, 1), "suggestion": row.suggestion,
        "suggested_seconds_per_unit": _round(row.suggested_seconds_per_unit, 1),
    }


def _operator(row: OperatorRow) -> dict:
    return {
        "operator_id": str(row.operator_id), "operator_name": row.operator_name, **_spread(row.spread),
        "relative": _round(row.relative), "coefficient": row.coefficient, "suggestion": row.suggestion,
    }


def _fact(fact: Fact) -> dict:
    return {
        "task_id": str(fact.task_id), "task_number": fact.task_number, "project": fact.project, "item": fact.item,
        "operation_id": str(fact.operation_id), "position": fact.position, "template": fact.template,
        "operation_code": fact.operation_code, "stage": fact.stage,
        "operator_id": str(fact.operator_id), "operator_name": fact.operator_name,
        "started_at": fact.started_at.isoformat(), "finished_at": fact.finished_at.isoformat(),
        "submissions": fact.submissions, "actual_seconds": fact.actual_seconds,
        "quantity": format(fact.quantity.normalize(), "f"), "norm_seconds": fact.norm_seconds,
        "actual_per_piece": _round(fact.actual_per_piece, 1), "norm_per_piece": _round(fact.norm_per_piece, 1),
        "ratio": _round(fact.ratio),
    }


@router.get("")
def norm_fact_report(
    _principal: Annotated[Principal, Depends(production_overview_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(production_uow_factory)],
    today: Annotated[date, Depends(moscow_today)],
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Норма и факт за дни периода по Москве (по дню сдачи); без дат — последние 60 дней."""
    date_to = date_to or today
    date_from = date_from or date_to - timedelta(days=DEFAULT_DAYS - 1)
    since, until = _period(date_from, date_to)
    with uow_factory() as uow:
        submissions = _logged(uow.session, since, until) + _before_log(uow, since, until)
    items = facts(submissions)
    templates = by_template(items)
    operators = by_operator(items, templates)
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "thresholds": {"min_facts": MIN_FACTS, "slow": SLOW, "fast": FAST},
        "templates": [_template(row) for row in templates],
        "operators": [_operator(row) for row in operators],
        "facts": [_fact(fact) for fact in items],
    }
