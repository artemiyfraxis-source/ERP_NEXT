"""VNA bench math ported from tools/vna-measure: parity with the PowerShell tool and the Pester cases.

Golden numbers: fixtures/vna_parity/vna-measure-golden.json, produced by fixtures/vna_parity/generate.ps1 from the tool
itself (dsp.ps1, checks.ps1, norms.ps1, s2vna.ps1) with the inputs stored next to the outputs; the limit lines are drawn
from rf-norms-snapshot.json, the norms file of that run. Where the tool's U (1,5/2,0·КСВН %) differs from the ERP table,
the parity cases pass the tool's U explicitly and the ERP default is checked against independently computed values.
"""

import cmath
import json
import math
from pathlib import Path

import pytest

from app.modules.production import instrument_norms
from app.modules.production.domain import ProductionValidationError
from app.modules.production.rf_interfaces import RfDetails
from app.modules.production.vna_bench_protocol import ApplyScreenArgs, screen_hash
from app.modules.production.vna_checks import (KitStandard, KitTolerance, confidence,
                                               confidence_from_receipt, control_standard, flex, journal_number,
                                               kit_response, kit_standard, load_vswr, table_tx_u, table_vswr_u)
from app.modules.production.vna_dsp import (TRACES, TwoPort, VnaDataError, from_t, invert, multiply, read_touchstone,
                                            remove_fixtures, select_band, summary, to_t, write_touchstone)
from app.modules.production.vna_screens import (SCREENS, ItemLimits, Line, item_limits, norm_top_pdiv, number_text,
                                                screen)

FIXTURES = Path(__file__).parent / "fixtures" / "vna_parity"
GOLDEN = json.loads((FIXTURES / "vna-measure-golden.json").read_text(encoding="utf-8"))
TOL = 1e-6


def tool_vswr_u(hz, value):
    """The tool's U (checks.ps1 Get-VnaVswrUncertainty): ±1,5·КСВН % to 10 GHz, ±2,0·КСВН % above."""
    return (0.015 if hz <= 10e9 else 0.02) * value


def tool_tx_u(hz):
    return 0.1


def num(value):
    return None if value is None else float(value)


def cplx(pair):
    return complex(float(pair[0]), float(pair[1]))


def twoport(raw) -> TwoPort:
    return TwoPort.from_traces([float(f) for f in raw["Frequencies"]], {name: [cplx(v) for v in raw[name]] for name in TRACES})


def close(actual, expected, tol=TOL):
    if expected is None or actual is None:
        return actual is expected
    if math.isinf(expected) or math.isnan(expected):
        return math.isinf(actual) == math.isinf(expected) and math.isnan(actual) == math.isnan(expected)
    return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=tol)


def assert_same(actual: TwoPort, raw):
    expected = twoport(raw)
    assert len(actual.frequencies) == len(expected.frequencies)
    assert all(math.isclose(a, b, rel_tol=1e-12) for a, b in zip(actual.frequencies, expected.frequencies))
    for name in TRACES:
        assert max(abs(a - b) for a, b in zip(actual.trace(name), expected.trace(name))) < TOL, name


def kit_from(raw) -> KitStandard:
    return KitStandard(raw["Type"], float(raw["Delay"]), float(raw["Loss"]), float(raw["Z0"]),
                       tuple(float(raw[f"C{i}"]) for i in range(4)), tuple(float(raw[f"L{i}"]) for i in range(4)))


def use_norms(monkeypatch, data):
    monkeypatch.setattr(instrument_norms, "norms_data", lambda: data)
    instrument_norms.catalog.cache_clear()


@pytest.fixture(autouse=True)
def fresh_catalog():
    yield
    instrument_norms.catalog.cache_clear()


# ---- parity with the tool's own numbers --------------------------------------------------------------------------

@pytest.mark.parametrize("case", GOLDEN["dsp"]["touchstone"])
def test_touchstone_reading_matches_tool(case):
    lines = [line.split("!", 1)[0].split() for line in case["text"].splitlines()]
    if any(values and not values[0].startswith("#") and len(values) != 9 for values in lines):
        # The tool regrouped a record split over lines; ERP wants 9 numbers per line (main review 19.11), so an .s1p
        # can never be read as a two-port file.
        with pytest.raises(VnaDataError, match="9"):
            read_touchstone(case["text"])
        return
    assert_same(read_touchstone(case["text"]), case["data"])


