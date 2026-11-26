"""Synthetic error-box recovery and an independent Mobius cross-ratio reference.

No instrument or catalog coefficient is claimed as characterized by these tests.
The reference uses projective invariance, not the production linear solve.
"""

import cmath
import math
import random
from dataclasses import FrozenInstanceError, replace

import pytest

from app.modules.production.arinst_calibration import (
    ALGORITHM_VERSION, SMA_MF06_STANDARDS, Acquisition, CalibrationContext,
    CalibrationStandard, StandardMeasurement, apply_calibration, solve_sol,
)
from app.modules.production.vna_checks import KitStandard
from app.modules.production.vna_dsp import VnaDataError

GRID = (1e6, 1e9, 2e9)
D = (0.03 + 0.02j, -0.02 + 0.01j, 0.01 - 0.04j)
S = (0.1 - 0.04j, 0.04 + 0.07j, -0.05 - 0.1j)
T = (0.8 + 0.2j, 0.85 - 0.1j, 0.6 + 0.3j)


@pytest.fixture
def context():
    return CalibrationContext("arinst:1-2-3", "2.0.0", "profile-1", 1000,
                              "physical-kit-7", "characterized-v1", "cables-and-adapters-1",
                              "DUT-A/DUT-B", "forward", "connection-1", ("male", "male"), (1e6, 6e9))


def embed(gamma, d=D, s=S, t=T):
    return tuple(a + b * g / (1 - c * g) for g, a, c, b in zip(gamma, d, s, t))


def acquisition(context, values, *, evidence="dut-1", **kwargs):
    return Acquisition(context, GRID, tuple(values), evidence, correction_origin="raw",
                       raw_semantics_evidence="synthetic-raw-semantics-v1", grid_confirmed=True, **kwargs)


def definition(context, kind, values):
    return CalibrationStandard(f"standard-{kind}", context.kit_id, context.kit_version,
                               kind, context.standard_genders if kind == "THRU" else context.standard_genders[:1],
                               (0, 6e9), "coefficients-v1", "synthetic-fixture", GRID, tuple(values))


def make_standards(context, d=D, s=S, t=T):
    # Nonideal frequency-dependent OPEN, SHORT and LOAD, independent of kit_response.
    actuals = ((0.98 - 0.03j, 0.80 - 0.45j, 0.5 - 0.75j),
               (-0.99 + 0.01j, -0.91 + 0.20j, -0.7 + 0.48j),
               (0.02 + 0.01j, 0.035 - 0.01j, -0.02 + 0.025j))
    return tuple(StandardMeasurement(definition(context, kind, actual),
                                     acquisition(context, embed(actual, d, s, t), evidence=f"raw-{kind}"))
                 for kind, actual in zip(("OPEN", "SHORT", "LOAD"), actuals))


def reference_correct(measured, measured_standards, actual_standards):
    """The unique Mobius map taking measured O/S/L onto characterized O/S/L.

    Cross-ratio is invariant under a fractional linear transform. This does not
    use any calibration/error-term routines or solve a system of equations.
    """
    mo, ms, ml = measured_standards
    go, gs, gl = actual_standards
    if measured == ml:
        return gl
    ratio = (measured - mo) * (ms - ml) / ((measured - ml) * (ms - mo))
    return (ratio * (gs - go) * gl - (gs - gl) * go) / (ratio * (gs - go) - (gs - gl))


def test_recovers_known_frequency_dependent_error_terms_and_nonideal_dut(context):
    standards = make_standards(context)
    cal = solve_sol(standards[::-1])  # Ordering of the caller's list is immaterial.
    assert cal.directivity == pytest.approx(D, abs=1e-13)
    assert cal.source_match == pytest.approx(S, abs=1e-13)
    assert cal.reflection_tracking == pytest.approx(T, abs=1e-13)
    assert max(cal.residuals) < 1e-13
    assert min(cal.condition_numbers) >= 1
    assert cal.standard_ids == tuple(m.standard.standard_id for m in standards)
    assert cal.measurement_ids == tuple(m.measured.evidence_id for m in standards)
    assert cal.measurement_hashes == tuple(m.measured.content_hash for m in standards)
    expected = (0.2 - 0.5j, -0.35 + 0.15j, 0.01 + 0.001j)
    raw = acquisition(context, embed(expected))
    corrected = apply_calibration(cal, raw)
    assert corrected.s11 == pytest.approx(expected, abs=1e-13)
    assert corrected.s21 is None
    assert corrected.correction_origin == "host"
    assert corrected.calibration_id == cal.calibration_id
    assert raw.correction_origin == "raw"
    assert raw.content_hash != corrected.content_hash
    assert not cal.quality_eligible and not corrected.quality_eligible
    assert cal.method == "sol" and cal.algorithm_version == ALGORITHM_VERSION


