"""Stock search by words: the pure matcher and the stock list over HTTP."""

from decimal import Decimal

import pytest

from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.word_search import matches_words
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def lot(**overrides) -> StockLot:
    return StockLot.create(**{
        "warehouse": "ЦАР", "grn": "FRX.26.0000001", "package": "П2", "article": "0430250600",
        "name": "Разъём Molex Micro-Fit", "item_type": "Разъёмы", "purpose": "FRX.001", "unit": "шт",
        "series": "LOT-77", "quantity": Decimal("10"), "storage_location": "1F4", "source_receiving_ids": ("r1",),
        **overrides,
    })


@pytest.mark.parametrize("query", [
    "molex разъем", "разъем molex", "  MOLEX   micro-fit ", "1f4 molex", "lot-77 разъёмы", "frx.001 1f4",
    "430250600 molex", "0430250600", "43-0250-600", "frx.26.0000001-П2", "frx.26.0000001 п2",
    "", "   ",
])
def test_every_word_found_in_some_field(query):
    assert matches_words(lot(), query)


def test_latin_and_cyrillic_lookalikes_are_one_letter():
    assert matches_words(lot(name="Крепёж А1В2"), "A1B2")
    assert matches_words(lot(name="Резистор СТ"), "ct резистор")
    assert matches_words(lot(name="Крепёж A1B2"), "а1в2")


@pytest.mark.parametrize("query", ["molex zzz", "конденсатор", "1f5 molex", "frx.26.0000001-п3", "430250601"])
def test_a_missing_word_means_no_match(query):
    assert not matches_words(lot(), query)


def test_a_word_without_digits_matches_from_a_word_start():
    # Owner: «Male» found «Female» (substring). Letters match from a word start, codes with digits stay substrings.
    female = lot(name="Разъём SMA-Female RG-316", article="S-211L")
    male = lot(name="Разъём SMA-Male RG-316", article="S-111L")
    assert matches_words(male, "male") and not matches_words(female, "male")
    assert matches_words(female, "female") and matches_words(female, "sma fem")
    assert matches_words(female, "211") and matches_words(male, "rg-316") and matches_words(male, "разъ")
    assert not matches_words(lot(name="Резистор С2-33Н"), "истор")  # not from a word start
    assert matches_words(lot(article="GHR-04V-S"), "ghr") and not matches_words(lot(article="GHR-04V-S"), "hr")
    assert matches_words(lot(article="GHR-04V-S"), "hr-04")  # with digits: a substring, in the article too


def _add(client):
    response = client.post("/api/stock/batches", json={"items": [{
        "warehouse": "ЦАР", "article": "0430250600", "name": "Разъём Molex", "quantity": "2",
        "storage_location": "2A1",
    }]})
    assert response.status_code == 200, response.text


def _found(client, query):
    result = client.get("/api/stock", params={"q": query})
    assert result.status_code == 200, result.text
    return result.json()["lots"]


def test_the_stock_list_searches_by_words_with_article_normalization(warehouse_api, monkeypatch):  # noqa: F811
    client, _ = warehouse_api
    monkeypatch.delenv("ERP_STAGE_OPTIONS", raising=False)  # production: no stage options file
    _add(client)
    assert len(_found(client, "molex")) == 1
    assert len(_found(client, "molex разъем")) == 1
    assert len(_found(client, "43-0250-600 2a1")) == 1
    assert _found(client, "430250600 1f4") == []
