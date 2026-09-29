"""PX-1: correcting a returned box replaces one output (SUPPLYMAP `prodWorkFix_`)."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.modules.production.domain import ProductionValidationError
from app.modules.production.execution import (
    BoxCommand,
    CorrectBoxCommand,
    DecideDefectCommand,
    HandoffBoxCommand,
    ReturnBoxCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    cancel_work,
    confirm_box,
    correct_box,
    decide_defect,
    handoff_box,
    return_box,
    start_work,
    submit_work,
)
from app.modules.production.infrastructure import (
    ProductionConsumptionRow,
    ProductionMaterialConsumptionRow,
    ProductionOutputRow,
)
from app.modules.production.quality import InspectBoxCommand, inspect_box
from app.modules.production.traceability import (
    BoxStatus,
    DefectDisposition,
    DefectStatus,
    MaterialInput,
    ProductionBox,
    WorkInput,
    format_box_number,
)
from app.modules.shipping.domain import FinishedGood
from tests.factories import Actor
from tests.production.test_execution_persistence import execution_setup
from tests.production.test_materials_finished_goods import stage_three_setup


def _submit(factory, operator, task, now, *, good, defect="0", inputs=(), materials=(), position=2, **defect_text):
    operation = task.operations[position - 1]
    with factory() as uow:
        numbers = tuple(uow.boxes.get(item.box_id).number for item in inputs)
    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=operation.id, box_numbers=numbers,
        materials=tuple(materials), actor=operator, now=now,
    ), factory())
    _completed, box, output, defect_record = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=operator, now=now + timedelta(minutes=5),
        good_quantity=Decimal(good), defect_quantity=Decimal(defect), **defect_text,
    ), factory())
    return box, output, defect_record


def _close_and_return(factory, master, operator, box, now):
    return return_box(ReturnBoxCommand(
        box_id=box.id, expected_version=box.version, actor=master, now=now, reason="Пересчитать",
    ), factory())


def _correct(factory, actor, box, output, now, *, good, defect="0", **defect_text):
    return correct_box(CorrectBoxCommand(
        box_id=box.id, expected_version=box.version, actor=actor, now=now,
        output_id=output.id, good_quantity=Decimal(good), defect_quantity=Decimal(defect), **defect_text,
    ), factory())


def _second_source(factory, master, operator, task, now):
    source = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now + timedelta(minutes=1),
    ).add_output(Decimal("10")).close(format_box_number(104235), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(source)
        uow.commit()
    return source


def _transferred(sessions, source_id, target_id) -> Decimal:
    with sessions() as session:
        value = session.scalar(select(func.coalesce(func.sum(ProductionConsumptionRow.quantity), 0)).where(
            ProductionConsumptionRow.source_box_id == source_id,
            ProductionConsumptionRow.target_box_id == target_id,
        ))
    return Decimal(value)


def _count(sessions, row) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(row))


def test_decrease_gives_pieces_back_to_the_input_box_and_drops_replaced_output_from_sums():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="10", inputs=(WorkInput(source.id, Decimal("10")),))
    returned = _close_and_return(factory, master, operator, box, now)

    corrected, correction, defect = _correct(factory, operator, returned, output, now, good="8")

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        stored_box = uow.boxes.get(box.id)
        total = uow.outputs.total_for_operation(task.operations[1].id)
        active = uow.outputs.active_for_box(box.id)
    assert corrected.quantity == stored_box.quantity == Decimal("8")
    assert stored_box.status is BoxStatus.OPERATOR_CLOSED
    assert stored_box.number == returned.number
    assert correction.correction_of_id == output.id
    assert correction.session_id is None
    assert defect is None
    assert stored_source.consumed == Decimal("8")
    assert stored_source.status is BoxStatus.CONFIRMED
    assert stored_source.available == Decimal("2")
    assert _transferred(sessions, source.id, box.id) == Decimal("8")
    assert total == Decimal("8")
    assert [item.id for item in active] == [correction.id]
    assert _count(sessions, ProductionOutputRow) == 2
    engine.dispose()


def test_increase_takes_the_difference_from_the_same_input_box_or_fails_whole():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="8", inputs=(WorkInput(source.id, Decimal("8")),))
    returned = _close_and_return(factory, master, operator, box, now)
    with factory() as uow:  # последнюю штуку первой коробки забрало другое задание
        stored = uow.boxes.get(source.id)
        uow.boxes.save(replace(stored, consumed=Decimal("9"), version=stored.version + 1))
        uow.commit()

    with pytest.raises(ProductionValidationError, match="Больше, чем в задании"):
        _correct(factory, operator, returned, output, now, good="11")
    with pytest.raises(ProductionValidationError, match="Добрать 2 шт не из чего: во входных коробках 1 шт"):
        _correct(factory, operator, returned, output, now, good="10")
    with factory() as uow:
        untouched = uow.boxes.get(box.id)
        untouched_source = uow.boxes.get(source.id)
    assert untouched.status is BoxStatus.RETURNED
    assert untouched.version == returned.version
    assert untouched_source.consumed == Decimal("9")
    assert _count(sessions, ProductionOutputRow) == 1

    corrected, _correction, _ = _correct(factory, operator, returned, output, now, good="9")
    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
    assert corrected.quantity == Decimal("9")
    assert stored_source.consumed == Decimal("10")
    assert stored_source.status is BoxStatus.CONSUMED
    assert _transferred(sessions, source.id, box.id) == Decimal("9")
    engine.dispose()

def test_repeated_correction_targets_the_latest_output_and_counts_once():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="10", inputs=(WorkInput(source.id, Decimal("10")),))
    returned = _close_and_return(factory, master, operator, box, now)
    first_box, first, _ = _correct(factory, operator, returned, output, now, good="8")
    returned_again = _close_and_return(factory, master, operator, first_box, now)

    with pytest.raises(ProductionValidationError, match="уже исправлена"):
        _correct(factory, operator, returned_again, output, now, good="9")
    corrected, second, _ = _correct(factory, operator, returned_again, first, now, good="9")

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        total = uow.outputs.total_for_operation(task.operations[1].id)
        active = uow.outputs.active_for_box(box.id)
        lineage = uow.outputs.lineage(second.id)
    assert corrected.quantity == Decimal("9")
    assert second.correction_of_id == first.id
    assert stored_source.consumed == Decimal("9")
    assert stored_source.available == Decimal("1")
    assert total == Decimal("9")
    assert [item.id for item in active] == [second.id]
    assert lineage == (second.id, first.id, output.id)
    engine.dispose()


def test_same_quantity_moves_nothing_and_hands_the_box_back():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="10", inputs=(WorkInput(source.id, Decimal("10")),))
    returned = _close_and_return(factory, master, operator, box, now)

    corrected, _correction, _ = _correct(factory, operator, returned, output, now, good="10")

    assert corrected.quantity == Decimal("10")
    assert corrected.status is BoxStatus.OPERATOR_CLOSED
    assert _count(sessions, ProductionConsumptionRow) == 1
    engine.dispose()


def test_each_submission_is_its_own_box_and_a_correction_moves_only_its_inputs():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    other = _second_source(factory, master, operator, task, now)
    first_box, _first_output, _ = _submit(factory, operator, task, now, good="6", inputs=(WorkInput(source.id, Decimal("6")),))
    box, second_output, _ = _submit(factory, operator, task, now, good="4", inputs=(WorkInput(other.id, Decimal("4")),))
    # prodWorkSubmit_: одна сдача — одна коробка, сразу «Закрыта оператором».
    assert first_box.id != box.id
    assert (first_box.quantity, box.quantity) == (Decimal("6"), Decimal("4"))
    assert first_box.status is box.status is BoxStatus.OPERATOR_CLOSED
    returned = _close_and_return(factory, master, operator, box, now)

    corrected, _correction, _ = _correct(factory, operator, returned, second_output, now, good="1")

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        stored_other = uow.boxes.get(other.id)
        active = uow.outputs.active_for_box(box.id)
    assert corrected.quantity == Decimal("1")
    assert stored_source.consumed == Decimal("6")
    assert stored_other.consumed == Decimal("1")
    assert {item.id for item in active} == {_correction.id}
    engine.dispose()

def test_give_back_returns_to_the_latest_input_box_first():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    other = _second_source(factory, master, operator, task, now)
    box, output, _ = _submit(factory, operator, task, now, good="10", inputs=(
        WorkInput(source.id, Decimal("5")), WorkInput(other.id, Decimal("5")),
    ))
    returned = _close_and_return(factory, master, operator, box, now)

    _correct(factory, operator, returned, output, now, good="3")

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        stored_other = uow.boxes.get(other.id)
    assert stored_other.consumed == Decimal("0")
    assert stored_source.consumed == Decimal("3")
    assert _transferred(sessions, other.id, box.id) == Decimal("0")
    engine.dispose()


def test_correction_is_refused_without_writing_anything():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="6", inputs=(WorkInput(source.id, Decimal("6")),))
    closed = box
    other_box, other_output, _ = _submit(factory, operator, task, now, good="4", inputs=(WorkInput(source.id, Decimal("4")),))

    with pytest.raises(ProductionValidationError, match="не возвращена мастером"):
        _correct(factory, operator, closed, output, now, good="5")
    returned = _close_and_return(factory, master, operator, closed, now)
    stranger = Actor(name="Чужой", department="Производство")
    with pytest.raises(ProductionValidationError, match="сдавал другой оператор"):
        _correct(factory, stranger, returned, output, now, good="5")
    with pytest.raises(ProductionValidationError, match="изменилась после загрузки"):
        correct_box(CorrectBoxCommand(
            box_id=returned.id, expected_version=returned.version - 1, actor=operator, now=now,
            output_id=output.id, good_quantity=Decimal("5"), defect_quantity=Decimal("0"),
        ), factory())
    with pytest.raises(ProductionValidationError, match="не относится к этой коробке"):
        _correct(factory, operator, returned, other_output, now, good="5")
    with pytest.raises(ProductionValidationError, match="не относится к этой коробке"):
        correct_box(CorrectBoxCommand(
            box_id=returned.id, expected_version=returned.version, actor=operator, now=now,
            output_id=uuid4(), good_quantity=Decimal("5"), defect_quantity=Decimal("0"),
        ), factory())
    with pytest.raises(ProductionValidationError, match="должна быть положительной"):
        _correct(factory, operator, returned, output, now, good="0")

    with factory() as uow:
        stored = uow.boxes.get(returned.id)
        stored_source = uow.boxes.get(source.id)
    assert stored.status is BoxStatus.RETURNED
    assert stored.version == returned.version
    assert stored.quantity == Decimal("6")
    assert stored_source.consumed == Decimal("10")
    assert _count(sessions, ProductionOutputRow) == 2
    assert other_box.id != box.id
    engine.dispose()


def test_defect_found_on_recount_opens_a_new_defect_record_without_moving_inputs():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, _ = _submit(factory, operator, task, now, good="10", inputs=(WorkInput(source.id, Decimal("10")),))
    returned = _close_and_return(factory, master, operator, box, now)

    with pytest.raises(ProductionValidationError, match="характер и описание"):
        _correct(factory, operator, returned, output, now, good="8", defect="2")
    corrected, correction, defect = _correct(
        factory, operator, returned, output, now, good="8", defect="2",
        defect_character="Обрыв", defect_description="Жила оборвана у контакта",
    )

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        total = uow.outputs.total_for_operation(task.operations[1].id)
    assert corrected.quantity == Decimal("8")
    assert defect.output_id == correction.id
    assert defect.quantity == Decimal("2")
    assert defect.number.startswith("Б-")
    assert stored_source.consumed == Decimal("10")
    assert _count(sessions, ProductionConsumptionRow) == 1
    assert total == Decimal("10")
    engine.dispose()


def test_changed_defect_replaces_the_old_record_which_keeps_its_quantity_and_decision():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, original = _submit(
        factory, operator, task, now, good="8", defect="2", inputs=(WorkInput(source.id, Decimal("10")),),
        defect_character="Обрыв", defect_description="Жила оборвана",
    )
    returned = _close_and_return(factory, master, operator, box, now)

    same_box, same, unchanged = _correct(factory, operator, returned, output, now, good="7", defect="2")
    assert unchanged is None
    returned = _close_and_return(factory, master, operator, same_box, now)
    more_box, more, replacement = _correct(
        factory, operator, returned, same, now, good="7", defect="3",
        defect_character="Обрыв", defect_description="Ещё одна жила",
    )
    returned = _close_and_return(factory, master, operator, more_box, now)
    _none_box, none, removed = _correct(factory, operator, returned, more, now, good="10", defect="0")

    with factory() as uow:
        first = uow.defects.get(original.id)
        second = uow.defects.get(replacement.id)
        stored_source = uow.boxes.get(source.id)
    assert removed is None
    assert first.replaced_by_output_id == more.id
    assert first.quantity == Decimal("2")
    assert first.status is DefectStatus.PENDING
    assert second.quantity == Decimal("3")
    assert second.replaced_by_output_id == none.id
    assert stored_source.consumed == Decimal("10")
    with pytest.raises(ProductionValidationError, match="заменена исправлением"):
        decide_defect(DecideDefectCommand(
            defect_id=first.id, expected_version=first.version,
            disposition=DefectDisposition.SCRAP, act_reference="Акт НС-18",
            actor=master, now=now,
        ), factory())
    engine.dispose()


def test_decided_defect_is_marked_replaced_but_keeps_its_decision():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    box, output, original = _submit(
        factory, operator, task, now, good="8", defect="2", inputs=(WorkInput(source.id, Decimal("10")),),
        defect_character="Обрыв", defect_description="Жила оборвана",
    )
    decide_defect(DecideDefectCommand(
        defect_id=original.id, expected_version=original.version,
        disposition=DefectDisposition.SCRAP, act_reference="Акт НС-17", actor=master, now=now,
    ), factory())
    returned = _close_and_return(factory, master, operator, box, now)

    _box, correction, _ = _correct(factory, operator, returned, output, now, good="9", defect="1",
                                   defect_character="Обрыв", defect_description="Одна жила")

    with factory() as uow:
        first = uow.defects.get(original.id)
    assert first.status is DefectStatus.DECIDED
    assert first.quantity == Decimal("2")
    assert first.replaced_by_output_id == correction.id
    engine.dispose()


def test_first_operation_limit_counts_only_active_outputs_and_materials_stay_consumed():
    engine, sessions, factory, master, operator, task, line_id, now = stage_three_setup(fulfillment=True)
    box, output, _ = _submit(
        factory, operator, task, now, good="10", materials=(MaterialInput(line_id, Decimal("4")),), position=1,
    )
    returned = _close_and_return(factory, master, operator, box, now)

    with pytest.raises(ProductionValidationError, match="Больше, чем в задании"):
        _correct(factory, operator, returned, output, now, good="11")
    _correct(factory, operator, returned, output, now, good="6")
    with factory() as uow:
        material = uow.materials.source(task.fulfillment_request_id, line_id)
    assert material.consumed == Decimal("4")
    assert _count(sessions, ProductionMaterialConsumptionRow) == 1

    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=task.operations[0].id, box_numbers=(), actor=operator, now=now,
        materials=(MaterialInput(line_id, Decimal("3")),),
    ), factory())
    with pytest.raises(ProductionValidationError, match="Больше, чем в задании"):
        submit_work(SubmitWorkCommand(
            session_id=work.id, actor=operator, now=now,
            good_quantity=Decimal("5"), defect_quantity=Decimal("0"),
        ), factory())
    _completed, new_box, _output, _defect = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=operator, now=now,
        good_quantity=Decimal("4"), defect_quantity=Decimal("0"),
    ), factory())
    assert new_box.id != box.id
    with factory() as uow:
        assert uow.outputs.total_for_operation(task.operations[0].id) == Decimal("10")
    engine.dispose()


def test_decreased_box_reaches_finished_goods_with_the_corrected_quantity():
    engine, _sessions, factory, master, operator, task, line_id, now = stage_three_setup(fulfillment=True)
    box, output, _ = _submit(
        factory, operator, task, now, good="10", materials=(MaterialInput(line_id, Decimal("4")),), position=1,
    )
    returned = _close_and_return(factory, master, operator, box, now)
    corrected, _correction, _ = _correct(factory, operator, returned, output, now, good="6")
    confirmed = confirm_box(BoxCommand(corrected.id, corrected.version, master, now), factory())
    good = FinishedGood.create(
        project="591K.3", item_index="КБ1", customer="Заказчик",
        article="", name="Жгут", ordered=Decimal("10"), unit="шт",
    )
    with factory() as uow:
        uow.goods.add(good)
        uow.commit()
    inspected, _inspection, _serials = inspect_box(InspectBoxCommand(
        box_id=confirmed.id, expected_version=confirmed.version, accepted=6, rejected=0,
        defect_character="", defect_description="", instrument_ids=(), actor=master, now=now,
    ), factory())

    _handed, accepted, receipt = handoff_box(HandoffBoxCommand(
        box_id=inspected.id, expected_version=inspected.version, actor=master,
        now=now, finished_good_id=good.id,
    ), factory())

    assert accepted.accepted == Decimal("6")
    assert receipt.quantity == Decimal("6")
    engine.dispose()


def test_top_up_may_use_an_input_box_everything_was_given_back_to():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    other = _second_source(factory, master, operator, task, now)
    with factory() as uow:  # шесть штук первой коробки уже ушли в другое задание
        stored = uow.boxes.get(source.id)
        uow.boxes.save(replace(stored, consumed=Decimal("6"), version=stored.version + 1))
        uow.commit()
    # Годные + брак берутся по порядку ввода: 4 из первой коробки, 4 из второй (prodPick_).
    box, output, _ = _submit(factory, operator, task, now, good="8", inputs=(
        WorkInput(source.id, Decimal("4")), WorkInput(other.id, Decimal("4")),
    ))
    assert _transferred(sessions, source.id, box.id) == Decimal("4")
    assert _transferred(sessions, other.id, box.id) == Decimal("4")
    returned = _close_and_return(factory, master, operator, box, now)
    down_box, down, _ = _correct(factory, operator, returned, output, now, good="4")
    assert _transferred(sessions, other.id, box.id) == Decimal("0")
    returned = _close_and_return(factory, master, operator, down_box, now)

    corrected, _correction, _ = _correct(factory, operator, returned, down, now, good="6")

    with factory() as uow:
        stored_other = uow.boxes.get(other.id)
    assert corrected.quantity == Decimal("6")
    assert stored_other.consumed == Decimal("2")
    assert _transferred(sessions, other.id, box.id) == Decimal("2")
    engine.dispose()
