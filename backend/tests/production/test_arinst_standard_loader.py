from dataclasses import replace

import pytest

from app.modules.production.arinst_standard_loader import load_standard, resample_characterization, validate_midpoints
from app.modules.production.kit_characterization_data import CharacterizationCurve
from app.modules.production.vna_checks import KitStandard, kit_response
from app.modules.production.vna_dsp import TwoPort, VnaDataError


def smooth_curve(grid, kind="OPEN"):
    model = KitStandard(kind, delay=75e-12, c=(35, 0, 0, 0))
    return CharacterizationCurve(kind, grid, tuple(kit_response(model, f) for f in grid))


SOURCE = tuple(10e6 + i * 2e6 for i in range(101))
FINE = tuple(10e6 + i * 1e6 for i in range(201))


def test_smooth_offset_open_interpolation_and_metadata():
    curve, fine = smooth_curve(SOURCE), smooth_curve(FINE)
    target = (10e6, 10.5e6, 111.3e6, 210e6)
    evidence = validate_midpoints(curve, fine, uncertainty=0.002)
    assert evidence.max_residual < evidence.limit == 0.0002
    result = load_standard(curve, target, standard_id="physical-open-1", genders=("male",), revision=2,
                           sha256=curve.source_hash, characterization_source="Planar session 17, ACM",
                           finer_acquisition=fine, uncertainty=0.002)
    assert result.frequencies == target
    assert result.response == pytest.approx(smooth_curve(target).s11, abs=2e-6)
    assert result.standard_id == "physical-open-1"
    assert result.kind == "OPEN" and result.genders == ("male",)
    assert result.revision == 2 and result.sha256 == curve.source_hash
    assert result.coefficient_version == f"2:{curve.source_hash}"
    assert "Planar session 17, ACM" in result.characterization_source


@pytest.mark.parametrize("target", [(0, 10e6), (9e6, 10e6), (210e6, 211e6), (12e6, 10e6),
                                     (10e6, 10e6), (10e6, float("nan")), ()])
def test_invalid_target_grid_is_refused(target):
    with pytest.raises(VnaDataError):
        resample_characterization(smooth_curve(SOURCE), target)


def test_interpolation_requires_finer_validation_but_exact_grid_does_not():
    curve = smooth_curve(SOURCE)
    assert resample_characterization(curve, (10e6, 12e6)) == curve.s11[:2]
    with pytest.raises(VnaDataError):
        resample_characterization(curve, (11e6, 13e6))


@pytest.mark.parametrize("uncertainty", [None, 0, -1, float("nan"), float("inf")])
def test_unknown_or_invalid_uncertainty_is_refused(uncertainty):
    with pytest.raises(VnaDataError):
        resample_characterization(smooth_curve(SOURCE), (11e6, 13e6),
                                  finer_acquisition=smooth_curve(FINE), uncertainty=uncertainty)


@pytest.mark.parametrize("grid", [(10e6, 12e6 + 1), (10e6, 12e6, 16e6)])
def test_source_spacing_and_gaps_are_refused_even_at_exact_samples(grid):
    with pytest.raises(VnaDataError):
        resample_characterization(smooth_curve(grid), grid)


def test_midpoint_validation_requires_every_midpoint_and_matching_kind():
    curve = smooth_curve(SOURCE)
    for fine in (smooth_curve(SOURCE), smooth_curve(FINE[1:]), smooth_curve(FINE, "SHORT")):
        with pytest.raises(VnaDataError):
            validate_midpoints(curve, fine, uncertainty=0.01)


@pytest.mark.parametrize("uncertainty,error", [(1, 0.0011), (0.001, 0.00011)])
def test_midpoint_residual_uses_both_absolute_and_uncertainty_limits(uncertainty, error):
    curve = CharacterizationCurve("LOAD", (10e6, 12e6), (0j, 0j))
    fine = CharacterizationCurve("LOAD", (10e6, 11e6, 12e6), (0j, error, 0j))
    with pytest.raises(VnaDataError):
        validate_midpoints(curve, fine, uncertainty=uncertainty)


def load(curve, **kwargs):
    options = dict(standard_id="piece-1", genders=("female",), revision=1,
                   sha256=curve.source_hash, characterization_source="test acquisition") | kwargs
    return load_standard(curve, curve.frequencies, **options)


@pytest.mark.parametrize("kind", ["OPEN", "SHORT"])
def test_female_open_short_limit(kind):
    assert load(smooth_curve((2998e6, 3e9), kind)).frequencies[-1] == 3e9
    curve = smooth_curve((3e9, 3001e6), kind)
    with pytest.raises(VnaDataError):
        load(curve)
    assert load(curve, genders=("male",)).frequencies == curve.frequencies


def test_female_load_has_no_open_short_limit():
    assert load(smooth_curve((4e9, 4002e6), "LOAD")).kind == "LOAD"


