from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from app.modules.production.domain import (
    OperatorSkill,
    ProductionOperation,
    ProductionTask,
    WorkShift,
    add_work_seconds,
)
from app.modules.production.scheduling import batch_sizes, schedule_task, schedule_variants


OPERATOR_A = UUID("10000000-0000-0000-0000-000000000001")
OPERATOR_B = UUID("10000000-0000-0000-0000-000000000002")


def _operation(position: int, code: str, unit: str) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=code, stage=code, description="",
        seconds_per_unit=Decimal(unit), setup_seconds=Decimal(0), quantity=100,
    )


def _task(quantity: int = 100) -> ProductionTask:
    return ProductionTask.create(
        number="ПЗ-0001", source_idempotency_key="schedule-test",
        source_fingerprint="fingerprint", project="708К.2", item="КБ1", name="Жгут",
        quantity=quantity, fulfillment_request_id=None, deadline=date(2026, 9, 30),
        launched_by="Мастер", operations=(
            _operation(1, "OP-1", "60"),
            _operation(2, "OP-2", "120"),
        ), created_at=datetime(2026, 9, 23, 8, 0),
    )


def _skills():
    return {
        "OP-1": {
            OPERATOR_A: OperatorSkill(OPERATOR_A, "OP-1", True, 5, Decimal("1")),
            OPERATOR_B: OperatorSkill(OPERATOR_B, "OP-1", True, 3, Decimal("1")),
        },
        "OP-2": {
            OPERATOR_A: OperatorSkill(OPERATOR_A, "OP-2", True, 5, Decimal("1")),
            OPERATOR_B: OperatorSkill(OPERATOR_B, "OP-2", True, 4, Decimal("2")),
        },
    }


def test_shift_uses_breaks_and_effective_hour_budget():
    shift = WorkShift(
        weekdays=(0,), start=time(9), finish=time(19),
        breaks=((time(12), time(13)),), effective_seconds=6 * 60 * 60,
    )
    assert add_work_seconds(
        datetime(2026, 9, 21, 9), 6 * 60 * 60, frozenset(), shift,
    ) == datetime(2026, 9, 21, 16)
    assert add_work_seconds(
        datetime(2026, 9, 21, 9), 7 * 60 * 60, frozenset(), shift,
    ) == datetime(2026, 9, 28, 10)


def test_auto_schedule_uses_skills_coefficients_and_transfer_batch():
    result = schedule_task(
        task=_task(), transfer_batch=10, team=(OPERATOR_A, OPERATOR_B),
        skills=_skills(), busy={}, now=datetime(2026, 9, 23, 9),
        holidays=frozenset(), max_crew=1,
    )
    assert result.operations[0].operator_ids == (OPERATOR_A,)
    assert result.operations[1].operator_ids == (OPERATOR_B,)
    assert result.operations[1].start < result.operations[0].finish
    assert result.first_ready < result.finish


def test_schedule_variants_keep_whole_batch_and_pareto_options():
    task = _task(quantity=250)
    assert batch_sizes(250) == (250, 63, 25, 13, 3, 2)
    variants = schedule_variants(
        task=task, team=(OPERATOR_A, OPERATOR_B), skills=_skills(), busy={},
        now=datetime(2026, 9, 23, 9), holidays=frozenset(), max_crew=2,
        replan=False,
    )
    assert variants[0].transfer_batch == 250
    assert all(variant.operations for variant in variants)
    assert len(variants) <= 5
