import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production.infrastructure import (ProductionInstrumentRow, ProductionOperationRow,
    ProductionTaskRow, SqlAlchemyProductionUnitOfWork)
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.vna_session_infrastructure import VnaArtifactRow, VnaSessionRepository
from app.modules.warehouse import scan_jobs
from app.workflows import vna_measurement_api as api
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401
from tests.production.test_vna_sessions import session_plan
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_vna_plan_api import _plan_setup
from tests.warehouse.test_vna_jobs import (CLAIM, HEARTBEAT, JOBS, NAME, S2_INSTRUMENT, capture, claim, heartbeat)  # noqa: F401

URL = "/api/production/vna-sessions"
SERIAL = "26030095"


def control_heartbeat(agent, *, version="2.8.0", protocol=1, methods=None, machine="pc-1"):
    data = dict(machine_id=machine, pc_name=machine, version=version, vna=dict(name=NAME, ready=True),
                vna_control=dict(version=protocol, methods=methods if methods is not None else ["solt2", "solt1", "acm"]))
    response = TestClient(app).post(HEARTBEAT, json=data, headers=agent)
    assert response.status_code == 200, response.text


@pytest.fixture()
def env(capture, monkeypatch, request):  # noqa: F811
    admin, agent, clock, sessions = capture
    control_heartbeat(agent)
    monkeypatch.setattr(app.state, "production_uow_factory", lambda: SqlAlchemyProductionUnitOfWork(sessions), raising=False)
    plan_data, assets = session_plan(getattr(request, "param", "solt2"))
    task_id, operation_id = uuid4(), uuid4()
    with sessions() as session:
        session.add(ProductionTaskRow(id=task_id, number="VNA-1", source_idempotency_key="vna", source_fingerprint="x",
            version=1, created_at=clock.now, project="Тест", item="КБ1", name="Кабель", quantity=2,
            status="Запущено", launched_by="Admin"))
        session.flush()
        session.add(ProductionOperationRow(id=operation_id, task_id=task_id, position=1, code="VSWR", stage="Контроль",
            description="КСВН", seconds_per_unit=1, setup_seconds=1, norm_seconds=2))
        for a in assets[1:]:
            session.add(ProductionInstrumentRow(id=a.id, version=1, inventory_number=str(a.id), name=a.label,
                        instrument_type="Оснастка", kind="Принадлежность", status="Действует", parent_id=UUID(S2_INSTRUMENT),
                        serial_number="26140008" if a.rf.role == "auto_calibration" else ""))
        session.flush()
        rf = RfRepository(session)
        rf.save(UUID(S2_INSTRUMENT), replace(assets[0].rf, max_points=200001))
        for a in assets[1:]:
            rf.save(a.id, a.rf)
        session.commit()
    plan_data.update(task_id=str(task_id), operation_id=str(operation_id), instrument_id=S2_INSTRUMENT)
    monkeypatch.setattr(api, "vna_plan", lambda *args: deepcopy(plan_data))
    payload = dict(task_id=str(task_id), operation_id=str(operation_id), instrument_id=S2_INSTRUMENT,
                   product_position=1, agent_id="pc-1")
    if plan_data["status"] == "qualified":
        payload["qualification_reason"] = "Контрольный замер с согласованной нагрузкой"
    return admin, agent, clock, sessions, payload, plan_data


def create(env, **extra):
    admin, _, _, _, payload, _ = env
    return admin.post(URL, json={**payload, **extra}, headers=_csrf(admin))


def action(env, view, name, **extra):
    admin = env[0]
    return admin.post(f"{URL}/{view['id']}/actions", json=dict(action=name, expected_version=view["version"],
                      expected_step=view["step"], **extra), headers=_csrf(admin))


def get_view(env, view):
    return env[0].get(f"{URL}/{view['id']}").json()


def poll(env, serial=SERIAL, machine="pc-1"):
    return TestClient(app).post(CLAIM, json=dict(machine_id=machine, kinds=["vna_control"], observed_vna_serial=serial), headers=env[1])


