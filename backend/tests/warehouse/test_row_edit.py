"""SK-7: manual edit of a stock row (SUPPLYMAP Stock05.gs stock05EditRowLocked_ 1027-1079)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.warehouse.application import (
    ConcurrentWarehouseUpdate,
    EditLotRowCommand,
    edit_lot_row,
    edited_unit_mass,
)
from app.modules.warehouse.domain import StockLot, WarehouseValidationError, lot_row_changes
from tests.factories import Actor
from tests.warehouse.test_application import Uow

ACTOR = Actor(name="Администратор", department="Склад")
NOW = datetime(2026, 9, 29, 10, 0)


def lot(**overrides) -> StockLot:
    return StockLot.create(**{
        "warehouse": "ЦАР", "grn": "FRX.26.0000001", "package": "P1", "article": "CON-001", "name": "Разъём",
        "item_type": "Комплектующие", "purpose": "FRX.001", "unit": "шт", "quantity": Decimal("10"),
        "storage_location": "1F4", "source_receiving_ids": ("r1",), **overrides,
    })


def test_reference_and_passport_fields_change_and_keep_the_lot_key():
    initial = lot()
    updated = initial.edit_row({
        "item_type": " Разъёмы ", "unit": "уп", "purpose": "Склад", "storage_location": " A-01 ",
        "unit_mass_g": Decimal("0.25"), "expiry_date": date(2027, 1, 31), "manufacturer": "Molex",
        "description": "", "storage_conditions": "Сухо", "datasheet": "ds.pdf", "certificate": "RU.1",
    })

    assert (updated.item_type, updated.unit, updated.purpose, updated.storage_location) == (
        "Разъёмы", "уп", "Склад", "A-01",
    )
    assert (updated.unit_mass_g, updated.expiry_date, updated.manufacturer) == (
        Decimal("0.25"), date(2027, 1, 31), "Molex",
    )
    assert updated.key == initial.key and updated.id == initial.id
    assert updated.version == initial.version + 1


def test_a_missing_field_is_left_alone_and_an_empty_text_clears_it():
    initial = lot(series="LOT-1", manufacturer="Molex")

    updated = initial.edit_row({"series": "", "name": "Разъём питания"})

    assert (updated.series, updated.name, updated.manufacturer, updated.article) == (
        "", "Разъём питания", "Molex", "CON-001",
    )


def test_nothing_changed_is_the_same_lot_without_a_new_version():
    initial = lot()

    assert initial.edit_row({"article": " CON-001 ", "inbound": Decimal("10.000")}) is initial
    assert initial.edit_row({}) is initial


def test_quantities_recount_the_balance():
    initial = lot().reserve(Decimal("2"))

    updated = initial.edit_row({"inbound": Decimal("12"), "written_off": Decimal("4"), "reserved": Decimal("3")})

    assert (updated.inbound, updated.written_off, updated.reserved) == (Decimal("12"), Decimal("4"), Decimal("3"))
    assert (updated.balance, updated.available) == (Decimal("8"), Decimal("5"))
    assert [change[1] for change in lot_row_changes(initial, updated)] == ["ПРИХОД", "СПИСАНО", "РЕЗЕРВ", "БАЛАНС"]


@pytest.mark.parametrize(("changes", "message"), [
    ({"written_off": Decimal("11")}, r"СПИСАНО \(11\) больше ПРИХОДА \(10\)"),
    ({"inbound": Decimal("1"), "written_off": Decimal("2")}, "остаток ушёл бы в минус"),
    ({"reserved": Decimal("11")}, r"РЕЗЕРВ \(11\) больше остатка \(10 шт\)"),
    ({"inbound": Decimal("-1")}, "ПРИХОД не может быть отрицательным"),
    ({"reserved": Decimal("-1")}, "РЕЗЕРВ не может быть отрицательным"),
    ({"article": "  "}, "Артикул обязателен"),
    ({"purpose": ""}, "Назначение обязательно"),
    ({"unit": ""}, "Единица измерения обязательна"),
    ({"grn": "FRX.26.0000002"}, "ключ партии"),
    ({"package": ""}, "ключ партии"),
    ({"warehouse": "ЖУК"}, "ключ партии"),
    ({"balance": Decimal("5")}, "Поле не правится: balance"),
    ({"manufacturer": "x" * 256}, "Слишком длинное"),
    ({"unit_mass_g": Decimal("-1")}, "Масса 1 шт не может быть отрицательной"),
])
def test_the_edit_refuses_what_would_break_the_lot(changes, message):
    with pytest.raises(WarehouseValidationError, match=message):
        lot().edit_row(changes)


def test_lowering_the_inbound_under_the_reserve_is_refused():
    reserved = lot().reserve(Decimal("6"))

    with pytest.raises(WarehouseValidationError, match=r"РЕЗЕРВ \(6\) больше остатка \(5 шт\)"):
        reserved.edit_row({"inbound": Decimal("5")})


def test_the_reserve_cannot_go_under_what_kitting_holds():
    reserved = lot().reserve(Decimal("4"))

    with pytest.raises(WarehouseValidationError, match="Комплектация держит .* резерв 4 шт"):
        reserved.edit_row({"reserved": Decimal("1")}, kitting_reserve=Decimal("4"))
    assert reserved.edit_row({"reserved": Decimal("6")}, kitting_reserve=Decimal("4")).reserved == Decimal("6")
    # Other fields stay editable whatever kitting holds.
    assert reserved.edit_row({"name": "X"}, kitting_reserve=Decimal("4")).name == "X"


def test_mass_of_the_row_edit_is_a_number_or_nothing():
    assert edited_unit_mass(" ") is None
    assert edited_unit_mass("0") is None
    assert edited_unit_mass("1,234567") == Decimal("1.23457")
    with pytest.raises(WarehouseValidationError, match="не число"):
        edited_unit_mass("abc")
    with pytest.raises(WarehouseValidationError, match="отрицательной"):
        edited_unit_mass("-2")


def _uow_with(initial: StockLot) -> Uow:
    uow = Uow(None)
    uow.warehouse.add(initial)
    return uow


def test_edit_journals_was_and_became_per_changed_field():
    initial = lot()
    uow = _uow_with(initial)

    updated, changes = edit_lot_row(EditLotRowCommand(
        lot_id=initial.id, expected_version=initial.version, actor=ACTOR, now=NOW,
        changes={"article": "CON-002", "manufacturer": "Molex", "written_off": Decimal("0"), "series": ""},
    ), uow)

    assert uow.warehouse.get(initial.id) == updated
    assert [column for _, column, _, _ in changes] == ["АРТИКУЛ", "ПРОИЗВОДИТЕЛЬ"]
    [event] = uow.audit.items
    assert event.event_type == "warehouse.row_edited"
    assert event.actor == ACTOR
    assert event.details["changes"] == "АРТИКУЛ: «CON-001» → «CON-002»; ПРОИЗВОДИТЕЛЬ: «—» → «Molex»"
    assert event.details | {
        "grn": "FRX.26.0000001", "package": "P1", "warehouse": "ЦАР",
        "article_from": "CON-001", "article_to": "CON-002", "manufacturer_from": "", "manufacturer_to": "Molex",
    } == event.details
    assert "series_from" not in event.details and "written_off_from" not in event.details
    assert uow.commits == 1


@pytest.mark.parametrize("changes", [
    {"inbound": Decimal("12")}, {"written_off": Decimal("3")}, {"reserved": Decimal("1")},
    {"name": "Разъём питания", "written_off": Decimal("3")},
])
def test_a_quantity_is_edited_by_hand_and_journaled(changes):
    # Owner 23.10.2026: quantities are edited by hand again (the «only by movements» rule is cut out).
    initial = lot()
    uow = _uow_with(initial)

    updated, _ = edit_lot_row(EditLotRowCommand(
        lot_id=initial.id, expected_version=initial.version, actor=ACTOR, now=NOW, changes=changes,
    ), uow)
    for field, value in changes.items():
        assert getattr(updated, field) == value
    [event] = uow.audit.items
    quantity = next(field for field in ("inbound", "written_off", "reserved") if field in changes)
    assert event.event_type == "warehouse.row_edited" and f"{quantity}_from" in event.details
    assert uow.commits == 1


def test_edit_without_changes_writes_nothing():
    initial = lot()
    uow = _uow_with(initial)

    updated, changes = edit_lot_row(EditLotRowCommand(
        lot_id=initial.id, expected_version=initial.version, actor=ACTOR, now=NOW, changes={"name": "Разъём"},
    ), uow)

    assert (updated, changes, uow.audit.items, uow.commits) == (initial, [], [], 0)


def test_edit_of_a_stale_form_is_refused():
    initial = lot()
    uow = _uow_with(initial)

    with pytest.raises(ConcurrentWarehouseUpdate):
        edit_lot_row(EditLotRowCommand(
            lot_id=initial.id, expected_version=initial.version + 1, actor=ACTOR, now=NOW, changes={"name": "X"},
        ), uow)
    assert uow.audit.items == []
