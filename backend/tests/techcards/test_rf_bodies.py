from copy import deepcopy
import json

import pytest

from app.modules.techcards.rf_bodies import load_rf_bodies, validate_rf_bodies


@pytest.fixture
def sma():
    return load_rf_bodies()["SMA"]


def test_documented_sma_bodies_keep_contact_and_shield_termination_separate(sma):
    assert {body["gender"] for body in sma["bodies"]} == {"male", "female"}
    for body in sma["bodies"]:
        assert body["angle"] == 0
        assert body["pin_type"] == "пайка" and body["shield_type"] == "обжим"
        assert body["cables"] == ["RG-174", "RG-316"]
        assert "Amphenol RF" in body["src"]
        assert "MIL-STD-348B" in body["interface"]["depth"]["src"]
    male, female = sma["bodies"]
    assert male["interface"]["depth"]["min"] == 0.38
    assert male["interface"]["depth"]["max"] == 1.14
    assert female["interface"]["depth"]["min"] == 1.88
    assert female["interface"]["depth"]["max"] == 2.23
    assert male["interface"]["thread"]["value"] == "1/4-36 UNS-2B"
    assert female["interface"]["thread"]["value"] == "1/4-36 UNS-2A"
    assert validate_rf_bodies(sma) == sma


@pytest.mark.parametrize("gender,limit,value", [
    ("male", "min", 0.37), ("male", "max", 1.15),
    ("female", "min", 1.87), ("female", "max", 2.41),
])
def test_sma_depth_cannot_drift_from_mil(sma, gender, limit, value):
    body = next(body for body in sma["bodies"] if body["gender"] == gender)
    body["interface"]["depth"][limit] = value
    with pytest.raises(ValueError, match="SMA depth must match MIL-STD-348B"):
        validate_rf_bodies(sma)


@pytest.mark.parametrize("key", ["depth", "hex", "thread", "ref_plane"])
@pytest.mark.parametrize("source", [None, "", "   "])
def test_every_interface_dimension_requires_a_source(sma, key, source):
    dimension = sma["bodies"][0]["interface"][key]
    dimension["src"] = source
    with pytest.raises(ValueError, match="src"):
        validate_rf_bodies(sma)


def test_scalar_interface_dimensions_are_not_accepted(sma):
    sma["bodies"][0]["interface"]["hex"] = 7.94
    with pytest.raises(ValueError, match="dimension must have src"):
        validate_rf_bodies(sma)


def test_inch_sources_need_an_explicit_conversion_note(sma):
    depth = sma["bodies"][0]["interface"]["depth"]
    depth["src"] = 'MIL-STD-348B fig. 310-1, E .015" to .045"'
    with pytest.raises(ValueError, match="пересчитано из дюймов"):
        validate_rf_bodies(sma)
    depth["src"] += "; пересчитано из дюймов"
    validate_rf_bodies(sma)


def test_inch_valued_geometry_is_rejected_even_with_a_conversion_note(sma):
    sma["bodies"][0]["interface"]["hex"]["unit"] = "inch"
    with pytest.raises(ValueError, match="millimetres"):
        validate_rf_bodies(sma)


@pytest.fixture
def with_segment(sma):
    # Synthetic geometry tests validation only; it is not catalog data.
    sma["bodies"][0]["segments"] = [{
        "part": "ferrule", "x_from": -2.0, "x_to": 3.0,
        "r_in": 1.0, "r_out": 2.0, "src": "Test drawing, all dimensions in mm",
    }]
    return sma


def test_sourced_geometry_allows_signed_axial_coordinates(with_segment):
    validate_rf_bodies(with_segment)


@pytest.mark.parametrize("key,value", [
    ("src", ""), ("part", "guessed"), ("x_from", 3.0), ("x_to", -3.0),
    ("r_in", -1.0), ("r_out", 0.5), ("x_from", float("nan")),
    ("r_out", float("inf")), ("r_in", True), ("x_to", "3 inches"),
])
def test_invalid_or_unsourced_segments_are_rejected(with_segment, key, value):
    with_segment["bodies"][0]["segments"][0][key] = value
    with pytest.raises(ValueError):
        validate_rf_bodies(with_segment)


def test_unknown_geometry_stays_absent(sma):
    assert all(body["segments"] == [] and body["note"] for body in sma["bodies"])
    assert "hex" not in sma["bodies"][1]["interface"]


def test_loader_reads_other_series_without_aliasing_sma(tmp_path, sma):
    (tmp_path / "sma.json").write_text(json.dumps(sma), encoding="utf-8")
    rp = deepcopy(sma)
    rp["series"] = "RP-SMA"
    (tmp_path / "rp-sma.json").write_text(json.dumps(rp), encoding="utf-8")
    assert set(load_rf_bodies(tmp_path)) == {"SMA", "RP-SMA"}


def test_loader_does_not_hide_invalid_or_missing_packaged_data(tmp_path, sma):
    with pytest.raises(FileNotFoundError):
        load_rf_bodies(tmp_path / "missing")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        load_rf_bodies(tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps(sma), encoding="utf-8")
    (tmp_path / "duplicate.json").write_text(json.dumps(sma), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate RF series SMA"):
        load_rf_bodies(tmp_path)


def test_loader_returns_independent_data_on_each_call():
    first = load_rf_bodies()
    first["SMA"]["bodies"].clear()
    assert len(load_rf_bodies()["SMA"]["bodies"]) == 2
