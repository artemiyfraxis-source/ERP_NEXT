"""ID-6: «Приёмка» in view mode — SUPPLYMAP prodhead/management/master/staff see the waitlist as «просмотр»;
without canReceive nothing is inspected, placed or revised."""

import pytest

from tests.receiving.test_api import receiving_api  # noqa: F401 — fixture
from tests.view_access import as_viewer, assert_every_write_refused


@pytest.mark.parametrize("role", ["prodhead", "management", "master", "staff"])
def test_view_role_reads_the_receiving_queue(receiving_api, role):  # noqa: F811
    client, line = receiving_api
    as_viewer(role, "receiving")

    response = client.get("/api/receiving")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["lines"]] == [str(line.id)]


def test_view_mode_refuses_every_write(receiving_api):  # noqa: F811
    client, _ = receiving_api
    as_viewer("master", "receiving")

    assert_every_write_refused(client, "receiving")


def test_receiver_downgraded_to_view_cannot_inspect(receiving_api):  # noqa: F811
    client, line = receiving_api
    as_viewer("receiver", "receiving", "view")

    assert client.get("/api/receiving").status_code == 200
    response = client.post(f"/api/receiving/{line.id}/place", json={"version": line.version, "storage_location": "1F4"})
    assert response.status_code == 403
    assert_every_write_refused(client, "receiving")


def test_putaway_sheet_is_storekeeper_work(receiving_api):  # noqa: F811
    """«Лист перемещения» is printed by the storekeeper to place lots (WH-6), so it keeps the edit gate."""
    client, _ = receiving_api
    as_viewer("management", "receiving")

    assert client.get("/api/receiving/putaway-sheet").status_code == 403


def test_role_outside_the_section_cannot_read(receiving_api):  # noqa: F811
    client, _ = receiving_api
    as_viewer("buyer", "receiving")

    assert client.get("/api/receiving").status_code == 403
