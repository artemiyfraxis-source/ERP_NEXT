"""Device presence, requester isolation and routing independent of browser addresses."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.warehouse.devices import DeviceRow, VnaBindingRow
from app.modules.warehouse.scan_jobs import ScanJobClosed, create_job
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import ADMIN_ID, OPERATOR_ID, _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401
from tests.warehouse.test_vna_jobs import (  # noqa: F401
    CLAIM, HEARTBEAT, JOBS, NAME, S2_INSTRUMENT, capture, claim, heartbeat, result, start,
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
    assert {item["scanner_id"] for item in admin.get("/api/stock/scan/agents").json()["offline_devices"]} == {"wia-1", "wia-2"}
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
    assert "закреплён за ПК PC-1; ПК PC-2 тоже сообщает" in start(admin)["message"]
    with sessions() as session:
        session.get(ProductionInstrumentRow, UUID(S2_INSTRUMENT)).serial_number = ""
        session.commit()
    assert "серийный номер" in start(admin)["message"]


def test_offline_owner_cannot_be_replaced_by_another_reporter(capture):
    admin, agent, clock, sessions = capture
    heartbeat(agent)
    clock.now += timedelta(minutes=4)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    for _ in range(2):
        answer = start(admin, "pc-2")
        assert answer["status"] == "no_agent"
        assert answer["message"] == ("ВАЦ № 26030095 закреплён за ПК PC-1; ПК PC-2 тоже сообщает этот номер — "
                                     "перенесите прибор, если его переставили")
        assert answer["transfer"]["owner_id"] == "pc-1"
        clock.now += timedelta(minutes=4)
        heartbeat(other, "pc-2")
    with sessions() as session:
        assert session.get(VnaBindingRow, "26030095").machine_id == "pc-1"
        assert session.get(DeviceRow, ("pc-2", "s2vna", "26030095")).present
        session.add(VnaBindingRow(serial="26030095", machine_id="pc-2"))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("claimed", [False, True])
def test_admin_transfer_is_audited_and_closes_old_jobs(capture, claimed):
    admin, agent, clock, sessions = capture
    heartbeat(agent)
    old_job = start(admin)["id"]
    if claimed:
        claim(agent, kinds=["vna"])
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    payload = {"instrument_id": S2_INSTRUMENT, "serial": "26030095", "owner_id": "pc-1", "agent_id": "pc-2"}
    response = admin.post(f"{JOBS}/transfer", json=payload, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    assert admin.get(f"{JOBS}/{old_job}").json()["status"] == "failed"
    assert claim(agent, kinds=["vna"]) is None
    assert result(agent, old_job).status_code == 409
    assert start(admin)["agent"]["id"] == "pc-2"
    # A stale page must not transfer over a newer administrator's decision.
    assert admin.post(f"{JOBS}/transfer", json=payload, headers=_csrf(admin)).status_code == 409
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "warehouse.vna_transferred")).all()
        assert len(events) == 1
        assert events[0].actor_id == ADMIN_ID
        assert events[0].aggregate_id == UUID(S2_INSTRUMENT)
        assert events[0].details == {"serial": "26030095", "from_machine_id": "pc-1", "to_machine_id": "pc-2"}
    with pytest.raises(ScanJobClosed, match="Привязка ВАЦ изменилась"):
        create_job(SqlAlchemyWarehouseUnitOfWork(sessions), requested_by=str(ADMIN_ID), now=clock.now,
            target="pc-1", kind="vna", vna_name=NAME, expected_vna_serial="26030095")
    # Even the former owner's fresh heartbeat cannot take it back.
    heartbeat(agent)
    assert start(admin)["status"] == "no_agent"
    with sessions() as session:
        assert session.get(VnaBindingRow, "26030095").machine_id == "pc-2"


def test_transfer_works_after_owner_goes_offline(capture):
    admin, agent, clock, sessions = capture
    heartbeat(agent)
    clock.now += timedelta(minutes=4)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    assert admin.post(f"{JOBS}/transfer", json={"instrument_id": S2_INSTRUMENT, "serial": "26030095", "owner_id": "pc-1", "agent_id": "pc-2"},
                      headers=_csrf(admin)).status_code == 200
    assert start(admin)["agent"]["id"] == "pc-2"


@pytest.mark.parametrize("who", ["operator", "agent"])
def test_transfer_requires_existing_admin_permission(capture, who):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    person = _login("operator") if who == "operator" else TestClient(app)
    response = person.post(f"{JOBS}/transfer", json={"instrument_id": S2_INSTRUMENT, "serial": "26030095", "owner_id": "pc-1", "agent_id": "pc-2"},
                           headers=_csrf(person) if who == "operator" else agent)
    assert response.status_code == 403
    with sessions() as session:
        assert session.get(VnaBindingRow, "26030095").machine_id == "pc-1"
        assert session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "warehouse.vna_transferred")) is None


@pytest.mark.parametrize("target", ["missing", "pc-1", "expired", "different-serial"])
def test_transfer_rejects_unconfirmed_destinations(capture, target):
    admin, agent, clock, sessions = capture
    heartbeat(agent)
    if target in ("expired", "different-serial"):
        other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
        heartbeat(other, "pc-2", capability={"name": NAME if target == "expired" else NAME.replace("26030095", "99999"), "ready": True})
        if target == "expired":
            clock.now += timedelta(minutes=4)
        target = "pc-2"
    response = admin.post(f"{JOBS}/transfer", json={"instrument_id": S2_INSTRUMENT, "serial": "26030095", "owner_id": "pc-1", "agent_id": target}, headers=_csrf(admin))
    assert response.status_code == 409
    with sessions() as session:
        assert session.get(VnaBindingRow, "26030095").machine_id == "pc-1"


def test_transfer_rejects_a_serial_edited_after_the_confirmation_was_shown(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    shown = start(admin)["transfer"]
    new_name = NAME.replace("26030095", "99999")
    heartbeat(agent, capability={"name": new_name, "ready": True})
    heartbeat(other, "pc-2", capability={"name": new_name, "ready": True})
    with sessions() as session:
        session.get(ProductionInstrumentRow, UUID(S2_INSTRUMENT)).serial_number = "99999"
        session.commit()
    response = admin.post(f"{JOBS}/transfer", json={"instrument_id": S2_INSTRUMENT, "serial": shown["serial"],
        "owner_id": shown["owner_id"], "agent_id": "pc-2"}, headers=_csrf(admin))
    assert response.status_code == 409
    assert "Серийный номер" in response.json()["detail"]
    with sessions() as session:
        assert session.get(VnaBindingRow, "99999").machine_id == "pc-1"
        assert session.get(VnaBindingRow, "26030095").machine_id == "pc-1"
        assert session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "warehouse.vna_transferred")) is None
