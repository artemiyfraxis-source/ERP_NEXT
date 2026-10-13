"""Одно подтверждение коробки — ОТК (владелец, 13.10.2026: «ОТК подтверждает и все»).

ОТК проверяет только выход финальной операции. Коробка остальных операций годится на вход следующей сразу после
«Сдать»; коробка финальной ждёт ОТК, и только его приёмка делает её «Подтверждена» (путь на склад ГП). «Вернуть…» —
действие ОТК; подтверждённые мастером до 13.10 и просто сданные старые коробки продолжают путь без переделки данных.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.production.domain import ProductionValidationError, link_inputs
from app.modules.production.execution import (
    BoxCommand,
    CorrectBoxCommand,
    HandoffBoxCommand,
    ReturnBoxCommand,
    confirm_box,
    correct_box,
    handoff_box,
    return_box,
)
from app.modules.production.quality import InspectBoxCommand, inspect_box
from app.modules.production.traceability import BoxStatus, ProductionBox, format_box_number
from app.modules.shipping.domain import FinishedGood
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_network_execution import NOW, OPERATOR, _op, _start, _submit, network_shop

QC = Actor(name="Контролёр", department="ОТК")


def chain_shop():
    """Нарезка → Сборка (финал, её проверяет ОТК)."""
    shop = network_shop(link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    assert shop.task.final_operation == shop.operation(2)
    return shop


def _handed(shop, position: int, good: str, *inputs: ProductionBox) -> ProductionBox:
    box, _output = _submit(shop, _start(shop, position, *inputs), good)
    return box


def _inspect(shop, box: ProductionBox, accepted: int, rejected: int = 0):
    return inspect_box(InspectBoxCommand(
        box_id=box.id, expected_version=box.version, accepted=accepted, rejected=rejected,
        defect_character="Обрыв" if rejected else "", defect_description="Нет контакта" if rejected else "",
        instrument_ids=(), actor=QC, now=NOW + timedelta(hours=1),
    ), shop.factory())


def _events(shop, box_id) -> list[str]:
    with Session(shop.engine) as session:
        return [row.event_type for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == box_id).order_by(AuditEventRow.occurred_at),
        )]


def test_a_handed_box_of_an_operation_without_qc_feeds_the_next_operation_at_once():
    shop = chain_shop()
    cut = _handed(shop, 1, "5")
    assert cut.status is BoxStatus.OPERATOR_CLOSED and cut.confirmed_at is None

    assembly = _handed(shop, 2, "5", cut)

    assert shop.box(cut).status is BoxStatus.CONSUMED
    assert assembly.status is BoxStatus.OPERATOR_CLOSED
    with pytest.raises(ProductionValidationError, match="брать можно только сданную"):
        _start(shop, 2, shop.box(cut))  # израсходованную второй раз не взять
    assert "production.box_confirmed" not in _events(shop, cut.id)
    shop.engine.dispose()


def test_the_final_box_waits_for_qc_and_qc_acceptance_is_its_confirmation():
    shop = chain_shop()
    good = FinishedGood.create(
        project="591K.3", item_index="КБ1", customer="Заказчик", article="", name="Жгут",
        ordered=Decimal("5"), unit="шт",
    )
    with shop.factory() as uow:
        uow.goods.add(good)
        uow.commit()
    assembly = _handed(shop, 2, "5", _handed(shop, 1, "5"))

    with pytest.raises(ProductionValidationError, match="должен принять ОТК"):
        handoff_box(HandoffBoxCommand(
            box_id=assembly.id, expected_version=assembly.version, actor=OPERATOR, now=NOW, finished_good_id=good.id,
        ), shop.factory())
    accepted, inspection, _serials = _inspect(shop, assembly, accepted=4, rejected=1)

    assert accepted.status is BoxStatus.CONFIRMED
    assert (accepted.confirmed_by, accepted.confirmed_at) == ("Контролёр", NOW + timedelta(hours=1))
    assert (accepted.rejected, accepted.available) == (Decimal(1), Decimal(4))
    assert inspection.defect_number
    assert _events(shop, assembly.id) == ["production.qc_accepted"]
    with pytest.raises(ProductionValidationError, match="уже принята ОТК"):
        _inspect(shop, accepted, accepted=4)
    handed, stocked, _receipt = handoff_box(HandoffBoxCommand(
        box_id=accepted.id, expected_version=accepted.version, actor=OPERATOR, now=NOW, finished_good_id=good.id,
    ), shop.factory())
    assert handed.status is BoxStatus.CONSUMED and stocked.accepted == Decimal(4)
    shop.engine.dispose()


def test_qc_returns_the_box_to_its_fitter_and_the_corrected_box_is_back_in_the_qc_queue():
    shop = chain_shop()
    assembly = _handed(shop, 2, "5", _handed(shop, 1, "5"))

    returned = return_box(ReturnBoxCommand(
        box_id=assembly.id, expected_version=assembly.version, actor=QC, now=NOW, reason="Пересчитать",
    ), shop.factory())
    assert (returned.status, returned.return_reason) == (BoxStatus.RETURNED, "Пересчитать")
    with pytest.raises(ProductionValidationError, match="ОТК принимает только сданную коробку"):
        _inspect(shop, returned, accepted=5)

    with shop.factory() as uow:
        output = uow.outputs.active_for_box(returned.id)[0]
    corrected, _output, _defect = correct_box(CorrectBoxCommand(
        box_id=returned.id, expected_version=returned.version, actor=OPERATOR, now=NOW,
        output_id=output.id, good_quantity=Decimal(4), defect_quantity=Decimal(0),
    ), shop.factory())
    assert corrected.status is BoxStatus.OPERATOR_CLOSED
    accepted, _inspection, _serials = _inspect(shop, corrected, accepted=4)
    assert accepted.status is BoxStatus.CONFIRMED
    assert _events(shop, assembly.id) == ["production.box_returned", "production.box_corrected", "production.qc_accepted"]
    shop.engine.dispose()


def test_a_taken_or_accepted_box_cannot_be_returned():
    shop = chain_shop()
    cut = _handed(shop, 1, "5")
    assembly = _handed(shop, 2, "5", cut)
    taken = shop.box(cut)
    with pytest.raises(ProductionValidationError):
        return_box(ReturnBoxCommand(
            box_id=taken.id, expected_version=taken.version, actor=QC, now=NOW, reason="Пересчитать",
        ), shop.factory())

    accepted, _inspection, _serials = _inspect(shop, assembly, accepted=5)
    with pytest.raises(ProductionValidationError, match="уже принята ОТК"):
        return_box(ReturnBoxCommand(
            box_id=accepted.id, expected_version=accepted.version, actor=QC, now=NOW, reason="Пересчитать",
        ), shop.factory())
    shop.engine.dispose()


def test_legacy_master_confirmed_boxes_still_flow_and_keep_their_mark():
    shop = chain_shop()
    # До 13.10: мастер подтвердил коробку нарезки и коробку сборки, ОТК сборку ещё не проверял.
    cut = _handed(shop, 1, "5")
    cut = confirm_box(BoxCommand(cut.id, cut.version, Actor(name="Мастер", department="Производство"), NOW), shop.factory())
    assembly = _handed(shop, 2, "5", cut)
    legacy = confirm_box(BoxCommand(assembly.id, assembly.version, Actor(name="Мастер", department="Производство"), NOW), shop.factory())

    accepted, _inspection, _serials = _inspect(shop, legacy, accepted=5)

    assert shop.box(cut).status is BoxStatus.CONSUMED
    assert accepted.status is BoxStatus.CONFIRMED
    assert (accepted.confirmed_by, accepted.confirmed_at) == ("Мастер", NOW)
    shop.engine.dispose()


def test_qc_acceptance_needs_a_handed_untouched_box():
    box = ProductionBox.create(
        task_id=OPERATOR.id, operation_id=OPERATOR.id, item="КБ1", operation_position=2,
        operator_id=OPERATOR.id, created_at=NOW,
    ).add_output(Decimal("5"))
    with pytest.raises(ProductionValidationError, match="ОТК принимает только сданную коробку"):
        box.qc_accept(Decimal(0), "Контролёр", NOW)  # открытая
    closed = box.close(format_box_number(104400), "Оператор", NOW)
    with pytest.raises(ProductionValidationError, match="превышает"):
        closed.qc_accept(Decimal(6), "Контролёр", NOW)
    scrapped = closed.qc_accept(Decimal(5), "Контролёр", NOW)
    assert scrapped.status is BoxStatus.CONSUMED and scrapped.available == 0
    with pytest.raises(ProductionValidationError, match="уже брали"):
        closed.reserve(Decimal(1), item="КБ1", previous_position=2).qc_accept(Decimal(0), "Контролёр", NOW)
