import base64
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.modules.production.vna_bench_protocol import (ARGS_V2, CONFIDENCE_POINTS_MAX, OPS_V2, RESULTS_V2, V2_OPS,
                                                      Screen, command_args, screen_hash, validate_result_v2)
from app.modules.production.vna_sessions import ARGS, OPS, RESULTS, validate_result
from app.modules.warehouse.scan_jobs_api import VNA_CONTROL_V2_OPS, VnaControlCapability
from tests.production.test_vna_sessions import steps_for

SERIAL = "26030095"
SWR = dict(pdiv=0.02, rlev=1.0, rpos=0)
LOSS = dict(pdiv=0.05, rlev=0.0, rpos=10)
SCREEN = dict(split=8, markers_hz=[1e9, 3e9, 6e9, 12e9, 18e9], title="КБ1 № 1 — ОТК", traces=[
    dict(param="S11", format="SWR", scale=SWR, limits=[dict(kind="upper", start_hz=1e7, stop_hz=1.8e10,
                                                           start_value=1.3, stop_value=1.3)]),
    dict(param="S21", format="MLOG", scale=LOSS, limits=[dict(kind="lower", start_hz=1e7, stop_hz=1.8e10,
                                                             start_value=-0.3, stop_value=-0.5)]),
    dict(param="S12", format="MLOG", scale=LOSS, hidden=True),
    dict(param="S22", format="SWR", scale=SWR, memory=True),
    dict(param="S11", format="SMIT", scale=dict(pdiv=1.0)),
    dict(param="S21", format="GDEL", scale="auto"),
])


def screen_args(screen=None):
    screen = deepcopy(screen or SCREEN)
    return dict(screen=screen, screen_hash=screen_hash(Screen.model_validate(screen)))


def capture_args():
    return next(s["args"] for s in steps_for("acm") if s["op"] == "capture")


def capture_result(args):
    traces = args["profile"]["traces"]
    return dict(serial=SERIAL, profile=args["profile"], correction={t: "COR" for t in traces}, artifacts=[dict(
        file_name="trace.s2p", traces=traces,
        content_base64=base64.b64encode(b"# Hz S RI R 50\n1000000 .1 0 .9 0 .9 0 .1 0\n").decode())])


def test_v2_extends_v1_tables_without_changing_them():
    assert OPS_V2 == OPS + V2_OPS and set(ARGS_V2) == set(RESULTS_V2) == set(OPS_V2)
    assert set(ARGS) == set(RESULTS) == set(OPS) and not set(V2_OPS) & set(OPS)
    assert all(ARGS_V2[op] is ARGS[op] and RESULTS_V2[op] is RESULTS[op] for op in OPS if op != "capture")
    # The heartbeat (warehouse) spells the same op list without importing production.
    assert VNA_CONTROL_V2_OPS == V2_OPS


def test_heartbeat_ops_strict_for_v2_and_filtered_for_newer_protocols():
    newer = VnaControlCapability.model_validate(dict(version=3, ops=["release", "future_op", "release", "device_status"]))
    assert newer.ops == ["release", "device_status"]
    with pytest.raises(ValidationError):
        VnaControlCapability.model_validate(dict(version=2, ops=["release", "future_op"]))
    with pytest.raises(ValidationError):
        VnaControlCapability.model_validate(dict(version=1, ops=["release"]))


def test_screen_numbers_normalized_so_a_com_readback_echo_matches():
    third = deepcopy(SCREEN)
    third["traces"][0]["scale"]["pdiv"] = 0.1 / 3
    third["traces"][0]["limits"][0]["start_value"] = 1 + 0.1 / 3
    third["markers_hz"][0] = 1e9 / 3
    args = command_args(2, "apply_screen", screen_args(third))
    assert args["screen"]["traces"][0]["scale"]["pdiv"] == 0.0333333333
    assert args["screen"]["markers_hz"][0] == 333333333.0
    readback = deepcopy(args)  # COM returns doubles with noise beyond 9 significant digits
    readback["screen"]["traces"][0]["scale"]["pdiv"] = 0.033333333300000004
    readback["screen"]["traces"][0]["limits"][0]["start_value"] = 1.0333333330000001
    readback["screen"]["markers_hz"][0] = 333333333.00000006
    assert validate_result_v2("apply_screen", args, SERIAL, dict(serial=SERIAL, **readback))
    rounded = deepcopy(third)
    rounded["traces"][0]["scale"]["pdiv"] = 0.0333333333
    assert screen_hash(Screen.model_validate(rounded)) == args["screen_hash"]


def test_apply_screen_round_trip_and_readback_mismatch():
    args = command_args(2, "apply_screen", screen_args())
    assert args["screen_hash"] == screen_args()["screen_hash"]
    receipt = dict(serial=SERIAL, **deepcopy(args))
    saved, artifacts = validate_result_v2("apply_screen", args, SERIAL, receipt)
    assert saved["screen_hash"] == args["screen_hash"] and artifacts == []
    receipt["screen"]["traces"][0]["scale"]["pdiv"] = 0.05
    with pytest.raises(ValueError, match="не совпадает"):
        validate_result_v2("apply_screen", args, SERIAL, receipt)
    with pytest.raises(ValueError, match="Серийный"):
        validate_result_v2("apply_screen", args, SERIAL, dict(serial="999", **deepcopy(args)))