def test_fixture_removal_band_and_summary_match_tool():
    raw = GOLDEN["dsp"]["deembed"]
    measured, left, right = twoport(raw["measured"]), twoport(raw["left"]), twoport(raw["right"])
    assert_same(remove_fixtures(measured, left, right), raw["both"])
    assert_same(remove_fixtures(measured, left=left), raw["left_only"])
    assert_same(remove_fixtures(measured, right=right), raw["right_only"])
    band = GOLDEN["dsp"]["band"]
    assert_same(select_band(twoport(band["data"]), float(band["fmax_hz"])), band["result"])
    for case in GOLDEN["dsp"]["summary"]:
        result, expected = summary(twoport(case["data"])), case["summary"]
        assert close(result.vswr1, num(expected["Vswr1"])) and close(result.vswr2, num(expected["Vswr2"]))
        assert close(result.loss_db, num(expected["LossDb"])) and result.fmax_hz == num(expected["FmaxHz"])


def test_confidence_flex_and_control_standard_match_tool():
    for case in GOLDEN["checks"]["confidence"]:
        result, expected = confidence(twoport(case["data"]), twoport(case["memory"])), case["result"]
        assert result.passed is expected["Passed"]
        assert close(result.tx_dev_db, num(expected["TxDevDb"])) and close(result.rx_dev_db, num(expected["RxDevDb"]))
    for case in GOLDEN["checks"]["flex"]:
        result, expected = flex(twoport(case["before"]), twoport(case["after"])), case["result"]
        assert result.passed is expected["Passed"]
        assert close(result.s21_dev_db, num(expected["S21DevDb"])) and close(result.vswr_dev, num(expected["VswrDev"]))
    for case in GOLDEN["checks"]["control_standard"]:
        result = control_standard(twoport(case["measured"]), twoport(case["reference"]), tool_vswr_u, tool_tx_u)
        expected = case["result"]
        assert result.passed is expected["Passed"]
        for field, key in (("vswr_dev", "VswrDev"), ("vswr_limit", "VswrLimit"), ("s21_dev_db", "S21DevDb"),
                           ("s21_limit_db", "S21LimitDb")):
            assert close(getattr(result, field), num(expected[key])), field


def test_kit_models_and_comparison_match_tool():
    for case in GOLDEN["checks"]["kit_response"]:
        standard = kit_from(case["standard"])
        for hz, value in zip(case["frequencies"], case["values"]):
            assert abs(kit_response(standard, float(hz)) - cplx(value)) < TOL
    assert len(GOLDEN["checks"]["kit_compare"]) == 28
    for case in GOLDEN["checks"]["kit_compare"]:
        tolerance = KitTolerance(**{k: float(v) for k, v in case["tolerance"].items()})
        result = kit_standard(kit_from(case["standard"]), case["port"], [twoport(s) for s in case["sweeps"]], tolerance,
                              tool_vswr_u, tool_tx_u)
        expected = case["result"]
        assert close(result.ratio, num(expected["Ratio"])) and close(result.deviation, num(expected["Deviation"]))
        assert close(result.limit, num(expected["Limit"]))


def test_journal_rounding_matches_tool():
    for case in GOLDEN["checks"]["rounded"]:
        assert journal_number(num(case["value"])) == num(case["rounded"])


