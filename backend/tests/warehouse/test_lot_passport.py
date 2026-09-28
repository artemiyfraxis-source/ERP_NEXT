"""Lot passport (SK-6): МАССА 1 ШТ, Г, СРОК ГОДНОСТИ, ПРОИЗВОДИТЕЛЬ, ОПИСАНИЕ, УСЛОВИЯ ХРАНЕНИЯ, DATASHEET,
СЕРТИФИКАТ (SUPPLYMAP Config.gs:164-174, Warehouse.gs:416-527, Stock05.gs:98-105)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.warehouse.application import (
    AddManualStockCommand,
    ManualStockItem,
    PoisonWarehouseMessage,
    add_manual_stock,
    dispatch_next_warehouse_received,
)
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from tests.factories import Actor
from tests.warehouse.test_application import Uow, _first_lot, _repeat, message


def test_receipt_carries_expiry_and_unit_mass_into_a_new_lot():
    uow = Uow(replace(message(), payload={
        **message().payload, "expiry_date": "2027-03-01", "unit_mass_g": "0.00123",
    }))

    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)

    assert (lot.expiry_date, lot.unit_mass_g) == (date(2027, 3, 1), Decimal("0.00123"))
    assert (lot.manufacturer, lot.description, lot.storage_conditions, lot.datasheet, lot.certificate) == ("",) * 5


def test_receipt_without_passport_keys_opens_a_lot_without_them():
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), Uow(message()))

    assert (lot.expiry_date, lot.unit_mass_g) == (None, None)


def test_repeated_receipt_replaces_expiry_and_a_real_mass_and_audits_them():
    uow = Uow(_repeat(expiry_date="2028-01-31", unit_mass_g="2.5"))
    first = _first_lot(uow, expiry_date=date(2027, 1, 1), unit_mass_g=Decimal("2.4"))

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert (received.id, received.expiry_date, received.unit_mass_g) == (first.id, date(2028, 1, 31), Decimal("2.5"))
    assert uow.audit.items[-1].details == {
        "grn": "FRX.26.0000001", "package": "", "quantity": "10",
        "expiry_date_from": "2027-01-01", "expiry_date_to": "2028-01-31",
        "unit_mass_g_from": "2.4", "unit_mass_g_to": "2.5",
    }


@pytest.mark.parametrize("mass", [None, "0", "0.00000"])
def test_repeated_receipt_without_a_mass_keeps_the_mass_but_takes_the_empty_expiry(mass):
    # Warehouse.gs:498 writes СРОК ГОДНОСТИ as it came, even empty; :508 writes the mass only when truthy.
    uow = Uow(_repeat(expiry_date=None, unit_mass_g=mass))
    first = _first_lot(uow, expiry_date=date(2027, 1, 1), unit_mass_g=Decimal("2.4"))

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert (received.id, received.expiry_date, received.unit_mass_g) == (first.id, None, Decimal("2.4"))


@pytest.mark.parametrize(("payload", "error"), [
    ({"expiry_date": "31.12.2027"}, "Invalid warehouse.received payload"),
    ({"unit_mass_g": "тяжёлый"}, "Invalid warehouse.received payload"),
    ({"unit_mass_g": "-1"}, "Масса 1 шт не может быть отрицательной"),
])
def test_bad_passport_in_a_received_event_is_quarantined(payload, error):
    uow = Uow(replace(message(), payload={**message().payload, **payload}))

    with pytest.raises(PoisonWarehouseMessage, match=error):
        dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    assert uow.warehouse.items == {}


def _manual(uow, **passport):
    return add_manual_stock(AddManualStockCommand(
        items=(ManualStockItem(
            warehouse="ЦАР", name="Шайба", item_type="Крепёж", purpose="Склад", unit="шт",
            quantity=Decimal("100"), storage_location="1F4", **passport,
        ),),
        actor=Actor(name="Кладовщик", department="Склад"),
        now=datetime(2026, 9, 22, 12, 0),
    ), uow)[0]


def test_manual_stock_takes_the_lot_passport_and_stores_the_mass_of_one_piece():
    lot = _manual(
        Uow(message()), mass_1="", mass_10="", mass_100="12,3", expiry_date=date(2030, 5, 1),
        manufacturer=" Molex ", description="Шайба пружинная", storage_conditions="Сухо, +5…+35 °C",
        datasheet="https://files.test/ds.pdf", certificate="РОСС RU.0001",
    )

    assert lot.unit_mass_g == Decimal("0.12300")
    assert (lot.expiry_date, lot.manufacturer, lot.description, lot.storage_conditions, lot.datasheet,
            lot.certificate) == (date(2030, 5, 1), "Molex", "Шайба пружинная", "Сухо, +5…+35 °C",
                                 "https://files.test/ds.pdf", "РОСС RU.0001")


@pytest.mark.parametrize(("fields", "stored"), [
    ({}, None),
    ({"mass_1": "abc"}, None),  # not a number: no mass and no refusal, as in SUPPLYMAP
    ({"mass_100": "0.0000001"}, None),  # rounds to zero → the lot has no mass (Warehouse.gs `if (it2.mass)`)
    ({"mass_1": "7", "mass_10": "65"}, Decimal("6.5")),  # the larger sample wins
])
def test_manual_stock_mass_edge_cases(fields, stored):
    assert _manual(Uow(message()), **fields).unit_mass_g == stored


def test_manual_stock_with_a_mass_beyond_the_column_is_refused_without_writing():
    uow = Uow(message())

    with pytest.raises(WarehouseValidationError, match="Масса 1 шт слишком большая"):
        _manual(uow, mass_1="1e13")
    assert (uow.commits, uow.warehouse.items) == (0, {})


def test_passport_text_fields_are_bounded():
    with pytest.raises(WarehouseValidationError, match="manufacturer"):
        StockLot.create(
            warehouse="ЦАР", grn="OLD.26.0000001", package="", name="Шайба", item_type="", purpose="Склад",
            unit="шт", quantity=Decimal("1"), storage_location="1F4", source_receiving_ids=("manual:1",),
            manufacturer="x" * 256,
        )
