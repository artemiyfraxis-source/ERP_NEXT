import math

import pytest

from app.modules.production.vna_profiles import C_M_S, derive_profile

NORM = {"status": "ok", "bandMhz": [1, 18000], "vswrMax": 1.85}


def profile(**changes):
    args = dict(length_m=.6, velocity_factor=.7, max_points=100001, instrument_band_ghz=(.0001, 18))
    args.update(changes)
    return derive_profile(NORM, **args)


def test_ripple_formula_sampling_limit_and_defaults():
    result = profile()
    ripple = C_M_S * .7 / (2 * .6) / 1e6
    assert result["required_points"] == max(401, math.ceil(17999 / (ripple / 10)) + 1)
    assert result["points"] == result["required_points"]
    assert result["actual_step_mhz"] <= ripple / 10
    assert result["status"] == "ready"
    assert (result["if_bandwidth_hz"], result["power_dbm"], result["format"]) == (1000, 0, "SWR")
    assert not result["smoothing"] and not result["averaging"]
    assert result["limit_line"] == {"band_mhz": [1, 18000], "vswr_max": 1.85}


def test_minimum_points_and_capacity_never_silently_pass():
    assert profile(length_m=.001)["points"] == 401
    result = profile(max_points=401)
    assert result["status"] == "blocked" and result["points"] == 401
    assert result["required_points"] > 401 and result["band_mhz"] == [1, 18000]


@pytest.mark.parametrize("changes", [{"length_m": None}, {"velocity_factor": None}, {"velocity_factor": 1.5},
    {"velocity_factor": 0}, {"velocity_factor": float('nan')}, {"length_m": float('inf')},
    {"max_points": None}, {"instrument_band_ghz": (.001, 6)}, {"instrument_band_ghz": None}])
def test_missing_or_incompatible_inputs_block(changes):
    result = profile(**changes)
    assert result["status"] == "blocked" and result["problems"]


@pytest.mark.parametrize("norm", [{}, {"status": "ok", "vswrMax": 1.3, "bandMhz": [1, None]},
    {"status": "ok", "vswrMax": float('nan'), "bandMhz": [1, 2]}])
def test_incomplete_norm_is_visible(norm):
    assert derive_profile(norm)["status"] == "blocked"
