"""VNA transport only; all agents are HTTP mocks, no instrument is contacted."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.warehouse.scan_jobs import ScanJobRow, VNA_FILE_LIMIT
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401

JOBS = "/api/stock/vna/jobs"
CLAIM = "/api/stock/scan/agent/claim"
HEARTBEAT = "/api/stock/scan/agent/heartbeat"
NAME = "Planar, S50180, 26030095, 26.2.2/2"
TRACE = f"! S2VNA {NAME} 2026-11-12T10:00:00Z S11\n# Hz S RI R 50\n100000000 0.1 0\n200000000 0.2 0\n".encode()


@pytest.fixture()
def capture(tokens_api):  # noqa: F811
    sessions, clock = tokens_api
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    return admin, agent, clock, sessions


def heartbeat(agent, machine="pc-1", vna=True, capability=None):
    payload = {"machine_id": machine, "pc_name": machine.upper(), "version": "2.3.1", "scanners": []}
    if vna:
        payload["vna"] = capability if capability is not None else {"name": NAME, "ready": True}
    response = TestClient(app).post(HEARTBEAT, json=payload, headers=agent)
    assert response.status_code == 200, response.text


def start(admin, machine="pc-1"):
    response = admin.post(JOBS, json={"agent_id": machine}, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    return response.json()


def claim(agent, machine="pc-1", kinds=None):
    payload = {"machine_id": machine}
    if kinds is not None:
        payload["kinds"] = kinds
    response = TestClient(app).post(CLAIM, json=payload, headers=agent)
    assert response.status_code == 200, response.text
    return response.json()["job"]


def result(agent, job, content=TRACE, machine="pc-1", name="trace.s1p"):
    return TestClient(app).post(f"/api/stock/scan/agent/jobs/{job}/result",
        files={"file": (name, content, "application/octet-stream")}, headers={**agent, "X-Agent-Machine-Id": machine})


def test_capture_requires_explicit_pc_and_old_agents_never_claim_it(capture):
    admin, agent, _, _ = capture
    assert start(admin)["status"] == "no_agent"
    heartbeat(agent)
    heartbeat(agent, "pc-2")
    choices = start(admin, "")
    assert choices["status"] == "choose"
    assert len(choices["agents"]) == 2
    assert choices["agents"][0]["vna"]["name"] == NAME
    job = start(admin)
    assert job["agent"]["id"] == "pc-1"
    assert claim(agent) is None
    assert claim(agent, "pc-2", ["vna"]) is None
    assert claim(agent, "", ["vna"]) is None
    assert claim(agent, kinds=["vna"])["id"] == job["id"]
    assert claim(agent, kinds=["vna"]) is None


@pytest.mark.parametrize("ready,reason", [(True, ""), (False, "s2vna_not_running"),
    (False, "device_not_connected"), (False, "probe_failed")])
def test_heartbeat_readiness_reason_survives_the_pc_list_and_can_be_cleared(capture, ready, reason):
    admin, agent, _, _ = capture
    capability = {"name": "S2VNA", "ready": ready, "reason": reason}
    heartbeat(agent, capability=capability)
    assert start(admin, "")["agents"][0]["vna"] == capability
    heartbeat(agent)  # Compatibility with clients that do not send a reason; clear stale reason.
    assert start(admin, "")["agents"][0]["vna"] == {"name": NAME, "ready": True, "reason": ""}


def test_status_file_and_retry_keep_exact_trace_and_actual_instrument(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    assert admin.get(f"{JOBS}/{job}/file").status_code == 409
    assert result(agent, job).status_code == 409
    assert claim(agent, kinds=["vna"])["kind"] == "vna"
    assert admin.get(f"{JOBS}/{job}").json()["status"] == "claimed"
    assert result(agent, job, machine="pc-2").status_code == 403
    actual = TRACE.replace(b"26030095", b"99999999")
    assert result(agent, job, actual).json()["status"] == "done"
    view = admin.get(f"{JOBS}/{job}").json()
    assert view["vna"]["name"] == NAME.replace("26030095", "99999999")
    file = admin.get(f"{JOBS}/{job}/file")
    assert file.content == actual
    assert "attachment" in file.headers["content-disposition"]
    assert result(agent, job).status_code == 409
    assert admin.get(f"/api/stock/receipts/scan-jobs/{job}").status_code == 404
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job))
        assert row.file_id is None and row.result == ""  # No receipt OCR is scheduled.


@pytest.mark.parametrize("content,name", [(b"", "x.s1p"), (TRACE, "x.pdf"), (b"garbage", "x.s1p"),
    (TRACE + b" " * VNA_FILE_LIMIT, "x.s1p"), (b"x" * (VNA_FILE_LIMIT + 65537), "x.s1p")],
    ids=["empty", "extension", "provenance", "file-limit", "body-limit"])
def test_invalid_or_large_capture_fails_the_job(capture, content, name):
    admin, agent, _, _ = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    assert result(agent, job, content, name=name).status_code == 422
    view = admin.get(f"{JOBS}/{job}").json()
    assert view["status"] == "failed" and view["error"]


def test_agent_error_is_returned_verbatim(capture):
    admin, agent, _, _ = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    error = "Активная трасса — S21: выберите S11 или S22 в S2VNA"
    response = TestClient(app).post(f"/api/stock/scan/agent/jobs/{job}/result", json={"error": error},
                                  headers={**agent, "X-Agent-Machine-Id": "pc-1"})
    assert response.status_code == 200
    assert admin.get(f"{JOBS}/{job}").json()["error"] == error


@pytest.mark.parametrize("claimed,seconds", [(False, 121), (True, 181)])
def test_capture_expires_and_late_result_is_rejected(capture, claimed, seconds):
    admin, agent, clock, _ = capture
    heartbeat(agent)
    job = start(admin)["id"]
    if claimed:
        claim(agent, kinds=["vna"])
    clock.now += timedelta(seconds=seconds)
    view = admin.get(f"{JOBS}/{job}").json()
    assert view["status"] == "failed" and "ВАЦ не ответил" in view["error"]
    assert result(agent, job).status_code == 409


def test_cancel_and_capability_removal(capture):
    admin, agent, _, _ = capture
    heartbeat(agent)
    job = start(admin)["id"]
    assert admin.delete(f"{JOBS}/{job}", headers=_csrf(admin)).json()["status"] == "failed"
    assert claim(agent, kinds=["vna"]) is None
    heartbeat(agent, vna=False)
    assert start(admin)["status"] == "no_agent"


def test_claim_touch_does_not_make_stale_vna_capability_fresh(capture):
    admin, agent, clock, _ = capture
    heartbeat(agent)
    clock.now += timedelta(minutes=4)
    claim(agent, kinds=["vna"])
    assert start(admin)["status"] == "no_agent"


def test_quality_permission_scope_and_requester_isolation(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    wrong_scope = _bearer(_issue(admin, scopes=("labels:print",))["token"])
    assert TestClient(app).post(CLAIM, json={"machine_id": "pc-1", "kinds": ["vna"]}, headers=wrong_scope).status_code == 403
    assert admin.post(CLAIM, json={}, headers=_csrf(admin)).status_code == 403
    assert TestClient(app).post(JOBS, json={}, headers=agent).status_code == 403
    operator = _login("operator")
    assert operator.post(JOBS, json={}, headers=_csrf(operator)).status_code == 403
    assert operator.get(f"{JOBS}/{job}/file").status_code == 403
    assert admin.post(JOBS, json={}).status_code == 403  # CSRF
    with sessions() as session:
        session.get(ScanJobRow, UUID(job)).requested_by = "another-user-id"
        session.commit()
    assert admin.get(f"{JOBS}/{job}").status_code == 404
    assert admin.get(f"{JOBS}/{job}/file").status_code == 404


def test_database_rejects_cross_kind_payload_and_done_without_content(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job))
        row.kind = "unknown"
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        row = session.get(ScanJobRow, UUID(job))
        row.capture_content = TRACE
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        row = session.get(ScanJobRow, UUID(job))
        row.status = "done"
        row.finished_at = row.claimed_at
        with pytest.raises(IntegrityError):
            session.commit()
