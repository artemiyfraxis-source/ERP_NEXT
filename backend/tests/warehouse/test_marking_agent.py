from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.devices import PrinterRow
from app.modules.warehouse.label_jobs import LabelAgentRow, LabelJobRow, add_job, add_marking_job, expire_marking
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


def _operator_resolves(sessions, now, job_id, outcome, reason):
    """What «Проверка» in ERP (workflows.marking_labels.resolve, tested there) writes on an unknown job."""
    with sessions.begin() as session:
        row = session.get(LabelJobRow, UUID(job_id))
        assert row.status == "unknown"
        row.status, row.finished_at, row.error = outcome, now, reason


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
    assert service.post(path, json=result, headers=agent).json() == {"ok": True, "status": "spooled"}
    assert service.post(path, json=result, headers=agent).json() == {"ok": True, "status": "spooled"}
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
    path = f"/api/stock/labels/jobs/{job['id']}/result"
    late = {"attempt": job["attempt"], "outcome": "spooled", "message": "Поздний ответ"}
    # The late receipt is stored for the operator and acknowledged idempotently; the job stays unknown.
    assert service.post(path, headers=agent, json=late).json() == {"ok": True, "status": "unknown"}
    assert service.post(path, headers=agent, json=late).json() == {"ok": True, "status": "unknown"}
    assert service.post(path, headers=agent, json={**late, "outcome": "failed"}).status_code == 409
    assert service.post(HEARTBEAT, headers=agent, json={"machine_id": "pc-1", "version": "2.9.0", "printers": []}).status_code == 200
    with sessions() as session:
        row = session.get(LabelJobRow, UUID(job["id"]))
        assert row.status == "unknown" and row.receipt["outcome"] == "spooled"
        assert session.get(PrinterRow, ("pc-1", "Godex EZ2350i")).present is False


def test_unknown_receipt_learns_the_operator_resolution(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    _batch(sessions, clock.now)
    payload = {"machine_id": "pc-1", "capabilities": ["gdi_png"]}
    job = service.post(CLAIM, json=payload, headers=agent).json()["job"]
    path = f"/api/stock/labels/jobs/{job['id']}/result"
    unknown = {"attempt": job["attempt"], "outcome": "unknown", "message": "EndDoc failed"}
    assert service.post(path, headers=agent, json=unknown).json() == {"ok": True, "status": "unknown"}
    # The printer lane stays paused while the result is unknown.
    assert service.post(CLAIM, json=payload, headers=agent).json()["job"] is None
    assert service.post(path, headers=agent, json=unknown).json()["status"] == "unknown"
    _operator_resolves(sessions, clock.now, job["id"], "failed", "Этикетка не вышла")
    # Re-sending the same receipt is how the agent learns the decision and unblocks itself.
    assert service.post(path, headers=agent, json=unknown).json() == {"ok": True, "status": "failed"}
    following = service.post(CLAIM, json=payload, headers=agent).json()["job"]
    assert following is not None and following["id"] != job["id"]


def test_late_receipt_after_operator_resolution_answers_the_decision(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    _batch(sessions, clock.now)
    payload = {"machine_id": "pc-1", "capabilities": ["gdi_png"]}
    job = service.post(CLAIM, json=payload, headers=agent).json()["job"]
    clock.now += timedelta(seconds=121)
    with sessions.begin() as session:  # the job became unknown by expiry before the operator looked
        expire_marking(session, clock.now)
    _operator_resolves(sessions, clock.now, job["id"], "spooled", None)
    answer = service.post(f"/api/stock/labels/jobs/{job['id']}/result", headers=agent,
        json={"attempt": job["attempt"], "outcome": "unknown", "message": "Агент прерван"})
    assert answer.json() == {"ok": True, "status": "spooled"}
    with sessions() as session:
        assert session.get(LabelJobRow, UUID(job["id"])).status == "spooled"


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


def test_agent_claim_body_with_both_printers_hands_out_tspl_and_survives_a_renamed_queue(tokens_api):
    """The exact body Invoke-MarkingPollOnce sends: machine, capabilities, the TSC queue and the host."""
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    headers = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="warehouse")["token"])

    def heartbeat(tsc):
        body = {"machine_id": "warehouse", "pc_name": "SK-1", "version": "2.9.0", "printers": [
            {"name": tsc, "driver": "TSC", "port": "USB002", "kind": "tspl", "present": True},
            {"name": "Godex EZ2350i", "driver": "Godex", "port": "USB001", "kind": "gdi_png", "present": True}]}
        assert service.post(HEARTBEAT, headers=headers, json=body).status_code == 200

    heartbeat("TSC MB341")
    with sessions.begin() as session:
        first = add_job(session, tspl=b"PRINT 1,1", copies=1, kind="sample", lot_id=None, created_by="test", now=clock.now)
    agent_body = {"machine_id": "warehouse", "capabilities": ["tspl", "gdi_png"], "printer": "TSC MB341", "host": "SK-1"}
    # Without the queue name the server cannot route TSPL: the agent must send it.
    no_printer = {"machine_id": "warehouse", "capabilities": ["tspl", "gdi_png"]}
    assert service.post(CLAIM, json=no_printer, headers=headers).json()["job"] is None
    answer = service.post(CLAIM, json=agent_body, headers=headers).json()["job"]
    assert answer["id"] == str(first)
    # Windows renamed the queue and the config followed: printing continues, the heartbeat row follows the new name.
    heartbeat("TSC MB341 (копия 1)")
    with sessions.begin() as session:
        second = add_job(session, tspl=b"PRINT 1,1", copies=1, kind="sample", lot_id=None, created_by="test", now=clock.now)
    renamed = service.post(CLAIM, json={**agent_body, "printer": "TSC MB341 (копия 1)"}, headers=headers).json()["job"]
    assert renamed["id"] == str(second)
    with sessions() as session:
        assert session.get(LabelAgentRow, 1).printer == "TSC MB341 (копия 1)"