def answer(command):
    op, args = command["op"], command["args"]
    result = dict(serial=SERIAL)
    if op == "prepare":
        result.update(ready=True, backup_id=str(uuid4()), state_saved=True, kit_assignments_saved=True)
    elif op == "apply_profile":
        result.update(profile=args["profile"])
    elif op == "begin_calibration":
        result.update(kit_fingerprint=args["kit_fingerprint"], definition_hash="a" * 64)
    elif op in ("measure_standard", "acm_calibrate"):
        result.update(acquired=True)
        if op == "acm_calibrate":
            result.update(module_serial=args["module_serial"], module_min_hz=1e5, module_max_hz=20e9)
    elif op == "save_calibration":
        result.update(method=args["method"], correction={t: "COR" for t in args["traces"]})
    elif op == "capture":
        traces = args["profile"]["traces"]
        result.update(profile=args["profile"], correction={t: "COR" for t in traces}, artifacts=[dict(
            file_name="trace.s2p" if len(traces) > 1 else "trace.s1p", traces=traces,
            content_base64=base64.b64encode(b"# Hz S RI R 50\n1000000 .1 0 .9 0 .9 0 .1 0\n").decode())])
    elif op == "abort":
        result.update(stopped=True, calibration_cleared=True)
    elif op == "restore":
        result.update(restored=True)
    return dict(session_id=command["session_id"], expected_version=command["expected_version"],
                expected_step=command["expected_step"], result=result)


def send(env, command, payload=None, machine="pc-1"):
    return TestClient(app).post(f"/api/stock/vna/commands/{command['id']}/result",
        json=payload or answer(command), headers={**env[1], "X-Agent-Machine-Id": machine})


def start(env):
    response = create(env)
    assert response.status_code == 200, response.text
    view = action(env, response.json(), "plan").json()
    return action(env, view, "start").json()


def advance_until(env, view, state):
    for _ in range(40):
        view = get_view(env, view)
        if view["state"] == state:
            return view
        if view["state"] in ("awaiting_standard", "awaiting_dut"):
            response = action(env, view, "confirm")
            assert response.status_code == 200, response.text
        command = poll(env).json()["job"]
        assert command
        response = send(env, command)
        assert response.status_code == 200, response.text
    pytest.fail(f"Не достигнуто состояние {state}: {view}")


def test_complete_session_receipts_evidence_survive_queue_purge_and_p4_records_atomically(env):
    view = advance_until(env, start(env), "review")
    assert view["artifacts"] and view["snapshot"]["plan"]["etk"] == {"revision": 3}
    assert len([c for c in view["commands"] if c["op"] == "measure_standard"]) == 8
    assert view["commands"][-1]["op"] == "restore"
    assert create(env).status_code == 409  # review retains the lease until P4 records / operator aborts.
    artifact = view["artifacts"][0]
    content = env[0].get(f"{URL}/{view['id']}/artifacts/{artifact['id']}").content
    old_job = uuid4()
    with env[3]() as session:
        session.add(scan_jobs.ScanJobRow(id=old_job, status="done", requested_by="historical", kind="vna",
            target_machine_id="pc-1", created_at=env[2].now, claimed_at=env[2].now, finished_at=env[2].now,
            capture_content=content, vna_name=NAME))
        session.commit()
    scan_jobs.claim(app.state.warehouse_uow_factory(), now=env[2].now + timedelta(days=31), machine_id="pc-1")
    assert env[0].get(f"{URL}/{view['id']}/artifacts/{artifact['id']}").content == content
    with env[3]() as session:
        assert session.get(scan_jobs.ScanJobRow, old_job) is None
        repo = VnaSessionRepository(session)
        assert repo.completed(UUID(view["id"]))["artifacts"] == view["artifacts"]
        repo.mark_recorded(UUID(view["id"]), expected_version=view["version"], reference="attempt-1", now=env[2].now)
        session.rollback()
    assert get_view(env, view)["state"] == "review"
    with env[3]() as session:
        repo = VnaSessionRepository(session)
        repo.mark_recorded(UUID(view["id"]), expected_version=view["version"], reference="attempt-1", now=env[2].now)
        session.commit()
        repo.mark_recorded(UUID(view["id"]), expected_version=view["version"], reference="attempt-1", now=env[2].now)
    assert get_view(env, view)["state"] == "recorded"


