"""VNA transport only; all agents are HTTP mocks, no instrument is contacted."""

from datetime import timedelta
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.infrastructure import ProductionInstrumentRow
from app.modules.warehouse.scan_jobs import ScanJobRow, VNA_FILE_LIMIT
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401

JOBS = "/api/stock/vna/jobs"
CLAIM = "/api/stock/scan/agent/claim"
HEARTBEAT = "/api/stock/scan/agent/heartbeat"
NAME = "Planar, S50180, 26030095, 26.2.2/2"
TRACE = f"! S2VNA {NAME} 2026-11-12T10:00:00Z S11\n# Hz S RI R 50\n100000000 0.1 0\n200000000 0.2 0\n".encode()
S2_INSTRUMENT = "11111111-1111-1111-1111-111111111111"
ARINST_INSTRUMENT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def capture(tokens_api):  # noqa: F811
    sessions, clock = tokens_api
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    with sessions() as session:
        for id, vna_app in ((S2_INSTRUMENT, "S2VNA"), (ARINST_INSTRUMENT, "Arinst VL")):
            session.add(ProductionInstrumentRow(id=UUID(id), version=1, inventory_number=id[:8],
                name=vna_app, instrument_type="ВАЦ", kind="Средство измерений", status="Действует", serial_number="26030095",
                verification_number="test", verified_at=clock.now - timedelta(days=1),
                verification_due=clock.now + timedelta(days=30), vna_app=vna_app))
        session.commit()
    return admin, agent, clock, sessions


def heartbeat(agent, machine="pc-1", vna=True, capability=None, sources=None, lan_ip=""):
    payload = {"machine_id": machine, "pc_name": machine.upper(), "version": "2.4.0", "scanners": [], "lan_ip": lan_ip}
    if vna:
        payload["vna"] = capability if capability is not None else {"name": NAME, "ready": True}
    if sources is not None:
        payload["vna_sources"] = sources
    response = TestClient(app).post(HEARTBEAT, json=payload, headers=agent)
    assert response.status_code == 200, response.text


def start(admin, machine="pc-1", source="s2vna"):
    response = admin.post(JOBS, json={"agent_id": machine,
        "instrument_id": ARINST_INSTRUMENT if source == "arinst" else S2_INSTRUMENT}, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    return response.json()


def claim(agent, machine="pc-1", kinds=None):
    payload = {"machine_id": machine}
    if kinds is not None:
        payload["kinds"] = kinds
    response = TestClient(app).post(CLAIM, json=payload, headers=agent)
    assert response.status_code == 200, response.text
    return response.json()["job"]


def result(agent, job, content=TRACE, machine="pc-1", name="trace.s1p", settings=None):
    return TestClient(app).post(f"/api/stock/scan/agent/jobs/{job}/result",
        files={"file": (name, content, "application/octet-stream")},
        data={"capture_settings": json.dumps(settings)} if settings is not None else None,
        headers={**agent, "X-Agent-Machine-Id": machine})


def test_capture_routes_by_serial_and_old_agents_never_claim_it(capture):
    admin, agent, _, _ = capture
    assert start(admin)["status"] == "no_agent"
    heartbeat(agent)
    job = start(admin, "")
    assert job["agent"]["id"] == "pc-1"
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2", capability={"name": NAME.replace("26030095", "99999999"), "ready": True})
    assert claim(agent) is None
    assert claim(other, "pc-2", ["vna"]) is None
    assert TestClient(app).post(CLAIM, json={"kinds": ["vna"]}, headers=agent).status_code == 403
    assert claim(agent, kinds=["vna"])["id"] == job["id"]
    assert claim(agent, kinds=["vna"]) is None


@pytest.mark.parametrize("ready,reason", [(True, ""), (False, "s2vna_not_running"),
    (False, "device_not_connected"), (False, "probe_failed")])
def test_heartbeat_readiness_controls_device_presence(capture, ready, reason):
    admin, agent, _, _ = capture
    heartbeat(agent, capability={"name": NAME, "ready": ready, "reason": reason})
    assert start(admin, "")["status"] == ("pending" if ready else "no_agent")
    heartbeat(agent)
    assert start(admin, "")["agent"]["vna"] == {"name": NAME, "ready": True, "reason": ""}


def test_status_file_and_retry_keep_exact_trace_and_actual_instrument(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    assert admin.get(f"{JOBS}/{job}/file").status_code == 409
    assert result(agent, job).status_code == 409
    assert claim(agent, kinds=["vna"])["kind"] == "vna"
    assert admin.get(f"{JOBS}/{job}").json()["status"] == "claimed"
    assert result(agent, job, machine="pc-2").status_code == 403
    actual = TRACE.replace(b"26.2.2/2", b"26.3.0/1")
    assert result(agent, job, actual).json()["status"] == "done"
    view = admin.get(f"{JOBS}/{job}").json()
    assert view["vna"]["name"] == NAME.replace("26.2.2/2", "26.3.0/1")
    file = admin.get(f"{JOBS}/{job}/file")
    assert file.content == actual
    assert "attachment" in file.headers["content-disposition"]
    assert result(agent, job).status_code == 409
    assert admin.get(f"/api/stock/receipts/scan-jobs/{job}").status_code == 404
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job))
        assert row.file_id is None and row.result == ""  # No receipt OCR is scheduled.


