"""ID-6: «Лист закупки» in view mode — SUPPLYMAP receiver/prodhead/management get it as «просмотр — что едет»
(Portal.gs portalButtons_); portalApplyAccess_ 'view' drops edit and canWork, so nothing is taken or changed."""

import pytest

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.procurement.test_api import procurement_api  # noqa: F401 — fixture
from tests.view_access import as_viewer, assert_every_write_refused


def _take_as_buyer(client, line) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    assert client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).status_code == 200


@pytest.mark.parametrize("role", ["receiver", "prodhead", "management"])
def test_view_role_sees_free_and_in_work_lines(procurement_api, role):  # noqa: F811
    client, line, _ = procurement_api
    as_viewer(role, "procurement")

    available = client.get("/api/procurement/available")
    assert available.status_code == 200, available.text
    assert [item["id"] for item in available.json()["lines"]] == [str(line.id)]
    assert client.get("/api/procurement/in-work").json()["lines"] == []

    _take_as_buyer(client, line)
    as_viewer(role, "procurement")

    in_work = client.get("/api/procurement/in-work")
    assert in_work.status_code == 200, in_work.text
    assert [(item["id"], item["buyer"]) for item in in_work.json()["lines"]] == [(str(line.id), "Петров")]
    assert client.get("/api/procurement/available").json()["lines"] == []


def test_view_mode_refuses_every_write(procurement_api):  # noqa: F811
    client, _, _ = procurement_api
    as_viewer("receiver", "procurement")

    assert_every_write_refused(client, "procurement")


def test_buyer_downgraded_to_view_cannot_take(procurement_api):  # noqa: F811
    client, line, _ = procurement_api
    as_viewer("buyer", "procurement", "view")

    assert client.get("/api/procurement/available").status_code == 200
    assert client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).status_code == 403
    assert_every_write_refused(client, "procurement")


@pytest.mark.parametrize("path", ["/api/procurement/mine", "/api/procurement/kp?reference=PO-001"])
def test_buyer_work_queues_stay_closed_in_view_mode(procurement_api, path):  # noqa: F811
    """«Мои позиции» and the КП form are the buyer's own work, not a reading of the sheet."""
    client, _, _ = procurement_api
    as_viewer("management", "procurement")

    assert client.get(path).status_code == 403


def test_in_work_queue_needs_the_procurement_section(procurement_api):  # noqa: F811
    # Access levels: the matrix decides; another section open on edit does not open «Лист закупки».
    client, _, _ = procurement_api
    as_viewer("operator", "requests", "edit")

    assert client.get("/api/procurement/in-work").status_code == 403
    assert client.get("/api/procurement/available").status_code == 403
