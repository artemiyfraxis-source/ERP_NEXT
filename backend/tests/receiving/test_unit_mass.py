"""SK-6: МАССА 1 ШТ weighed at incoming control (Receiving.gs:437-441) and СРОК ГОДНОСТИ travel to the warehouse
(Warehouse.gs:416-421)."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from app.modules.receiving.application import (
    InspectReceivingCommand,
    PlaceReceivingCommand,
    consume_receiving_expected,
    inspect_receiving,
    place_receiving,
)
from app.modules.receiving.domain import ReceivingStatus, ReceivingValidationError
from tests.factories import Actor
from tests.receiving.test_api import receiving_api  # noqa: F401 — fixture
from tests.receiving.test_application import Uow, inspection, message

ACTOR = Actor(name="Приёмщик", department="ОТК")


def _inspect(uow, line, **changes):
    return inspect_receiving(InspectReceivingCommand(
        line_id=line.id, expected_version=line.version, inspection=replace(inspection(), **changes),
        actor=ACTOR, now=datetime(2026, 9, 21, 11, 0),
    ), uow)


def test_mass_and_expiry_go_with_the_receipt_to_the_warehouse():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    inspected = _inspect(uow, line, mass_10="0,5", mass_100="4,8")
    place_receiving(PlaceReceivingCommand(
        line_id=line.id, expected_version=inspected.version, storage_location="1F4",
        actor=ACTOR, now=datetime(2026, 9, 21, 11, 5),
    ), uow)

    assert inspected.unit_mass_g == Decimal("0.04800")  # the hundred wins over the ten
    payload = uow.outbox.items[0].payload
    assert (payload["expiry_date"], payload["unit_mass_g"]) == ("2027-01-01", "0.04800")


def test_receipt_without_mass_or_expiry_sends_them_empty():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    inspected = _inspect(uow, line, expiry_date=None)
    place_receiving(PlaceReceivingCommand(
        line_id=line.id, expected_version=inspected.version, storage_location="1F4",
        actor=ACTOR, now=datetime(2026, 9, 21, 11, 5),
    ), uow)

    payload = uow.outbox.items[0].payload
    assert (payload["expiry_date"], payload["unit_mass_g"]) == (None, None)


@pytest.mark.parametrize("again", [{}, {"mass_1": "abc"}, {"mass_1": "", "mass_10": "0"}])
def test_reinspection_without_a_usable_mass_keeps_the_weighed_one(again):
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    waiting = _inspect(uow, line, manual_nonconformities=("Механическое повреждение / брак",), defect_quantity=Decimal("1"), mass_1="2,5")  # брак -> «Ожидание»
    assert (waiting.status, waiting.unit_mass_g) == (ReceivingStatus.WAITING, Decimal("2.50000"))

    accepted = _inspect(uow, waiting, **again)

    assert (accepted.status, accepted.unit_mass_g) == (ReceivingStatus.ACCEPTED, Decimal("2.50000"))


def test_reinspection_with_a_new_mass_replaces_it():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    waiting = _inspect(uow, line, manual_nonconformities=("Механическое повреждение / брак",), defect_quantity=Decimal("1"), mass_1="2,5")  # брак -> «Ожидание»

    assert _inspect(uow, waiting, mass_100="245").unit_mass_g == Decimal("2.45000")


def test_mass_beyond_the_column_is_refused_at_incoming_control():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)

    with pytest.raises(ReceivingValidationError, match="Масса 1 шт слишком большая"):
        _inspect(uow, line, mass_1="1e13")
    assert uow.receiving.get(line.id).status is ReceivingStatus.IN_TRANSIT


def test_incoming_control_api_takes_the_mass_and_offers_the_field_to_prefill(receiving_api):  # noqa: F811
    client, line = receiving_api

    response = client.post(f"/api/receiving/{line.id}/inspect", json={
        "version": line.version, "fact_quantity": "10", "document_quantity": "10",
        "shipping_document_url": "https://files.test/upd.pdf", "mass_100": "0,123",
    })

    assert response.status_code == 200, response.text
    assert response.json()["line"]["unit_mass_g"] == "0.00123"
    assert response.json()["line"]["mass_fields"] == {"mass_1": "", "mass_10": "", "mass_100": "0.123"}