def test_double_click_stale_callback_reordering_and_idempotent_result(env):
    view = start(env)
    assert action(env, view, "start").status_code == 409
    command = poll(env).json()["job"]
    assert poll(env).json()["job"] == command  # lost claim response resumes same command id
    payload = answer(command)
    assert send(env, command, {**payload, "expected_step": 99}).status_code == 409
    assert send(env, command, payload).status_code == 200
    after = get_view(env, view)
    assert send(env, command, payload).status_code == 200
    assert get_view(env, view) == after
    assert send(env, command, answer(command)).status_code == 409  # different backup id is not the same receipt
    stale = action(env, view, "abort")
    assert stale.status_code == 409


def test_abort_rejects_late_result_and_requires_stop_then_restore(env):
    view = start(env)
    command = poll(env).json()["job"]
    aborted = action(env, view, "abort")
    assert aborted.status_code == 200, aborted.text
    assert send(env, command).status_code == 409
    assert create(env).status_code == 409
    stop = poll(env).json()["job"]
    assert stop["op"] == "abort"
    assert send(env, stop).status_code == 200
    restore = poll(env).json()["job"]
    assert restore["op"] == "restore"
    assert send(env, restore).status_code == 200
    assert get_view(env, view)["state"] == "aborted"
    assert create(env).status_code == 200


def test_lost_ack_and_restore_failure_keep_lease_until_explicit_recovery(env):
    view = start(env)
    command = poll(env).json()["job"]
    env[2].now += timedelta(seconds=181)
    assert send(env, command).status_code == 409
    view = get_view(env, view)
    assert view["state"] == "recovery_required"
    control_heartbeat(env[1])
    assert create(env).status_code == 409
    assert poll(env).json()["job"] is None
    view = action(env, view, "recover").json()
    stop = poll(env).json()["job"]
    assert send(env, stop).status_code == 200
    restore = poll(env).json()["job"]
    payload = answer(restore)
    payload.pop("result")
    payload["error"] = "Не удалось восстановить состояние"
    assert send(env, restore, payload).status_code == 200
    assert get_view(env, view)["state"] == "recovery_required"
    assert create(env).status_code == 409


def test_human_wait_does_not_expire(env):
    view = advance_until(env, start(env), "awaiting_standard")
    env[2].now += timedelta(days=1)
    assert get_view(env, view)["state"] == "awaiting_standard"
    assert get_view(env, view)["version"] == view["version"]


@pytest.mark.parametrize("version,protocol,methods", [("2.7.0", 1, ["solt2"]), ("2.8.0", 2, ["solt2"]),
    ("2.8.0", 1, []), ("bad", 1, ["solt2"])])
def test_capability_mismatch(env, version, protocol, methods):
    control_heartbeat(env[1], version=version, protocol=protocol, methods=methods)
    assert create(env).status_code == 409


def test_capture_only_heartbeat_revokes_control_without_disabling_capture(env):
    heartbeat(env[1])
    assert create(env).status_code == 409
    assert env[0].post(JOBS, json={"instrument_id": S2_INSTRUMENT}, headers=_csrf(env[0])).status_code == 200


def test_serial_checked_before_dispatch_and_again_in_result(env):
    view = start(env)
    assert poll(env, serial="999").status_code == 409
    command = poll(env).json()["job"]
    payload = answer(command)
    payload["result"]["serial"] = "999"
    assert send(env, command, payload).status_code == 422
    assert get_view(env, view)["state"] == "preparing"


def test_capture_and_session_exclude_each_other(env):
    view = start(env)
    response = env[0].post(JOBS, json={"instrument_id": S2_INSTRUMENT}, headers=_csrf(env[0]))
    assert response.status_code == 409
    assert "сессией" in response.text
    advance_until(env, action(env, view, "abort").json(), "aborted")
    response = env[0].post(JOBS, json={"instrument_id": S2_INSTRUMENT}, headers=_csrf(env[0]))
    assert response.status_code == 200
    assert create(env).status_code == 409