def test_tracked_spool_job_reports_final_print_state_and_batch_progress(tokens_api):
    from app.workflows.marking_labels import STUCK_TEXT, batch_view
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    batch_id = _batch(sessions, clock.now)
    payload = {"machine_id": "pc-1", "capabilities": ["gdi_png"]}
    jobs = []
    for _ in range(2):
        job = service.post(CLAIM, json=payload, headers=agent).json()["job"]
        spooled = {"attempt": job["attempt"], "outcome": "spooled", "message": "Передано принтеру",
                   "spool_job_id": 7, "tracked": True}
        assert service.post(f"/api/stock/labels/jobs/{job['id']}/result", json=spooled, headers=agent).json()["status"] == "spooled"
        jobs.append(job)

    def view(now=None):
        with sessions() as session:
            return batch_view(session.get(MarkingBatchRow, batch_id), session, now)

    first = view(clock.now)
    assert (first["printed"], first["queued"], first["progress_label"]) == (0, 2, "Напечатано 0 из 2 · в очереди 2")
    assert not first["stuck"] and first["status_label"] == "Печатается"
    assert first["jobs"][0]["status_label"] == "В очереди принтера"
    # No label taken for a while: the printer is waiting (peel-off mode / paper / offline).
    assert view(clock.now + timedelta(minutes=5))["stuck_label"] == STUCK_TEXT
    state = f"/api/stock/labels/jobs/{jobs[0]['id']}/print-state"
    body = {"attempt": jobs[0]["attempt"], "state": "printed", "message": ""}
    assert service.post(state, json=body, headers=agent).json() == {"ok": True, "print_state": "printed"}
    assert service.post(state, json={**body, "attempt": str(uuid4())}, headers=agent).status_code == 409
    # A later `error` never undoes a final state.
    assert service.post(state, json={**body, "state": "error"}, headers=agent).json()["print_state"] == "printed"
    done = view(clock.now)
    assert (done["printed"], done["queued"], done["progress_label"]) == (1, 1, "Напечатано 1 из 2 · в очереди 1")
    assert not done["stuck"]
    error = {"attempt": jobs[1]["attempt"], "state": "error", "message": "Принтер: нет бумаги"}
    assert service.post(f"/api/stock/labels/jobs/{jobs[1]['id']}/print-state", json=error, headers=agent).status_code == 200
    failed = view(clock.now)
    assert failed["print_errors"] == 1 and failed["jobs"][1]["message"] == "Принтер: нет бумаги"
    assert failed["jobs"][1]["status_label"] == "Ошибка принтера"
    # The same spooled receipt again stays idempotent after print-state was added.
    again = {"attempt": jobs[1]["attempt"], "outcome": "spooled", "message": "Передано принтеру", "spool_job_id": 7, "tracked": True}
    assert service.post(f"/api/stock/labels/jobs/{jobs[1]['id']}/result", json=again, headers=agent).json()["status"] == "spooled"


def test_legacy_agent_receipt_is_not_claimed_as_printed(tokens_api):
    from app.workflows.marking_labels import batch_view
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    batch_id = _batch(sessions, clock.now)
    job = service.post(CLAIM, json={"machine_id": "pc-1", "capabilities": ["gdi_png"]}, headers=agent).json()["job"]
    result = {"attempt": job["attempt"], "outcome": "spooled", "message": "Передано", "spool_job_id": 1}
    assert service.post(f"/api/stock/labels/jobs/{job['id']}/result", json=result, headers=agent).status_code == 200
    assert service.post(f"/api/stock/labels/jobs/{job['id']}/print-state", headers=agent,
                        json={"attempt": job["attempt"], "state": "printed"}).status_code == 409
    with sessions() as session:
        view = batch_view(session.get(MarkingBatchRow, batch_id), session, clock.now + timedelta(hours=1))
    assert view["untracked"] == 1 and not view["stuck"] and view["jobs"][0]["print_state"] == "untracked"
