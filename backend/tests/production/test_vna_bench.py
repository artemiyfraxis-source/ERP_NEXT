"""Bench session domain: steps for the v2 agent, screens it accepts, the 11.5 gates and the kit choice."""

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.production import vna_bench as bench
from app.modules.production.domain import ProductionValidationError
from app.modules.production.instrument_norms import norm_by_id
from app.modules.production.vna_bench_protocol import ApplyScreenArgs, screen_hash
from app.modules.production.vna_screens import screen

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone(timedelta(hours=3)))
TEMPS = {"vna_c": [30.0, 30.5], "acm_c": 29.8}
SNAPSHOT = dict(profile=bench.adapter_profile().model_dump(mode="json"),
                acm=dict(id="11111111-1111-1111-1111-111111111111", serial="26140008", definition={"role": "auto_calibration"}))


def test_session_steps_follow_the_agent_order():
    """26.11.2026: the ACM calibration, «Кор», then one device reading — no confidence test, no LOAD (owner)."""
    steps = bench.session_steps(SNAPSHOT)
    assert [s["op"] for s in steps] == ["prepare", "apply_profile", "begin_calibration", "acm_calibrate", "save_calibration",
                                        "device_status"]
    assert [s["human"] for s in steps] == [False, False, False, True, False, False]
    assert {s["item"]["kind"] for s in steps[2:]} == {"calibration"} and len({s["item"]["run_id"] for s in steps[2:]}) == 1
    assert "acm_confidence" not in bench.SESSION_OPS
    assert not bench.calibration_steps(SNAPSHOT, acm_connected=True)[1]["human"]


def test_item_screens_carry_every_traced_parameter():
    steps = bench.item_steps(bench.adapter_profile(), screen("adapter"), "Развёртка", bench.run("instrument"))
    applied = steps[1]["args"]
    ApplyScreenArgs.model_validate(applied)
    assert steps[2]["args"]["screen_hash"] == applied["screen_hash"]
    assert {t["param"] for t in applied["screen"]["traces"]} >= {"S11", "S21", "S12", "S22"}


def test_fit_screen_adds_hidden_traces_clips_lines_and_drops_markers_outside_the_sweep():
    profile = bench.adapter_profile().model_copy(update=dict(start_hz=1e6, stop_hz=6e9))
    fitted = bench.fit_screen(screen("assembly", band_hz=(1e6, 6e9), assembly_vswr=1.3), profile)
    hidden = [t for t in fitted.traces if t.hidden]
    assert [t.param for t in hidden] == ["S12"] and hidden[0].format == "MLOG"
    assert all(m <= 6e9 for m in fitted.markers_hz)
    flex = bench.fit_screen(screen("load_check"), profile)
    assert all(seg.stop_hz <= 6e9 for t in flex.traces for seg in t.limits)
    assert screen_hash(flex) != screen_hash(screen("load_check"))


@pytest.mark.parametrize("calibrated,expected", [
    (None, "Нет действующей калибровки"), (NOW - timedelta(hours=4, seconds=1), "Калибровка старше 4 часов"),
    (NOW + timedelta(minutes=1), "Калибровка старше 4 часов"), (NOW - timedelta(hours=3, minutes=59), "")])
def test_calibration_lasts_four_hours(calibrated, expected):
    assert bench.calibration_refusal(calibrated, NOW).startswith(expected)
    if calibrated is not None:   # SQLite returns the moment without its zone
        assert bench.calibration_refusal(calibrated.replace(tzinfo=None), NOW).startswith(expected)


@pytest.mark.parametrize("now,code", [
    (TEMPS, None), ({"vna_c": [31.0, 31.5], "acm_c": 1.0}, None),
    ({"vna_c": [31.01, 30.5], "acm_c": 29.8}, "temperature_drift"),
    ({"vna_c": [None, 30.5], "acm_c": 29.8}, "temperature_unknown"),
    ({"vna_c": [30.0, 30.5], "acm_c": None}, "temperature_unknown"),
    ({"vna_c": [30.0], "acm_c": 29.8}, "temperature_unknown")])
def test_temperature_stays_within_one_degree_and_unknown_never_passes(now, code):
    refusal = bench.temperature_refusal(TEMPS, now)
    assert (bench.refusal_code(refusal) if refusal else None) == code


def test_device_reading_refuses_missing_acm_and_interpolated_correction():
    good = dict(ready=True, acm=dict(serial="26140008"), correction={t: "COR" for t in bench.ALL_TRACES})
    assert bench.device_refusal(good, "26140008", bench.ALL_TRACES) == ""
    assert bench.refusal_code(bench.device_refusal({**good, "acm": None}, "26140008", bench.ALL_TRACES)) == "acm_missing"
    assert bench.refusal_code(bench.device_refusal({**good, "correction": {**good["correction"], "S22": "C?"}},
                                                   "26140008", bench.ALL_TRACES)) == "correction_lost"
    assert bench.refusal_code(bench.device_refusal({**good, "ready": False}, "26140008", bench.ALL_TRACES)) == "not_ready"


def test_upper_frequency_follows_series_norm_and_band():
    profile = bench.adapter_profile()
    assert bench.fmax_hz("Переход BNC-SMA", None, None, profile) == 4e9
    assert bench.fmax_hz("Переход TNC", None, None, profile) == 11e9
    assert bench.fmax_hz("Переход 3,5 мм", None, norm_by_id("adp1a-35"), profile) == 18e9
    assert bench.fmax_hz("Переход N", 12.0, None, profile, requested_ghz=6) == 6e9
    with pytest.raises(ProductionValidationError):
        bench.fmax_hz("Переход BNC", None, None, profile, requested_ghz=6)


def test_kit_choice_needs_the_operator_only_for_several_standards_of_a_type():
    kit = [dict(label="OPEN-F", type="OPEN"), dict(label="SHORT-F", type="SHORT"), dict(label="LOAD-F", type="LOAD"),
           dict(label="MA6666A", type="THRU"), dict(label="MA6666B", type="THRU")]
    with pytest.raises(ProductionValidationError, match="MA6666A, MA6666B"):
        bench.kit_choice(kit, {})
    plan = bench.kit_choice(kit, {"THRU": "MA6666B"})
    assert [(k, p) for k, p, _ in plan] == list(bench.KIT_ORDER) and plan[-1][2]["label"] == "MA6666B"
    with pytest.raises(ProductionValidationError, match="нет меры"):
        bench.kit_choice(kit[1:], {})


def test_read_kit_is_a_typed_v2_op_without_paths_or_slots():
    from app.modules.production.vna_bench_protocol import command_args, validate_result_v2

    assert command_args(2, "read_kit", {"label": "6650F27"}) == {"label": "6650F27"}
    for bad in ({"label": "6650F27", "slot": 28}, {"label": "a\nb"}, {"label": " "}):
        with pytest.raises(ValueError):
            command_args(2, "read_kit", bad)
    with pytest.raises(ValueError):
        command_args(1, "read_kit", {"label": "6650F27"})
    receipt = dict(serial="1", label="6650F27", standards=[dict(label="OPEN-F", type="OPEN", c=[1, 0, 0, 0])])
    saved, _ = validate_result_v2("read_kit", {"label": "6650F27"}, "1", receipt)
    assert saved["standards"][0]["z0"] == 50.0
    with pytest.raises(ValueError, match="другой"):
        validate_result_v2("read_kit", {"label": "6650F27"}, "1", {**receipt, "label": "85033"})