def test_seeded_error_boxes_agree_with_independent_cross_ratio_reference(context):
    rng = random.Random(4267)
    for _ in range(100):
        d = tuple(complex(rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1)) for _ in GRID)
        s = tuple(complex(rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2)) for _ in GRID)
        t = tuple(cmath.rect(rng.uniform(0.4, 1.2), rng.uniform(-math.pi, math.pi)) for _ in GRID)
        standards = make_standards(context, d, s, t)
        cal = solve_sol(standards)
        gamma = tuple(cmath.rect(rng.uniform(0, 1), rng.uniform(-math.pi, math.pi)) for _ in GRID)
        raw = acquisition(context, embed(gamma, d, s, t))
        corrected = apply_calibration(cal, raw)
        for i in range(len(GRID)):
            reference = reference_correct(raw.s11[i], [x.measured.s11[i] for x in standards],
                                          [x.standard.response[i] for x in standards])
            assert corrected.s11[i] == pytest.approx(reference, abs=1e-12)
            assert corrected.s11[i] == pytest.approx(gamma[i], abs=1e-12)


def test_thru_is_limited_response_normalization_not_full_solt(context):
    standards = make_standards(context)
    true_thru = tuple(0.94 * cmath.exp(-2j * math.pi * f * 75e-12) for f in GRID)
    tracking = (0.8 + 0.1j, 0.7 - 0.3j, 0.5 - 0.4j)
    thru = StandardMeasurement(definition(context, "THRU", true_thru),
                               acquisition(context, embed((0j,) * 3), evidence="raw-THRU",
                                           s21=tuple(a * b for a, b in zip(true_thru, tracking))))
    cal = solve_sol(standards, thru=thru)
    assert cal.transmission_tracking == pytest.approx(tracking)
    assert cal.method == "sol+thru_response"
    expected = (0.2 + 0.4j, -0.3 + 0.15j, 0.1 - 0.07j)
    raw = acquisition(context, embed((0.1j,) * 3), s21=tuple(a * b for a, b in zip(expected, tracking)))
    corrected = apply_calibration(cal, raw)
    assert corrected.s21 == pytest.approx(expected)
    # Independent scalar response reference; no production tracking helper used.
    assert corrected.s21 == pytest.approx(tuple(m * a / r for m, a, r in
                                                zip(raw.s21, true_thru, thru.measured.s21)))
    assert not hasattr(corrected, "s12") and not hasattr(corrected, "s22")
    assert not corrected.quality_eligible


def test_s21_is_never_silently_left_raw_by_sol(context):
    cal = solve_sol(make_standards(context))
    with pytest.raises(VnaDataError, match="передачу"):
        apply_calibration(cal, acquisition(context, (0j,) * 3, s21=(1 + 0j,) * 3))


@pytest.mark.parametrize("origin", ["unknown", "instrument", "host"])
def test_nonraw_standard_and_dut_are_rejected(context, origin):
    standards = make_standards(context)
    cal = solve_sol(standards)
    raw = replace(standards[0].measured, correction_origin=origin,
                  calibration_id=cal.calibration_id if origin == "host" else None)
    with pytest.raises(VnaDataError, match="исходных данных"):
        solve_sol((replace(standards[0], measured=raw), *standards[1:]))
    with pytest.raises(VnaDataError, match="исходных данных"):
        apply_calibration(cal, raw)


def test_raw_label_without_commissioning_evidence_is_insufficient(context):
    standards = make_standards(context)
    raw = replace(standards[0].measured, raw_semantics_evidence="")
    with pytest.raises(VnaDataError, match="подтверждение"):
        solve_sol((replace(standards[0], measured=raw), *standards[1:]))