def test_operator_and_agent_authority_are_not_headers(env):
    view = start(env)
    assert create(env, agent_id="pc-invented").status_code == 409
    command = poll(env).json()["job"]
    assert send(env, command, machine="pc-invented").status_code == 403
    other = _bearer(_issue(env[0], scopes=("scan:upload",))["token"])
    control_heartbeat(other, machine="pc-2")
    response = TestClient(app).post(f"/api/stock/vna/commands/{command['id']}/result", json=answer(command),
                                  headers={**other, "X-Agent-Machine-Id": "pc-2"})
    assert response.status_code == 403
    browser = env[0].post(f"/api/stock/vna/commands/{command['id']}/result", json=answer(command), headers=_csrf(env[0]))
    assert browser.status_code == 403
    operator = _login("operator")
    assert operator.get(f"{URL}/{view['id']}").status_code in (403, 404)


def test_browser_cannot_inject_steps_commands_norm_or_files(env):
    for field, value in (("steps", []), ("norm", {}), ("command", "SCPI"), ("profile", {}), ("path", "C:/x")):
        assert create(env, **{field: value}).status_code == 422


def test_capture_result_idempotency_does_not_duplicate_artifacts(env):
    view = advance_until(env, start(env), "awaiting_dut")
    view = action(env, view, "confirm").json()
    command = poll(env).json()["job"]
    payload = answer(command)
    assert send(env, command, payload).status_code == 200
    assert send(env, command, payload).status_code == 200
    with env[3]() as session:
        assert session.scalar(select(func.count()).select_from(VnaArtifactRow)) == 1


def test_stale_repository_writer_loses_cas_even_without_http(env):
    from app.modules.production.vna_sessions import SessionConflict

    view = create(env).json()
    with env[3]() as first, env[3]() as second:
        one, two = VnaSessionRepository(first), VnaSessionRepository(second)
        a, b = one.get(UUID(view["id"])), two.get(UUID(view["id"]))
        one.act(a, "plan", a.version, a.step, env[2].now)
        first.commit()
        with pytest.raises(SessionConflict):
            two.act(b, "abort", b.version, b.step, env[2].now)
    assert get_view(env, view)["state"] == "planned"


def test_top_level_qualification_also_requires_reason(env):
    env[5]["status"] = "qualified"  # P1 instrument-verification warning, with otherwise ready chains.
    assert create(env).status_code == 422
    assert create(env, qualification_reason="Проверка прибора без приёмки").status_code == 200


def test_draft_abort_releases_lease_without_sending_writes(env):
    view = create(env).json()
    result = action(env, view, "abort").json()
    assert result["state"] == "aborted" and result["commands"] == []
    assert create(env).status_code == 200


def test_result_before_claim_and_unknown_command_rejected(env):
    view = start(env)
    command = view["commands"][0]
    assert send(env, command).status_code == 409
    assert send(env, {**command, "id": str(uuid4())}).status_code == 404


def test_invalid_artifact_names_and_bytes_are_not_persisted(env):
    view = advance_until(env, start(env), "awaiting_dut")
    action(env, view, "confirm")
    command = poll(env).json()["job"]
    for field, value in (("file_name", "../../trace.s2p"), ("content_base64", "abcd!")):
        payload = answer(command)
        payload["result"]["artifacts"][0][field] = value
        assert send(env, command, payload).status_code == 422
    with env[3]() as session:
        assert session.scalar(select(func.count()).select_from(VnaArtifactRow)) == 0


@pytest.mark.parametrize("env", ["acm", "solt1"], indirect=True)
def test_other_calibration_methods_complete_one_atomic_session(env):
    view = advance_until(env, start(env), "review")
    if env[5]["chains"].get("mode") == "one_port_reversal":
        assert len(view["artifacts"]) == 2
        assert {tuple(a["metadata"]["port_to_end"].items()) for a in view["artifacts"]} == {(("1", "А"),), (("1", "Б"),)}
    else:
        assert sum(c["op"] == "acm_calibrate" for c in view["commands"]) == 1


