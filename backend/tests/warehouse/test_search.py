"""SK-8: warehouse search by words (SUPPLYMAP Stock05.gs stock05Match_ 114-121)."""

from decimal import Decimal

import pytest

from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseRepository

matches = SqlAlchemyWarehouseRepository._matches


def lot(**overrides) -> StockLot:
    return StockLot.create(**{
        "warehouse": "ЦАР", "grn": "FRX.26.0000001", "package": "P1", "article": "R-001",
        "name": "Резистор С2-33Н Б2", "item_type": "Комплектующие", "purpose": "FRX.001", "unit": "шт",
        "quantity": Decimal("10"), "storage_location": "1F4", "source_receiving_ids": ("r1",), **overrides,
    })


@pytest.mark.parametrize("query", [
    "резистор б2", "б2 резистор", "  РЕЗИСТОР    б2  ", "Резистор С2-33Н Б2", "резистор", "", "   ",
    "резистор 1f4", "r-001 комплектующие", "frx.26.0000001 p1",
])
def test_every_word_found_in_some_field_matches(query):
    assert matches(lot(), query)


@pytest.mark.parametrize("query", ["резистор в2", "конденсатор", "резистор б2 zzz", "1f5 резистор"])
def test_a_missing_word_means_no_match(query):
    assert not matches(lot(), query)