def test_double_application_is_rejected(context):
    cal = solve_sol(make_standards(context))
    corrected = apply_calibration(cal, acquisition(context, embed((0.2j,) * 3)))
    with pytest.raises(VnaDataError, match="исходных данных"):
        apply_calibration(cal, corrected)
    with pytest.raises(VnaDataError, match="ссылку"):
        replace(corrected, correction_origin="raw")


@pytest.mark.parametrize("field,value", [
    ("instrument_uid", "arinst:4-5-6"), ("firmware", "2.1.0"), ("profile_fingerprint", "profile-2"),
    ("if_bandwidth_hz", 100), ("kit_id", "other-kit"), ("kit_version", "v2"),
    ("fixture_fingerprint", "changed-cable"), ("reference_plane", "adapter-plane"),
    ("orientation", "reverse"), ("connection_id", "reconnected"),
    ("standard_genders", ("female", "female")), ("usable_band_hz", (1e6, 3e9)),
])
def test_all_reuse_context_changes_invalidate_calibration(context, field, value):
    standards = make_standards(context)
    cal = solve_sol(standards)
    raw = replace(standards[0].measured, context=replace(context, **{field: value}))
    with pytest.raises(VnaDataError, match="Не совпадают"):
        apply_calibration(cal, raw)
    raw = replace(raw, evidence_id=standards[1].measured.evidence_id)
    with pytest.raises(VnaDataError, match="Не совпадают"):
        solve_sol((standards[0], replace(standards[1], measured=raw), standards[2]))


@pytest.mark.parametrize("grid", [(1e6, 1.5e9, 2e9), (1e6, 1e9, 3e9), (1e6, 1e9)])
def test_no_interpolation_extrapolation_or_implicit_subsetting(context, grid):
    cal = solve_sol(make_standards(context))
    raw = replace(acquisition(context, (0j,) * 3), frequencies=grid, s11=(0j,) * len(grid))
    with pytest.raises(VnaDataError, match="Сетка"):
        apply_calibration(cal, raw)


@pytest.mark.parametrize("grid", [(), (0,), (-1,), (math.inf,), (math.nan,), (2e6, 1e6), (1e6, 1e6)])
def test_bad_frequency_grids(context, grid):
    with pytest.raises(VnaDataError):
        Acquisition(context, grid, (0j,) * len(grid), "invalid")


@pytest.mark.parametrize("trace", [(), (0j,), (complex(math.inf, 0),) * 3, (complex(0, math.nan),) * 3,
                                  (complex(1.7e308, 1.7e308),) * 3])
def test_bad_trace_shapes_and_nonfinite_values(context, trace):
    with pytest.raises(VnaDataError):
        acquisition(context, trace)
    with pytest.raises(VnaDataError):
        acquisition(context, (0j,) * 3, s21=trace)


@pytest.mark.parametrize("field,value", [("kit_id", "other"), ("kit_version", "v2"),
                                        ("genders", ("female",)), ("frequencies", (1e6, 1.1e9, 2e9))])
def test_wrong_standard_identity_gender_and_grid(context, field, value):
    standards = make_standards(context)
    wrong = replace(standards[0].standard, **{field: value})
    with pytest.raises(VnaDataError):
        solve_sol((replace(standards[0], standard=wrong), *standards[1:]))


def test_band_intersection_and_missing_characterization(context):
    with pytest.raises(VnaDataError, match="полосу"):
        acquisition(replace(context, usable_band_hz=(1e6, 1.5e9)), (0j,) * 3)
    standards = make_standards(context)
    with pytest.raises(VnaDataError, match="полосу"):
        replace(standards[0].standard, band_hz=(0, 1.5e9))
    unknown = replace(standards[0].standard, frequencies=(), response=(),
                      coefficient_version="", characterization_source="")
    with pytest.raises(VnaDataError, match="Нет характеристики"):
        solve_sol((replace(standards[0], standard=unknown), *standards[1:]))
    with pytest.raises(VnaDataError, match="источник"):
        replace(standards[0].standard, characterization_source="")


