"""Pure rules of «Характеристика мер»: the 3000 MHz join of the two passes and the named fixture sides."""

import cmath
import math

import pytest

from app.modules.production import kit_characterization_run as kc
from app.modules.production.kit_characterization_data import CharacterizationCurve
from app.modules.production.vna_dsp import VnaDataError


def gamma(f):
    return 0.995 * cmath.exp(-2j * math.pi * f * 30e-12)


def pass_curve(band, offset=0j, kind="OPEN"):
    grid = kc.band_grid(band)
    values = tuple(gamma(f) + offset for f in grid)
    if kind == "THRU":
        return CharacterizationCurve("THRU", grid, values, values, values, values)
    return CharacterizationCurve(kind, grid, values)


def merged(offset, kind="OPEN"):
    parts = {"low": pass_curve("low", kind=kind), "high": pass_curve("high", offset, kind=kind)}
    return kc.merge(parts, 0.0, 6e9), parts


@pytest.mark.parametrize("offset", [0.001, 0.003, complex(0.002, -0.002)])
def test_reconnection_offset_between_passes_validates_within_each_pass_and_reports_the_join(offset):
    """Realistic repeatability (0.001–0.003) between the passes: the old cross-join midpoint (≈ offset / 2) would
    exceed 0.001; validation stays within each pass and the step is its own check."""
    curve, parts = merged(offset)
    validation, reason = kc.evidence(curve, parts)
    assert validation["valid"] and reason == "", reason
    assert validation["checks"]["midpoints"] and validation["checks"]["join"]
    assert validation["midpoints"]["max_residual"] < 1e-6
    assert validation["midpoints"]["excluded_join_hz"] == [[3000e6, 3002e6]]
    assert validation["join"]["step"] == pytest.approx(abs(offset))
    assert validation["join"]["limit"] == kc.JOIN_STEP_LIMIT == kc.ALLOCATED_UNCERTAINTY


def test_a_join_step_above_the_allocated_uncertainty_fails_with_its_own_reason():
    curve, parts = merged(0.02, kind="THRU")
    validation, reason = kc.evidence(curve, parts)
    assert not validation["valid"] and validation["checks"]["midpoints"] and not validation["checks"]["join"]
    assert "Скачок на стыке проходов 3000 МГц" in reason


def test_a_curve_crossing_the_join_needs_both_passes_and_a_one_pass_curve_has_no_join():
    curve, _ = merged(0.0)
    validation, reason = kc.evidence(curve)
    assert not validation["valid"] and "обоих проходов" in reason
    low = kc.merge({"low": pass_curve("low")}, 0.0, 3e9)
    validation, _ = kc.evidence(low, {"low": pass_curve("low")})
    assert validation["valid"] and "join" not in validation["checks"] and validation["join"] == {}


def test_arinst_never_interpolates_across_the_join():
    curve, _ = merged(0.003)
    piece = dict(id="p", piece_label="OPEN-M", kind="OPEN", gender="male", gender2=None, f_min_hz=0.0, f_max_hz=6e9)
    revision = dict(revision=1, sha256=curve.source_hash, content=kc.content(curve))

    def load(target):
        return kc.arinst_standard(piece, revision, target, kit_id="k", kit_version="1:x", source="test").response

    # Exactly on the measured 1 MHz point of the second pass: its own sample, not a line from pass 1.
    at_2999, at_3000, at_3001, at_3003 = load((2999e6, 3000e6, 3001e6, 3003e6))
    assert at_2999 == pytest.approx(gamma(2999e6), abs=1e-3)            # pass 1, interpolated within it
    assert at_3000 == pytest.approx(gamma(3000e6))                      # pass 1 node
    assert at_3001 == pytest.approx(gamma(3001e6) + 0.003)              # pass 2 sample, exact
    assert at_3003 == pytest.approx(gamma(3003e6) + 0.003, abs=1e-3)   # pass 2, interpolated within it
    with pytest.raises(VnaDataError, match="стыка двух калибровок"):
        load((2990e6, 3000.5e6, 3010e6))
    with pytest.raises(VnaDataError, match="стыка двух калибровок"):
        load((2990e6, 3001.5e6, 3010e6))
    # A grid with integer-MHz points across the join loads; pass 2 owns everything above 3000 MHz.
    response = load(tuple(2990e6 + i * 1e6 for i in range(21)))
    assert response[11] == pytest.approx(gamma(3001e6) + 0.003)


def test_hints_name_the_fixture_side_toward_each_cable():
    sol = kc.connection_hint("OPEN", "male", "OPEN-M", [("СИ.908", False)])
    assert "сторона 1 (маркировка) — к кабелю порта 1" in sol and "на её сторону 2" in sol
    flipped = kc.connection_hint("OPEN", "male", "OPEN-M", [("СИ.908", True)])
    assert "сторона 2 — к кабелю порта 1" in flipped and "на её сторону 1 (маркировка);" in flipped
    thru = kc.connection_hint("THRU", "male", "THRU-M", [("СИ.908", False), ("СИ.909", False)])
    assert "СИ.908: сторона 1 (маркировка) — к кабелю порта 1" in thru
    assert "СИ.909: сторона 2 — к кабелю порта 2" in thru
    thru = kc.connection_hint("THRU", "male", "THRU-M", [("СИ.908", True), ("СИ.909", True)])
    assert "СИ.908: сторона 2 — к кабелю порта 1" in thru and "СИ.909: сторона 1 (маркировка) — к кабелю порта 2" in thru
    assert "сторона 1 (маркировка) — к кабелю порта 1, сторона 2 — к кабелю порта 2" in kc.fixture_hint()
    assert [kc.cable_side(port, flag) for port in (1, 2) for flag in (False, True)] == [1, 2, 2, 1]