@pytest.mark.parametrize("actual_serial", ["99999999", ""])
def test_capture_rejects_a_swapped_or_unidentified_instrument(capture, actual_serial):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    # Neither a refreshed inventory nor an edited registry may change this job's identity.
    heartbeat(agent, capability={"name": NAME.replace("26030095", "99999999"), "ready": True})
    with sessions() as session:
        session.get(ProductionInstrumentRow, UUID(S2_INSTRUMENT)).serial_number = "99999999"
        session.commit()
    response = result(agent, job, TRACE.replace(b"26030095", actual_serial.encode()))
    assert response.status_code == 422
    assert "Серийный номер" in response.json()["detail"]
    assert admin.get(f"{JOBS}/{job}").json()["status"] == "failed"
    assert admin.get(f"{JOBS}/{job}/file").status_code == 409
    with sessions() as session:
        assert session.get(ScanJobRow, UUID(job)).capture_content is None


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


@pytest.mark.parametrize("source", ["unknown", "ARINST", "", 1])
def test_start_validates_source(capture, source):
    admin, agent, _, _ = capture
    heartbeat(agent, sources=["s2vna", "arinst"])
    response = admin.post(JOBS, json={"agent_id": "pc-1", "source": source, "instrument_id": S2_INSTRUMENT}, headers=_csrf(admin))
    assert response.status_code == 422


@pytest.mark.parametrize("sources,vna,requested", [(["arinst"], False, "s2vna"), (["s2vna"], True, "arinst")])
def test_start_rejects_source_unavailable_on_selected_pc(capture, sources, vna, requested):
    admin, agent, _, _ = capture
    heartbeat(agent, vna=vna, sources=sources)
    response = admin.post(JOBS, json={"agent_id": "pc-1",
        "instrument_id": ARINST_INSTRUMENT if requested == "arinst" else S2_INSTRUMENT}, headers=_csrf(admin))
    assert response.status_code == 200
    assert response.json()["status"] == "no_agent"


def test_both_sources_survive_heartbeat_and_arinst_removal(capture):
    admin, agent, _, _ = capture
    heartbeat(agent, sources=["s2vna", "arinst"])
    assert start(admin, "", "arinst")["source"] == "arinst"
    assert start(admin, "")["vna"]["name"] == NAME
    heartbeat(agent)
    assert start(admin, "", "arinst")["status"] == "no_agent"
    assert start(admin)["source"] == "s2vna"


def test_start_requires_instrument_and_derives_source(capture):
    admin, agent, _, _ = capture
    heartbeat(agent)
    response = admin.post(JOBS, json={"agent_id": "pc-1"}, headers=_csrf(admin))
    assert response.status_code == 422
    job = start(admin)
    assert job["source"] == "s2vna"
    assert job["instrument_id"] == S2_INSTRUMENT
    assert claim(agent, kinds=["vna"])["id"] == job["id"]


@pytest.mark.parametrize("sources,vna", [(["other"], False), (["s2vna"], False), ([], True)])
def test_heartbeat_rejects_invalid_source_list(capture, sources, vna):
    _, agent, _, _ = capture
    payload = {"machine_id": "pc-1", "vna_sources": sources}
    if vna:
        payload["vna"] = {"name": NAME, "ready": True}
    assert TestClient(app).post(HEARTBEAT, json=payload, headers=agent).status_code == 422


@pytest.mark.parametrize("extension", ["s1p", "s2p", "S2P"])
def test_arinst_capture_keeps_bytes_filename_and_source_without_s2vna_header(capture, extension):
    admin, agent, _, sessions = capture
    heartbeat(agent, vna=False, sources=["arinst"])
    job = start(admin, source="arinst")
    pc = job["agent"]
    assert pc["vna_sources"] == ["arinst"] and pc["vna"] is None
    assert job["source"] == "arinst" and job["vna"]["name"] == "Arinst VNA-DL"
    claimed = claim(agent, kinds=["scan", "vna"])
    assert claimed["id"] == job["id"] and claimed["source"] == "arinst"
    content = b"! Arinst Virtual Lab\r\n# MHz S RI R 50\r\n100 0.1 0 0 0 0 0 0 0\r\n"
    filename = f"Кабель 001.{extension}"
    assert result(agent, job["id"], content, name=filename).status_code == 200
    view = admin.get(f"{JOBS}/{job['id']}").json()
    assert view["file_name"] == filename and view["source"] == "arinst"
    file = admin.get(f"{JOBS}/{job['id']}/file")
    assert file.content == content and "attachment" in file.headers["content-disposition"]
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job["id"]))
        assert row.source == "arinst" and row.capture_file_name == filename
        assert row.file_id is None and row.result == ""


