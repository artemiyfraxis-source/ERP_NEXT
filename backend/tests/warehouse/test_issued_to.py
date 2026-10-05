from __future__ import annotations

from uuid import uuid4

from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def _write_off(client, lot_id, quantity, purpose, version, date=None):
    response = client.post(f"/api/stock/batches/{lot_id}/writeoff", json={
        "version": version, "quantity": quantity, "purpose": purpose, **({"date": date} if date else {}),
    })
    assert response.status_code == 200, response.text
    return response.json()["lot"]["version"]


def test_issued_to_lists_destinations_with_what_is_still_out(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    # Distinct dates: the spelling shown is the latest write-off's, and equal timestamps have no stable order.
    version = _write_off(client, lot.id, "3", "FRX.001", lot.version, "2026-09-10")
    version = _write_off(client, lot.id, "2", "Иванов", version, "2026-09-20")
    version = _write_off(client, lot.id, "1", " frx.001 ", version, "2026-09-25")  # the same destination, other case and spaces
    returned = client.post(f"/api/stock/batches/{lot.id}/return", json={
        "version": version, "quantity": "1", "source": "FRX.001",
    })
    assert returned.status_code == 200, returned.text

    body = client.get(f"/api/stock/batches/{lot.id}/issued-to").json()

    assert [row["name"] for row in body["destinations"]] == ["frx.001", "Иванов"]
    project, person = body["destinations"]
    assert (project["written_off"], project["returned"], project["outstanding"], project["unit"]) == ("4", "1", "3", "шт")
    assert (person["written_off"], person["returned"], person["outstanding"]) == ("2", "0", "2")


def test_issued_to_omits_a_fully_returned_destination(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    version = _write_off(client, lot.id, "2", "FRX.001", lot.version)
    version = _write_off(client, lot.id, "1", "Петров", version, "2026-09-20")
    client.post(f"/api/stock/batches/{lot.id}/return", json={"version": version, "quantity": "2", "source": "frx.001"})

    body = client.get(f"/api/stock/batches/{lot.id}/issued-to").json()

    assert [row["name"] for row in body["destinations"]] == ["Петров"]


def test_issued_to_is_empty_for_a_lot_without_history_and_404_for_unknown(warehouse_api):  # noqa: F811
    client, lot = warehouse_api

    assert client.get(f"/api/stock/batches/{lot.id}/issued-to").json() == {"destinations": []}
    assert client.get(f"/api/stock/batches/{uuid4()}/issued-to").status_code == 404