def test_limit_lines_titles_and_scale_match_tool_for_every_norm(monkeypatch):
    use_norms(monkeypatch, json.loads((FIXTURES / "rf-norms-snapshot.json").read_text(encoding="utf-8")))
    cases = GOLDEN["norms"]["limits"]
    assert len(cases) > 100
    for case in cases:
        rf = RfDetails(role="adapter", norm_id=case["norm_id"])
        result = item_limits(case["number"], case["name"], rf, start_hz=num(case["start_hz"]), stop_hz=num(case["band_hz"]))
        # ERP wording: the norms file is the server's own, there is no «file next to the program» to update.
        assert result.title == case["title"].replace(" — обновите файл рядом с программой", ""), case["norm_id"]
        for lines, raw, kind in ((result.swr, case["swr"], "upper"), (result.loss, case["loss"], "lower")):
            assert len(lines) == len(raw), (case["norm_id"], case["band_hz"])
            for line, expected in zip(lines, raw):
                assert line.kind == kind
                assert math.isclose(line.start_hz, num(expected["StartHz"]), rel_tol=1e-12)
                assert math.isclose(line.stop_hz, num(expected["StopHz"]), rel_tol=1e-12)
                assert close(line.start_value, num(expected["From"])) and close(line.stop_value, num(expected["To"]))
        assert close(norm_top_pdiv(result.swr), num(case["swr_scale"]["PDIVision"]))


def test_six_screens_match_tool_presets():
    presets = GOLDEN["norms"]["presets"]
    assert [p["name"] for p in presets] == [spec.name for spec in SCREENS.values()]
    kinds = {None: None, "Swr": "swr", "Loss": "loss", "Assembly": "assembly", "Cal": "cal"}
    for preset, (key, spec) in zip(presets, SCREENS.items()):
        built = screen(key, band_hz=None if spec.full_band else (1e6, 6e9))
        assert built.split == preset["split"] and spec.full_band == (preset["stimulus"] is not None)
        assert len(built.traces) == len(preset["traces"])
        for trace, ours, raw in zip(built.traces, spec.traces, preset["traces"]):
            assert (trace.param, trace.format, trace.hidden, trace.memory) == (raw["param"], raw["format"], raw["hidden"], raw["memory"])
            assert ours.limit == kinds[raw["limit"]]
            if raw["auto"]:
                assert trace.scale == "auto"
                continue
            assert close(trace.scale.pdiv, num(raw["scale"]["PDIVision"]))
            assert trace.scale.rlev == num(raw["scale"].get("RLEVel"))
            assert trace.scale.rpos == raw["scale"].get("RPOSition")
    assert [float(hz) for hz in GOLDEN["norms"]["markers_hz"]] == list(screen("adapter").markers_hz)


# ---- the Pester cases (tools/vna-measure/tests), same inputs ------------------------------------------------------

def dsp_data(frequencies=(1e7, 1e9, 18e9), reflection=0.01, transmission=0.95) -> TwoPort:
    """dsp.Tests.ps1 New-DspTestData."""
    traces = {}
    for name in TRACES:
        if name in ("S11", "S22"):
            traces[name] = [complex(reflection, 0.001 * i) for i in range(len(frequencies))]
        else:
            traces[name] = [cmath.rect(transmission, -0.2 * i) for i in range(len(frequencies))]
    return TwoPort.from_traces(frequencies, traces)


def replace(data: TwoPort, name: str, index: int, value: complex) -> TwoPort:
    traces = {n: list(data.trace(n)) for n in TRACES}
    traces[name][index] = value
    return TwoPort.from_traces(data.frequencies, traces)


