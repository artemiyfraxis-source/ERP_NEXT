"""C0 driver receipts, bounded partial data, and commissioning barriers."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.production import vna_bench as bench
from app.modules.production.vna_bench_protocol import command_args, validate_result_v3
from app.modules.production.vna_sessions import fingerprint
from tests.production.test_arinst_calibration import context, make_standards, D  # noqa: F401

SERIAL = "arinst:3932198-827215893-540422231"
PROFILE = dict(start_hz=1000000.0, stop_hz=2000000.0, points=2, if_bandwidth_hz=1000)


def receipt(op="capture", args=None):
    args = args or dict(profile=PROFILE, orientation="forward")
    raw = dict(drain=dict(discarded_bytes=0, discarded_base64=""),
               wire=[dict(tx_base64="KlVJRD8K", rx_base64="MQo=", complete=True)])
    result = dict(source="arinst", serial=SERIAL, hardware_uid=SERIAL[7:], firmware="2.0.0",
        license=dict(license_state="TRIAL", trial_remaining_seconds=600, message="Пробный режим API — осталось 10 мин"),
        op=op, started_at="2026-11-23T12:00:00Z", finished_at="2026-11-23T12:00:01Z", duration_ms=1000,
        parser_status="complete", correction_origin="unknown", raw_evidence=raw, raw_sha256=fingerprint(raw))
    if op in ("apply_profile", "read_profile", "capture", "measure_standard"):
        result["profile"] = deepcopy(args.get("profile", PROFILE))
    if op in ("capture", "measure_standard"):
        result["acquisition"] = dict(schema="arinst.partial.v1", measured_channels=["S11", "S21"],
            data={"S11": [[0.1, 0.0]] * PROFILE["points"], "S21": [[0.9, 0.0]] * PROFILE["points"]},
            orientation=args["orientation"], grid_confirmed=False, frequencies_hz=None,
            correction_origin="unknown", points=PROFILE["points"])
    if op == "measure_standard":
        result["standard"] = args["standard"]
    if op == "close":
        result.update(closed=True, rf_stopped=None)
    return result


@pytest.mark.parametrize("stored,wire,args", [
    ("prepare", "connect", {}), ("device_status", "read_profile", {}), ("release", "close", {}),
    ("abort", "close", {}), ("apply_profile", "apply_profile", dict(profile=PROFILE)),
    ("capture", "capture", dict(profile=PROFILE, orientation="reverse")),
    ("measure_standard", "measure_standard", dict(profile=PROFILE, orientation="forward", standard="OPEN")),
])
def test_driver_receipt(stored, wire, args):
    assert validate_result_v3(stored, args, SERIAL, receipt(wire, args))["op"] == wire


@pytest.mark.parametrize("field,value", [("points", 1001), ("points", True), ("start_hz", 0),
    ("stop_hz", 1e6), ("stop_hz", 8.9e9), ("if_bandwidth_hz", 200), ("power_dbm", 0)])
def test_only_supported_profile_settings(field, value):
    with pytest.raises(ValueError):
        command_args(3, "apply_profile", {"profile": {**PROFILE, field: value}})


@pytest.mark.parametrize("patch", [
    {"serial": "arinst:1-2-3"}, {"hardware_uid": "1-2-3"}, {"op": "connect"},
    {"raw_sha256": "0" * 64}, {"parser_status": "incomplete"}, {"correction_origin": "host"},
    {"started_at": "2026-11-23T12:00:00"}, {"finished_at": "2026-11-22T12:00:00Z"},
    {"profile": {**PROFILE, "points": 3}}, {"standard": "LOAD"}, {"closed": True}, {"rf_stopped": True},
])
def test_invalid_receipt_is_not_persistable(patch):
    raw = receipt(); raw.update(patch)
    with pytest.raises(ValueError):
        validate_result_v3("capture", dict(profile=PROFILE, orientation="forward"), SERIAL, raw)


@pytest.mark.parametrize("patch", [
    {"data": {"S11": [[0.1, 0.0]] * 2}}, {"points": 3}, {"orientation": "reverse"},
    {"grid_confirmed": True, "frequencies_hz": [1e6, 2e6]}, {"correction_origin": "raw"},
    {"measured_channels": ["S11", "S11"]}, {"data": {"S11": [[float("nan"), 0]] * 2, "S21": [[1, 0]] * 2}},
])
def test_partial_trace_cannot_fabricate_grid_or_channels(patch):
    raw = receipt(); raw["acquisition"].update(patch)
    with pytest.raises(ValueError):
        validate_result_v3("capture", dict(profile=PROFILE, orientation="forward"), SERIAL, raw)


def test_trial_counts_down_with_sqlite_wall_clock_and_does_not_renew():
    now = datetime(2026, 11, 23, 15, tzinfo=timezone(timedelta(hours=3)))
    observed = now.replace(tzinfo=None)
    assert bench.arinst_license("TRIAL", 60, observed, now)["trial_remaining_seconds"] == 60
    assert bench.arinst_license("TRIAL", 60, observed, now + timedelta(seconds=61))["state"] == "LOCKED"
    assert bench.arinst_license("ACTIVATED", None, observed, now)["message"] == "Лицензия активна"


def test_unknown_grid_is_never_calibration_input():
    with pytest.raises(ValueError, match="не подтверждены"):
        bench.arinst_solve({}, [(receipt("measure_standard", dict(profile=PROFILE, orientation="forward", standard="OPEN")),
                                  "artifact", "0" * 64)])


def solver_inputs(context):
    profile = {**PROFILE, "points": 3, "stop_hz": 2e9}
    context = replace(context, instrument_uid=SERIAL[7:], profile_fingerprint=fingerprint(profile))
    standards = make_standards(context)
    snapshot = dict(operator=dict(id="operator", name="Test", department="QC"),
        commissioning=dict(raw_semantics_evidence="synthetic-raw-semantics-v1", grid_evidence="synthetic-grid"),
        calibration_context=asdict(context),
        characterized_standards={m.standard.kind: bench.arinst_json(asdict(m.standard)) for m in standards})
    captures = []
    for index, m in enumerate(standards):
        raw = receipt("measure_standard", dict(profile=profile, orientation="forward", standard=m.standard.kind))
        raw["acquisition"].update(points=3, frequencies_hz=list(m.measured.frequencies), grid_confirmed=True,
            correction_origin="raw", measured_channels=["S11"], data=dict(S11=bench.arinst_json(m.measured.s11)))
        captures.append((raw, m.measured.evidence_id, str(index + 1) * 64))
    return snapshot, captures


def test_solver_adapter_preserves_coefficients_and_immutable_evidence(context):
    snapshot, captures = solver_inputs(context)
    solved = bench.arinst_solve(snapshot, captures)
    assert solved["method"] == "sol" and not solved["acceptance_eligible"]
    assert [complex(*v) for v in solved["coefficients"]["directivity"]] == pytest.approx(D)
    assert solved["raw_artifacts"] == [dict(id=i, sha256=h) for _, i, h in captures]
    assert solved["verification"] == dict(status="required", passed=False)
    assert solved["characterized_kit_sha256"] == fingerprint(snapshot["characterized_standards"])


@pytest.mark.parametrize("change", ["grid", "origin", "firmware", "orientation", "profile"])
def test_solver_adapter_rejects_incompatible_or_already_corrected_acquisitions(context, change):
    snapshot, captures = solver_inputs(context)
    raw = captures[0][0]
    if change == "grid":
        raw["acquisition"]["frequencies_hz"][1] += 1
    elif change == "origin":
        raw["acquisition"]["correction_origin"] = "host"
    elif change == "firmware":
        raw["firmware"] = "different"
    elif change == "orientation":
        raw["acquisition"]["orientation"] = "reverse"
    else:
        raw["profile"]["if_bandwidth_hz"] = 500
    with pytest.raises(ValueError):
        bench.arinst_solve(snapshot, captures)