@pytest.mark.parametrize("kind", ["OPEN", "SHORT"])
def test_female_boundary_cannot_interpolate_from_an_out_of_band_sample(kind):
    curve = smooth_curve((2999e6, 3001e6), kind)
    fine = smooth_curve((2999e6, 3000e6, 3001e6), kind)
    with pytest.raises(VnaDataError):
        load_standard(curve, (3e9,), standard_id="piece-1", genders=("female",), revision=1,
                      sha256=curve.source_hash, characterization_source="boundary measurement",
                      finer_acquisition=fine, uncertainty=0.01)


@pytest.mark.parametrize("kwargs", [
    {"sha256": "0" * 64}, {"sha256": "bad"}, {"revision": 0}, {"revision": True},
    {"standard_id": ""}, {"characterization_source": " "}, {"genders": ("unknown",)},
    {"genders": ("female", "male")}, {"orientation": "unknown"}, {"orientation": "reverse"},
])
def test_invalid_metadata_is_refused(kwargs):
    with pytest.raises(VnaDataError):
        load(smooth_curve(SOURCE), **kwargs)


def test_reversed_thru_uses_s12_and_reverses_connector_order():
    curve = CharacterizationCurve.from_two_port(TwoPort((10e6, 12e6), (0j, 0j), (0.8j, 0.7j),
                                                        (0.6j, 0.5j), (0j, 0j)))
    forward = load(curve, genders=("male", "female"))
    reverse = load(curve, genders=("male", "female"), orientation="reverse")
    assert forward.response == curve.s21
    assert reverse.response == curve.s12
    assert reverse.genders == ("female", "male")
    assert "reverse" in reverse.characterization_source
    assert forward.coefficient_version == reverse.coefficient_version


def test_fine_evidence_cannot_be_reused_for_changed_curve():
    curve, fine = smooth_curve(SOURCE), smooth_curve(FINE)
    changed = replace(curve, s11=tuple(v + 0.01j for v in curve.s11), source_hash="")
    with pytest.raises(VnaDataError):
        resample_characterization(changed, (11e6, 13e6), finer_acquisition=fine, uncertainty=0.01)


def test_thru_interpolation_validates_reverse_trace_and_preserves_complex_phase():
    curve = CharacterizationCurve.from_two_port(TwoPort((10e6, 12e6), (0j, 0j),
                                                        (-0.9 + 0.1j, -0.9 - 0.1j),
                                                        (0.6j, 0.8j), (0j, 0j)))
    fine = CharacterizationCurve.from_two_port(TwoPort((10e6, 11e6, 12e6), (0j,) * 3,
                                                       (-0.9 + 0.1j, -0.9 + 0j, -0.9 - 0.1j),
                                                       (0.6j, 0.7j, 0.8j), (0j,) * 3))
    assert resample_characterization(curve, (11e6,), finer_acquisition=fine,
                                     uncertainty=0.01) == pytest.approx((-0.9,))
    assert resample_characterization(curve, (11e6,), orientation="reverse", finer_acquisition=fine,
                                     uncertainty=0.01) == pytest.approx((0.7j,))
    bad = replace(fine, s12=(0.6j, 0.72j, 0.8j), source_hash="")
    with pytest.raises(VnaDataError):
        resample_characterization(curve, (11e6,), orientation="reverse", finer_acquisition=bad,
                                  uncertainty=0.01)


def two_pass_curves(offset):
    """Source nodes 10…210 MHz every 2 MHz; the second pass (from 110 MHz on) is offset by a reconnection."""
    fine = smooth_curve(FINE)
    values = tuple(v + (offset if f > 110e6 else 0) for f, v in zip(FINE, fine.s11))
    finer = CharacterizationCurve("OPEN", FINE, values)
    source = CharacterizationCurve("OPEN", FINE[::2], values[::2])
    return source, finer, ((110e6, 112e6),)


def test_midpoints_are_validated_within_each_pass_and_the_join_is_excluded():
    source, finer, joins = two_pass_curves(0.003)
    with pytest.raises(VnaDataError, match="Невязка"):
        validate_midpoints(source, finer, uncertainty=0.01)          # 111 MHz mixes the two passes
    evidence = validate_midpoints(source, finer, uncertainty=0.01, joins=joins)
    assert evidence.max_residual < evidence.limit and evidence.joins == joins
    assert evidence.midpoint_count == len(source.frequencies) - 2
    with pytest.raises(VnaDataError, match="Стык"):
        validate_midpoints(source, finer, uncertainty=0.01, joins=((110e6, 114e6),))


def test_targets_inside_the_join_take_the_owning_pass_sample_or_are_refused():
    source, finer, joins = two_pass_curves(0.003)
    args = dict(finer_acquisition=finer, uncertainty=0.01, joins=joins)
    result = resample_characterization(source, (109.5e6, 111e6, 112.5e6), **args)
    assert result[1] == finer.s11[FINE.index(111e6)]
    with pytest.raises(VnaDataError, match="стыка двух калибровок"):
        resample_characterization(source, (109.5e6, 110.5e6), **args)
    with pytest.raises(VnaDataError, match="стыка двух калибровок"):
        resample_characterization(source, (111.5e6,), joins=joins)   # no finer sample to own it
