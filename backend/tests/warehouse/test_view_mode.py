"""ID-6: «Склад» in view mode — SUPPLYMAP portalStock05List/Refs pass portalErpGuard_ (any grant), every write goes
through portalStock05Guard_ («Склад открыт вам только на просмотр»)."""

import pytest

from tests.view_access import as_viewer, assert_every_write_refused
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


@pytest.mark.parametrize("role", ["buyer", "prodhead", "management", "master", "staff"])
def test_view_role_reads_stock_and_places(warehouse_api, role):  # noqa: F811
    client, lot = warehouse_api
    as_viewer(role, "warehouse")

    stock = client.get("/api/stock")
    places = client.get("/api/stock/places", params={"warehouse": "ЦАР", "shelf": "1F"})

    assert stock.status_code == 200, stock.text
    assert [item["id"] for item in stock.json()["lots"]] == [str(lot.id)]
    assert places.status_code == 200, places.text


def test_view_mode_refuses_every_write(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    as_viewer("management", "warehouse")

    assert_every_write_refused(client, "warehouse")


def test_storekeeper_downgraded_to_view_cannot_write(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    as_viewer("receiver", "warehouse", "view")

    assert client.get("/api/stock").status_code == 200
    response = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "1", "purpose": "FRX.001",
    })
    assert response.status_code == 403
    assert_every_write_refused(client, "warehouse")


@pytest.mark.parametrize("path", ["/api/stock/export", "/api/stock/item-types", "/api/stock/article-info?article=X"])
def test_edit_tools_stay_closed_in_view_mode(warehouse_api, path):  # noqa: F811
    """portalStock05Export / TypesGet use the edit guard (FixGuard), ArtInfo feeds the entry form (Stock05Guard)."""
    client, _ = warehouse_api
    as_viewer("management", "warehouse")

    assert client.get(path).status_code == 403


def test_a_level_without_the_section_cannot_read(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    as_viewer("tech_lead", "fulfillment", "edit")  # access levels: «Склад» closed in the matrix

    assert client.get("/api/stock").status_code == 403


def test_no_grant_no_read(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    as_viewer("management", "production")

    assert client.get("/api/stock").status_code == 403
