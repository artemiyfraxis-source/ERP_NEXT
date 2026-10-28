from dataclasses import replace
from decimal import Decimal

import pytest

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.application import sort_stock_lots
from tests.warehouse.test_api import warehouse_api  # noqa: F401  (the fixture)


def _lot(number: int, *, article: str = "", quantity: str = "5", series: str = "") -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=f"OLD.26.{number:07d}", package="", article=article or f"ART-{number}", series=series,
        name=f"Позиция {number}", item_type="Комплектующие", purpose="FRX.001", unit="шт", quantity=Decimal(quantity),
        storage_location="1F4", source_receiving_ids=("20000000-0000-0000-0000-000000000009",),
    )


@pytest.fixture()
def lots_client(warehouse_api):  # noqa: F811
    client, _first = warehouse_api
    # ГРН is a text with a number: 0000598 must come before 0000100 when descending, whatever the insert (id) order.
    numbers = [595, 561, 559, 100, 598, 591, 592, 593, 594, 596, 597, 560, 9, 1000]
    with app.state.warehouse_uow_factory() as uow:
        for index, number in enumerate(numbers):
            uow.warehouse.add(_lot(number, quantity=str(1 + (index * 7) % 11), series=f"S{index % 3}"))
        uow.commit()
    return client


def _pages(client, **params):
    lots, after = [], None
    while True:
        page = client.get("/api/stock", params={**params, "limit": 3, **({"after": after} if after else {})}).json()
        lots += page["lots"]
        after = page["next_cursor"]
        if not after:
            return lots, page["total"]


def test_grn_descending_is_global_across_pages(lots_client):
    lots, total = _pages(lots_client, sort="grn", desc="true")
    grns = [lot["grn"] for lot in lots]
    assert grns[:8] == [f"OLD.26.{n:07d}" for n in (1000, 598, 597, 596, 595, 594, 593, 592)]
    assert grns == sorted(grns, reverse=True)
    assert total == len(lots) == len(set(grns)) > 3


def test_first_page_alone_already_has_the_global_top(lots_client):
    first = lots_client.get("/api/stock", params={"sort": "grn", "desc": "true", "limit": 3}).json()
    assert [lot["grn"] for lot in first["lots"]] == [f"OLD.26.{n:07d}" for n in (1000, 598, 597)]
    assert first["next_cursor"] is not None


@pytest.mark.parametrize("key,field", [("balance", "balance"), ("grn", "grn"), ("series", "series")])
@pytest.mark.parametrize("desc", [False, True])
def test_every_column_sorts_the_whole_stock(lots_client, key, field, desc):
    lots, _ = _pages(lots_client, sort=key, desc=str(desc).lower())
    if key == "balance":
        values = [Decimal(lot[field]) for lot in lots]
    else:
        values = [lot[field] for lot in lots]
    assert values == sorted(values, reverse=desc)
    assert len({lot["id"] for lot in lots}) == len(lots)  # no lot twice, none lost between pages


def test_unknown_sort_is_rejected(lots_client):
    assert lots_client.get("/api/stock", params={"sort": "id; drop"}).status_code == 422


def test_natural_order_of_numbers_in_text():
    first, second = _lot(9, article="A-9"), _lot(10, article="A-10")
    assert [lot.article for lot in sort_stock_lots([second, first], "article", False)] == ["A-9", "A-10"]
    assert [lot.article for lot in sort_stock_lots([first, second], "article", True)] == ["A-10", "A-9"]


def test_a_row_is_one_lot_so_lots_of_one_article_sort_apart():
    # Owner 28.10.2026: a row is one lot (since 27.10), so the order is the lots', not the summed article's — else the
    # first page holds the top articles and the screen re-sorts an incomplete set.
    big, small = _lot(1, article="SMA-C58P", quantity="744"), _lot(2, article="SMA-C58P", quantity="3")
    middle = _lot(3, article="RP-SMA", quantity="500")
    by_balance = sort_stock_lots([small, middle, big], "balance", True)
    assert [lot.balance for lot in by_balance] == [Decimal("744"), Decimal("500"), Decimal("3")]
    late = _lot(99, article="SMA-C58P")
    by_grn = sort_stock_lots([late, middle, big], "grn", False)
    assert [lot.grn for lot in by_grn] == ["OLD.26.0000001", "OLD.26.0000003", "OLD.26.0000099"]


def test_maker_and_document_columns_sort_the_whole_stock():
    first = replace(_lot(1), manufacturer="RUICHI", upd_name="SMA-Female")
    second = replace(_lot(2), manufacturer="MOLEX", upd_name="SMA-C58P")
    assert [lot.manufacturer for lot in sort_stock_lots([first, second], "maker", False)] == ["MOLEX", "RUICHI"]
    assert [lot.upd_name for lot in sort_stock_lots([first, second], "upd_name", True)] == ["SMA-Female", "SMA-C58P"]


def test_the_screen_gets_the_whole_stock_in_one_answer(warehouse_api):  # noqa: F811
    client, _first = warehouse_api
    with app.state.warehouse_uow_factory() as uow:
        for number in range(1, 251):
            uow.warehouse.add(_lot(number))
        uow.commit()
    page = client.get("/api/stock", params={"sort": "article", "group": "series"}).json()
    assert len(page["lots"]) == page["total"] >= 250
    assert page["next_cursor"] is None
