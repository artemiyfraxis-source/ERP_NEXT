from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.traceability import (
    BoxStatus,
    ProductionBox,
    WorkSession,
    WorkStatus,
    box_check_digit,
    format_box_number,
    issue_box_number,
    validate_box_number,
)


def test_box_number_uses_six_digits_and_luhn_control_digit_like_supplymap():
    assert box_check_digit(104233) == 2
    assert format_box_number(104233) == "104233-2"
    assert validate_box_number("104233-2") == "104233-2"
    for wrong in ("104233-7", "104323-2"):
        with pytest.raises(ProductionValidationError, match="контрольной цифры"):
            validate_box_number(wrong)


def test_hand_typed_box_number_tolerates_spaces_long_dash_and_missing_hyphen():
    assert validate_box_number(" 104 233—2 ") == "104233-2"
    assert validate_box_number("104233–2") == "104233-2"
    assert validate_box_number("1042332") == "104233-2"
    with pytest.raises(ProductionValidationError, match="не номер коробки"):
        validate_box_number("10423-2")


def test_erp_issues_box_numbers_below_supplymap_series():
    assert issue_box_number(1) == "000001-8"
    assert issue_box_number(99_999) == format_box_number(99_999)
    with pytest.raises(ProductionValidationError, match="исчерпана"):
        issue_box_number(100_000)


def test_confirmed_box_is_reserved_then_consumed_without_changing_number():
    now = datetime(2026, 9, 22, 9, 0)
    operator_id = uuid4()
    box = ProductionBox.create(
        task_id=uuid4(), operation_id=uuid4(), item="КБ-1",
        operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("10"))
    box = box.close("104233-2", "Оператор", now).confirm("Мастер", now)
    reserved = box.reserve(Decimal("4"), item="КБ-1", previous_position=1)
    consumed = reserved.consume(Decimal("4"))

    assert consumed.number == "104233-2"
    assert consumed.status is BoxStatus.CONFIRMED
    assert consumed.consumed == Decimal("4")
    assert consumed.available == Decimal("6")


def test_returned_box_keeps_number_when_operator_reopens_and_closes_again():
    now = datetime(2026, 9, 22, 9, 0)
    operator_id = uuid4()
    box = ProductionBox.create(
        task_id=uuid4(), operation_id=uuid4(), item="КБ-1",
        operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("2")).close("104233-2", "Оператор", now)
    returned = box.return_to_operator("Пересчитать количество")
    reopened = returned.reopen(operator_id)

    assert reopened.close("104233-2", "Оператор", now).number == "104233-2"
    with pytest.raises(ProductionValidationError, match="менять нельзя"):
        reopened.close(format_box_number(104234), "Оператор", now)


def test_work_session_counts_pause_with_sqlite_naive_datetime_and_can_cancel():
    started = datetime(2026, 9, 22, 9, 0)
    session = WorkSession.start(uuid4(), uuid4(), uuid4(), started)
    paused = session.pause(started + timedelta(minutes=2))
    resumed = paused.resume(datetime(2026, 9, 22, 9, 5, tzinfo=timezone.utc))

    assert resumed.pause_seconds == 180
    assert resumed.status is WorkStatus.ACTIVE
    assert resumed.cancel().status is WorkStatus.CANCELLED