@pytest.mark.parametrize("content,filename", [(b"", "x.s1p"), (TRACE, "x.pdf"), (b"x" * (VNA_FILE_LIMIT + 1), "x.s2p")],
                         ids=["empty", "extension", "file-limit"])
def test_arinst_invalid_file_fails_job(capture, content, filename):
    admin, agent, _, _ = capture
    heartbeat(agent, vna=False, sources=["arinst"])
    job = start(admin, source="arinst")["id"]
    claim(agent, kinds=["vna"])
    assert result(agent, job, content, name=filename).status_code == 422
    assert admin.get(f"{JOBS}/{job}").json()["status"] == "failed"


def test_database_rejects_unknown_source(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    with sessions() as session:
        session.get(ScanJobRow, UUID(job)).source = "unknown"
        with pytest.raises(IntegrityError):
            session.commit()


def test_serial_routes_from_different_client_address_and_ignores_pc_preference(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "aaa-local", capability={"name": NAME.replace("26030095", "99999999"), "ready": True})
    from app.modules.warehouse.scan_agents import ScanAgentRow
    with sessions() as session:
        session.get(ScanAgentRow, "pc-1").ip_seen = "192.0.2.10"
        session.get(ScanAgentRow, "pc-1").ip_lan = "192.0.2.10"
        session.commit()
    assert start(admin, "aaa-local")["agent"]["id"] == "pc-1"
    assert start(admin, "")["agent"]["id"] == "pc-1"


def test_source_filter_automatically_selects_each_device_owner(capture):
    admin, agent, _, _ = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "arinst-pc", vna=False, sources=["arinst"])
    assert start(admin, "")["agent"]["id"] == "pc-1"
    assert start(admin, "", "arinst")["agent"]["id"] == "arinst-pc"


@pytest.mark.parametrize("changes", [{"status": "Не используется"}, {"status": "Поверка не подтверждена",
    "verification_number": None, "verified_at": None, "verification_due": None}, {"vna_app": ""}])
def test_start_rejects_inactive_or_unconfigured_instrument(capture, changes):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    with sessions() as session:
        row = session.get(ProductionInstrumentRow, UUID(S2_INSTRUMENT))
        for key, value in changes.items():
            setattr(row, key, value)
        session.commit()
    response = admin.post(JOBS, json={"instrument_id": S2_INSTRUMENT}, headers=_csrf(admin))
    assert response.status_code == 422


def test_start_rejects_missing_instrument_and_conflicting_source(capture):
    admin, agent, _, _ = capture
    heartbeat(agent, sources=["s2vna", "arinst"])
    for body in ({"instrument_id": "33333333-3333-3333-3333-333333333333"},
                 {"instrument_id": S2_INSTRUMENT, "source": "arinst"}):
        assert admin.post(JOBS, json=body, headers=_csrf(admin)).status_code == 422


def test_capture_settings_survive_the_result_and_status_without_blocking_warnings(capture):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    settings = {"points": 2, "start_hz": 100000000, "stop_hz": 200000000, "frequency_step_hz": 100000000,
        "if_bandwidth_hz": 1000, "averaging_enabled": True, "averaging_factor": 8,
        "smoothing_enabled": True, "smoothing_aperture_percent": 1.5,
        "correction_enabled": False, "correction_status": "C?", "s_parameter": "S11"}
    assert result(agent, job, settings=settings).status_code == 200
    assert admin.get(f"{JOBS}/{job}").json()["capture_settings"] == settings
    with sessions() as session:
        row = session.get(ScanJobRow, UUID(job))
        assert row.capture_settings == settings and str(row.instrument_id) == S2_INSTRUMENT
    assert admin.get(f"{JOBS}/{job}/file").content == TRACE


@pytest.mark.parametrize("settings", [{"points": 1}, {"smoothing_enabled": "false"},
    {"if_bandwidth_hz": float("inf")}, {"s_parameter": "S22"}, {"unknown": 1}])
def test_invalid_capture_settings_fail_the_job(capture, settings):
    admin, agent, _, _ = capture
    heartbeat(agent)
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    assert result(agent, job, settings=settings).status_code == 422
    assert admin.get(f"{JOBS}/{job}").json()["status"] == "failed"