def test_touchstone_roundtrip_formats_and_refusals():
    data = dsp_data()
    text = write_touchstone(data, ["СИ.001, АКМ", "line one\n# harmless comment"])
    assert "СИ.001" in text and "! # harmless comment" in text
    back = read_touchstone(text.encode("utf-8"))
    assert back.frequencies[2] == 18e9
    assert all(abs(a - b) < 1e-14 for name in TRACES for a, b in zip(back.trace(name), data.trace(name)))
    ma = read_touchstone("# GHZ S MA R 50\n! fixture\n1 0.1 90 0.5 -90 0.5 -90 0.2 180 ! one record per line")
    assert ma.frequencies[0] == 1e9 and abs(ma.s11[0].imag - 0.1) < 1e-14 and abs(ma.s22[0].real + 0.2) < 1e-14
    dbs = read_touchstone("# MHZ S DB R 50\n1D1 -20 0 -6.020599913279624 0 -6.020599913279624 0 -20 0")
    assert dbs.frequencies[0] == 1e7 and abs(dbs.s21[0].real - 0.5) < 1e-14
    for content in ("# HZ S RI R 75\n10 0 0 1 0 1 0 0 0", "10 0 0 1 0 1 0 0 0", "# HZ S RI R 50\n10 0 0",
                    "# HZ S RI R 50\n10 NaN 0 1 0 1 0 0 0", "# HZ S RI R 50\n10 0 0 1 0 1 0 0 0\n9 0 0 1 0 1 0 0 0",
                    "# HZ S RI R 50\n10 0,5 0 1 0 1 0 0 0", "# HZ S MA R 50\n10 -0.1 0 1 0 1 0 0 0"):
        with pytest.raises(VnaDataError):
            read_touchstone(content)
    one_port = "# HZ S RI R 50\n1e7 0.1 0\n2e7 0.1 0\n3e7 0.1 0\n"   # 9 numbers in all: must not become one record
    with pytest.raises(VnaDataError, match="3 чисел"):
        read_touchstone(one_port)
    with pytest.raises(VnaDataError, match="двухпортовый"):
        read_touchstone(text, file_name="Замер.s1p")
    with pytest.raises(VnaDataError, match="двухпортовый"):
        read_touchstone(text, ports=1)
    assert read_touchstone(text, file_name="Замер.S2P").frequencies == data.frequencies
    with pytest.raises(VnaDataError, match="9"):
        read_touchstone("# GHZ S MA R 50\n1 0.1 90 0.5 -90\n0.5 -90 0.2 180")
    with pytest.raises(VnaDataError):
        replace(data, "S21", 0, complex(math.inf, 0))
    with pytest.raises(VnaDataError):
        TwoPort(data.frequencies, data.s11, data.s21, data.s12, ())


def test_fixture_removal_recovers_dut_and_never_interpolates():
    dut, left, right = dsp_data(reflection=0.035, transmission=0.82), dsp_data(reflection=0.07, transmission=0.93), \
        dsp_data(reflection=0.09, transmission=0.88)
    left = replace(left, "S12", 1, complex(0.87, -0.12))
    right = replace(right, "S22", 2, complex(0.06, 0.03))
    traces = {name: [] for name in TRACES}
    for i in range(3):
        cascade = multiply(multiply(to_t(*(left.trace(n)[i] for n in TRACES)), to_t(*(dut.trace(n)[i] for n in TRACES))),
                           to_t(*(right.trace(n)[i] for n in TRACES)))
        for name, value in zip(TRACES, from_t(cascade)):
            traces[name].append(value)
    actual = remove_fixtures(TwoPort.from_traces(dut.frequencies, traces), left, right)
    assert all(abs(a - b) < 1e-9 for name in TRACES for a, b in zip(actual.trace(name), dut.trace(name)))
    direct = replace(dsp_data(), "S21", 0, 0j)
    assert remove_fixtures(direct).s21[0] == 0 and remove_fixtures(direct).s11[2] == direct.s11[2]
    with pytest.raises(VnaDataError, match="интерполяция"):
        remove_fixtures(dsp_data(), left=dsp_data(frequencies=(1e7, 1.1e9, 18e9)))
    with pytest.raises(VnaDataError):
        remove_fixtures(dsp_data(), right=dsp_data(frequencies=(1e7, 1e9)))
    with pytest.raises(VnaDataError):
        invert((1, 1, 1, 1))
    with pytest.raises(VnaDataError):
        to_t(0, 0, 0, 0)


def test_band_and_summary_pester_cases():
    data = dsp_data()
    cut = select_band(data, 11e9)
    assert len(cut.frequencies) == 2 and cut.frequencies[-1] == 1e9
    assert len(select_band(data, 1e7).s22) == 1
    for bad in (1, math.nan):
        with pytest.raises(VnaDataError):
            select_band(data, bad)
    data = replace(dsp_data(reflection=0.1, transmission=0.5), "S11", 2, complex(0.2, 0))
    result = summary(data)
    assert abs(result.vswr1 - 1.5) < 1e-12 and result.vswr2 > 1.22
    assert abs(result.loss_db - 6.020599913279624) < 1e-12 and result.fmax_hz == 18e9
    worse = replace(dsp_data(reflection=0.1, transmission=0.5), "S12", 1, complex(0.25, 0))
    assert abs(summary(worse).loss_db - 12.041199826559248) < 1e-12
    lost = replace(replace(dsp_data(), "S11", 0, 1 + 0j), "S21", 0, 0j)
    assert math.isinf(summary(lost).vswr1) and math.isinf(summary(lost).loss_db)


