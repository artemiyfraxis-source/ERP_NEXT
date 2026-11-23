"""Machine-held length calibration: measured input, guarded pulse and signed D54 evidence."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.production import machine_writes as commands
from tests.identity.test_api_tokens import tokens_api  # noqa: F401
from tests.workflows.test_machine_writes_api import (
    AGENT, CARD, NEXT, _events, _job, _legacy_length_offset, _post, _recipe, _transfer,
)
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

RUNNER = {**AGENT, "runner_version": "1.5.0"}
SEEN = {"32": WORDS["32"], "33": 0, "54": 100, "55": 0}
INPUT = {"parameter": "length_mm", "case": "machine", "measured": "320", "seen": "325"}


def prepare(admin):
    bridge = _bridge(admin)
    assert _report(bridge, registers={**WORDS, **SEEN}).status_code == 200
    return bridge


def queued(admin, bridge):
    answer = _post(admin, CARD + "/correction", INPUT)
    assert answer.status_code == 200, answer.text
    row = answer.json()["command"]
    claim = TestClient(app).post(NEXT, headers=bridge, json=RUNNER).json()["command"]
    assert row["id"] == claim["id"]
    return row, claim


def result(bridge, row, **changes):
    body = {**RUNNER, "ok": True, "before": SEEN, "correction_pulsed": True,
            "readback": {"658": 32000, "659": 0, "54": 65536 - 150, "55": 65535}}
    return TestClient(app).post(f"/api/production/machines/agent/writes/{row['id']}/result",
                               headers=bridge, json={**body, **changes})


def test_measured_length_is_queued_without_d32_and_signed_plc_result_is_displayed_and_journaled(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = prepare(admin)
    _legacy_length_offset(sessions, ob019, clock.now)
    row, claim = queued(admin, bridge)
    assert claim["kind"] == "length_correction"
    assert claim["blocks"] == [{"addr": 658, "values": [32000, 0]}]
    assert claim["bits"] == [] and claim["seen"] == SEEN and claim["deadline_ms"] == 10000
    assert row["length_correction_before"] == "1,00"
    assert row["length_correction_after"] is None
    assert result(bridge, row).json() == {"status": "done", "error": ""}
    card = admin.get(CARD + "/control").json()
    assert card["length_correction"] == {"register": "D54", "value": "-1,50", "legacy_offset": "0,50"}
    assert card["commands"][0]["length_correction_after"] == "-1,50"
    legacy = next(item for item in card["offsets"] if item["key"] == "length_mm")
    assert legacy["legacy"] and legacy["offset"] == "0,50"
    assert any(kind == "production.machine_length_corrected" and "D54 = 1,00 → -1,50" in text
               for kind, text in _events(sessions, ob019))
    assert result(bridge, row).json()["status"] == "done"
    assert sum(kind == "production.machine_length_corrected" for kind, _ in _events(sessions, ob019)) == 1
    # A newer snapshot is authoritative again; it may reflect a local adjustment at the panel.
    clock.now += timedelta(seconds=1)
    _report(bridge, seq=2, registers={**WORDS, "54": 25, "55": 0})
    assert admin.get(CARD + "/control").json()["length_correction"]["value"] == "0,25"


@pytest.mark.parametrize("changes", [
    {"seen": None}, {"seen": "325.001"}, {"seen": "326"}, {"measured": None}, {"measured": "0"},
    {"measured": "-1"}, {"measured": "999999.991"}, {"measured": "1000000"}, {"offset": "1"},
])
def test_invalid_or_changed_measurement_never_queues(machines_api, changes):
    _, _, admin, _, _ = machines_api
    bridge = prepare(admin)
    assert _post(admin, CARD + "/correction", {**INPUT, **changes}).status_code in (409, 422)
    assert TestClient(app).post(NEXT, headers=bridge, json=RUNNER).json()["command"] is None


@pytest.mark.parametrize("scenario", ["stale", "open", "busy", "running", "missing_d54", "old_runner"])
def test_length_correction_keeps_safety_refusals(machines_api, scenario):
    _, clock, admin, _, _ = machines_api
    bridge = prepare(admin)
    if scenario == "stale":
        clock.now += timedelta(seconds=11)
    elif scenario == "open":
        assert _post(admin, CARD + "/write", {"values": {"length_mm": "325"}}).status_code == 200
    elif scenario == "busy":
        assert _report(bridge, seq=2, link="port_busy", registers={}).status_code == 200
    elif scenario == "running":
        _report(bridge, seq=2, registers={**WORDS, **SEEN, "766": 103})
    elif scenario == "missing_d54":
        _report(bridge, seq=2, registers=WORDS)
    else:
        TestClient(app).post(NEXT, headers=bridge, json={**AGENT, "runner_version": "1.4.0"})
    assert _post(admin, CARD + "/correction", INPUT).status_code == 409


def test_old_runner_cannot_claim_a_correction_queued_before_its_heartbeat(machines_api):
    _, _, admin, _, _ = machines_api
    bridge = prepare(admin)
    assert _post(admin, CARD + "/correction", INPUT).status_code == 200
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT).json()["command"] is None
    assert admin.get(CARD + "/control").json()["commands"][0]["status"] == "failed"


@pytest.mark.parametrize("changes", [
    {"readback": {"658": 32000, "659": 0}}, {"readback": {"658": 31999, "659": 0, "54": 0, "55": 0}},
    {"before": {**SEEN, "54": 200}}, {"correction_pulsed": False},
    {"ok": False, "outcome": "unknown", "error": "D600.6 pulse not confirmed cleared"},
])
def test_missing_pulse_or_readback_evidence_never_claims_success(machines_api, changes):
    sessions, _, admin, ob019, _ = machines_api
    bridge = prepare(admin)
    row, _ = queued(admin, bridge)
    assert result(bridge, row, **changes).json()["status"] in ("failed", "unknown")
    assert admin.get(CARD + "/control").json()["commands"][0]["length_correction_after"] is None
    assert not any(kind == "production.machine_length_corrected" for kind, _ in _events(sessions, ob019))


def test_unanswered_correction_is_unknown_and_never_requeued(machines_api):
    _, clock, admin, _, _ = machines_api
    bridge = prepare(admin)
    row, _ = queued(admin, bridge)
    clock.now += timedelta(seconds=31)
    assert admin.get(CARD + "/control").json()["commands"][0]["status"] == "unknown"
    assert result(bridge, row).json()["status"] == "unknown"
    assert TestClient(app).post(NEXT, headers=bridge, json=RUNNER).json()["command"] is None


def test_transfer_d32_ignores_legacy_erp_length_offset(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = prepare(admin)
    _legacy_length_offset(sessions, ob019, clock.now)
    group_id = _job(sessions)
    _recipe(sessions, ob019)
    assert _transfer(admin, group_id).status_code == 200
    claim = TestClient(app).post(NEXT, headers=bridge, json=RUNNER).json()["command"]
    words = {block["addr"] + i: value for block in claim["blocks"] for i, value in enumerate(block["values"])}
    assert words[32] == 32500 and words[33] == 0


@pytest.mark.parametrize("permissions", [{"production": "view"}, {"myops": "view"}, {"quality": "edit"}])
def test_viewers_and_quality_cannot_queue_length_correction(machines_api, permissions):
    from app.shared.http_auth import authenticated_principal
    from tests.identity.test_level_gates import _principal

    _, _, admin, _, _ = machines_api
    bridge = prepare(admin)
    app.dependency_overrides[authenticated_principal] = lambda: _principal("operator", permissions)
    try:
        assert _post(admin, CARD + "/correction", INPUT).status_code == 403
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
    assert TestClient(app).post(NEXT, headers=bridge, json=RUNNER).json()["command"] is None
