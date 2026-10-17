"""Код коробки для подписи маркером (владелец, 13.10.2026): проект·КБ·дата·код сотрудника·номер."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import AssignProductionCommand, assign_production
from app.modules.production.domain import ProductionValidationError
from app.modules.production.execution import (
    BoxCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    confirm_box,
    start_work,
    submit_work,
)
from app.modules.production.infrastructure import ProductionOperatorCodeRow
from app.modules.production.traceability import (
    box_code,
    box_code_key,
    box_code_kb,
    box_code_prefix,
    next_box_code,
    operator_short_code,
)
from tests.factories import Actor
from tests.production.test_execution_persistence import execution_setup


# --- формат -----------------------------------------------------------------------------------------------------

def test_code_reads_project_kb_date_worker_and_sequence():
    prefix = box_code_prefix("180К.11", "КБ1", date(2026, 10, 13), "Б07")
    assert box_code(prefix, 3) == "180К.11·КБ1·13.10·Б07·3"


@pytest.mark.parametrize(("item", "expected"), [
    ("КБ1", "КБ1"), ("КБ-1", "КБ1"), ("кб 12", "КБ12"), ("591K.3.КБ2", "КБ2"), ("KB4", "КБ4"),
    ("180К.11.Жгут А", "ЖгутА"), ("", "КБ"),
])
def test_kb_segment_is_short_and_without_the_project(item, expected):
    assert box_code_kb("180К.11", item) == expected


def test_code_needs_a_project_and_a_worker_code():
    with pytest.raises(ProductionValidationError):
        box_code_prefix(" ", "КБ1", date(2026, 10, 13), "Б07")
    with pytest.raises(ProductionValidationError):
        box_code_prefix("180К.11", "КБ1", date(2026, 10, 13), "")


def test_project_with_the_separator_does_not_break_the_segments():
    assert box_code_prefix("180·К 11", "КБ1", date(2026, 1, 2), "Б07") == "180.К11·КБ1·02.01·Б07"


def test_next_code_continues_after_the_largest_number_of_the_same_prefix():
    prefix = "180К.11·КБ1·13.10·Б07"
    assert next_box_code(prefix, []) == f"{prefix}·1"
    others = [f"{prefix}·1", f"{prefix}·4", "180К.11·КБ1·13.10·Б071·9", "180К.11·КБ1·14.10·Б07·7", f"{prefix}·x"]
    assert next_box_code(prefix, others) == f"{prefix}·5"


def test_search_key_ignores_case_spaces_separators_and_latin_lookalikes():
    code = "180К.11·КБ1·13.10·Б07·3"
    for typed in ("180к.11 кб1 13.10 б07 3", "180K11.KB1.1310.Б07.3", " 180К.11-КБ1-13.10-Б07-3 "):
        assert box_code_key(typed) == box_code_key(code)
    assert box_code_key("180К.11·КБ1·13.10·Б07·4") != box_code_key(code)


# --- код сотрудника ----------------------------------------------------------------------------------------------

def test_worker_code_takes_personnel_number_when_free():
    assert operator_short_code("Белов Иван", "7", []) == "Б07"
    assert operator_short_code("Белов Иван", "0123", []) == "Б123"


def test_worker_code_falls_back_to_the_smallest_free_number():
    assert operator_short_code("Белов Иван", "", []) == "Б01"
    assert operator_short_code("Борисов", "", ["Б01", "б02", "А03"]) == "Б03"
    # Табельный занят другим (или не из цифр) — свободный номер.
    assert operator_short_code("Быков", "7", ["Б07"]) == "Б01"
    assert operator_short_code("Быков", "ЦАР-7", []) == "Б01"


def test_worker_code_has_no_letters_read_as_digits():
    assert operator_short_code("Орлов", "", []) == "ОР01"
    assert operator_short_code("Зайцев", "5", []) == "ЗА05"
    assert operator_short_code("ёлкин", "", []) == "Е01"
    assert operator_short_code("Smith", "", []) == "С01"


# --- сдача -------------------------------------------------------------------------------------------------------

def _first_stage_submit(factory, operator, task, now, good):
    operation = task.operations[0]
    work = start_work(StartWorkCommand(task_id=task.id, operation_id=operation.id, box_numbers=(), actor=operator, now=now), factory())
    _session, box, _output, _defect = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=operator, now=now + timedelta(minutes=5),
        good_quantity=Decimal(good), defect_quantity=Decimal("0"),
    ), factory())
    return box


def test_submit_gives_each_box_a_unique_code_numbered_per_worker_and_day():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    first = _first_stage_submit(factory, operator, task, now, "3")
    second = _first_stage_submit(factory, operator, task, now + timedelta(hours=1), "2")
    next_day = _first_stage_submit(factory, operator, task, now + timedelta(days=1), "1")

    # «Оператор»: «О» от руки — как ноль, поэтому две буквы.
    assert first.code == "591K.3·КБ1·22.09·ОП01·1"
    assert second.code == "591K.3·КБ1·22.09·ОП01·2"
    assert next_day.code == "591K.3·КБ1·23.09·ОП01·1"
    with factory() as uow:
        assert uow.boxes.get(first.id).code == first.code
        assert [box.id for box in uow.boxes.by_code("591к.3 кб1 22.09 оп01 2")] == [second.id]
        assert {box.id for box in uow.boxes.search("22.09")} == {first.id, second.id}
        assert [box.id for box in uow.boxes.search(first.number)] == [first.id]
    with sessions() as session:
        assert session.get(ProductionOperatorCodeRow, operator.id).code == "ОП01"


def test_second_worker_gets_own_code_and_own_sequence():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    other_id = UUID("10000000-0000-0000-0000-000000000003")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=other_id, login="belov", email="belov@example.test", name="Белов Иван", personnel_number="7",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        session.commit()
    task, _ = assign_production(AssignProductionCommand(
        task_id=task.id, expected_version=task.version, operation_id=task.operations[0].id,
        operator_ids=(operator.id, other_id), requested_start=now, actor=master, now=now,
    ), factory())
    other = Actor(id=other_id, name="Белов Иван", department="Производство")

    mine = _first_stage_submit(factory, operator, task, now, "2")
    theirs = _first_stage_submit(factory, other, task, now, "2")

    assert mine.code.endswith("·ОП01·1")
    assert theirs.code == "591K.3·КБ1·22.09·Б07·1"


def test_moscow_date_is_used_for_an_aware_time():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    # 22.09 22:30 UTC — в Москве уже 23.09.
    late = datetime(2026, 9, 22, 22, 25, tzinfo=timezone.utc)
    box = _first_stage_submit(factory, operator, task, late, "1")
    assert "·23.09·" in box.code


def test_next_stage_takes_the_box_by_the_code_written_on_it():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box = _first_stage_submit(factory, operator, task, now, "4")
    confirm_box(BoxCommand(box.id, box.version, master, now), factory())

    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=task.operations[1].id,
        box_numbers=("591к.3 кб1 22.09 оп01 1", box.number), actor=operator, now=now + timedelta(hours=1),
    ), factory())

    with factory() as uow:
        assert [item.box_id for item in uow.work.inputs(work.id)] == [box.id]


def test_unknown_code_is_refused_with_a_hint():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    with pytest.raises(ProductionValidationError, match="нет такой коробки"):
        start_work(StartWorkCommand(
            task_id=task.id, operation_id=task.operations[1].id,
            box_numbers=("591K.3·КБ1·22.09·ОП01·9",), actor=operator, now=now,
        ), factory())


def test_database_refuses_two_boxes_with_one_code():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box = _first_stage_submit(factory, operator, task, now, "1")
    from dataclasses import replace
    with factory() as uow:
        uow.boxes.save(replace(source, code=box.code))
        with pytest.raises(IntegrityError):
            uow.commit()


def test_database_refuses_two_workers_with_one_code():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    _first_stage_submit(factory, operator, task, now, "1")
    other_id = UUID("10000000-0000-0000-0000-000000000004")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=other_id, login="opt", email="opt@example.test", name="Оптов",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        session.commit()
    with sessions() as session:
        session.add(ProductionOperatorCodeRow(operator_id=other_id, code="ОП01"))
        with pytest.raises(IntegrityError):
            session.commit()
