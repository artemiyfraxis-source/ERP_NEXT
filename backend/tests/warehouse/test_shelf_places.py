"""SK-15: the smallest free № on a shelf and the taken places (Stock05.gs stock05UsedPlaces_ 322-344,
PortalApp.html placeNextNum_ / wh05PlaceTaken_). A hint only — nothing is refused."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.warehouse_location import existing_shelf, next_free_number, places_on_shelf
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def test_empty_shelf_starts_at_one():
    assert next_free_number([], "12C") == 1
    assert places_on_shelf([], "12C") == []


def test_smallest_gap_is_offered_not_the_next_after_the_largest():
    assert next_free_number(["12C1", "12C2", "12C4"], "12C") == 3
    assert next_free_number(["12C2"], "12C") == 1


def test_a_cell_letter_does_not_free_the_number():
    assert next_free_number(["12C1A", "12C2B"], "12C") == 3
    assert places_on_shelf(["12C2B", "12C1", "12C1A", "12C2B"], "12C") == ["12C1", "12C1A", "12C2B"]


def test_other_rack_shelf_and_old_addresses_are_not_counted():
    occupied = ["12D1", "1C1", "11C1", "112C1", "A-01", "", "12C"]
    assert next_free_number(occupied, "12C") == 1
    assert places_on_shelf(occupied, "12C") == []


def test_leading_zeros_of_stored_codes_are_read():
    assert places_on_shelf(["01C001", "1c2"], "1C") == ["1C1", "1C2"]


def test_a_full_shelf_has_no_free_number():
    assert next_free_number([f"3A{n}" for n in range(1, 1000)], "3A") is None


@pytest.mark.parametrize("value, expected", [("12C", "12C"), (" 01в ", "1B"), ("1f", "1F")])
def test_shelf_code_is_normalized(value, expected):
    assert existing_shelf(value) == expected


@pytest.mark.parametrize("value", ["", "12", "C", "12CC", "12C4", "0C", "100A", "12Ы", "33A", "1AA"])
def test_bad_shelf_code_is_refused(value):
    with pytest.raises(ValueError):
        existing_shelf(value)


def _put(factory, place, *, warehouse="ЦАР", grn, emptied=False):
    lot = StockLot.create(
        warehouse=warehouse, grn=grn, package="", name="Разъём", item_type="Комплектующие", purpose="Склад",
        unit="шт", quantity=Decimal("5"), storage_location=place, source_receiving_ids=("20000000-0000-0000-0000-000000000009",),
    )
    if emptied:
        lot = replace(lot, written_off=lot.inbound)
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()


def test_api_offers_the_smallest_free_number_and_lists_taken_places(warehouse_api):  # noqa: F811
    client, _ = warehouse_api  # the fixture lot lies on 1F4 (ЦАР)
    factory = app.state.warehouse_uow_factory
    _put(factory, "1F1", grn="OLD.26.0000001")
    _put(factory, "1F2B", grn="OLD.26.0000002")
    _put(factory, "1F3", grn="OLD.26.0000003", emptied=True)  # an emptied lot frees its place
    _put(factory, "1F5", grn="OLD.26.0000004", warehouse="ЖУК")  # other warehouse: other city
    _put(factory, "1G1", grn="OLD.26.0000005")  # other shelf

    response = client.get("/api/stock/places", params={"warehouse": "ЦАР", "shelf": "1f"})

    assert response.status_code == 200, response.text
    assert response.json() == {"warehouse": "ЦАР", "shelf": "1F", "occupied": ["1F1", "1F2B", "1F4"], "next_number": 3}
    other = client.get("/api/stock/places", params={"warehouse": "ЖУК", "shelf": "1F"}).json()
    assert (other["occupied"], other["next_number"]) == (["1F5"], 1)


def test_api_empty_shelf_is_number_one(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    body = client.get("/api/stock/places", params={"warehouse": "ЦАР", "shelf": "9Z"}).json()

    assert (body["occupied"], body["next_number"]) == ([], 1)


@pytest.mark.parametrize("params", [
    {"warehouse": "ЦАР", "shelf": "33A"},  # no such rack
    {"warehouse": "ЦАР", "shelf": "12"},
    {"warehouse": "ЦАР", "shelf": "1F4"},
    {"warehouse": "СКЛ", "shelf": "1F"},
    {"warehouse": "ЦАР"},
])
def test_api_refuses_a_bad_shelf_or_warehouse(warehouse_api, params):  # noqa: F811
    client, _ = warehouse_api

    assert client.get("/api/stock/places", params=params).status_code == 422


def test_api_needs_warehouse_rights(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Инженер", department="РНД"), email="engineer@example.test",
        roles=frozenset({"engineer"}), permissions={"warehouse": "edit"},
    )

    assert client.get("/api/stock/places", params={"warehouse": "ЦАР", "shelf": "1F"}).status_code == 403