def check_data(reflection=0.01, transmission=0.98, points=5) -> TwoPort:
    """checks.Tests.ps1 New-CheckData."""
    frequencies = [10e6 + (18e9 - 10e6) * i / (points - 1) for i in range(points)]
    return TwoPort.from_traces(frequencies, {name: [complex(reflection if name in ("S11", "S22") else transmission, 0)] * points
                                             for name in TRACES})


def factor(db_value):
    return 10 ** (db_value / 20)


def test_confidence_pester_cases():
    memory = check_data(0.5, 0.5)
    assert confidence(check_data(0.5, 0.5 * factor(0.09)), memory).passed
    far = confidence(check_data(0.5, 0.5 * factor(0.11)), memory)
    assert not far.passed and round(far.tx_dev_db, 2) == 0.11
    low = confidence(check_data(0.03, 0.5), check_data(0.01, 0.5))
    assert low.passed and low.rx_dev_db is None
    assert not confidence(check_data(0.5 * factor(0.45), 0.5), memory).passed
    with pytest.raises(ProductionValidationError, match="сетка"):
        confidence(check_data(points=4), check_data())


def test_flex_control_standard_and_kit_pester_cases_with_erp_uncertainty():
    before = check_data()
    assert flex(before, check_data(transmission=0.98 * factor(0.04))).passed
    assert not flex(before, check_data(transmission=0.98 * factor(0.06))).passed
    reference = check_data(0.02)
    good = control_standard(check_data(0.025), reference)
    assert good.passed and good.vswr_dev < good.vswr_limit
    bad = control_standard(check_data(0.04), reference)
    assert not bad.passed
    # ERP table: ±0,022 at VSWR 1,2 to 10 GHz, proportional to VSWR, rounded half up to 0,0001 (0,019082 → 0,0191);
    # the worst point is the smaller U below 10 GHz.
    assert bad.vswr_limit == 0.0191
    assert math.isclose(bad.vswr_dev, 1.04 / 0.96 - 1.02 / 0.98, rel_tol=1e-12)
    assert bad.s21_limit_db == 0.1
    # The tool's own U gives the Pester number.
    assert round(control_standard(check_data(0.04), reference, tool_vswr_u, tool_tx_u).vswr_limit, 4) == round(0.015 * (1.02 / 0.98), 4)

    def std(kind, **values):
        return KitStandard(kind, values.get("delay", 0.0), 0.0, 50.0)
    assert round(kit_response(std("OPEN"), 1e9).real, 6) == 1
    assert round(kit_response(std("SHORT"), 1e9).real, 6) == -1
    assert round(abs(kit_response(std("LOAD"), 1e9)), 6) == 0
    assert round(abs(kit_response(std("THRU", delay=1e-10), 5e9)), 6) == 1
    assert round(kit_response(std("OPEN", delay=5e-11), 5e9).real, 6) == -1   # half-wave round trip: 2·ω·τ = π
    load = std("LOAD")
    good = kit_standard(load, 1, [check_data(0.01), check_data(0.02), check_data(0.03)])
    assert good.ratio < 1 and round(good.deviation, 3) == 0.03
    assert math.isclose(good.limit, math.hypot(0.0183 / 2, 0.05), rel_tol=1e-12)   # U(1,0) = 0,018333 → 0,0183
    assert kit_standard(load, 2, [check_data(0.01), check_data(0.06)]).ratio > 1
    with pytest.raises(ProductionValidationError):
        kit_standard(load, 0, [check_data()])
    with pytest.raises(ProductionValidationError):
        KitTolerance(reflection_db=-0.1)
    assert KitStandard.from_s2vna({"TYPE": "short", "DELay": 3e-11, "LOSS": 2e9, "Z0": 50, "L0": 2.1, "LABel": "S"}).l[0] == 2.1


