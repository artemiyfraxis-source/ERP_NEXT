from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.traceability import (
    BoxStatus,
    DefectRecord,
    DefectStatus,
    ProductionBox,
    WorkInput,
    WorkSession,
    WorkStatus,
    box_check_digit,
    format_box_number,
    give_back_inputs,
    issue_box_number,
    pick_inputs,
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



def _returned_box(operator_id, good="10", now=datetime(2026, 9, 22, 9, 0)):
    return ProductionBox.create(
        task_id=uuid4(), operation_id=uuid4(), item="КБ-1",
        operation_position=2, operator_id=operator_id, created_at=now,
    ).add_output(Decimal(good)).close("104233-2", "Оператор", now).return_to_operator("Пересчитать")


def _confirmed_box(quantity="10", consumed="0", now=datetime(2026, 9, 22, 9, 0)):
    box = ProductionBox.create(
        task_id=uuid4(), operation_id=uuid4(), item="КБ-1",
        operation_position=1, operator_id=uuid4(), created_at=now,
    ).add_output(Decimal(quantity)).close(format_box_number(104234), "Оператор", now).confirm("Мастер", now)
    if Decimal(consumed):
        box = box.reserve(Decimal(consumed), item="КБ-1", previous_position=1).consume(Decimal(consumed))
    return box


def test_correction_replaces_output_quantity_and_hands_box_back_to_master():
    operator_id = uuid4()
    now = datetime(2026, 9, 22, 10, 0)
    returned = _returned_box(operator_id)

    down = returned.correct(replaced_good=Decimal("10"), good=Decimal("8"), operator_id=operator_id, actor="Оператор", now=now)
    up = returned.correct(replaced_good=Decimal("10"), good=Decimal("12"), operator_id=operator_id, actor="Оператор", now=now)
    empty = returned.correct(replaced_good=Decimal("10"), good=Decimal("0"), operator_id=operator_id, actor="Оператор", now=now)

    assert down.quantity == Decimal("8")
    assert down.status is BoxStatus.OPERATOR_CLOSED
    assert down.number == "104233-2"
    assert down.return_reason == ""
    assert down.closed_at == now
    assert up.quantity == Decimal("12")
    assert empty.quantity == Decimal("0")
    assert empty.status is BoxStatus.OPERATOR_CLOSED


def test_only_the_operator_of_a_returned_box_may_correct_it():
    operator_id = uuid4()
    now = datetime(2026, 9, 22, 10, 0)
    returned = _returned_box(operator_id)
    closed = returned.correct(replaced_good=Decimal("10"), good=Decimal("8"), operator_id=operator_id, actor="Оператор", now=now)

    with pytest.raises(ProductionValidationError, match="не возвращена мастером"):
        closed.correct(replaced_good=Decimal("8"), good=Decimal("7"), operator_id=operator_id, actor="Оператор", now=now)
    with pytest.raises(ProductionValidationError, match="сдавал другой оператор"):
        returned.correct(replaced_good=Decimal("10"), good=Decimal("8"), operator_id=uuid4(), actor="Чужой", now=now)
    with pytest.raises(ProductionValidationError, match="не возвращена мастером"):
        returned.reopen(operator_id).correct(replaced_good=Decimal("10"), good=Decimal("8"), operator_id=operator_id, actor="Оператор", now=now)


def test_give_back_undoes_consumption_and_revives_an_emptied_box():
    emptied = _confirmed_box(consumed="10")
    assert emptied.status is BoxStatus.CONSUMED

    revived = emptied.give_back(Decimal("2"))

    assert revived.status is BoxStatus.CONFIRMED
    assert revived.consumed == Decimal("8")
    assert revived.available == Decimal("2")
    with pytest.raises(ProductionValidationError, match="больше расхода"):
        revived.give_back(Decimal("9"))


def test_pick_inputs_takes_in_order_from_confirmed_boxes_only():
    first = _confirmed_box(consumed="9")
    second = _confirmed_box()
    emptied = _confirmed_box(consumed="10")

    taken = pick_inputs((emptied, first, second), Decimal("3"))

    assert taken == (WorkInput(first.id, Decimal("1")), WorkInput(second.id, Decimal("2")))
    with pytest.raises(ProductionValidationError, match="Добрать 12 шт не из чего: во входных коробках 11 шт"):
        pick_inputs((emptied, first, second), Decimal("12"))


def test_give_back_inputs_returns_to_the_latest_taken_box_first():
    first, second = uuid4(), uuid4()
    taken = (WorkInput(first, Decimal("6")), WorkInput(second, Decimal("4")))

    assert give_back_inputs(taken, Decimal("5")) == (WorkInput(second, Decimal("4")), WorkInput(first, Decimal("1")))
    with pytest.raises(ProductionValidationError, match="некуда"):
        give_back_inputs(taken, Decimal("11"))


def test_replaced_defect_keeps_quantity_and_status_but_cannot_be_decided():
    now = datetime(2026, 9, 22, 10, 0)
    defect = DefectRecord.create(
        number="000001-8", output_id=uuid4(), source_box_id=uuid4(), task_id=uuid4(),
        operation_id=uuid4(), operator_id=uuid4(), quantity=Decimal("2"),
        character="Обрыв", description="Жила оборвана", created_at=now,
    )
    correction_id = uuid4()

    replaced = defect.mark_replaced(correction_id)

    assert replaced.replaced_by_output_id == correction_id
    assert replaced.quantity == Decimal("2")
    assert replaced.status is DefectStatus.PENDING
    assert replaced.version == defect.version + 1
    with pytest.raises(ProductionValidationError, match="уже заменена"):
        replaced.mark_replaced(uuid4())
    with pytest.raises(ProductionValidationError, match="заменена исправлением"):
        replaced.decide()
    decided = defect.decide().mark_replaced(correction_id)
    assert decided.status is DefectStatus.DECIDED
