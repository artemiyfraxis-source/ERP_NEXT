"""Device presence, requester isolation and routing independent of browser addresses."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.warehouse.devices import DeviceRow
from tests.identity.test_api_tokens import OPERATOR_ID, _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401
from tests.warehouse.test_vna_jobs import (  # noqa: F401
    CLAIM, HEARTBEAT, JOBS, NAME, S2_INSTRUMENT, capture, heartbeat, start,
)

SCAN = "/api/stock/receipts/scan-jobs"


def test_scanner_inventory_expires_even_while_its_agent_polls(capture):
    admin, agent, clock, sessions = capture
    payload = {"machine_id": "pc-1", "pc_name": "СКЛАД", "version": "2.6.0",
               "scanners": [{"id": "wia-1", "name": "Pantum"}, {"id": "wia-2", "name": "Canon"}]}
    assert TestClient(app).post(HEARTBEAT, json=payload, headers=agent).status_code == 200
    answer = admin.post(SCAN, json={}, headers=_csrf(admin)).json()
    assert answer["status"] == "choose"
    assert len(answer["agents"][0]["scanners"]) == 2
    payload["scanners"] = payload["scanners"][:1]
    TestClient(app).post(HEARTBEAT, json=payload, headers=agent)
    answer = admin.post(SCAN, json={}, headers=_csrf(admin)).json()
    assert answer["device"] == {"agent_id": "pc-1", "scanner_id": "wia-1", "name": "Pantum", "pc": "СКЛАД"}
    clock.now += timedelta(minutes=4)
    assert TestClient(app).post(CLAIM, json={"machine_id": "pc-1"}, headers=agent).status_code == 200
    assert admin.get("/api/stock/scan/agents").json()["agents"] == []
    assert admin.post(SCAN, json={}, headers=_csrf(admin)).json()["status"] == "no_agent"
    with sessions() as session:
        assert session.get(DeviceRow, ("pc-1", "scan", "wia-2")).present is False


@pytest.mark.parametrize("operation", ["claim", "result", "heartbeat"])
def test_scan_only_requests_enforce_machine_binding_and_requester(capture, operation):
    admin, agent, _, sessions = capture
    body = {"machine_id": "pc-1", "version": "2.6.0", "scanners": [{"id": "wia", "name": "Pantum"}]}
    TestClient(app).post(HEARTBEAT, json=body, headers=agent)
    job = admin.post(SCAN, json={}, headers=_csrf(admin)).json()["id"]
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2", vna=False)
    if operation == "claim":
        response = TestClient(app).post(CLAIM, json={"machine_id": "pc-1", "kinds": ["scan"]}, headers=other)
    elif operation == "heartbeat":
        response = TestClient(app).post(HEARTBEAT, json=body, headers=other)
    else:
        TestClient(app).post(CLAIM, json={"machine_id": "pc-1"}, headers=agent)
        response = TestClient(app).post(f"/api/stock/scan/agent/jobs/{job}/result", json={"error": "wrong"},
                                       headers={**other, "X-Agent-Machine-Id": "pc-1"})
    assert response.status_code == 403
    with sessions() as session:
        session.get(IdentityUserRow, OPERATOR_ID).access_level = "admin"
        session.commit()
    operator = _login("operator")
    assert operator.get(f"{SCAN}/{job}").status_code == 404
    assert operator.delete(f"{SCAN}/{job}", headers=_csrf(operator)).status_code == 404
    assert admin.get(f"{SCAN}/{job}").status_code == 200


def test_unknown_job_kind_is_rejected_before_queue_access(capture):
    _, agent, _, _ = capture
    heartbeat(agent)
    assert TestClient(app).post(CLAIM, json={"machine_id": "pc-1", "kinds": ["shell"]}, headers=agent).status_code == 422


@pytest.mark.parametrize("missing", ["expired", "disconnected", "uninstalled"])
def test_offline_vna_message_keeps_last_device_location_and_time(capture, missing):
    admin, agent, clock, _ = capture
    heartbeat(agent)
    if missing == "expired":
        clock.now += timedelta(minutes=4)
    elif missing == "disconnected":
        heartbeat(agent, capability={"name": NAME, "ready": False, "reason": "device_not_connected"})
    else:
        heartbeat(agent, vna=False, sources=[])
    answer = start(admin, "")
    assert answer["status"] == "no_agent"
    assert all(text in answer["message"] for text in ("11111111", "PC-1", "последний раз", "не запущена S2VNA"))


def test_missing_or_duplicate_serial_never_selects_an_arbitrary_pc(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    assert "несколько ПК" in start(admin)["message"]
    with sessions() as session:
        session.get(ProductionInstrumentRow, UUID(S2_INSTRUMENT)).serial_number = ""
        session.commit()
    assert "серийный номер" in start(admin)["message"]
