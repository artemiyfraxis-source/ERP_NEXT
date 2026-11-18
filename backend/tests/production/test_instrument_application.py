"""Standard steps, round limits and the range within the item's own norm (owner 18.11: no shop-wide 1,30)."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_characterization import (
    LOSS_LADDER, VSWR_LADDER, application, characterization, round_up_ladder,
)
from app.modules.production.instrument_norms import LossNorm, LossSegment, Norm, Segment
from app.modules.production.vna_files import VnaTrace
from tests.production.test_instrument_passport import measured_file


def summary(vswr_by_ghz, loss_by_ghz=None, right=None):
    frequencies = [f * 1e9 for f in vswr_by_ghz]
    left = list(vswr_by_ghz.values())
    losses = loss_by_ghz or {f: 0.05 for f in vswr_by_ghz}
    traces = tuple(VnaTrace(name, tuple(zip(frequencies, values))) for name, values in (
        ("S11", left), ("S22", right or left), ("S21", [10 ** (-losses[f] / 20) for f in vswr_by_ghz]),
        ("S12", [0.99] * len(frequencies))))
    return replace(characterization(measured_file()), traces=traces)


@pytest.mark.parametrize("value,expected", [(1.0, 1.05), (1.05, 1.05), (1.0500000001, 1.05), (1.051, 1.10), (1.22, 1.25),
                                            (1.49, 1.50), (1.5, 1.5), (2.0, 2.0), (2.01, None)])
def test_vswr_is_rounded_up_to_the_ladder(value, expected):
    assert round_up_ladder(value, VSWR_LADDER) == expected


def test_loss_ladder_rounds_up():
    assert round_up_ladder(0.142, LOSS_LADDER) == 0.15
    assert round_up_ladder(0.342, LOSS_LADDER) == 0.5


NORM = Norm("Лист", "", 0.0, (Segment(18, 1.25),))


def test_resonance_above_twelve_ghz_ends_the_range_within_the_norm():
    measured = summary({0.01: 1.02, 1: 1.017, 3: 1.06, 6: 1.15, 12: 1.22, 13.3: 1.49, 18: 1.41})
    result = application(measured, (), NORM)
    assert [step.stop_ghz for step in result.steps] == [1, 3, 6, 12, 18]
    assert result.fit_ghz == 12 and result.limit_ghz == 12
    assert result.fit_line == "Пригоден до 12 ГГц"
    assert result.range_line == "Применять до 12 ГГц"
    assert result.table_lines()[0].split() == ["до", "ГГц", "1", "3", "6", "12"]
    assert result.at_limit.vswr_round == 1.25
    assert "КСВН ≤ 1,25" in result.values_line


def test_the_range_stops_below_the_band_holding_the_first_excess():
    # Owner decision 18.11: «Пригоден до X ГГц» stops below the classic band of the first excess (14 GHz → 12–18 → 12).
    result = application(summary({0.01: 1.02, 6: 1.1, 13.04: 1.2, 14: 1.3, 18: 1.1}), (), NORM)
    assert result.fit_ghz == 12 and result.fit_line == "Пригоден до 12 ГГц"
    assert result.limit_ghz == 12 and result.range_line == "Применять до 12 ГГц"


def test_clean_adapter_is_good_to_eighteen():
    result = application(summary({0.01: 1.01, 6: 1.02, 12: 1.03, 18: 1.04}), (), NORM)
    assert result.limit_ghz == 18
    assert result.at_limit.vswr_round == 1.05


def test_failing_at_the_first_point_does_not_conform():
    result = application(summary({0.01: 1.8, 1: 1.9, 18: 1.9}), (), NORM)
    assert result.limit_ghz is None and result.fit_line == ""
    assert result.range_line == "Не соответствует норме"
    assert result.table_lines() == ("", "", "")
    assert result.values_line == ""


def test_one_bad_side_decides():
    result = application(summary({0.01: 1.02, 6: 1.04, 12: 1.05}, right={0.01: 1.02, 6: 1.5, 12: 1.05}), (), NORM)
    assert result.fit_ghz is None and result.limit_ghz is None  # 6 GHz fails and nothing between 0,01 and 6 was measured
    dense = application(summary({0.01: 1.02, 3: 1.04, 6: 1.04, 12: 1.05}, right=[1.02, 1.04, 1.5, 1.05]),
                        (), NORM)
    assert dense.fit_ghz == 3 and dense.range_line == "Применять до 3 ГГц"


def test_loss_outside_its_norm_ends_the_range_too():
    norm = Norm("Лист", "", 0.0, (Segment(18, 1.25),), loss=LossNorm("Лист", 0.0, (LossSegment(18, 0.3),)))
    measured = summary({0.01: 1.02, 6: 1.04, 12: 1.05, 18: 1.06}, {0.01: 0.01, 6: 0.03, 12: 0.5, 18: 0.5})
    assert application(measured, (), norm).fit_ghz == 6


def test_without_a_norm_bands_are_quoted_as_measured_and_no_range_is_claimed():
    result = application(summary({0.01: 1.02, 6: 1.15, 12: 1.22, 13.3: 1.49, 18: 1.41}))
    assert not result.judged and result.fit_line == "" and result.limit_ghz is None
    assert result.range_line == "Норма не установлена"
    assert [step.stop_ghz for step in result.bands] == [1, 3, 6, 12, 18]


@pytest.mark.parametrize("series,last_step", [(("BNC",), 4), (("N",), 18), (("N", "SMA"), 18), (("TNC", "SMA"), 11),
                                               (("SMA",), 18), ((), 18)])
def test_connector_series_cap_the_steps(series, last_step):
    result = application(summary({0.01: 1.01, 6: 1.02, 12: 1.03, 18: 1.04}), series, NORM)
    assert result.steps[-1].stop_ghz == last_step
    assert result.limit_ghz == last_step
