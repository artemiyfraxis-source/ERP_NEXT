from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.warehouse.application import (
    AddManualStockCommand,
    CorrectLotLocationCommand,
    ManualStockItem,
    MoveLocationCommand,
    MoveLotCommand,
    PoisonWarehouseMessage,
    ReturnWriteOffCommand,
    WriteOffCommand,
    add_manual_stock,
    correct_lot_location,
    dispatch_next_warehouse_received,
    move_location,
    move_lot,
    return_reason,
    return_writeoff,
    write_off,
)
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.shared.events import OutboxMessage
from app.shared.warehouse_location import remap_storage_location
from tests.factories import Actor


class Lots:
    def __init__(self):
        self.items = {}
        self.sequence = 0

    def get(self, lot_id):
        return self.items.get(lot_id)

    def by_key(self, warehouse, grn, package):
        return next(
            (
                lot
                for lot in self.items.values()
                if lot.key == (warehouse, grn, package)
            ),
            None,
        )

    def list(self, limit=200, after_id=None, query="", warehouse=None, include_zero=False):
        items = sorted(self.items.values(), key=lambda item: item.id)
        if after_id is not None:
            items = [item for item in items if item.id > after_id]
        if query:
            needle = query.lower()
            items = [item for item in items if needle in " ".join((item.grn, item.package, item.name, item.item_type, item.purpose, item.storage_location)).lower()]
        if warehouse:
            items = [item for item in items if item.warehouse == warehouse]
        if not include_zero:
            items = [item for item in items if item.balance > 0]
        return items[:limit]

    def count(self, query="", warehouse=None, include_zero=False):
        return len(self.list(10_000, None, query, warehouse, include_zero))

    def at_location(self, warehouse, storage_location):
        return [
            item for item in self.items.values()
            if item.warehouse == warehouse and item.balance > 0
            and remap_storage_location(item.storage_location, storage_location, storage_location) is not None
        ]

    def add(self, lot):
        self.items[lot.id] = lot

    def reserve_manual_numbers(self, year, count):
        first = self.sequence + 1
        self.sequence += count
        return first


class Audit:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class Outbox:
    def __init__(self, message):
        self.message = message
        self.published = False
        self.failed = None

    def next_unpublished(self, topic, now):
        return None if self.published or self.message.topic != topic else self.message

    def mark_published(self, message_id, published_at):
        self.published = True

    def mark_failed(self, message_id, failed_at, error):
        self.failed = (message_id, failed_at, error)


class Uow:
    def __init__(self, message):
        self.warehouse = Lots()
        self.audit = Audit()
        self.outbox = Outbox(message)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def message():
    return OutboxMessage(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        topic="warehouse.received",
        idempotency_key="receiving:one:warehouse",
        created_at=datetime(2026, 9, 21, 12, 0),
        payload={
            "receiving_line_id": "20000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-1",
            "warehouse": "ЦАР",
            "grn": "FRX.26.0000001",
            "package": "",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "unit": "шт",
            "quantity": "10",
            "storage_location": "1F4",
        },
    )


def test_received_event_creates_lot_then_move_and_writeoff_are_audited():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    actor = Actor(name="Кладовщик", department="Склад")
    moved = move_lot(
        MoveLotCommand(
            lot_id=lot.id,
            expected_version=lot.version,
            storage_location="1F5",
            actor=actor,
            now=datetime(2026, 9, 21, 12, 2),
        ),
        uow,
    )
    written = write_off(
        WriteOffCommand(
            lot_id=lot.id,
            expected_version=moved.version,
            quantity=Decimal("2"),
            purpose="FRX.001", reason="Выдача в производство",
            actor=actor,
            now=datetime(2026, 9, 21, 12, 3),
        ),
        uow,
    )

    assert written.balance == Decimal("8")
    assert [event.event_type for event in uow.audit.items] == [
        "warehouse.received",
        "warehouse.moved",
        "warehouse.written_off",
    ]
    assert uow.commits == 3


