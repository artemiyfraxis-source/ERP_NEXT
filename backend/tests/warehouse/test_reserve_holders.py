"""Who holds a lot's РЕЗЕРВ: procurement covers, techcard reserves and kit requests, all next to the number."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest, KitReserve, LotSnapshot
from app.modules.procurement.domain import StockCover
from app.modules.warehouse.api import _reserve_holders, _reserve_note
from app.modules.warehouse.domain import StockLot

NOW = datetime(2026, 10, 6, 10, 0)


def lot() -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Разъём", item_type="Комплектующие",
        purpose="Склад", unit="шт", quantity=Decimal("100"), storage_location="1F4",
        source_receiving_ids=("receiving-1",),
    )


class Uow:
    def __init__(self, **parts):
        self.__dict__.update(parts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def factory(**parts):
    return lambda: Uow(**parts)


def make_request(lot_id, reserved: str):
    line = FulfillmentLine(
        id=uuid4(), requirement_id=uuid4(), article="КД-1", name="Разъём", unit="шт",
        requirement=Decimal("9"), reserved=Decimal(reserved),
        lot=LotSnapshot(lot_id=lot_id, warehouse="ЦАР", grn="FRX.26.0000001", package="", storage_location="1F4"),
    )
    other = replace(line, id=uuid4(), reserved=Decimal("7"), lot=None)
    kit = FulfillmentRequest.__new__(FulfillmentRequest)
    for name, value in dict(id=uuid4(), number="REQ-2026-0001", purpose="728К.2 · КБ2 · Жгут", lines=(line, other)).items():
        object.__setattr__(kit, name, value)
    return kit


def test_lot_lists_procurement_techcard_and_kit_holders_adding_up_to_reserved():
    target = lot()
    cover = StockCover(
        id=uuid4(), line_id=uuid4(), legacy_id="SM-20261002-01", project_reference="591К.3", lot_id=target.id,
        warehouse="ЦАР", grn=target.grn, package="", storage_location="1F4", unit="шт", quantity=Decimal("30"),
        created_at=NOW, created_by="Закупщик",
    )
    reserve = KitReserve(
        id=uuid4(), project="728К.2", item_index="КБ1", article="КД-1", lot_id=target.id, warehouse="ЦАР",
        grn=target.grn, package="", unit="шт", quantity=Decimal("10"), created_at=NOW, created_by="Технолог",
    )
    kit = make_request(target.id, "5")
    reserved = target.reserve(Decimal("45"))
    app = SimpleNamespace(state=SimpleNamespace(
        procurement_uow_factory=factory(stock_covers=SimpleNamespace(active_on_lots=lambda ids: [cover])),
        fulfillment_uow_factory=factory(
            kit_reserves=SimpleNamespace(active_on_lots=lambda ids: [reserve]),
            fulfillment=SimpleNamespace(list=lambda limit, after_id=None: [kit] if after_id is None else []),
        ),
    ))

    holders = _reserve_holders(SimpleNamespace(app=app), [reserved])[reserved.id]

    assert [item["kind"] for item in holders] == ["procurement", "techcard", "kit"]
    assert sum(Decimal(item["quantity"]) for item in holders) == reserved.reserved == Decimal("45")
    note = _reserve_note(holders)
    assert "SM-20261002-01 / 591К.3 — 30 шт" in note
    assert "728К.2 · КБ1 · техкарта — 10 шт" in note
    assert "REQ-2026-0001 · 728К.2 · КБ2 · Жгут — 5 шт" in note


def test_unwired_modules_add_no_holders():
    target = lot().reserve(Decimal("3"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert _reserve_holders(request, [target]) == {}
