"""SK-6 over HTTP: the manual entry takes the lot passport, the stock list returns it."""

import pytest

from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture

ITEM = {
    "warehouse": "ЦАР", "article": "WSH-M3", "name": "Шайба", "item_type": "Крепёж",
    "purpose": "Склад", "unit": "шт", "quantity": "500", "storage_location": "2A1",
}


def test_manual_stock_with_passport_is_listed_with_it(warehouse_api):  # noqa: F811
    client, existing = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{
        **ITEM, "mass_1": "", "mass_10": 0.5, "mass_100": "4,8", "expiry_date": "2030-05-01",
        "manufacturer": "Molex", "description": "Шайба плоская", "storage_conditions": "Сухо",
        "datasheet": "https://files.test/ds.pdf", "certificate": "РОСС RU.0001",
    }]})
    listed = {lot["article"]: lot for lot in client.get("/api/stock").json()["lots"]}

    assert response.status_code == 200, response.text
    assert {key: listed["WSH-M3"][key] for key in (
        "unit_mass_g", "expiry_date", "manufacturer", "description", "storage_conditions", "datasheet", "certificate",
    )} == {
        "unit_mass_g": "0.048", "expiry_date": "2030-05-01", "manufacturer": "Molex", "description": "Шайба плоская",
        "storage_conditions": "Сухо", "datasheet": "https://files.test/ds.pdf", "certificate": "РОСС RU.0001",
    }
    old = listed[existing.article]
    assert (old["unit_mass_g"], old["expiry_date"], old["manufacturer"]) == (None, None, "")


@pytest.mark.parametrize(("extra", "status", "detail"), [
    ({"expiry_date": "31.12.2030"}, 422, None),
    ({"manufacturer": "x" * 256}, 422, None),
    ({"mass_100": "x" * 65}, 422, None),
])
def test_manual_stock_rejects_a_bad_passport(warehouse_api, extra, status, detail):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{**ITEM, **extra}]})

    assert response.status_code == status, response.text
    if detail:
        assert response.json()["detail"] == detail


def test_manual_stock_mass_beyond_the_column_is_the_row_error(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{**ITEM, "mass_1": "1e13"}]})

    assert response.status_code == 200, response.text
    assert (response.json()["added"], response.json()["errors"]) == (
        0, [{"i": 0, "error": "Масса 1 шт слишком большая"}],
    )


def test_manual_stock_ignores_an_unreadable_mass_as_supplymap_does(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{**ITEM, "mass_1": "abc"}]})

    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["unit_mass_g"] is None