def test_invalid_decimal_event_is_quarantined_instead_of_blocking_worker():
    invalid = replace(
        message(),
        payload={**message().payload, "quantity": "not-a-number"},
    )
    uow = Uow(invalid)

    with pytest.raises(PoisonWarehouseMessage):
        dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)

    assert uow.outbox.failed[0] == invalid.id
    assert uow.commits == 1


def test_manual_stock_input_gets_old_grns_and_one_audit_event_per_lot():
    uow = Uow(message())
    actor = Actor(name="Кладовщик", department="Склад")

    lots = add_manual_stock(
        AddManualStockCommand(
            items=(
                ManualStockItem(
                    warehouse="ЦАР",
                    name="Разъём",
                    item_type="Комплектующие",
                    purpose="Склад",
                    unit="шт",
                    quantity=Decimal("10"),
                    storage_location="1F4",
                ),
                ManualStockItem(
                    warehouse="ЖУК",
                    name="Корпус",
                    item_type="Материалы",
                    purpose="35К.С.13",
                    unit="шт",
                    quantity=Decimal("5"),
                    storage_location="2A1",
                ),
            ),
            actor=actor,
            now=datetime(2026, 9, 22, 12, 0),
        ),
        uow,
    )

    assert [lot.grn for lot in lots] == ["OLD.26.0000001", "OLD.26.0000002"]
    assert [event.event_type for event in uow.audit.items] == [
        "warehouse.manually_received",
        "warehouse.manually_received",
    ]
    assert uow.commits == 1


def test_whole_location_move_preserves_slots_and_skips_empty_lots():
    uow = Uow(message())
    first = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    first = replace(first, storage_location="18C3A")
    second = replace(
        first, id=UUID("30000000-0000-0000-0000-000000000010"),
        grn="FRX.26.0000010", storage_location="18C3B",
    )
    empty = replace(
        first, id=UUID("30000000-0000-0000-0000-000000000011"),
        grn="FRX.26.0000011", storage_location="18C3C", written_off=first.inbound,
    )
    for item in (first, second, empty):
        uow.warehouse.add(item)

    moved = move_location(MoveLocationCommand(
        warehouse="ЦАР", source_location="18C3", target_location="20B5",
        actor=Actor(name="Кладовщик", department="Склад"),
        now=datetime(2026, 9, 22, 12, 0),
    ), uow)

    assert [item.storage_location for item in moved] == ["20B5A", "20B5B"]
    assert uow.warehouse.get(empty.id).storage_location == "18C3C"
    assert [event.event_type for event in uow.audit.items[-2:]] == [
        "warehouse.location_moved", "warehouse.location_moved",
    ]


def test_whole_location_move_leaves_old_addresses_and_lots_without_a_place():
    uow = Uow(message())
    first = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    first = replace(first, storage_location="18C3")
    others = [
        replace(first, id=UUID(f"30000000-0000-0000-0000-00000000002{index}"),
                grn=f"OLD.25.000002{index}", storage_location=location)
        for index, location in enumerate(("", "A-01", "18C3Д"))
    ]
    for item in (first, *others):
        uow.warehouse.add(item)

    moved = move_location(MoveLocationCommand(
        warehouse="ЦАР", source_location="18C3", target_location="20B5",
        actor=Actor(name="Кладовщик", department="Склад"),
        now=datetime(2026, 9, 22, 12, 0),
    ), uow)

    assert [item.storage_location for item in moved] == ["20B5"]
    assert [uow.warehouse.get(item.id).storage_location for item in others] == ["", "A-01", "18C3Д"]


def _transfer(uow, lot, warehouse, place=""):
    return move_lot(MoveLotCommand(
        lot_id=lot.id, expected_version=lot.version, storage_location=place,
        actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 28, 12, 0),
        warehouse=warehouse,
    ), uow)