def test_watchdog_race_returns_conflict_not_server_error(env, monkeypatch):
    from app.modules.production.vna_sessions import SessionConflict

    def expired(*args, **kwargs):
        raise SessionConflict("Сессия изменилась")
    monkeypatch.setattr(api, "_expire", expired)
    assert poll(env).status_code == 409


def test_agent_can_reconcile_only_its_assigned_session_even_after_abort(env):
    view = start(env)
    command = poll(env).json()["job"]
    url = f"/api/stock/vna/sessions/{view['id']}"
    headers = {**env[1], "X-Agent-Machine-Id": "pc-1"}
    response = TestClient(app).get(url, headers=headers)
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["lease_held"] and status["commands"][0]["command_id"] == command["id"]
    assert status["commands"][0]["status"] == "claimed"
    assert status["kit_definitions"] == view["snapshot"]["kit_definitions"]
    assert TestClient(app).get(f"{URL}/{view['id']}", headers=headers).status_code == 403
    assert env[0].get(url).status_code == 403
    other = _bearer(_issue(env[0], scopes=("scan:upload",))["token"])
    control_heartbeat(other, machine="pc-2")
    assert TestClient(app).get(url, headers={**other, "X-Agent-Machine-Id": "pc-2"}).status_code == 403
    view = advance_until(env, action(env, view, "abort").json(), "aborted")
    status = TestClient(app).get(url, headers=headers).json()
    assert status["state"] == "aborted" and not status["lease_held"]


def test_creation_composes_real_p1_plan_and_pinned_norm(production_api, monkeypatch):
    from app.modules.warehouse import devices, scan_agents
    from app.shared.clock import moscow_now

    client, _ = production_api
    params = _plan_setup(client, monkeypatch)
    factory = app.state.production_uow_factory
    now = app.dependency_overrides[moscow_now]()
    with factory() as uow:
        item = uow.session.get(ProductionInstrumentRow, UUID(params["instrument_id"]))
        item.vna_app, item.serial_number = "S2VNA", SERIAL
        uow.commit()
    scan_agents.heartbeat(factory(), machine_id="pc-real-plan", pc_name="PC", version="2.8.0", ip_seen="", ip_lan="",
                          scanners=[], now=now, vna=dict(name=NAME, ready=True))
    devices.control_heartbeat(factory(), "pc-real-plan", dict(version=1, methods=["solt2"]), now)
    expected = client.get("/api/production/vna-plan", params=params).json()
    response = client.post(URL, json={**params, "product_position": 1, "agent_id": "pc-real-plan"})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["snapshot"]["plan"] == expected
    assert view["snapshot"]["profile"]["start_hz"] == expected["profile"]["band_mhz"][0] * 1e6


def test_workflow_refuses_a_missing_agent_even_without_machine_fk(env):
    from app.modules.warehouse.scan_agents import ScanAgentRow

    with env[3]() as session:
        session.delete(session.get(ScanAgentRow, "pc-1"))
        session.commit()
    response = create(env)
    assert response.status_code == 409
    assert response.json()["detail"] == "ПК агента не найден"


@pytest.mark.parametrize("env", ["acm"], indirect=True)
def test_acm_result_requires_matching_module_serial_and_full_band(env):
    view = advance_until(env, start(env), "awaiting_standard")
    module_id = next(iter(view["snapshot"]["kit_definitions"]))
    assert view["snapshot"]["kit_definitions"][module_id]["serial_number"] == "26140008"
    action(env, view, "confirm")
    command = poll(env).json()["job"]
    assert command["op"] == "acm_calibrate"
    for change in ({"module_serial": "99999999"}, {"module_max_hz": 5e9}, {"module_min_hz": 2e6}):
        payload = answer(command)
        payload["result"].update(change)
        assert send(env, command, payload).status_code == 422
    assert send(env, command).status_code == 200
