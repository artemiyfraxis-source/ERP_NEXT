from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.production.domain import (
    BusyInterval,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
    add_work_seconds,
    operation_duration_seconds,
    plan_operation,
)


OPERATOR = UUID("10000000-0000-0000-0000-000000000001")


def operation(position: int = 1) -> ProductionOperation:
    return ProductionOperation.create(
        position=position,
        code="OP-01",
        stage="Разделка",
        description="Подготовить кабель",
        seconds_per_unit=Decimal("60"),
        setup_seconds=Decimal("120"),
        quantity=10,
    )


def task(*operations: ProductionOperation) -> ProductionTask:
    return ProductionTask.create(
        number="ПЗ-0001",
        source_idempotency_key="project:591K.3:KB-1:v1",
        source_fingerprint="fingerprint",
        project="591K.3",
        item="591K.3.КБ1",
        name="Жгут",
        quantity=10,
        fulfillment_request_id=None,
        deadline=None,
        launched_by="Мастер",
        operations=operations,
        created_at=datetime(2026, 9, 22, 8, 0),
    )


def test_launch_keeps_sorted_route_snapshot_without_operation_status():
    created = task(operation(2), operation(1))

    assert created.status is ProductionStatus.RUNNING
    assert [item.position for item in created.operations] == [1, 2]
    assert created.operations[0].norm_seconds == 720
    assert not hasattr(created.operations[0], "status")
    with pytest.raises(ProductionValidationError, match="запускать нечего"):
        task()


def test_working_calendar_skips_lunch_weekend_and_holiday():
    friday = datetime(2026, 9, 25, 12, 30)
    assert add_work_seconds(friday, 60 * 60, frozenset()) == datetime(2026, 9, 25, 14, 0)
    assert add_work_seconds(friday, 5 * 60 * 60, frozenset({date(2026, 9, 28)})) == datetime(2026, 9, 29, 9, 30)


def test_multiple_operators_share_unit_work_but_each_has_setup_time():
    planned = operation()
    assert operation_duration_seconds(planned, quantity=10, operators=1) == 720
    assert operation_duration_seconds(planned, quantity=10, operators=2) == 420


def test_auto_plan_finds_first_common_free_window_and_manual_plan_warns():
    second = UUID("10000000-0000-0000-0000-000000000002")
    busy = {
        OPERATOR: (BusyInterval("ПЗ-0000 · этап 1", datetime(2026, 9, 22, 9, 0), datetime(2026, 9, 22, 10, 0)),),
        second: (BusyInterval("Отпуск", datetime(2026, 9, 23, 0, 0), datetime(2026, 9, 24, 0, 0)),),
    }
    automatic = plan_operation(
        operation=operation(), quantity=10, operator_ids=(OPERATOR, second),
        requested_start=None, earliest_start=datetime(2026, 9, 22, 9, 0), busy=busy,
        holidays=frozenset(),
    )
    manual = plan_operation(
        operation=operation(), quantity=10, operator_ids=(OPERATOR,),
        requested_start=datetime(2026, 9, 22, 9, 30), earliest_start=datetime(2026, 9, 22, 10, 30),
        busy=busy, holidays=frozenset(),
    )

    assert automatic.start == datetime(2026, 9, 22, 10, 0)
    assert automatic.finish == datetime(2026, 9, 22, 10, 7)
    assert manual.warnings == ("Старт раньше окончания предыдущего этапа", "Оператор занят: ПЗ-0000 · этап 1")


def test_cancel_preserves_operations_and_restore_only_changes_task_status():
    created = task(operation())
    cancelled = created.set_cancelled(True)
    restored = cancelled.set_cancelled(False)

    assert cancelled.operations == created.operations
    assert restored.status is ProductionStatus.RUNNING
    assert restored.operations == created.operations
