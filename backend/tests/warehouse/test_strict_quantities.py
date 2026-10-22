"""Quantities move only through movements: the row edit may not change inbound / written_off / reserved by hand
(write-off, return and transfer keep the journal complete)."""

import pytest

from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def _lot(client):
    [lot] = client.get("/api/stock").json()["lots"]
    return lot


@pytest.mark.parametrize("field,value", [("inbound", "12"), ("written_off", "1"), ("reserved", "1")])
def test_a_quantity_edit_is_refused(warehouse_api, monkeypatch, field, value):  # noqa: F811
    client, lot = warehouse_api
    monkeypatch.delenv("ERP_STAGE_OPTIONS", raising=False)  # production: no stage options file
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, field: value})
    assert response.status_code == 422
    assert response.json()["detail"] == "Количества меняются только движениями: списание, возврат, перемещение"
    after = _lot(client)
    assert (after["inbound"], after["written_off"], after["reserved"], after["version"]) == (
        format(lot.inbound, "f"), "0", "0", lot.version)


def test_a_quantity_edit_mixed_with_other_fields_changes_nothing(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={
        "version": lot.version, "manufacturer": "Molex", "written_off": "3",
    })
    assert response.status_code == 422
    assert _lot(client)["version"] == lot.version


def test_other_fields_and_unchanged_quantities_are_allowed(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    response = client.put(f"/api/stock/batches/{lot.id}/row", json={
        "version": lot.version, "manufacturer": "Molex", "inbound": "10", "written_off": "0", "reserved": "0",
    })
    assert response.status_code == 200, response.text
    assert response.json()["lot"]["manufacturer"] == "Molex"