def test_erp_uncertainty_table_load_and_confidence_receipt():
    assert table_vswr_u()(5e9, 1.2) == pytest.approx(0.022) and table_vswr_u()(12e9, 1.2) == pytest.approx(0.029)
    assert table_tx_u()(17e9) == 0.1
    assert load_vswr(check_data(0.02), 2) == pytest.approx(1.02 / 0.98)
    receipt = {"frequencies_hz": [1e7, 4.5e9, 9e9],
               "data": {name: [[0.5 * factor(0.05), 0.0]] * 3 for name in TRACES},
               "memory": {name: [[0.5, 0.0]] * 3 for name in TRACES}}
    result = confidence_from_receipt(receipt)
    assert result.passed and result.tx_dev_db == pytest.approx(0.05) and result.rx_dev_db == pytest.approx(0.05)
    del receipt["data"]["S12"]
    with pytest.raises(ProductionValidationError, match="S12"):
        confidence_from_receipt(receipt)
    assert journal_number(None) is None and journal_number(-4e-7) == 0 and str(journal_number(-4e-7)) == "0.0"


PESTER_NORMS = {
    "makers": [{"series": "ADP1A", "match": "ADP1A", "title": "Планар ADP1A", "source": "Планар, РЭ ADP1A",
                "groups": [{"title": "3,5 мм", "ports": ["3.5mm", "3.5mm"], "from_ghz": 0, "id": "adp1a-35",
                            "segments": [{"to_ghz": 20, "a": 1.06, "b": 0}, {"to_ghz": 26.5, "a": 1.1, "b": 0}],
                            "loss": {"from_ghz": 0, "segments": [{"to_ghz": 32, "a": 0.2, "k": 0}], "source": "РЭ"}}]}],
    "mil_adapters": [
        {"id": "mil-55339-55", "sheet": "MIL-PRF-55339/55", "title": "SMA–SMA", "ports": ["SMA", "SMA"], "from_ghz": 0.5,
         "segments": [{"to_ghz": 27, "a": 1.05, "b": 0.005}],
         "loss": {"from_ghz": 0.5, "segments": [{"to_ghz": 18, "a": 0, "k": 0.03}, {"to_ghz": 27, "a": 0, "k": 0.05}], "source": "лист"}},
        {"id": "mil-55339-54b", "sheet": "MIL-PRF-55339/54B", "title": "SMA–N", "ports": ["SMA", "N"], "from_ghz": 0,
         "segments": [{"to_ghz": 12.4, "a": 1.06, "b": 0.005}, {"to_ghz": 18, "a": 0.83, "b": 0.023}],
         "loss": {"points": [{"ghz": 9, "max_db": 0.18}], "source": "лист"}},
        {"id": "mil-55339-40b", "sheet": "MIL-PRF-55339/40B, /41B, /42A", "title": "SMA–TNC", "ports": ["SMA", "TNC"],
         "from_ghz": 0.5, "segments": [{"to_ghz": 11, "a": 1.15, "b": 0.015}], "loss": {"points": [{"ghz": 6, "k": 0.06}], "source": "лист"}}],
    "type_norms": [
        {"id": "escc-3402-003-sma", "level": "escc", "reference": "ESCC 3402/003", "title": "SMA–SMA", "ports": ["SMA", "SMA"],
         "from_ghz": 0, "segments": [{"to_ghz": 18, "a": 1.1, "b": 0.008}],
         "loss": {"from_ghz": 0, "segments": [{"to_ghz": 18, "a": 0.02, "k": 0.06}], "source": "лист"}},
        {"id": "iec-61169-8-bnc", "level": "iec", "reference": "IEC 61169-8", "title": "BNC", "ports": ["BNC", "BNC"],
         "from_ghz": 0, "segments": [{"to_ghz": 4, "a": 1.3, "b": 0}]}],
    "vna": [],
}


def limits(norm_id, number="СИ.035", name="Переходник SMA вилка – SMA розетка", stop_hz=18e9):
    return item_limits(number, name, RfDetails(role="adapter", norm_id=norm_id), stop_hz=stop_hz)


