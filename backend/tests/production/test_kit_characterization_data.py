import hashlib
from dataclasses import replace

import pytest

from app.modules.production.kit_characterization_data import (
    CharacterizationCurve, deembed_one_port, deembed_thru, parse_characterization,
    reverse_two_port, write_characterization,
)
from app.modules.production.vna_dsp import TRACES, TwoPort, VnaDataError, from_t, multiply, to_t


GRID = (10e6, 12e6, 14e6)


def network(s11=0.03 + 0.01j, s21=0.8 - 0.1j, s12=0.7 - 0.2j, s22=-0.04 + 0.02j):
    return TwoPort(GRID, *((value,) * len(GRID) for value in (s11, s21, s12, s22)))


@pytest.mark.parametrize("kind", ["OPEN", "SHORT", "LOAD", "THRU"])
def test_touchstone_round_trip_preserves_complex_values_and_hash(kind):
    curve = (CharacterizationCurve.from_two_port(network()) if kind == "THRU" else
             CharacterizationCurve(kind, GRID, (1 + 0.1j, -0.4 + 0.7j, 0.012345678901234567 - 0.1j)))
    content = write_characterization(curve)
    assert content.startswith(b"# HZ S RI R 50\n")
    assert hashlib.sha256(content).hexdigest() == curve.source_hash
    assert parse_characterization(content, kind=kind) == curve
    assert write_characterization(parse_characterization(content, kind=kind)) == content


@pytest.mark.parametrize("text", [
    "# HZ S RI R 75\n100 1 0\n200 1 0\n",
    "# HZ S RI R 50\n200 1 0\n100 1 0\n",
    "# HZ S RI R 50\n100 1 0\n100 1 0\n",
    "# HZ S RI R 50\n100 nan 0\n200 1 0\n",
    "# HZ S RI R 50\n100 1e309 0\n200 1 0\n",
    "# HZ S RI R 50\n0 1 0\n200 1 0\n",
    "100 1 0\n200 1 0\n",
    "# HZ S RI R 50\n100 1 0 0 0 0 0 1 0\n200 1 0 0 0 0 0 1 0\n",
    "! S2VNA fixture 2026-11-23T10:00:00 S22\n# HZ S RI R 50\n100 1 0\n200 1 0\n",
    "# HZ S DB R 50\n100 10000 0\n200 1 0\n",
])
def test_touchstone_refuses_invalid_data(text):
    with pytest.raises(VnaDataError):
        parse_characterization(text.encode(), kind="OPEN")


def test_ma_input_is_canonicalized_without_losing_phase():
    curve = parse_characterization(b"# MHZ S MA R 50\n10 1 90\n12 1 -90\n", kind="OPEN")
    assert curve.frequencies == (10e6, 12e6)
    assert curve.s11 == pytest.approx((1j, -1j))


@pytest.mark.parametrize("changes", [
    {"frequencies": (12e6, 10e6, 14e6)}, {"frequencies": (10e6, float("nan"), 14e6)},
    {"s11": (1, complex(0, float("inf")), 1)}, {"z0": 75}, {"kind": "IDEAL"},
    {"source_hash": "0" * 64}, {"s21": (1, 1, 1)}, {"s11": (1,)},
])
def test_curve_validates_direct_construction(changes):
    with pytest.raises(VnaDataError):
        CharacterizationCurve(**({"kind": "OPEN", "frequencies": GRID, "s11": (1, 1, 1)} | changes))


@pytest.mark.parametrize("kind,gamma", [("OPEN", 0.9 - 0.3j), ("SHORT", -0.8 + 0.4j), ("LOAD", 0.02j)])
def test_one_port_fixture_round_trip(kind, gamma):
    fixture = network()
    measured = tuple(a + c * b * gamma / (1 - d * gamma)
                     for a, b, c, d in zip(fixture.s11, fixture.s21, fixture.s12, fixture.s22))
    curve = CharacterizationCurve(kind, GRID, measured)
    result = deembed_one_port(curve, fixture, fixture_orientation="port1_toward_vna")
    assert result.s11 == pytest.approx((gamma,) * 3, abs=1e-9)
    assert result.source_hash != curve.source_hash


@pytest.mark.parametrize("orientation", [None, "unknown", "port2_toward_vna"])
def test_one_port_refuses_wrong_or_unknown_orientation(orientation):
    with pytest.raises(VnaDataError):
        deembed_one_port(CharacterizationCurve("OPEN", GRID, (1,) * 3), network(),
                         fixture_orientation=orientation)