@pytest.mark.parametrize("operation", ["heartbeat", "claim", "file", "error"])
def test_vna_rejects_another_bound_token_without_mutating_the_job(capture, operation):
    admin, agent, _, sessions = capture
    heartbeat(agent)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2", vna=False, sources=["arinst"])
    job = start(admin)["id"]
    if operation in ("file", "error"):
        claim(agent, kinds=["vna"])
    client = TestClient(app)
    if operation == "heartbeat":
        response = client.post(HEARTBEAT, json={"machine_id": "pc-1", "vna": {"name": "wrong", "ready": False}}, headers=other)
    elif operation == "claim":
        response = client.post(CLAIM, json={"machine_id": "pc-1", "kinds": ["vna"]}, headers=other)
    elif operation == "file":
        response = result(other, job)
    else:
        response = client.post(f"/api/stock/scan/agent/jobs/{job}/result", json={"error": "wrong"},
                               headers={**other, "X-Agent-Machine-Id": "pc-1"})
    assert response.status_code == 403
    assert admin.get(f"{JOBS}/{job}").json()["status"] == ("claimed" if operation in ("file", "error") else "pending")
    # Even an honest header for the other token cannot submit a job targeted to this PC.
    assert result(other, job, machine="pc-2").status_code == 403
    from app.modules.warehouse.vna_jobs import AgentVnaRow
    with sessions() as session:
        assert session.get(AgentVnaRow, "pc-1").name == NAME


def test_legacy_token_binds_only_on_first_heartbeat_and_cannot_rebind(capture):
    admin, agent, _, sessions = capture
    client = TestClient(app)
    assert client.post(CLAIM, json={"machine_id": "pc-1", "kinds": ["vna"]}, headers=agent).status_code == 403
    heartbeat(agent)
    heartbeat(agent)  # Idempotent heartbeat for the same binding.
    assert client.post(HEARTBEAT, json={"machine_id": "pc-2"}, headers=agent).status_code == 403
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    assert client.post(HEARTBEAT, json={"machine_id": "pc-1"}, headers=other).status_code == 403
    job = start(admin)["id"]
    claim(agent, kinds=["vna"])
    assert result(other, job).status_code == 403  # A result never establishes a legacy binding.
    from app.modules.warehouse.scan_jobs import AgentTokenRow
    with sessions() as session:
        assert [row.machine_id for row in session.query(AgentTokenRow)] == ["pc-1"]
    assert result(agent, job).status_code == 200


def test_arinst_preference_is_per_user_and_follows_user_to_another_browser(capture):
    from app.modules.identity.infrastructure import IdentityUserRow
    from tests.identity.test_api_tokens import OPERATOR_ID
    admin, agent, _, sessions = capture
    heartbeat(agent, vna=False, sources=["arinst"])
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2", vna=False, sources=["arinst"])
    assert start(admin, "", "arinst")["status"] == "choose"
    job = start(admin, "pc-2", "arinst")
    assert job["agent"]["id"] == "pc-2"
    assert start(_login("admin"), "", "arinst")["agent"]["id"] == "pc-2"
    with sessions() as session:
        session.get(IdentityUserRow, OPERATOR_ID).access_level = "admin"
        session.commit()
    other_user = _login("operator")
    assert start(other_user, "", "arinst")["preferred_agent_id"] == ""
    for suffix in ("", "/file"):
        assert other_user.get(f"{JOBS}/{job['id']}{suffix}").status_code == 404
    assert other_user.delete(f"{JOBS}/{job['id']}", headers=_csrf(other_user)).status_code == 404
    choices = admin.post(JOBS, json={"instrument_id": ARINST_INSTRUMENT, "choose_device": True}, headers=_csrf(admin)).json()
    assert choices["status"] == "choose" and choices["preferred_agent_id"] == "pc-2"


def test_same_instrument_moves_only_after_explicit_admin_transfer(capture):
    admin, agent, clock, _ = capture
    heartbeat(agent)
    start(admin)
    clock.now += timedelta(minutes=4)
    other = _bearer(_issue(admin, scopes=("scan:upload",))["token"])
    heartbeat(other, "pc-2")
    assert start(admin)["status"] == "no_agent"
    response = admin.post(f"{JOBS}/transfer", json={"instrument_id": S2_INSTRUMENT, "serial": "26030095", "owner_id": "pc-1", "agent_id": "pc-2"},
                          headers=_csrf(admin))
    assert response.status_code == 200
    answer = start(admin)
    assert answer["agent"]["id"] == "pc-2"
    assert claim(other, "pc-2", ["vna"])["id"] == answer["id"]
