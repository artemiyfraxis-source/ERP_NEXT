from decimal import Decimal

import pytest

from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.shared.warehouse_location import remap_storage_location, storage_location_has_slot


def lot() -> StockLot:
    return StockLot.create(
        warehouse="ЦАР",
        grn="FRX.26.0000001",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="RND",
        unit="шт",
        quantity=Decimal("10"),
        storage_location="1F4",
        source_receiving_ids=("10000000-0000-0000-0000-000000000001",),
    )


def test_balance_is_inbound_minus_writeoff_and_reserve_is_separate():
    initial = lot()
    reserved = initial.reserve(Decimal("3"))
    written = reserved.write_off(Decimal("4"))

    assert initial.balance == Decimal("10")
    assert reserved.available == Decimal("7")
    assert written.balance == Decimal("6")
    assert written.available == Decimal("3")


def test_cannot_overreserve_or_write_off_more_than_balance():
    with pytest.raises(WarehouseValidationError, match="резерв"):
        lot().reserve(Decimal("11"))
    with pytest.raises(WarehouseValidationError, match="остатка"):
        lot().write_off(Decimal("11"))


def test_return_cannot_exceed_previously_written_off_quantity():
    written = lot().write_off(Decimal("4"))
    returned = written.return_writeoff(Decimal("2"))

    assert returned.balance == Decimal("8")
    with pytest.raises(WarehouseValidationError, match="списано"):
        returned.return_writeoff(Decimal("3"))


def test_reserved_issue_reduces_reserve_and_physical_balance_atomically():
    reserved = lot().reserve(Decimal("4"))
    issued = reserved.issue_reserved(Decimal("4"))

    assert issued.reserved == Decimal("0")
    assert issued.written_off == Decimal("4")
    assert issued.balance == Decimal("6")
    with pytest.raises(WarehouseValidationError, match="зарезервированного"):
        issued.issue_reserved(Decimal("1"))


def test_whole_lot_move_changes_location_but_not_quantities():
    moved = lot().move("17C120")

    assert moved.storage_location == "17C120"
    assert moved.inbound == Decimal("10")
    assert moved.written_off == Decimal("0")


@pytest.mark.parametrize("location", ["", "0A1", "100A1", "1A0", "1A1000", "1Д1", "1-А-1"])
def test_storage_location_uses_rack_shelf_number_code(location):
    with pytest.raises(WarehouseValidationError, match="Место хранения"):
        lot().move(location)


def test_storage_location_accepts_all_shelves_and_optional_slot():
    assert lot().move("18z3х").storage_location == "18Z3X"


@pytest.mark.parametrize(
    ("field", "message"),
    [("purpose", "Назначение"), ("unit", "Единица")],
)
def test_stock_identity_fields_cannot_be_blank(field, message):
    values = {
        "warehouse": "ЦАР",
        "grn": "OLD.26.0000001",
        "package": "",
        "name": "Разъём",
        "item_type": "",
        "purpose": "Склад",
        "unit": "шт",
        "quantity": Decimal("1"),
        "storage_location": "1F4",
        "source_receiving_ids": ("manual:one",),
    }
    values[field] = "   "

    with pytest.raises(WarehouseValidationError, match=message):
        StockLot.create(**values)


def test_article_identifies_stock_while_name_is_optional_legacy_description():
    created = StockLot.create(
        warehouse="ЦАР",
        grn="OLD.26.0000001",
        package="",
        article="GHR-04V-S",
        series="LOT-2409",
        name="",
        item_type="Комплектующие",
        purpose="Склад",
        unit="шт",
        quantity=Decimal("1"),
        storage_location="1F4",
        source_receiving_ids=("manual:one",),
    )

    assert created.article == "GHR-04V-S"
    assert created.series == "LOT-2409"
    assert created.name == ""


def test_legacy_name_becomes_article_and_repeated_receipt_updates_series():
    initial = lot()
    updated = initial.receive(
        Decimal("2"),
        "receiving:two",
        article="IGNORED-BECAUSE-ARTICLE-EXISTS",
        series="SERIES-2",
    )

    assert initial.article == "Разъём"
    assert updated.article == "Разъём"
    assert updated.series == "SERIES-2"
    assert updated.inbound == Decimal("12")

    blank_description = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000002", package="", article="CON-001",
        name="", item_type="Комплектующие", purpose="Склад", unit="шт",
        quantity=Decimal("1"), storage_location="1F4",
        source_receiving_ids=("receiving:one",),
    ).receive(Decimal("1"), "receiving:two", article="CON-001", series="SERIES-3")
    assert blank_description.name == ""


