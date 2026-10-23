"""New lots take «XHP-4@JST» apart on the server (owner 23.10.2026): «Приход на склад», «Внести позиции» and the
`warehouse.received` event of «Приёмка» keep the bare article and put the maker into an empty «Производитель»;
«article-info» looks the bare article up."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.warehouse.application import dispatch_next_warehouse_received
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import StockLotRow
from tests.warehouse.test_application import Uow, message
from tests.warehouse.test_receipts import _accept, _receipt, stand  # noqa: F401  (fixture)


def _stored(sessions, lot_id: str) -> StockLotRow:
    with sessions() as session:
        return session.get(StockLotRow, UUID(lot_id))


def test_accepted_line_keeps_the_bare_article_and_the_maker_apart(stand):
    client, sessions = stand
    receipt = _receipt(client)

    plain = _accept(client, receipt["id"], article="XHP-4@JST", manufacturer="")
    kept = _accept(client, receipt["id"], article="0039012085@Molex", manufacturer="Molex Inc.", storage_location="2A2")
    packed = _accept(client, receipt["id"], article="SXH-001T-P0.6##6000@JST", storage_location="2A3")

    for response in (plain, kept, packed):
        assert response.status_code == 200, response.text
    rows = [_stored(sessions, response.json()["lot"]["id"]) for response in (plain, kept, packed)]
    assert [(row.article, row.manufacturer, row.description) for row in rows] == [
        ("XHP-4", "JST", ""),
        ("0039012085", "Molex Inc.", ""),  # the line's own maker wins
        ("SXH-001T-P0.6", "JST", "упаковка 6000 шт"),
    ]


def test_quick_entry_batches_split_the_article(stand):
    client, sessions = stand
    response = client.post("/api/stock/batches", json={"items": [
        {"article": "0500588001 @MOLEX", "quantity": "5", "storage_location": "1F4"},
        {"article": "GHR-04V-S", "quantity": "2", "storage_location": "1F5"},
    ]})
    assert response.status_code == 200, response.text
    assigned = response.json()["assigned"]
    assert [row["article"] for row in assigned] == ["0500588001", "GHR-04V-S"]
    with sessions() as session:
        rows = {row.article: row.manufacturer for row in session.scalars(select(StockLotRow))}
    assert rows == {"0500588001": "MOLEX", "GHR-04V-S": ""}


def test_article_info_looks_up_the_bare_article(stand):
    client, _ = stand
    receipt = _receipt(client)
    _accept(client, receipt["id"], article="XHP-4", name="Корпус XH 4", manufacturer="", storage_location="1F4")

    found = client.get("/api/stock/article-info", params={"q": "XHP-4@JST", "warehouse": "ЦАР"}).json()

    assert found["info"]["name"] == "Корпус XH 4"  # the lot «XHP-4» is the same article
    assert found["info"]["manufacturer"] == "JST"
    assert found["places"] == ["1F4"]


def test_received_event_splits_the_article_and_fills_an_empty_maker():
    split = replace(message(), payload={**message().payload, "article": "XHP-4@JST", "manufacturer": ""})
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), Uow(split))
    assert (lot.article, lot.manufacturer) == ("XHP-4", "JST")

    named = replace(message(), payload={**message().payload, "article": "XHP-4@JST", "manufacturer": "J.S.T."})
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), Uow(named))
    assert (lot.article, lot.manufacturer) == ("XHP-4", "J.S.T.")


def test_received_event_without_the_manufacturer_key_stays_valid_and_a_top_up_fills_an_empty_maker():
    old = message()  # events written before 23.10.2026 have no `manufacturer`
    assert "manufacturer" not in old.payload
    uow = Uow(replace(old, payload={**old.payload, "receiving_line_id": "receiving:two", "manufacturer": "Amphenol"}))
    imported = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Разъём", item_type="Комплектующие",
        purpose="FRX.001", unit="шт", quantity=Decimal("4"), storage_location="1F4",
        source_receiving_ids=("import:ЦАР:7",),
    )
    uow.warehouse.add(imported)
    lot = dispatch_next_warehouse_received(datetime(2026, 9, 22, 12, 0), uow)
    assert (lot.id, lot.manufacturer, lot.inbound) == (imported.id, "Amphenol", Decimal("14"))
    assert dispatch_next_warehouse_received(datetime(2026, 9, 21, 12, 1), Uow(old)).manufacturer == ""


def test_top_up_keeps_a_filled_maker():
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Разъём", item_type="Комплектующие",
        purpose="FRX.001", unit="шт", quantity=Decimal("4"), storage_location="1F4",
        source_receiving_ids=("import:ЦАР:7",), manufacturer="JST",
    )
    assert lot.receive(Decimal("1"), "receiving:two", manufacturer="Molex").manufacturer == "JST"