def test_lot_transfer_to_the_other_warehouse_keeps_balance_history_and_is_audited():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    written = write_off(WriteOffCommand(
        lot_id=lot.id, expected_version=lot.version, quantity=Decimal("3"), purpose="FRX.001", reason="Выдача",
        actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 22, 12, 0),
    ), uow)

    moved = _transfer(uow, written, "ЖУК", "2A1")

    assert uow.warehouse.by_key("ЦАР", lot.grn, "") is None
    assert uow.warehouse.by_key("ЖУК", lot.grn, "") == moved
    assert len(uow.warehouse.items) == 1
    assert (moved.inbound, moved.written_off, moved.balance) == (Decimal("10"), Decimal("3"), Decimal("7"))
    assert [event.aggregate_id for event in uow.audit.items] == [lot.id] * 3
    assert uow.audit.items[-1].event_type == "warehouse.transferred"
    assert uow.audit.items[-1].details == {
        "from_warehouse": "ЦАР", "to_warehouse": "ЖУК", "from": "1F4", "to": "2A1",
        "grn": lot.grn, "package": "", "inbound": "10", "written_off": "3", "balance": "7",
    }


def test_lot_transfer_refuses_empty_reserved_and_occupied_target_without_writing():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    commits = uow.commits

    with pytest.raises(WarehouseValidationError, match="Укажи новое место или склад."):
        _transfer(uow, lot, "")
    with pytest.raises(WarehouseValidationError, match="Неизвестный склад «СПБ»."):
        _transfer(uow, lot, "СПБ")
    reserved = lot.reserve(Decimal("1"))
    uow.warehouse.add(reserved)
    with pytest.raises(WarehouseValidationError, match="в резерве"):
        _transfer(uow, reserved, "ЖУК")
    empty = replace(reserved, reserved=Decimal("0"), written_off=Decimal("10"))
    uow.warehouse.add(empty)
    with pytest.raises(WarehouseValidationError, match="Нечего перемещать: остаток по FRX.26.0000001 пуст."):
        _transfer(uow, empty, "ЖУК")
    full = replace(empty, written_off=Decimal("0"))
    uow.warehouse.add(full)
    twin = replace(full, id=UUID("40000000-0000-0000-0000-000000000001"), warehouse="ЖУК")
    uow.warehouse.add(twin)
    with pytest.raises(WarehouseValidationError, match="На складе ЖУК уже есть партия FRX.26.0000001"):
        _transfer(uow, full, "ЖУК")

    assert uow.commits == commits
    assert uow.warehouse.get(lot.id).warehouse == "ЦАР"
    assert [event.event_type for event in uow.audit.items] == ["warehouse.received"]


def test_lot_move_within_its_own_warehouse_stays_a_place_move():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)

    same = _transfer(uow, lot, "ЦАР")
    moved = _transfer(uow, lot, "ЦАР", "1F5")

    assert same == lot
    assert (moved.warehouse, moved.storage_location) == ("ЦАР", "1F5")
    assert uow.audit.items[-1].event_type == "warehouse.moved"


def test_manual_location_correction_is_audited_and_unchanged_place_is_not_written():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    actor = Actor(name="Кладовщик", department="Склад")

    corrected = correct_lot_location(CorrectLotLocationCommand(
        lot_id=lot.id, expected_version=lot.version, storage_location="A-01",
        actor=actor, now=datetime(2026, 9, 22, 12, 0),
    ), uow)
    commits = uow.commits
    unchanged = correct_lot_location(CorrectLotLocationCommand(
        lot_id=lot.id, expected_version=corrected.version, storage_location=" A-01 ",
        actor=actor, now=datetime(2026, 9, 22, 12, 1),
    ), uow)

    assert corrected.storage_location == "A-01"
    assert unchanged == corrected
    assert uow.commits == commits
    assert uow.audit.items[-1].event_type == "warehouse.location_corrected"
    assert uow.audit.items[-1].details == {"from": "1F4", "to": "A-01"}