def test_norm_lines_pester_cases(monkeypatch):
    use_norms(monkeypatch, json.loads(json.dumps(PESTER_NORMS)))
    mil = limits("mil-55339-55")
    assert len(mil.swr) == 1 and (mil.swr[0].start_hz, mil.swr[0].stop_hz) == (5e8, 18e9)
    assert round(mil.swr[0].start_value, 6) == 1.0525 and round(mil.swr[0].stop_value, 6) == 1.14
    assert mil.title.startswith("СИ.035 · КСВН ≤ 1,05 + 0,005·F (0,5–18 ГГц) — MIL-PRF-55339/55")
    assert len(mil.loss) == 12 and mil.loss[0].kind == "lower" and mil.loss[0].start_hz == 5e8 and mil.loss[11].stop_hz == 18e9
    assert round(mil.loss[0].start_value, 5) == round(-0.03 * math.sqrt(0.5), 5)
    assert round(mil.loss[11].stop_value, 5) == round(-0.03 * math.sqrt(18), 5)
    assert "потери ≤ 0,03·√F дБ (0,5–18 ГГц) — MIL-PRF-55339/55" in mil.title and "1,30" not in mil.title
    point = limits("mil-55339-54b", "СИ.040", "Переходник SMA розетка – N вилка")
    assert len(point.loss) == 1 and point.loss[0].start_hz == 8.95e9 and point.loss[0].start_value == -0.18
    assert "потери ≤ 0,18 дБ на частоте испытания 9 ГГц" in point.title
    assert len(point.swr) == 2 and point.swr[1].start_hz == 12.4e9
    assert round(point.swr[1].start_value, 4) == 1.1152 and round(point.swr[1].stop_value, 4) == 1.244
    assert "норма MIL-PRF-55339/54B не покрывает полосу" in limits("mil-55339-54b", stop_hz=4e9).title
    cut = limits("mil-55339-54b", stop_hz=11e9)
    assert len(cut.swr) == 1 and cut.swr[0].stop_hz == 11e9 and round(cut.swr[0].stop_value, 6) == 1.115
    k_point = limits("mil-55339-40b", "СИ.041", "Переходник SMA розетка – TNC вилка", 11e9)
    assert round(k_point.loss[0].start_value, 4) == round(-0.06 * math.sqrt(6), 4)
    assert "потери ≤ 0,147 дБ на частоте испытания 6 ГГц" in k_point.title
    maker = limits("adp1a-35", "СИ.009", "Переход")
    assert maker.swr[0].start_value == 1.06 and (maker.swr[0].start_hz, maker.swr[0].stop_hz) == (10e6, 18e9)
    assert "КСВН ≤ 1,06 (0,01–18 ГГц) — Планар ADP1A" in maker.title and "26,5" not in maker.title
    assert maker.loss[0].start_value == -0.2 and "потери ≤ 0,2 дБ (0,01–18 ГГц) — Планар ADP1A" in maker.title
    bnc = limits("iec-61169-8-bnc", "СИ.014", "Переходник BNC female–BNC female B-322", 4e9)
    assert bnc.swr[0].start_value == 1.3 and "КСВН ≤ 1,3 (0,01–4 ГГц) — IEC 61169-8" in bnc.title
    assert "потери: нормы нет" in bnc.title
    esa = limits("escc-3402-003-sma", "СИ.034", "Переходник SMA female–SMA female S-322")
    assert round(esa.swr[0].stop_value, 4) == 1.244 and len(esa.loss) == 12
    foreign = item_limits("СИ.050", "Переходник F вилка – BNC розетка 75 Ом", RfDetails(role="adapter"), stop_hz=4e9)
    assert not foreign.swr and "75 Ом — не для 50 Ом тракта" in foreign.title
    for rf in (None, RfDetails(role="adapter")):
        none = item_limits("СИ.035", "Переходник SMA вилка – SMA розетка ADP1A", rf)
        assert not none.swr and not none.loss and "норма не назначена" in none.title
    missing = limits("escc-3402-010-tnc-plug-jack")
    assert not missing.swr and "норма escc-3402-010-tnc-plug-jack отсутствует в rf-norms.json" in missing.title
    without_loss = json.loads(json.dumps(PESTER_NORMS))
    del without_loss["mil_adapters"][0]["loss"]
    use_norms(monkeypatch, without_loss)
    old = limits("mil-55339-55")
    assert not old.loss and "потери: нормы нет" in old.title


