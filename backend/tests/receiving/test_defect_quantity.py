"""КОЛ-ВО БРАКА (owner 24.10.2026): how many of the fact are defective — a record for «Брак, ₽», never a stock move."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.receiving.application import (
    InspectReceivingCommand,
    OpenVkRevisionCommand,
    PlaceReceivingCommand,
    build_act_print_sheet,
    consume_receiving_expected,
    inspect_receiving,
    open_vk_revision,
    place_receiving,
    return_to_supplier,
)
from app.modules.receiving.domain import (
    MECHANICAL_DAMAGE,
    ReceivingStatus,
    ReceivingValidationError,
    inspect_delivery,
)
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.warehouse.application import dispatch_next_warehouse_received
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.receiving.test_delivery import deliver, expect, item, lines_now
from tests.receiving.test_supplier_return import (  # noqa: F401 — fixtures
    NOW,
    RECEIVER,
    accept_and_stock,
    api,
    command,
    erp,
    expected_message,
    inspection,
)

BRAK = (MECHANICAL_DAMAGE,)


def brak(fact: str = "10", defect: str | None = "3", **values):
    return replace(
        inspection(fact), manual_nonconformities=BRAK, defect_quantity=Decimal(defect) if defect else None, **values
    )


def run(receiving, line, data):
    return inspect_receiving(
        InspectReceivingCommand(line_id=line.id, expected_version=line.version, inspection=data, actor=RECEIVER, now=NOW),
        receiving(),
    )


# ---- the rule ------------------------------------------------------------------------------------------------------

def test_brak_needs_its_quantity_within_the_fact():
    assert inspect_delivery(brak(defect="3")).defect_quantity == Decimal("3")
    assert inspect_delivery(brak(defect="10")).defect_quantity == Decimal("10")  # the whole line
    with pytest.raises(ReceivingValidationError, match="укажите количество брака"):
        inspect_delivery(brak(defect=None))
    for wrong in ("0", "-1", "10.5"):
        with pytest.raises(ReceivingValidationError, match="больше нуля и не больше фактического"):
            inspect_delivery(brak(defect=wrong))


def test_no_brak_no_quantity():
    assert inspect_delivery(inspection()).defect_quantity is None
    wrong_item = replace(inspection(), manual_nonconformities=("Не тот товар (несоответствие счету)",))
    assert inspect_delivery(wrong_item).defect_quantity is None
    with pytest.raises(ReceivingValidationError, match="брак не отмечен"):
        inspect_delivery(replace(inspection(), defect_quantity=Decimal("2")))


def test_the_quantity_is_a_record_the_held_line_keeps_its_whole_fact(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    waiting = run(receiving, line, brak(defect="3"))
    assert waiting.status is ReceivingStatus.WAITING
    assert waiting.defect_quantity == Decimal("3")
    assert waiting.accepted_quantity == waiting.fact_quantity == Decimal("10")  # not 7: acceptance is unchanged
    with receiving() as uow:
        details = uow.session.scalar(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "receiving.inspected")
        )
    assert details["defect_quantity"] == "3"  # the journal keeps each count; a re-inspection overwrites the line

    # The technologist clears the brak on a re-inspection: the quantity goes with it, the stock gets the whole fact.
    released = run(receiving, waiting, inspection())
    assert released.status is ReceivingStatus.ACCEPTED and released.defect_quantity is None
    placed = place_receiving(
        PlaceReceivingCommand(line_id=released.id, expected_version=released.version, storage_location="1F4",
                              actor=RECEIVER, now=NOW),
        receiving(),
    )
    assert placed.warehouse_event_emitted


def test_stock_is_the_same_with_or_without_a_defect_count(erp):
    receiving, warehouse, _ = erp
    counted, plain = expect(receiving, 1, 2)
    held = deliver(receiving, [item(counted, deviation="brak", deviation_quantity=Decimal("4"))]).lines[0]
    assert held.defect_quantity == Decimal("4") and held.accepted_quantity == Decimal("10")
    with receiving() as uow:  # a held brak line hands nothing to the warehouse
        assert uow.session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == "warehouse.received")).all() == []
    # Released by a later delivery: the lot gets the full 10, the same as a line never marked brak.
    deliver(receiving, [item(held, storage_location="1F4")])
    deliver(receiving, [item(plain, storage_location="1F5")])
    lots = [dispatch_next_warehouse_received(NOW, warehouse()) for _ in range(2)]
    assert [lot.balance for lot in lots] == [Decimal("10"), Decimal("10")]


def test_delivery_brak_without_a_count_is_refused_and_nothing_moves(erp):
    receiving, _, _ = erp
    (line,) = expect(receiving, 1)
    with pytest.raises(ReceivingValidationError, match="укажите количество брака"):
        deliver(receiving, [item(line, deviation="brak")])
    with pytest.raises(ReceivingValidationError, match="больше нуля и не больше факта"):
        deliver(receiving, [item(line, deviation="brak", fact_quantity=Decimal("6"), deviation_quantity=Decimal("7"))])
    assert lines_now(receiving, [line]) == [line]
    # Another deviation keeps its quantity only in the description, not as a defect count.
    (wrong,) = deliver(receiving, [item(line, deviation="peresort", deviation_quantity=Decimal("2"))]).lines
    assert wrong.defect_quantity is None and wrong.nonconformity_description.endswith(": 2 шт")


def test_a_vk_revision_starts_without_the_count_the_archive_keeps_it(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    waiting = run(receiving, line, brak(defect="2"))
    archived, revision = open_vk_revision(
        OpenVkRevisionCommand(line_id=waiting.id, expected_version=waiting.version, actor=RECEIVER, now=NOW),
        receiving(),
    )
    assert archived.defect_quantity == Decimal("2")
    assert revision.defect_quantity is None and revision.nonconformities == ()
    again = run(receiving, revision, brak(defect="5"))
    assert again.defect_quantity == Decimal("5")
    assert lines_now(receiving, [archived])[0].defect_quantity == Decimal("2")


# ---- the database --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("fact", "defect"), [("10", "11"), ("10", "0"), ("10", "-1"), (None, "1")],
)
def test_the_database_refuses_a_count_outside_the_fact(erp, fact, defect):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    with receiving() as uow:
        row = uow.session.get(ReceivingLineRow, line.id)
        row.fact_quantity = Decimal(fact) if fact else None
        row.defect_quantity = Decimal(defect)
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_the_database_keeps_a_count_within_the_fact_and_null(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    with receiving() as uow:
        row = uow.session.get(ReceivingLineRow, line.id)
        row.fact_quantity, row.defect_quantity = Decimal("10"), Decimal("10")
        uow.commit()
    assert lines_now(receiving, [line])[0].defect_quantity == Decimal("10")


# ---- API and acts --------------------------------------------------------------------------------------------------

def test_api_inspection_takes_and_returns_the_count(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())
    body = {"version": line.version, "fact_quantity": "10", "manual_nonconformities": [MECHANICAL_DAMAGE]}

    missing = client.post(f"/api/receiving/{line.id}/inspect", json=body)
    assert missing.status_code == 422 and "укажите количество брака" in missing.json()["detail"]
    above = client.post(f"/api/receiving/{line.id}/inspect", json={**body, "defect_quantity": "12"})
    assert above.status_code == 422 and "не больше фактического" in above.json()["detail"]

    done = client.post(f"/api/receiving/{line.id}/inspect", json={**body, "defect_quantity": "2.5"})
    assert done.status_code == 200, done.text
    assert done.json()["line"]["defect_quantity"] == "2.5"
    listed = client.get("/api/receiving").json()["lines"]
    assert [row["defect_quantity"] for row in listed] == ["2.5"]


def test_api_batches_carry_their_own_count(api):
    client, receiving, _ = api
    line = consume_receiving_expected(expected_message(), receiving())
    body = {
        "version": line.version, "manual_nonconformities": [MECHANICAL_DAMAGE],
        "batches": [{"fact_quantity": "6", "defect_quantity": "1"}, {"fact_quantity": "4", "defect_quantity": "4"}],
    }
    shared = client.post(f"/api/receiving/{line.id}/inspect", json={**body, "defect_quantity": "1"})
    assert shared.status_code == 422 and "у каждой партии" in shared.json()["detail"]

    done = client.post(f"/api/receiving/{line.id}/inspect", json=body)
    assert done.status_code == 200, done.text
    assert [(row["fact_quantity"], row["defect_quantity"]) for row in done.json()["lines"]] == [("6", "1"), ("4", "4")]


def test_nonconformity_and_complaint_acts_show_the_count(erp):
    receiving, _, _ = erp
    line = consume_receiving_expected(expected_message(), receiving())
    waiting = run(receiving, line, brak(defect="3", disposition="Возврат поставщику"))
    for kind in ("nonconformity", "complaint"):
        rows = {row[1]: row[2] for row in build_act_print_sheet(waiting, kind).rows}
        assert rows["Кол-во брака"] == "3 шт"
        assert rows["Кол-во фактически"] == "10 шт"
    # A brak line inspected before the count existed says so instead of a dash or a zero.
    old = replace(waiting, defect_quantity=None)
    assert {row[1]: row[2] for row in build_act_print_sheet(old, "nonconformity").rows}["Кол-во брака"] == "не указано"
    # No brak — no row.
    shortfall = run(receiving, consume_receiving_expected(expected_message(2), receiving()), inspection("8"))
    assert "Кол-во брака" not in {row[1] for row in build_act_print_sheet(shortfall, "nonconformity").rows}


def test_a_supplier_return_does_not_invent_a_count(erp):
    receiving, warehouse, stock = erp
    placed, _ = accept_and_stock(receiving, warehouse)
    (returned,) = return_to_supplier(command(placed, quantity=Decimal("2")), receiving(), stock).lines
    # The returned pieces are «Возвраты, ₽» (by the journal); the line carries no brak of the inspection.
    assert returned.defect_quantity is None and MECHANICAL_DAMAGE not in returned.nonconformities
