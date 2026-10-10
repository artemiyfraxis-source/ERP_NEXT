from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.main import app
from app.modules.warehouse.application import stock_article_key
from app.modules.warehouse.domain import StockLot
from tests.warehouse.test_api import warehouse_api  # noqa: F401  (the fixture)


def _lot(index: int, *, article: str, series: str, quantity: str, warehouse: str = "ЦАР", item_type: str = "Комплектующие", place: str = "1F4") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=f"FRX.26.{index:07d}", package="", article=article, series=series,
        name=f"Позиция {article}", item_type=item_type, purpose="FRX.001", unit="шт", quantity=Decimal(quantity),
        storage_location=place, source_receiving_ids=("20000000-0000-0000-0000-000000000009",),
    )


@pytest.fixture()
def many_lots(warehouse_api):  # noqa: F811
    client, _first = warehouse_api
    factory = app.state.warehouse_uow_factory
    lots = [
        _lot(10 + i, article=f"ART-{i % 4}" if i % 7 else " art-1 ", series=f"LOT-{i % 3}", quantity=str(5 + i),
             warehouse="ЖУК" if i % 5 == 0 else "ЦАР", place=f"{1 + i % 3}F{1 + i}")
        for i in range(23)
    ]
    with factory() as uow:
        for lot in lots:
            uow.warehouse.add(lot)
        uow.commit()
    return client


def _all_pages(client, **params):
    lots, after = [], None
    while True:
        page = client.get("/api/stock", params={**params, "limit": 4, **({"after": after} if after else {})}).json()
        lots += page["lots"]
        after = page["next_cursor"]
        if not after:
            return lots


def _expected(lots, group_of):
    groups, articles = defaultdict(lambda: [Decimal(0), 0]), defaultdict(lambda: [Decimal(0), 0])
    for lot in lots:
        key = group_of(lot)
        article = stock_article_key(SimpleNamespace(article=lot["article"], name=lot["name"], grn=lot["grn"], package=""))
        groups[key][0] += Decimal(lot["balance"]); groups[key][1] += 1
        articles[(key, article)][0] += Decimal(lot["balance"]); articles[(key, article)][1] += 1
    return groups, articles


@pytest.mark.parametrize("params", [{}, {"zero": "true"}, {"warehouse": "ЖУК"}, {"q": "art-1"}])
def test_series_totals_equal_the_sums_over_every_page(many_lots, params):
    paged = _all_pages(many_lots, **params)
    answer = many_lots.get("/api/stock/totals", params={"group": "series", **params})
    assert answer.status_code == 200, answer.text
    groups, articles = _expected(paged, lambda lot: lot["series"].strip())

    got_groups = {row["group"]: row for row in answer.json()["groups"]}
    assert set(got_groups) == set(groups)
    for key, (balance, count) in groups.items():
        assert Decimal(got_groups[key]["balance"]) == balance and got_groups[key]["lots"] == count
        assert Decimal(got_groups[key]["available"]) == balance - Decimal(got_groups[key]["reserved"])
    got_articles = {(row["group"], row["article"]): row for row in answer.json()["articles"]}
    assert set(got_articles) == set(articles)
    for key, (balance, count) in articles.items():
        assert Decimal(got_articles[key]["balance"]) == balance and got_articles[key]["lots"] == count


def test_totals_without_group_and_by_place_use_the_table_keys(many_lots):
    plain = many_lots.get("/api/stock/totals").json()
    assert [row["group"] for row in plain["groups"]] == [""]
    assert plain["groups"][0]["lots"] == many_lots.get("/api/stock").json()["total"]
    by_place = many_lots.get("/api/stock/totals", params={"group": "place"}).json()
    assert {row["group"] for row in by_place["groups"]} <= {"1F", "2F", "3F"}


def test_totals_reject_an_unknown_group(many_lots):
    assert many_lots.get("/api/stock/totals", params={"group": "colour"}).status_code == 422