@pytest.mark.parametrize("separation", [0.0, 1e-11])
def test_singular_and_ill_conditioned_standards_are_rejected(context, separation):
    standards = make_standards(context)
    changed = []
    for i, measurement in enumerate(standards):
        actual = (complex(0.5 + separation * i),) * 3
        changed.append(StandardMeasurement(replace(measurement.standard, response=actual),
                                            replace(measurement.measured, s11=embed(actual))))
    with pytest.raises(VnaDataError, match="обусловленная"):
        solve_sol(changed)


def test_constant_receiver_and_near_zero_tracking_are_rejected(context):
    for tracking in ((0j,) * 3, (1e-13 + 0j,) * 3):
        with pytest.raises(VnaDataError):
            solve_sol(make_standards(context, t=tracking))


def test_inverse_pole_is_rejected_without_nan_or_infinity(context):
    cal = solve_sol(make_standards(context))
    raw = acquisition(context, tuple(d - t / s for d, t, s in zip(D, T, S)))
    with pytest.raises(VnaDataError, match="знаменатель"):
        apply_calibration(cal, raw)


@pytest.mark.parametrize("actual,measured", [(0j, 1 + 0j), (1 + 0j, 0j), (1e-13 + 0j, 1 + 0j)])
def test_singular_thru_is_rejected(context, actual, measured):
    thru = StandardMeasurement(definition(context, "THRU", (actual,) * 3),
                               acquisition(context, (0j,) * 3, evidence="raw-THRU", s21=(measured,) * 3))
    with pytest.raises(VnaDataError, match="знаменатель"):
        solve_sol(make_standards(context), thru=thru)


def test_thru_requires_measured_channel_and_correct_standard_type(context):
    thru = StandardMeasurement(definition(context, "THRU", (1 + 0j,) * 3),
                               acquisition(context, (0j,) * 3, evidence="raw-THRU"))
    with pytest.raises(VnaDataError, match="S21"):
        solve_sol(make_standards(context), thru=thru)


def test_three_distinct_standards_and_distinct_artifacts_required(context):
    standards = make_standards(context)
    for invalid in (standards[:2], standards + standards[:1], (standards[0],) * 3):
        with pytest.raises(VnaDataError, match="ровно три"):
            solve_sol(invalid)
    duplicated = replace(standards[1], standard=replace(standards[1].standard,
                                                       standard_id=standards[0].standard.standard_id))
    with pytest.raises(VnaDataError, match="Идентификаторы"):
        solve_sol((standards[0], duplicated, standards[2]))
    duplicated = replace(standards[1], measured=replace(standards[1].measured,
                                                       evidence_id=standards[0].measured.evidence_id))
    with pytest.raises(VnaDataError, match="отдельные"):
        solve_sol((standards[0], duplicated, standards[2]))


def test_calibration_hash_is_deterministic_and_covers_characterization_and_raw_data(context):
    standards = make_standards(context)
    cal = solve_sol(standards)
    assert cal.calibration_id == solve_sol(standards[::-1]).calibration_id
    changed = replace(standards[0], standard=replace(standards[0].standard, coefficient_version="v2"))
    assert cal.calibration_id != solve_sol((changed, *standards[1:])).calibration_id
    changed = replace(standards[0], measured=replace(standards[0].measured, evidence_id="new-sweep"))
    assert cal.calibration_id != solve_sol((changed, *standards[1:])).calibration_id


def test_catalog_is_uncharacterized_and_has_gender_specific_limits():
    assert len(SMA_MF06_STANDARDS) == 8
    for item in SMA_MF06_STANDARDS:
        expected_max = 3e9 if item.genders == ("female",) and item.kind in ("OPEN", "SHORT") else 6e9
        assert item.band_hz == (0, expected_max)
        assert item.response == () and item.characterization_source == ""
        assert item.coefficient_version == ""


