from decimal import Decimal

import pytest

from app.modules.warehouse.domain import StockLot, WarehouseValidationError


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