def test_one_port_refuses_grid_mismatch():
    with pytest.raises(VnaDataError):
        deembed_one_port(CharacterizationCurve("OPEN", GRID, (1,) * 3),
                         replace(network(), frequencies=(10e6, 12e6, 14e6 + 1)),
                         fixture_orientation="port1_toward_vna")


@pytest.mark.parametrize("fixture,measured", [
    (network(s21=0), 0.03 + 0.01j),
    (network(s11=0, s21=1e-7, s12=1e-7, s22=0), 1e-14),
    (network(s11=0, s21=1, s12=1, s22=1), -1 + 1e-13),
    (network(s11=0, s21=1e-7, s12=1e-7, s22=1), 1),
    (network(s11=0, s21=100, s12=100, s22=1), -1e4 + 1e-9),
    (network(s11=0, s21=1, s12=1, s22=1), -1 + 1e-7),
])
def test_one_port_refuses_ill_conditioning(fixture, measured):
    with pytest.raises(VnaDataError):
        deembed_one_port(CharacterizationCurve("OPEN", GRID, (measured,) * 3), fixture,
                         fixture_orientation="port1_toward_vna")


@pytest.mark.parametrize("left_orientation", ["port1_toward_vna", "port2_toward_vna"])
@pytest.mark.parametrize("right_orientation", ["port1_toward_vna", "port2_toward_vna"])
def test_two_port_removes_both_fixtures_in_explicit_orientation(left_orientation, right_orientation):
    left, right, dut = network(), network(s11=0.1j, s22=-0.2j), network(s21=0.91j, s12=0.81j)
    l = left if left_orientation == "port1_toward_vna" else reverse_two_port(left)
    r = reverse_two_port(right) if right_orientation == "port1_toward_vna" else right
    rows = [from_t(multiply(multiply(to_t(*(l.trace(t)[i] for t in TRACES)),
                                    to_t(*(dut.trace(t)[i] for t in TRACES))),
                           to_t(*(r.trace(t)[i] for t in TRACES)))) for i in range(len(GRID))]
    measured = TwoPort(GRID, *(tuple(row[k] for row in rows) for k in range(4)))
    result = deembed_thru(CharacterizationCurve.from_two_port(measured), left=left, right=right,
                         left_orientation=left_orientation, right_orientation=right_orientation)
    for trace in TRACES:
        assert result.as_two_port().trace(trace) == pytest.approx(dut.trace(trace), abs=1e-9)


def test_two_port_refuses_missing_orientation_and_grid_mismatch():
    curve = CharacterizationCurve.from_two_port(network())
    with pytest.raises(VnaDataError):
        deembed_thru(curve, left=network())
    with pytest.raises(VnaDataError):
        deembed_thru(curve, right=replace(network(), frequencies=(10e6, 12e6, 15e6)),
                     right_orientation="port1_toward_vna")
    with pytest.raises(VnaDataError):
        deembed_thru(curve, left=network(s21=0), left_orientation="port1_toward_vna")


@pytest.mark.parametrize("trace_index", range(4))
def test_two_port_rejects_nonfinite_in_each_trace(trace_index):
    content = write_characterization(CharacterizationCurve.from_two_port(network())).decode()
    lines = content.splitlines()
    row = lines[1].split()
    row[1 + trace_index * 2] = "nan"
    lines[1] = " ".join(row)
    with pytest.raises(VnaDataError):
        parse_characterization("\n".join(lines).encode(), kind="THRU")


def test_two_port_rejects_wrong_impedance_and_unsorted_rows():
    content = write_characterization(CharacterizationCurve.from_two_port(network()))
    for bad in (content.replace(b"R 50", b"R 75"),
                b"\n".join([content.splitlines()[0], *reversed(content.splitlines()[1:])])):
        with pytest.raises(VnaDataError):
            parse_characterization(bad, kind="THRU")


def test_parse_checks_expected_canonical_hash():
    curve = CharacterizationCurve("LOAD", GRID, (0j,) * 3)
    content = write_characterization(curve)
    assert parse_characterization(content, kind="LOAD", source_hash=curve.source_hash) == curve
    with pytest.raises(VnaDataError):
        parse_characterization(content, kind="LOAD", source_hash="0" * 64)