def test_identity_edit_keeps_grn_and_accounting_unchanged():
    initial = lot()
    updated = initial.edit_identity(
        article="CON-001",
        name="Разъём питания",
        series="LOT-2409",
    )

    assert (updated.article, updated.name, updated.series) == (
        "CON-001", "Разъём питания", "LOT-2409",
    )
    assert (updated.grn, updated.inbound, updated.written_off) == (
        initial.grn, initial.inbound, initial.written_off,
    )
    assert updated.version == initial.version + 1


def old_lot(storage_location: str) -> StockLot:
    return StockLot.create(
        warehouse="ЖУК", grn="OLD.25.0000042", package="", article="CON-042", name="",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("7"),
        storage_location=storage_location, source_receiving_ids=("import:ЖУК:42",),
        location_as_is=True,
    )


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("01A1", "1A1"), ("01a01", "1A1"), ("1A0999", "1A999"), ("09 с 012 в", "9C12B")],
)
def test_leading_zeros_and_homoglyphs_normalize_to_the_rack_code(typed, stored):
    assert lot().move(typed).storage_location == stored
    assert old_lot(typed).storage_location == stored


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("", ""),
        ("   ", ""),
        ("  A-01 ", "A-01"),
        ("А-01", "А-01"),
        ("у окна, верхняя полка", "у окна, верхняя полка"),
        ("1A1000", "1A1000"),
        ("1Д1", "1Д1"),
    ],
)
def test_old_data_keeps_an_empty_place_or_an_old_address_as_is(typed, stored):
    created = old_lot(typed)

    assert created.storage_location == stored
    assert created.balance == Decimal("7")


@pytest.mark.parametrize("location", ["", "A-01"])
def test_regular_entry_still_requires_a_rack_code(location):
    with pytest.raises(WarehouseValidationError, match="Место хранения"):
        StockLot.create(
            warehouse="ЦАР", grn="OLD.26.0000001", package="", article="CON-001", name="",
            item_type="", purpose="Склад", unit="шт", quantity=Decimal("1"),
            storage_location=location, source_receiving_ids=("manual:one",),
        )


def test_old_address_longer_than_the_column_is_rejected():
    with pytest.raises(WarehouseValidationError, match="длиннее 255"):
        old_lot("я" * 256)
    assert old_lot("я" * 255).storage_location == "я" * 255


@pytest.mark.parametrize("current", ["", "A-01"])
def test_lot_without_a_place_or_with_an_old_address_moves_to_a_rack_place(current):
    initial = old_lot(current)
    moved = initial.move("02b03")

    assert moved.storage_location == "2B3"
    assert moved.version == initial.version + 1
    assert moved.balance == initial.balance


def test_keeping_an_old_address_is_not_a_move_but_a_new_one_needs_a_rack_code():
    initial = old_lot("A-01")

    assert initial.move(" A-01 ") is initial
    with pytest.raises(WarehouseValidationError, match="Место хранения"):
        initial.move("B-02")
    with pytest.raises(WarehouseValidationError, match="Место хранения"):
        old_lot("").move("")


def test_manual_correction_writes_an_old_address_or_clears_the_place():
    initial = lot()
    legacy = initial.correct_location("  A-01 ")
    cleared = legacy.correct_location("")

    assert (legacy.storage_location, legacy.version) == ("A-01", initial.version + 1)
    assert (cleared.storage_location, cleared.version) == ("", initial.version + 2)
    assert initial.correct_location("01f04") is initial
    assert cleared.balance == initial.balance


@pytest.mark.parametrize("current", ["", "A-01", "18C3Д", "18C3 угол"])
def test_empty_place_and_old_address_never_move_with_a_rack_place(current):
    assert remap_storage_location(current, "18C3", "20B5") is None
    assert not storage_location_has_slot(current)
    assert remap_storage_location("18c03b", "18C3", "20B5") == "20B5B"


def test_repeated_receipt_keeps_a_lot_without_a_place():
    received = old_lot("").receive(Decimal("3"), "receiving:two")

    assert received.storage_location == ""
    assert received.inbound == Decimal("10")
