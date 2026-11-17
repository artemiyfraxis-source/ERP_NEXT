from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.devices import PrinterRow
from app.modules.warehouse.label_jobs import LabelAgentRow, LabelJobRow, add_job, add_marking_job
from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingProfileRow
from app.modules.warehouse.marking_render import Geometry, presets, render
from app.modules.warehouse.scan_jobs import AgentTokenRow
from app.shared.identity import Actor
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401

CLAIM = "/api/stock/labels/jobs/claim"
HEARTBEAT = "/api/stock/scan/agent/heartbeat"


def _agent(admin, machine):
    headers = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name=machine)["token"])
    payload = {"machine_id": machine, "pc_name": machine, "version": "2.9.0",
               "printers": [{"name": "Godex EZ2350i", "driver": "Godex", "port": "USB001", "kind": "gdi_png", "present": True}]}
    response = TestClient(app).post(HEARTBEAT, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return headers


def _batch(sessions, now):
    png, geometry = render("ТЕСТ 1", Geometry.model_validate(presets()[-1][2]))
    profile_id, batch_id = uuid4(), uuid4()
    with sessions.begin() as session:
        session.add(MarkingProfileRow(id=profile_id, inventory_number="ОБ.101", name="Тест", version=1,
                                     calibrated=False, geometry=presets()[-1][2]))
        session.add(MarkingBatchRow(id=batch_id, created_by=uuid4(), created_at=now, idempotency_key="batch",
            fingerprint="0" * 64, profile_id=profile_id, manifest={}, total=2, reason=""))
        for sequence in (1, 2):
            add_marking_job(session, batch_id=batch_id, sequence=sequence, png=png, geometry=geometry,
                machine_id="pc-1", printer="Godex EZ2350i", actor=Actor(uuid4(), "Тест", ""), now=now)
    return batch_id


def test_heartbeat_bound_claim_receipt_and_legacy_isolation(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    other = _agent(admin, "pc-2")
    _batch(sessions, clock.now)
    with sessions.begin() as session:
        tspl = add_job(session, tspl=b"PRINT 1,1", copies=1, kind="sample", lot_id=None, created_by="test", now=clock.now)
    payload = {"machine_id": "pc-1", "capabilities": ["gdi_png"]}
    assert admin.post(CLAIM, json=payload, headers=_csrf(admin)).status_code == 403
    assert service.post(CLAIM, json=payload, headers=other).status_code == 403
    job = service.post(CLAIM, json=payload, headers=agent).json()["job"]
    assert job["format"] == "gdi_png" and job["copies"] == 1 and job["printer"] == "Godex EZ2350i"
    assert service.post(CLAIM, json=payload, headers=agent).json()["job"] is None
    path = f"/api/stock/labels/jobs/{job['id']}/result"
    result = {"attempt": job["attempt"], "outcome": "spooled", "message": "Передано", "spool_job_id": 42}
    assert service.post(path, json=result, headers=other).status_code == 403
    assert service.post(path, json={**result, "attempt": str(uuid4())}, headers=agent).status_code == 409
    assert service.post(path, json=result, headers=agent).status_code == 200
    assert service.post(path, json=result, headers=agent).status_code == 200
    assert service.post(path, json={**result, "outcome": "unknown"}, headers=agent).status_code == 409
    assert service.post(f"/api/stock/labels/agent/jobs/{job['id']}/result", json={"ok": True}, headers=agent).status_code == 422
    # Omitted capability fields cannot turn a known Godex queue into a TSPL destination.
    assert service.post("/api/stock/labels/agent/claim", json={"host": "pc-1", "printer": "Godex EZ2350i"}, headers=agent).json()["job"] is None
    with sessions() as session:
        assert session.get(LabelJobRow, tspl).status == "pending"
        assert session.get(LabelJobRow, UUID(job["id"])).status == "spooled"
        assert session.get(LabelAgentRow, 1) is None
        printer = session.get(PrinterRow, ("pc-1", "Godex EZ2350i"))
        assert printer.driver == "Godex" and printer.port == "USB001"


def test_marking_timeout_and_disappeared_inventory_are_not_reprints(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    _batch(sessions, clock.now)
    payload = {"machine_id": "pc-1", "capabilities": ["gdi_png"]}
    job = service.post(CLAIM, json=payload, headers=agent).json()["job"]
    clock.now += timedelta(seconds=121)
    assert service.post(CLAIM, json=payload, headers=agent).json()["job"] is None
    late = service.post(f"/api/stock/labels/jobs/{job['id']}/result", headers=agent,
        json={"attempt": job["attempt"], "outcome": "spooled", "message": "Поздний ответ"})
    assert late.status_code == 409
    assert service.post(HEARTBEAT, headers=agent, json={"machine_id": "pc-1", "version": "2.9.0", "printers": []}).status_code == 200
    with sessions() as session:
        assert session.get(LabelJobRow, UUID(job["id"])).status == "unknown"
        assert session.get(PrinterRow, ("pc-1", "Godex EZ2350i")).present is False


def test_warehouse_route_survives_enrolled_token_replacement(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    first = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="old")["token"])
    heartbeat = {"machine_id": "warehouse", "pc_name": "warehouse", "version": "2.9.0",
                 "printers": [{"name": "TSC", "kind": "tspl", "present": True}]}
    assert service.post(HEARTBEAT, headers=first, json=heartbeat).status_code == 200
    payload = {"machine_id": "warehouse", "printer": "TSC", "host": "warehouse", "capabilities": ["tspl"]}
    assert service.post(CLAIM, json=payload, headers=first).status_code == 200
    second = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="new")["token"])
    # Reproduce enroll_agent's retained historical binding and active replacement.
    with sessions.begin() as session:
        old = session.scalar(select(AgentTokenRow).where(AgentTokenRow.machine_id == "warehouse"))
        old.retired = True
    assert service.post(HEARTBEAT, headers=second, json=heartbeat).status_code == 200
    with sessions.begin() as session:
        job_id = add_job(session, tspl=b"PRINT 1,1", copies=1, kind="sample", lot_id=None, created_by="test", now=clock.now)
    answer = service.post(CLAIM, json=payload, headers=second)
    assert answer.status_code == 200, answer.text
    assert answer.json()["job"]["id"] == str(job_id)