def test_repeated_receipt_reaches_a_lot_without_a_place():
    uow = Uow(replace(message(), id=UUID("30000000-0000-0000-0000-000000000002"),
                      payload={**message().payload, "receiving_line_id": "receiving:two"}))
    imported = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Разъём",
        item_type="Комплектующие", purpose="FRX.001", unit="шт", quantity=Decimal("4"),
        storage_location="", source_receiving_ids=("import:ЦАР:7",), location_as_is=True,
    )
    uow.warehouse.add(imported)

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert received.id == imported.id
    assert received.inbound == Decimal("14")
    assert received.storage_location == "1F4"


def _repeat(**payload):
    return replace(message(), id=UUID("30000000-0000-0000-0000-000000000002"),
                   payload={**message().payload, "receiving_line_id": "receiving:two", **payload})


def _first_lot(uow, **changes):
    first = replace(dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), Uow(message())), **changes)
    uow.warehouse.add(first)
    return first


def test_repeated_receipt_updates_place_and_purpose_and_audits_old_and_new():
    uow = Uow(_repeat(purpose="FRX.002", storage_location="02b07"))
    first = _first_lot(uow)

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert (received.id, received.inbound) == (first.id, Decimal("20"))
    assert (received.purpose, received.storage_location) == ("FRX.002", "2B7")
    assert uow.audit.items[-1].event_type == "warehouse.received"
    assert uow.audit.items[-1].details == {
        "grn": "FRX.26.0000001", "package": "", "quantity": "10",
        "storage_location_from": "1F4", "storage_location_to": "2B7",
        "purpose_from": "FRX.001", "purpose_to": "FRX.002",
    }


def test_repeated_receipt_with_the_same_place_and_purpose_audits_only_the_quantity():
    uow = Uow(_repeat())
    _first_lot(uow)

    dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert uow.audit.items[-1].details == {"grn": "FRX.26.0000001", "package": "", "quantity": "10"}


def test_repeated_receipt_keeps_the_reserve_of_a_reserved_lot():
    uow = Uow(_repeat(storage_location="2B7"))
    first = _first_lot(uow, reserved=Decimal("6"))

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert (received.id, received.reserved, received.available) == (first.id, Decimal("6"), Decimal("14"))
    assert received.storage_location == "2B7"


def test_receipt_of_a_grn_that_sits_in_the_other_warehouse_opens_its_own_lot():
    # SUPPLYMAP looks the ГРН+ПАКЕТ up only on the receiving warehouse sheet (applyWarehouseAdds_).
    uow = Uow(_repeat(storage_location="2B7", purpose="FRX.002"))
    other = _first_lot(uow, warehouse="ЖУК")

    received = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)

    assert received.id != other.id
    assert (received.warehouse, received.storage_location, received.inbound) == ("ЦАР", "2B7", Decimal("10"))
    assert uow.warehouse.get(other.id) == other
    assert "storage_location_from" not in uow.audit.items[-1].details


@pytest.mark.parametrize("location", ["", "A-01"])
def test_manual_stock_still_needs_a_full_rack_address(location):
    uow = Uow(message())

    with pytest.raises(WarehouseValidationError, match="Место хранения"):
        add_manual_stock(AddManualStockCommand(
            items=(ManualStockItem(
                warehouse="ЦАР", name="Разъём", item_type="", purpose="Склад", unit="шт",
                quantity=Decimal("1"), storage_location=location,
            ),),
            actor=Actor(name="Кладовщик", department="Склад"),
            now=datetime(2026, 9, 22, 12, 0),
        ), uow)
    assert uow.commits == 0


def _written_lot(uow, quantity="4"):
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    return write_off(WriteOffCommand(
        lot_id=lot.id, expected_version=lot.version, quantity=Decimal(quantity), purpose="FRX.001", reason="Выдача",
        actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 22, 12, 0),
    ), uow)


def _return(uow, lot, quantity, source="FRX.001", reason=""):
    return return_writeoff(ReturnWriteOffCommand(
        lot_id=lot.id, expected_version=lot.version, quantity=Decimal(quantity), source=source, reason=reason,
        actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 23, 12, 0),
    ), uow)