@pytest.mark.parametrize("kind", ["OPEN", "SHORT", "LOAD", "THRU"])
def test_explicit_nonideal_model_reuses_existing_helper(context, kind):
    blank = replace(definition(context, kind, (0j,) * 3), frequencies=(), response=())
    model = KitStandard(kind, delay=35e-12, c=(65, 0, 0, 0), l=(85, 0, 0, 0))
    characterized = blank.with_model(model, GRID, coefficient_version="v1", characterization_source="synthetic")
    for f, actual in zip(GRID, characterized.response):
        omega = 2 * math.pi * f
        if kind == "THRU":
            expected = cmath.exp(-1j * omega * model.delay)
        elif kind == "LOAD":
            expected = 0j
        else:
            impedance = 1 / (1j * omega * 65e-15) if kind == "OPEN" else 1j * omega * 85e-12
            expected = (impedance - 50) / (impedance + 50) * cmath.exp(-2j * omega * model.delay)
        assert actual == pytest.approx(expected, abs=1e-14)


@pytest.mark.parametrize("changes", [{"delay": math.nan}, {"delay": -1}, {"loss": math.inf},
                                     {"loss": -1}, {"c": (math.nan, 0, 0, 0)}, {"l": (0, 0, 0)}])
def test_bad_model_coefficients_rejected(context, changes):
    standard = definition(context, "OPEN", (1 + 0j,) * 3)
    with pytest.raises(VnaDataError):
        standard.with_model(KitStandard("OPEN", **changes), GRID, coefficient_version="v1",
                            characterization_source="synthetic")


def test_single_point_and_reverse_are_explicitly_supported_without_channel_remapping(context):
    reverse = replace(context, orientation="reverse")
    standards = tuple(StandardMeasurement(replace(m.standard, frequencies=GRID[:1], response=m.standard.response[:1]),
                                          replace(m.measured, frequencies=GRID[:1], s11=m.measured.s11[:1]))
                      for m in make_standards(reverse))
    cal = solve_sol(standards)
    dut = replace(acquisition(reverse, embed((0.3j,) * 3)), frequencies=GRID[:1],
                  s11=embed((0.3j,) * 3)[:1])
    corrected = apply_calibration(cal, dut)
    assert corrected.s11 == pytest.approx((0.3j,))
    assert corrected.context.orientation == "reverse"
    assert not hasattr(corrected, "s22")


def test_sequences_are_copied_and_results_are_immutable(context):
    values = [0j] * 3
    raw = acquisition(context, values, s21=values)
    values[0] = 10j
    assert raw.s11[0] == raw.s21[0] == 0j
    with pytest.raises(FrozenInstanceError):
        raw.correction_origin = "host"


def test_changed_raw_semantics_evidence_is_rejected(context):
    cal = solve_sol(make_standards(context))
    raw = replace(acquisition(context, (0j,) * 3), raw_semantics_evidence="different-commissioning")
    with pytest.raises(VnaDataError, match="Изменилось"):
        apply_calibration(cal, raw)


def test_unconfirmed_frequency_grid_cannot_calibrate_or_be_corrected(context):
    standards = make_standards(context)
    cal = solve_sol(standards)
    raw = replace(standards[0].measured, grid_confirmed=False)
    with pytest.raises(VnaDataError, match="сетка частот не подтверждена"):
        solve_sol((replace(standards[0], measured=raw), *standards[1:]))
    with pytest.raises(VnaDataError, match="сетка частот не подтверждена"):
        apply_calibration(cal, raw)


def test_intermediate_solver_magnitude_overflow_is_a_validation_error(context):
    standards = make_standards(context)
    # Each input magnitude is finite; their product is complex(1.7e308, 1.7e308).
    extreme = StandardMeasurement(replace(standards[0].standard, response=(1 + 1j,) * 3),
                                  replace(standards[0].measured, s11=(1.7e308 + 0j,) * 3))
    with pytest.raises(VnaDataError, match="Переполнение"):
        solve_sol((extreme, *standards[1:]))


def test_intermediate_correction_overflow_is_a_validation_error(context):
    cal = solve_sol(make_standards(context))
    # An invalid persisted coefficient must not leak OverflowError from abs(s*delta).
    extreme = replace(cal, source_match=(1.7e308 + 0j,) * 3)
    raw = acquisition(context, tuple(d + 1 + 1j for d in D))
    with pytest.raises(VnaDataError, match="переполнение"):
        apply_calibration(extreme, raw)
