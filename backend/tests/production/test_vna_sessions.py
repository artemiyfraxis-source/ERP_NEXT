from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from pydantic import ValidationError

from app.modules.production.vna_profiles import derive_profile
from app.modules.production.vna_sessions import (ARGS, Override, Profile, ordered_steps, resolved_profile,
                                                validate_result)
from tests.production.test_vna_planner import asset, fixture, one_port_fixture, plan, port


def session_plan(method="solt2"):
    if method == "solt1":
        vna, cable, adapter, load, kit, _ = one_port_fixture("BNC")
        assets = [vna, cable, adapter, load, kit]
        chains = plan(vna, (port("BNC", True),) * 2, *assets[1:])
    else:
        vna, cable, kit = fixture()
        if method == "acm":
            cable = replace(cable, rf=replace(cable.rf, interfaces=(port("N", True), port("3.5mm", True))))
            kit = asset("auto_calibration", port("3.5mm", False), port("3.5mm", False))
        assets = [vna, cable, kit]
        chains = plan(vna, (port("SMA", method != "acm"),) * 2, cable, kit)
    profile = derive_profile(dict(status="ok", bandMhz=[1, 6000], vswrMax=1.5),
                             length_m=1, velocity_factor=.7, max_points=200001, instrument_band_ghz=(.0001, 18))
    result = dict(status=chains["status"], profile=profile, chains=chains, etk={"revision": 3})
    return result, assets


def steps_for(method="solt2"):
    result, assets = session_plan(method)
    profile = resolved_profile(result, [])
    return ordered_steps(result, profile, {str(a.id): {**asdict(a.rf), "serial_number": "26140008"} for a in assets})


@pytest.mark.parametrize("method,measurements,captures", [("solt2", 8, 1), ("solt1", 6, 2), ("acm", 1, 1)])
def test_steps_are_derived_from_p1_including_acm_and_one_port_reversal(method, measurements, captures):
    steps = steps_for(method)
    assert steps[0]["op"] == "prepare" and steps[-1]["op"] == "restore"
    assert sum(s["op"] in ("measure_standard", "acm_calibrate") for s in steps) == measurements
    assert sum(s["op"] == "capture" for s in steps) == captures
    assert all(s["args"]["method"] == method for s in steps if s["op"] == "begin_calibration")
    if method == "solt2":
        thru = [s["args"]["standard"]["ports"] for s in steps if s["op"] == "measure_standard"
                and s["args"]["standard"]["kind"] == "THRU"]
        assert thru == [[1, 2], [2, 1]]
    if method == "solt1":
        assert [s["args"]["orientation"] for s in steps if s["op"] == "capture"] == ["А", "Б"]


def test_one_port_requires_explicit_far_end_matched_load():
    result, assets = session_plan("solt1")
    result["chains"]["orientations"][0]["termination"].pop("load_id")
    with pytest.raises(ValueError, match="нагрузка"):
        ordered_steps(result, resolved_profile(result, []), {str(a.id): asdict(a.rf) for a in assets})


def test_overrides_are_bounded_recorded_and_cannot_shorten_the_required_grid():
    plan_data, _ = session_plan()
    base = resolved_profile(plan_data, [])
    changed = resolved_profile(plan_data, [Override(field="if_bandwidth_hz", value=10, reason="Повторный замер")])
    assert changed.if_bandwidth_hz == 10 and changed.points == base.points
    for override in (Override(field="points", value=2, reason="Тест"),
                     Override(field="power_dbm", value=10, reason="Тест"),
                     Override(field="points", value=200001, reason="Тест")):
        with pytest.raises(ValueError):
            resolved_profile(plan_data, [override])
    with pytest.raises(ValidationError):
        Profile.model_validate({**base.model_dump(), "start_hz": float("nan")})


@pytest.mark.parametrize("op", list(ARGS))
def test_browser_cannot_supply_executable_commands_or_paths(op):
    with pytest.raises(ValidationError):
        ARGS[op].model_validate({"scpi": "*RST", "path": "C:/state", "powershell": "anything"})


def test_readback_requires_exact_profile_and_serial():
    args = next(s["args"] for s in steps_for() if s["op"] == "apply_profile")
    with pytest.raises(ValueError, match="Серийный"):
        validate_result("apply_profile", args, "123", dict(serial="999", **args))
    result = deepcopy(args)
    result["profile"]["points"] += 1
    with pytest.raises(ValueError, match="настройки"):
        validate_result("apply_profile", args, "123", dict(serial="123", **result))


def test_manual_points_can_resolve_missing_sampling_facts_only():
    plan_data, assets = session_plan()
    plan_data["profile"] = derive_profile(dict(status="ok", bandMhz=[1, 6000], vswrMax=1.5),
        length_m=None, velocity_factor=None, max_points=200001, instrument_band_ghz=(.0001, 18))
    plan_data["status"] = "blocked"
    override = Override(field="points", value=1601, reason="Число точек согласовано технологом")
    profile = resolved_profile(plan_data, [override])
    assert profile.points == 1601
    assert ordered_steps(plan_data, profile, {str(a.id): asdict(a.rf) for a in assets})
    plan_data["profile"]["problems"].append("Не подтверждена рабочая полоса ВАЦ")
    with pytest.raises(ValueError, match="Профиль P1"):
        resolved_profile(plan_data, [override])


def test_acm_step_carries_registered_module_identity_and_full_required_band():
    args = next(s["args"] for s in steps_for("acm") if s["op"] == "acm_calibrate")
    assert args["module_serial"] == "26140008"
    assert (args["start_hz"], args["stop_hz"]) == (1e6, 6e9)
    result = dict(serial="vna", acquired=True, module_serial="26140008", module_min_hz=1e5, module_max_hz=20e9)
    assert validate_result("acm_calibrate", args, "vna", result)[0] == result
    for change in ({"module_serial": "other"}, {"module_min_hz": 2e6}, {"module_max_hz": 5e9}):
        with pytest.raises(ValueError, match="АКМ"):
            validate_result("acm_calibrate", args, "vna", {**result, **change})


def test_acm_requires_registered_serial_before_start():
    plan_data, assets = session_plan("acm")
    with pytest.raises(ValueError, match="серийный номер АКМ"):
        ordered_steps(plan_data, resolved_profile(plan_data, []), {str(a.id): asdict(a.rf) for a in assets})


def test_two_port_acm_commissioning_does_not_authorize_one_port_binding():
    vna, cable, adapter, load, kit, acm = one_port_fixture("BNC")
    plan_data, _ = session_plan("solt1")
    plan_data["chains"] = plan(vna, (port("BNC", True),) * 2, cable, adapter, load, kit, acm)
    with pytest.raises(ValueError, match="Однопортовая калибровка АКМ"):
        ordered_steps(plan_data, resolved_profile(plan_data, []),
                      {str(a.id): {**asdict(a.rf), "serial_number": "26140008"} for a in (kit, acm)})