@pytest.mark.parametrize(("reason", "stored"), [
    ("", "Возврат"),
    ("  ", "Возврат"),
    ("из производства", "Возврат: из производства"),
    ("возврат остатка", "возврат остатка"),
    ("  Возврат из цеха ", "Возврат из цеха"),
])
def test_return_reason_always_starts_with_the_supplymap_marker(reason, stored):
    assert return_reason(reason) == stored


def test_writeoff_return_restores_the_balance_and_is_audited_with_its_source():
    uow = Uow(message())
    written = _written_lot(uow)

    returned = _return(uow, written, "3", source="  Иванов ", reason="из производства")

    assert (returned.written_off, returned.balance, returned.version) == (Decimal("1"), Decimal("9"), written.version + 1)
    assert uow.audit.items[-1].event_type == "warehouse.writeoff_returned"
    assert uow.audit.items[-1].aggregate_id == written.id
    assert uow.audit.items[-1].details == {
        "quantity": "3", "reason": "Возврат: из производства", "purpose": "Иванов",
    }


def test_writeoff_return_of_everything_written_off_is_allowed():
    uow = Uow(message())
    written = _written_lot(uow)

    returned = _return(uow, written, "4")

    assert (returned.written_off, returned.balance) == (Decimal("0"), Decimal("10"))


@pytest.mark.parametrize(("quantity", "source", "error"), [
    ("5", "FRX.001", "Больше списанного: по FRX.26.0000001 списано 4 шт — вернуть можно только их."),
    ("0", "FRX.001", "больше нуля"),
    ("-1", "FRX.001", "больше нуля"),
    ("1", "   ", "Укажи, откуда возврат: номер проекта или фамилия ответственного."),
])
def test_writeoff_return_refuses_over_return_non_positive_quantity_and_missing_source(quantity, source, error):
    uow = Uow(message())
    written = _written_lot(uow)
    commits = uow.commits

    with pytest.raises(WarehouseValidationError, match=error):
        _return(uow, written, quantity, source=source)

    assert uow.commits == commits
    assert uow.warehouse.get(written.id).written_off == Decimal("4")
    assert uow.audit.items[-1].event_type == "warehouse.written_off"


def test_nothing_to_return_on_a_lot_that_was_never_written_off():
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)

    with pytest.raises(WarehouseValidationError, match="списано 0 шт"):
        _return(uow, lot, "1")


def _write_off(uow, lot, purpose, reason):
    return write_off(WriteOffCommand(
        lot_id=lot.id, expected_version=lot.version, quantity=Decimal("2"), purpose=purpose, reason=reason,
        actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 22, 12, 0),
    ), uow)


@pytest.mark.parametrize(("purpose", "reason", "stored"), [
    ("FRX.001", "", {"purpose": "FRX.001", "reason": "Выдача"}),
    ("  Иванов ", "   ", {"purpose": "Иванов", "reason": "Выдача"}),
    ("FRX.001", " Брак ", {"purpose": "FRX.001", "reason": "Брак"}),
])
def test_writeoff_keeps_its_purpose_and_defaults_the_basis_to_issue(purpose, reason, stored):
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)

    written = _write_off(uow, lot, purpose, reason)

    assert written.written_off == Decimal("2")
    event = uow.audit.items[-1]
    assert event.event_type == "warehouse.written_off"
    assert event.details == {"quantity": "2", **stored}


@pytest.mark.parametrize("purpose", ["", "   ", None])
def test_writeoff_without_a_purpose_is_refused_with_the_supplymap_text(purpose):
    uow = Uow(message())
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), uow)
    commits = uow.commits

    with pytest.raises(WarehouseValidationError, match="^Укажи назначение: номер проекта или фамилию ответственного.$"):
        _write_off(uow, lot, purpose, "Выдача")

    assert uow.commits == commits
    assert uow.warehouse.get(lot.id).written_off == Decimal("0")
    assert uow.audit.items[-1].event_type == "warehouse.received"