def test_screens_pester_cases_and_v2_contract():
    item = ItemLimits(swr=(Line("upper", 5e8, 18e9, 1.0525, 1.14), Line("upper", 1e7, 18e9, 1.3, 1.3)),
                      loss=(Line("lower", 5e8, 18e9, -0.0212, -0.1273),), title="СИ.035 · КСВН ≤ 1,05 + 0,005·F")
    engineer = screen("engineer", item=item)
    assert engineer.split == 15 and len(engineer.traces) == 12
    assert [t.param for t in engineer.traces] == "S11,S21,S12,S22,S11,S21,S21,S22,S11,S21,S12,S22".split(",")
    assert [t.format for t in engineer.traces] == "SWR,MLOG,MLOG,SWR,SMIT,PHAS,GDEL,SMIT,MLOG,MLOG,MLOG,MLOG".split(",")
    assert [(s.kind, s.start_hz, s.stop_hz, s.start_value, s.stop_value) for s in engineer.traces[0].limits] == [
        ("upper", 5e8, 18e9, 1.0525, 1.14), ("upper", 1e7, 18e9, 1.3, 1.3)]
    assert [(s.kind, s.start_value) for s in engineer.traces[2].limits] == [("lower", -0.0212)]
    assert not engineer.traces[4].limits and not engineer.traces[9].limits and engineer.traces[6].scale == "auto"
    assert engineer.markers_hz == [1e9, 3e9, 6e9, 12e9, 18e9] and engineer.title == "СИ.035 · КСВН ≤ 1,05 + 0,005·F"
    # SWR top = max(norm × 1,15; 1,20): the highest line 1,30 → 1,495 → 1,50; adapter loss 0…−0,5 dB; overview 5 dB.
    assert engineer.traces[0].scale.pdiv == 0.05 and engineer.traces[1].scale.pdiv == 0.05 and engineer.traces[9].scale.pdiv == 5
    assert screen("adapter").traces[0].scale.pdiv == 0.02
    load = screen("load_check", item=item)
    assert load.split == 3 and [t.hidden for t in load.traces] == [False, True, True, False]
    assert [(s.start_hz, s.stop_hz, s.start_value) for s in load.traces[0].limits] == [(1e7, 18e9, 1.06)]
    assert load.traces[0].scale.pdiv == 0.01 and load.title == ""
    bend = screen("flex")
    assert [t.format for t in bend.traces] == ["SWR", "SWR", "MLOG", "PHAS"] and all(t.memory for t in bend.traces)
    assert [t.scale.pdiv for t in bend.traces] == [0.01, 0.01, 0.03, 36]
    assembly = screen("assembly", band_hz=(1e6, 6e9), assembly_vswr=1.32)
    assert assembly.title == "КСВН сборки ≤ 1,32 по ЭТК" and assembly.markers_hz == [1e9, 3e9, 6e9]
    assert [(s.start_hz, s.stop_hz, s.start_value) for s in assembly.traces[0].limits] == [(1e6, 6e9, 1.32)]
    assert not assembly.traces[1].limits and screen("one_port", band_hz=(1e6, 6e9)).traces[0].limits == []
    for bad in ({}, {"band_hz": (1e6, 19e9)}, {"band_hz": (6e9, 1e6)}, {"band_hz": (1e6, 6e9), "assembly_vswr": 1.0}):
        with pytest.raises(ProductionValidationError):
            screen("assembly", **bad)
    with pytest.raises(ProductionValidationError):
        screen("nope")
    crowded = ItemLimits(swr=tuple(Line("upper", 1e7 + i, 2e7 + i, 1.1, 1.1) for i in range(21)))
    with pytest.raises(ProductionValidationError, match="20"):
        screen("adapter", item=crowded)
    for key, spec in SCREENS.items():
        built = screen(key, band_hz=None if spec.full_band else (1e6, 6e9), assembly_vswr=None if spec.full_band else 1.3)
        ApplyScreenArgs.model_validate({"screen": built.model_dump(mode="json"), "screen_hash": screen_hash(built)})
    assert number_text(1.0525) == "1,053" and number_text(18, 2) == "18" and number_text(-0.0) == "0"