def test_screen_hash_is_bound_to_the_screen():
    forged = screen_args()
    forged["screen"]["title"] = "Другой"
    with pytest.raises(ValidationError, match="Отпечаток"):
        command_args(2, "apply_screen", forged)


def _bad(path, value):
    screen = deepcopy(SCREEN)
    target = screen
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return screen


@pytest.mark.parametrize("screen", [
    _bad(["split"], 0), _bad(["split"], 17), _bad(["split"], "8"),
    _bad(["traces"], []), _bad(["traces"], [SCREEN["traces"][0]] * 17),
    _bad(["traces", 0, "param"], "S33"), _bad(["traces", 0, "format"], "REAL"),
    _bad(["traces", 0, "scale"], "AUTO"), _bad(["traces", 0, "scale"], dict(pdiv=0.02)),
    _bad(["traces", 0, "scale"], dict(SWR, rpos=11)), _bad(["traces", 0, "scale"], dict(SWR, rpos=5.0)),
    _bad(["traces", 0, "scale"], dict(SWR, pdiv=0)), _bad(["traces", 0, "scale"], dict(SWR, pdiv=float("nan"))),
    _bad(["traces", 4, "scale"], dict(pdiv=1.0, rlev=0.0)),
    _bad(["traces", 4, "limits"], SCREEN["traces"][0]["limits"]),
    _bad(["traces", 0, "limits", 0, "stop_hz"], 1e7), _bad(["traces", 0, "limits", 0, "kind"], "both"),
    _bad(["traces", 0, "limits"], SCREEN["traces"][0]["limits"] * 21),
    _bad(["traces", 0, "scpi"], "CALC:PAR1:DEF S11"),
    _bad(["markers_hz"], [3e9, 1e9]), _bad(["markers_hz"], [1e9, 1e9]),
    _bad(["markers_hz"], [1e9 + i for i in range(16)]), _bad(["markers_hz"], [0]),
    _bad(["title"], "a\nb"), _bad(["title"], "x" * 257),
    _bad(["path"], "C:\\VNA\\Экраны\\ОТК.sta"),
])
def test_screen_boundaries(screen):
    with pytest.raises(ValidationError):
        Screen.model_validate(screen)


@pytest.mark.parametrize("protocol,op,raw", [
    (1, "apply_screen", screen_args()), (1, "device_status", {}), (1, "release", dict(screen_hash=None)),
    (2, "raw_scpi", dict(command="*RST")), (2, "load_state", dict(path="C:/state.sta")), (4, "prepare", {}),
])
def test_unknown_ops_and_protocols_are_refused(protocol, op, raw):
    with pytest.raises(ValueError, match="не входит"):
        command_args(protocol, op, raw)


@pytest.mark.parametrize("op,raw", [
    ("device_status", dict(command="*IDN?")),
    ("release", {}), ("release", dict(screen_hash=None, path="C:/x.sta")),
    ("store_memory", dict(screen_hash="a" * 64, traces=[1, 1])), ("store_memory", dict(screen_hash="a" * 64, traces=[17])),
    ("store_memory", dict(screen_hash="A" * 64, traces=[1])),
])
def test_unknown_fields_and_bounds_rejected_in_v2_args(op, raw):
    with pytest.raises(ValidationError):
        command_args(2, op, raw)


def test_v1_contract_is_unchanged():
    args = capture_args()
    assert command_args(1, "capture", args) == args
    with pytest.raises(ValidationError):
        command_args(1, "capture", dict(args, screen_hash="a" * 64))
    receipt = capture_result(args)
    saved, artifacts = validate_result("capture", args, SERIAL, receipt)
    assert "temperatures" not in saved and len(artifacts) == 1
    with pytest.raises(ValidationError):
        validate_result("capture", args, SERIAL, dict(receipt, temperatures=dict(vna_c=[25.0]), screen_hash="a" * 64))
    prepared = dict(serial=SERIAL, ready=True, backup_id="6f1c1c39-6d43-4f7c-9a9d-8a2c3f1f9e10",
                    state_saved=True, kit_assignments_saved=True)
    assert validate_result_v2("prepare", {}, SERIAL, prepared) == validate_result("prepare", {}, SERIAL, prepared)


