"""Protocol-3 bench through authenticated HTTP; no instrument or USB is accessed."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails
from app.modules.production.vna_session_infrastructure import VnaArtifactRow, VnaBenchItemRow, VnaSessionRow
from app.modules.warehouse.devices import VnaBindingRow, VnaControlRow, VnaIdentityRow
from app.workflows.vna_capture_instrument import capture_instrument
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.production.test_arinst_session_protocol import PROFILE, SERIAL, receipt
from tests.warehouse.test_arinst_control import beat, reports
from tests.warehouse.test_vna_jobs import ARINST_INSTRUMENT, CLAIM, JOBS, capture  # noqa: F401

URL = "/api/production/vna-bench"


@pytest.fixture()
def env(capture, monkeypatch):  # noqa: F811
    admin, agent, clock, sessions = capture
    items = reports(clock)
    items[1]["ops"] += ["apply_profile", "capture", "measure_standard"]
    assert beat(capture, items).status_code == 200
    response = admin.post(JOBS + "/identity", json=dict(instrument_id=ARINST_INSTRUMENT,
        serial=SERIAL, agent_id="pc-1"), headers=_csrf(admin))
    assert response.status_code == 200, response.text
    monkeypatch.setattr(app.state, "production_uow_factory", lambda: SqlAlchemyProductionUnitOfWork(sessions), raising=False)
    return capture


def post(env, path, body, expected=200):
    response = env[0].post(URL + path, json=body, headers=_csrf(env[0]))
    assert response.status_code == expected, response.text
    return response.json()


def state(env):
    response = env[0].get(URL + "/state", params=dict(instrument_id=ARINST_INSTRUMENT))
    assert response.status_code == 200, response.text
    return response.json()


def open_session(env):
    return post(env, "/sessions", dict(instrument_id=ARINST_INSTRUMENT))


def action(env, view, suffix, **extra):
    return post(env, f"/sessions/{view['session']['id']}/{suffix}", dict(version=view["session"]["version"], **extra))


def poll(env, serial=SERIAL):
    response = TestClient(app).post(CLAIM, json=dict(machine_id="pc-1", kinds=["vna_control"],
        observed_vna_serial=serial), headers=env[1])
    assert response.status_code == 200, response.text
    return response.json()["job"]


def send(env, command, result=None, error="", expected=200):
    body = dict(session_id=command["session_id"], expected_version=command["expected_version"],
                expected_step=command["expected_step"])
    if error:
        body["error"] = error
    else:
        body["result"] = result if result is not None else receipt(command["op"], command["args"])
    response = TestClient(app).post(f"/api/stock/vna/commands/{command['id']}/result", json=body,
        headers={**env[1], "X-Agent-Machine-Id": "pc-1"})
    assert response.status_code == expected, response.text
    return response.json()


def ready(env):
    open_session(env)
    connect = poll(env)
    assert connect["op"] == "connect" and connect["source"] == "arinst" and connect["protocol_version"] == 3
    send(env, connect)
    profile = poll(env)
    assert profile["op"] == "read_profile"
    send(env, profile)
    return state(env)


def test_open_read_capture_receipt_replay_close_and_partial_artifacts(env):
    view = ready(env)
    assert view["profile"] == PROFILE and view["device"]["printed_serial"] == "26030096"
    assert view["device"]["license"]["state"] == "not_required"
    assert view["device"]["license"]["trial_remaining_seconds"] is None
    assert view["device"]["options"]["scan_range_hz"] == [1000000, 9000000000]
    assert view["device"]["power_dbm"] is None and not view["calibration"]["valid"]
    view = action(env, view, "arinst/capture", orientation="reverse")
    assert view["session"]["state"] == "awaiting_dut"
    assert poll(env) is None
    view = action(env, view, "arinst/confirm")
    command = poll(env)
    assert command["args"]["orientation"] == "reverse"
    send(env, command)
    after = state(env)
    assert after["latest_trace"]["acquisition"]["orientation"] == "reverse"
    assert all("raw_evidence" not in c["result"] for c in after["session"]["commands"])
    assert all("data" not in (c["result"].get("acquisition") or {}) for c in after["session"]["commands"])
    send(env, command)
    assert state(env)["session"]["version"] == after["session"]["version"]
    with env[3]() as session:
        assert session.scalar(select(func.count()).select_from(VnaArtifactRow)) == 3
        assert session.scalar(select(func.count()).select_from(VnaBenchItemRow)) == 0
        artifact = session.scalar(select(VnaArtifactRow).where(VnaArtifactRow.command_id == UUID(command["id"])))
        assert artifact.file_name.endswith(".json") and artifact.evidence["orientation"] == "reverse"
        assert b'"S12"' not in artifact.content and b'"frequencies_hz":[1000000,2000000]' in artifact.content
    view = action(env, after, "close")
    command = poll(env)
    assert command["op"] == "close"
    send(env, command)
    assert env[0].get(f"/api/production/vna-sessions/{view['session']['id']}").json()["state"] == "recorded"


def test_exact_identity_does_not_head_of_line_block_planar(env):
    open_session(env)
    assert poll(env, "26030095") is None
    assert poll(env)["instrument_serial"] == SERIAL


def test_native_connect_without_host_profile_requires_explicit_apply(env):
    open_session(env)
    send(env, poll(env))
    read = poll(env)
    result = receipt("read_profile"); result["profile"] = None
    send(env, read, result)
    view = state(env)
    assert view["profile"] is None and view["device"]["control_ready"]
    post(env, f"/sessions/{view['session']['id']}/arinst/capture",
         dict(version=view["session"]["version"], orientation="forward"), 422)
    view = action(env, view, "arinst/profile", profile=PROFILE)
    send(env, poll(env))
    view = state(env)
    assert view["profile"] == PROFILE
    assert view["acceptance"]["eligible"] is False and "стендовое сравнение" in view["acceptance"]["message"]
    post(env, f"/sessions/{view['session']['id']}/arinst/profile",
         dict(version=view["session"]["version"], profile={**PROFILE, "if_bandwidth_hz": 200}), 422)


def test_direct_identity_required_but_legacy_capture_keeps_printed_serial(env):
    with env[3]() as session:
        session.delete(session.get(VnaIdentityRow, UUID(ARINST_INSTRUMENT)))
        session.commit()
    post(env, "/sessions", dict(instrument_id=ARINST_INSTRUMENT), 422)
    legacy = capture_instrument(SqlAlchemyProductionUnitOfWork(env[3]), UUID(ARINST_INSTRUMENT))
    assert legacy["serial"] == "26030096"


def test_exclusive_session_and_legacy_capture_conflict(env):
    assert beat(env, vna_sources=["s2vna", "arinst"]).status_code == 200
    open_session(env)
    post(env, "/sessions", dict(instrument_id=ARINST_INSTRUMENT), 409)
    response = env[0].post(JOBS, json=dict(instrument_id=ARINST_INSTRUMENT, agent_id="pc-1"), headers=_csrf(env[0]))
    assert response.status_code == 409


def test_locked_connect_and_close_allowed_without_fresh_capability(env):
    with env[3]() as session:
        control = session.get(VnaControlRow, ("pc-1", SERIAL))
        control.license_state, control.trial_remaining_seconds, control.control_ready = "LOCKED", None, False
        control.reported_at = env[2].now - timedelta(hours=1)
        session.commit()
    open_session(env)
    command = poll(env)
    result = receipt("connect")
    result["license"] = dict(license_state="LOCKED", trial_remaining_seconds=None, message="Время пробного режима вышло")
    send(env, command, result)
    view = state(env)
    assert view["session"]["state"] == "ready" and view["device"]["license"]["state"] == "LOCKED"
    assert poll(env) is None
    post(env, f"/sessions/{view['session']['id']}/arinst/profile", dict(version=view["session"]["version"], profile=PROFILE), 409)
    action(env, view, "close")
    close = poll(env)
    result = receipt("close"); result["license"] = dict(license_state="LOCKED", trial_remaining_seconds=None, message="Время вышло")
    send(env, close, result)


def test_profile_change_invalidates_and_readback_mismatch_keeps_command(env):
    view = ready(env)
    view = action(env, view, "arinst/profile", profile={**PROFILE, "stop_hz": 3e6})
    assert not view["calibration"]["valid"]
    command = poll(env)
    send(env, command, receipt("apply_profile", dict(profile=PROFILE)), expected=422)
    assert state(env)["session"]["command"]["status"] == "claimed"
    send(env, command)
    assert state(env)["profile"]["stop_hz"] == 3e6


def test_diagnostic_is_never_quality_save(env):
    view = ready(env)
    body = dict(version=view["session"]["version"], orientation="forward", target=dict(kind="instrument", instrument_id=ARINST_INSTRUMENT))
    result = post(env, f"/sessions/{view['session']['id']}/arinst/capture", body, 409)
    assert result["detail"]["code"] == "commissioning_required"
    for path in ("items", "screen", "steps/calibration"):
        body = dict(version=view["session"]["version"])
        if path == "items":
            body["target"] = dict(kind="instrument", instrument_id=ARINST_INSTRUMENT)
        if path == "screen":
            body["screen_id"] = str(uuid4())
        result = post(env, f"/sessions/{view['session']['id']}/{path}", body, 409)
        assert result["detail"]["code"] == "commissioning_required"


def test_timeout_is_unknown_no_replay_and_abort_uses_only_close(env, tmp_path):
    ready(env)
    view = state(env)
    action(env, view, "arinst/profile", profile=PROFILE)
    old = poll(env)
    env[2].now += timedelta(seconds=181)
    # Refresh only the PC, keeping explicit close available after the device observation expires.
    assert beat(env).status_code == 200
    view = state(env)
    assert view["session"]["state"] == "recovery_required" and not view["calibration"]["valid"]
    send(env, old, expected=409)
    action(env, view, "abort")
    close = poll(env)
    assert close["op"] == "close"
    status = TestClient(app).get(f"/api/stock/vna/sessions/{close['session_id']}",
        headers={**env[1], "X-Agent-Machine-Id": "pc-1"})
    assert status.status_code == 200
    assert status.json()["state"] == "releasing"
    assert_real_agent_lease(close, status.json(), tmp_path)
    send(env, close)
    assert env[0].get(f"/api/production/vna-sessions/{view['session']['id']}").json()["state"] == "aborted"


def test_standard_sequence_preserves_raw_and_blocks_uncommissioned_solver(env):
    view = ready(env)
    kit = uuid4()
    with env[3]() as session:
        session.add(ProductionInstrumentRow(id=kit, version=1, inventory_number="СИ.903", name="Набор мер",
            instrument_type="Оснастка", kind="Принадлежность", status="Действует"))
        session.flush()
        RfRepository(session).save(kit, RfDetails((), "solt_kit", False, "Характеристики ещё не подтверждены"))
        session.commit()
    view = action(env, view, "arinst/calibration", kit_id=str(kit), reference_plane="DUT", transmission=True)
    for standard in ("OPEN", "SHORT", "LOAD", "THRU"):
        assert view["session"]["state"] == "awaiting_standard"
        action(env, view, "arinst/confirm")
        command = poll(env)
        assert command["op"] == "measure_standard" and command["args"]["standard"] == standard
        send(env, command)
        view = state(env)
    assert view["session"]["state"] == "ready"
    assert view["calibration"]["status"] == "blocked" and not view["calibration"]["valid"]
    assert len(view["session"]["artifacts"]) == 6
    assert not any(a["file_name"].endswith(".s2p") for a in view["session"]["artifacts"])


def test_late_result_after_owner_binding_changes_is_refused(env):
    ready(env)
    view = state(env)
    action(env, view, "arinst/profile", profile=PROFILE)
    command = poll(env)
    with env[3]() as session:
        session.delete(session.get(VnaBindingRow, SERIAL))
        session.commit()
    send(env, command, expected=409)


def assert_real_agent_lease(command, status, tmp_path):
    """Extract ONLY the pure lease guard AST, never load/run the hardware agent."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell required for real driver lease contract")
    agent = Path(__file__).resolve().parents[3] / "tools/print-agent/agent.ps1"
    inputs = tmp_path / "lease.json"
    inputs.write_text(json.dumps(dict(command=command, status=status)), encoding="utf-8")
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    script = f"""$ErrorActionPreference = 'Stop'
$ast = [Management.Automation.Language.Parser]::ParseFile({quote(agent)}, [ref]$null, [ref]$null)
$guard = $ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-VnaLease'}}, $true)
if ($null -eq $guard) {{ throw 'Missing lease guard' }}
Invoke-Expression $guard.Extent.Text
$fixture = Get-Content -Raw -LiteralPath {quote(inputs)} | ConvertFrom-Json
Assert-VnaLease $fixture.command $fixture.status
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
        base64.b64encode(script.encode("utf-16-le")).decode()], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_delayed_receipt_does_not_renew_trial_or_override_newer_heartbeat(env):
    open_session(env)
    command = poll(env)
    result = receipt("connect")
    result["license"]["license_state"] = "TRIAL"
    result["license"]["trial_remaining_seconds"] = 60
    env[2].now += timedelta(seconds=50)
    send(env, command, result)
    assert state(env)["device"]["license"]["trial_remaining_seconds"] == 10
    env[2].now += timedelta(seconds=11)
    assert state(env)["device"]["license"]["state"] == "LOCKED"


def test_uncommissioned_setters_not_enqueued_and_browser_cannot_supply_evidence(env):
    assert beat(env).status_code == 200  # read-only operations only
    post(env, "/sessions", dict(instrument_id=ARINST_INSTRUMENT, arinst_profile=PROFILE), 409)
    view = ready(env)
    post(env, f"/sessions/{view['session']['id']}/arinst/profile",
        dict(version=view["session"]["version"], profile=PROFILE), 409)
    assert "apply_profile" not in view["device"]["supported_ops"]
    post(env, f"/sessions/{view['session']['id']}/arinst/capture", dict(version=view["session"]["version"],
         orientation="forward", commissioning={"grid_evidence": "browser"}), 422)
    assert poll(env) is None


def test_delayed_receipt_cannot_override_newer_license_observation(env):
    open_session(env)
    command = poll(env)
    env[2].now += timedelta(seconds=50)
    items = reports(env[2]); items[1]["trial_remaining_seconds"] = 5
    items[1]["license_state"] = "TRIAL"
    assert beat(env, items).status_code == 200
    send(env, command, receipt("connect"))  # An older receipt must not override newer observation.
    assert state(env)["device"]["license"]["trial_remaining_seconds"] == 5


def test_both_quality_save_entrypoints_remain_closed(env):
    view = ready(env)
    post(env, f"/sessions/{view['session']['id']}/items/{uuid4()}/record", dict(version=view["session"]["version"]), 409)
    response = env[0].post(f"/api/production/vna-sessions/{view['session']['id']}/record",
        json=dict(expected_version=view["session"]["version"]), headers=_csrf(env[0]))
    assert response.status_code == 409


def test_loads_only_kit_is_not_promoted_to_sol_standards(env):
    view = ready(env)
    kit = uuid4()
    with env[3]() as session:
        session.add(ProductionInstrumentRow(id=kit, version=1, inventory_number="СИ.904", name="SMA-MF06",
            instrument_type="Оснастка", kind="Принадлежность", status="Действует"))
        session.flush()
        RfRepository(session).save(kit, RfDetails((), "load_kit", False, "Нагрузки", kit_definition="SMA-MF06-loads"))
        session.commit()
    post(env, f"/sessions/{view['session']['id']}/arinst/calibration",
         dict(version=view["session"]["version"], kit_id=str(kit), reference_plane="DUT"), 422)
