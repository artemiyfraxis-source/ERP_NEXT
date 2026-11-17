"""Standard steps, round limits and the application limit that the label and the planner share."""

from dataclasses import replace

import pytest

from app.modules.production.instrument_characterization import (
    APPLICATION_VSWR_LIMIT, LOSS_LADDER, VSWR_LADDER, application, characterization, round_up_ladder,
)
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


def test_resonance_above_twelve_ghz_limits_application_to_twelve():
    measured = summary({0.01: 1.02, 1: 1.017, 3: 1.06, 6: 1.15, 12: 1.22, 13.3: 1.49, 18: 1.41})
    result = application(measured)
    assert [step.stop_ghz for step in result.steps] == [1, 3, 6, 12, 18]
    assert result.limit_ghz == 12
    assert result.limit_line == "Применять до 12 ГГц"
    assert result.at_limit.vswr_round == 1.25
    assert "КСВН ≤ 1,25" in result.values_line


def test_clean_adapter_is_good_to_eighteen():
    result = application(summary({0.01: 1.01, 6: 1.02, 12: 1.03, 18: 1.04}))
    assert result.limit_ghz == 18
    assert result.at_limit.vswr_round == 1.05


def test_failing_at_the_first_step_is_not_usable():
    result = application(summary({0.01: 1.8, 1: 1.9, 18: 1.9}))
    assert result.limit_ghz is None
    assert result.limit_line == "Не годен для 50 Ом тракта"
    assert result.values_line == ""


def test_one_bad_side_decides():
    result = application(summary({0.01: 1.02, 6: 1.04, 12: 1.05}, right={0.01: 1.02, 6: 1.5, 12: 1.05}))
    assert result.limit_ghz == 3 or result.limit_ghz == 1


@pytest.mark.parametrize("series,last_step", [(("BNC",), 3), (("N",), 6), (("TNC", "SMA"), 6), (("SMA",), 18), ((), 18)])
def test_connector_series_cap_the_steps(series, last_step):
    result = application(summary({0.01: 1.01, 6: 1.02, 12: 1.03, 18: 1.04}), series)
    assert result.steps[-1].stop_ghz == last_step
    assert result.limit_ghz == last_step


def test_threshold_is_the_documented_constant():
    assert APPLICATION_VSWR_LIMIT == 1.30