def test_v2_capture_requires_temperatures_and_unchanged_screen():
    args = command_args(2, "capture", dict(capture_args(), screen_hash="b" * 64))
    receipt = dict(capture_result(args), temperatures=dict(vna_c=[31.2, None], acm_c=30.9), screen_hash="b" * 64)
    saved, artifacts = validate_result_v2("capture", args, SERIAL, receipt)
    assert saved["temperatures"] == dict(vna_c=[31.2, None], acm_c=30.9) and saved["screen_hash"] == "b" * 64
    assert len(saved["artifacts"]) == 1 and "content_base64" not in saved["artifacts"][0] and len(artifacts) == 1
    with pytest.raises(ValueError, match="Экран"):
        validate_result_v2("capture", args, SERIAL, dict(receipt, screen_hash="c" * 64))
    for broken in ({k: v for k, v in receipt.items() if k != "temperatures"},
                   dict(receipt, temperatures=dict(vna_c=[200.0])), dict(receipt, temperatures=dict(vna_c=[])),
                   dict(receipt, temperatures=dict(vna_c=[25.0], board_c=20.0))):
        with pytest.raises(ValidationError):
            validate_result_v2("capture", args, SERIAL, broken)
    with pytest.raises(ValidationError):
        command_args(2, "capture", capture_args())  # v2 capture names the ERP screen it expects


def test_device_status_reports_unknowns_as_null():
    status = dict(serial=SERIAL, name="Planar, S50180, 26030095, 26.2.2/2", ready=True, warmup_min=None,
                  temperatures=dict(vna_c=[None, None]), acm=None, correction={}, screen_hash=None)
    assert validate_result_v2("device_status", {}, SERIAL, status)[0]["acm"] is None
    full = dict(status, warmup_min=95.5, acm=dict(model="ACM2520.2", serial="26140008", min_hz=1e5, max_hz=2e10),
                correction={"S11": "COR", "S21": "C?"}, screen_hash="d" * 64)
    assert validate_result_v2("device_status", {}, SERIAL, full)[0]["correction"]["S21"] == "C?"
    for broken in (dict(status, correction={"S11": "cor"}), dict(status, acm=dict(full["acm"], min_hz=3e10)),
                   {k: v for k, v in status.items() if k != "screen_hash"}, dict(status, scpi_log=[])):
        with pytest.raises(ValidationError):
            validate_result_v2("device_status", {}, SERIAL, broken)


def _confidence(points=5, traces=("S11", "S21", "S12", "S22")):
    profile = dict(capture_args()["profile"], points=points)
    args = command_args(2, "acm_confidence", dict(profile=profile, module_serial="26140008"))
    start, stop = profile["start_hz"], profile["stop_hz"]
    freq = [start + (stop - start) * i / (points - 1) for i in range(points)]
    arrays = {t: [[0.01 * i, -0.02] for i in range(points)] for t in traces}
    result = dict(serial=SERIAL, module_serial="26140008", frequencies_hz=freq, data=arrays, memory=deepcopy(arrays),
                  temperatures=dict(vna_c=[30.0, 30.5], acm_c=29.8))
    return args, result


def test_acm_confidence_arrays_are_bounded_and_aligned():
    args, result = _confidence()
    saved, _ = validate_result_v2("acm_confidence", args, SERIAL, result)
    assert set(saved["data"]) == set(saved["memory"]) == {"S11", "S21", "S12", "S22"}
    assert saved["memory"]["S11"][1] == [0.01, -0.02]
    for broken in (dict(result, memory={"S11": result["memory"]["S11"]}),
                   dict(result, data=dict(result["data"], S11=result["data"]["S11"][:-1])),
                   dict(result, data=dict(result["data"], S11=[[float("nan"), 0]] * 5)),
                   dict(result, data=dict(result["data"], S11=[[0, 0, 0]] * 5)),
                   dict(result, frequencies_hz=list(reversed(result["frequencies_hz"])))):
        with pytest.raises(ValidationError):
            validate_result_v2("acm_confidence", args, SERIAL, broken)
    shifted = dict(result, frequencies_hz=[f + 1e3 for f in result["frequencies_hz"]])
    for broken in (shifted, dict(result, module_serial="other"),
                   dict(result, data={"S11": result["data"]["S11"]}, memory={"S11": result["memory"]["S11"]})):
        with pytest.raises(ValueError):
            validate_result_v2("acm_confidence", args, SERIAL, broken)
    with pytest.raises(ValidationError, match="не более"):
        _confidence(points=CONFIDENCE_POINTS_MAX + 1)


def test_store_memory_and_release_echo_the_erp_screen():
    digest = "e" * 64
    args = command_args(2, "store_memory", dict(screen_hash=digest, traces=[2, 1]))
    assert validate_result_v2("store_memory", args, SERIAL, dict(serial=SERIAL, screen_hash=digest, memorized=[1, 2]))
    with pytest.raises(ValueError, match="память"):
        validate_result_v2("store_memory", args, SERIAL, dict(serial=SERIAL, screen_hash=digest, memorized=[1]))
    release = command_args(2, "release", dict(screen_hash=digest))
    released = dict(serial=SERIAL, released=True, continuous=True, screen_hash=digest)
    assert validate_result_v2("release", release, SERIAL, released)[0]["continuous"] is True
    with pytest.raises(ValueError, match="экран ERP"):
        validate_result_v2("release", release, SERIAL, dict(released, screen_hash=None))
    with pytest.raises(ValidationError):
        validate_result_v2("release", release, SERIAL, dict(released, continuous=False))
    assert validate_result_v2("release", command_args(2, "release", dict(screen_hash=None)), SERIAL,
                              dict(released, screen_hash=None))
