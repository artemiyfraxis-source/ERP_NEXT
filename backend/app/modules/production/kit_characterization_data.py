"""Pure, 50-ohm standard characterization; no catalog, database or ideal fallback.

``source_hash`` identifies the canonical RI bytes, not the original acquisition.
Raw acquisition and fixture hashes belong in the workflow's separate provenance.
THRU retains all four traces in physical port order, including nonreciprocal data.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass

from .vna_dsp import TRACES, TwoPort, VnaDataError, remove_fixtures, write_touchstone
from .vna_files import MAX_POINTS, VnaFileError, parse_vna_traces

KINDS = ("OPEN", "SHORT", "LOAD", "THRU")
FIXTURE_ORIENTATIONS = ("port1_toward_vna", "port2_toward_vna")
# Dimensionless S parameters: refuse absolute loss of tracking, cancellation in
# the denominator, and inverse sensitivity |dGamma_std/dGamma_m| above 1e12.
# This numerical floor is not an uncertainty or acceptance-grade accuracy claim.
INVERSION_FLOOR = 1e-12


def frequency_grid(values: Sequence[float], *, minimum_points: int = 1) -> tuple[float, ...]:
    try:
        grid = tuple(float(value) for value in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VnaDataError("Некорректная сетка частот.") from exc
    if not minimum_points <= len(grid) <= MAX_POINTS:
        raise VnaDataError(f"Нужно от {minimum_points} до {MAX_POINTS} частотных точек.")
    if any(not math.isfinite(f) or f <= 0 for f in grid) or any(b <= a for a, b in zip(grid, grid[1:])):
        raise VnaDataError("Частоты должны быть конечными, положительными и строго возрастающими.")
    return grid


@dataclass(frozen=True)
class CharacterizationCurve:
    """An immutable SOL S11 or complete THRU sweep on an ascending Hz grid.

    Empty source_hash computes it; a supplied hash must match the canonical bytes.
    Use ``source_hash=''`` when constructing a new, numerically changed revision.
    Kinds/genders are physical metadata, not inferred from electrical behavior.
    """

    kind: str
    frequencies: tuple[float, ...]
    s11: tuple[complex, ...]
    s21: tuple[complex, ...] = ()
    s12: tuple[complex, ...] = ()
    s22: tuple[complex, ...] = ()
    z0: float = 50.0
    source_hash: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise VnaDataError("Неизвестный тип калибровочной меры.")
        if self.z0 != 50:
            raise VnaDataError("Характеристика меры должна быть приведена к 50 Ом.")
        object.__setattr__(self, "frequencies", frequency_grid(self.frequencies, minimum_points=2))
        for trace in TRACES:
            try:
                values = tuple(complex(v) for v in getattr(self, trace.lower()))
            except (TypeError, ValueError, OverflowError) as exc:
                raise VnaDataError(f"Некорректная трасса {trace}.") from exc
            needed = trace == "S11" or self.kind == "THRU"
            if len(values) != (len(self.frequencies) if needed else 0):
                raise VnaDataError(f"Число значений {trace} не соответствует типу меры и сетке.")
            if any(not math.isfinite(v.real) or not math.isfinite(v.imag) for v in values):
                raise VnaDataError(f"Неконечное значение {trace}.")
            object.__setattr__(self, trace.lower(), values)
        digest = hashlib.sha256(write_characterization(self)).hexdigest()
        if self.source_hash and self.source_hash != digest:
            raise VnaDataError("SHA-256 характеристики не совпадает с каноническими данными.")
        object.__setattr__(self, "source_hash", digest)

    @classmethod
    def from_two_port(cls, data: TwoPort) -> CharacterizationCurve:
        return cls("THRU", data.frequencies, data.s11, data.s21, data.s12, data.s22)

    def as_two_port(self) -> TwoPort:
        if self.kind != "THRU":
            raise VnaDataError("Для двухпортовой обработки нужна полная характеристика THRU.")
        return TwoPort(self.frequencies, self.s11, self.s21, self.s12, self.s22)


def write_characterization(curve: CharacterizationCurve) -> bytes:
    """Deterministic UTF-8 Touchstone 1.x RI, 17 significant digits, LF, no metadata."""
    if curve.kind == "THRU":
        return write_touchstone(curve.as_two_port()).encode("utf-8")
    rows = ["# HZ S RI R 50"]
    rows.extend(f"{f:.17g} {value.real:.17g} {value.imag:.17g}" for f, value in zip(curve.frequencies, curve.s11))
    return ("\n".join(rows) + "\n").encode("utf-8")


def parse_characterization(content: bytes, *, kind: str, source_hash: str = "") -> CharacterizationCurve:
    """Read S1P (SOL) or S2P (THRU) using the existing complex Touchstone parser.

    A strict wrapper rejects missing/ambiguous headers, reordered input and wrong
    impedance before the general parser can sort or normalize them. One complete
    frequency record per line is required, as in the bench's two-port parser.
    RI/MA/DB input is normalized to RI; source_hash, if given, pins that output.
    """
    if kind not in KINDS:
        raise VnaDataError("Неизвестный тип калибровочной меры.")
    try:
        text = content.decode("utf-8-sig")
        records = [line.split("!", 1)[0].strip() for line in text.splitlines()]
        records = [line for line in records if line]
        options = records[0].upper().split() if records else []
        units = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
        if (len(options) != 6 or options[0] != "#" or options[1] not in units
                or options[2] != "S" or options[3] not in ("RI", "MA", "DB")
                or options[4] != "R" or float(options[5]) != 50):
            raise VnaDataError("Нужна явная строка Touchstone # HZ S RI R 50 (также MA/DB и KHZ/MHZ/GHZ).")
        rows = [line.split() for line in records[1:]]
        if any(len(row) != (9 if kind == "THRU" else 3) for row in rows):
            raise VnaDataError("Нужна одна полная частотная точка в каждой строке Touchstone.")
        grid = frequency_grid(tuple(float(row[0]) * units[options[1]] for row in rows), minimum_points=2)
        traces = parse_vna_traces(content, "standard.s2p" if kind == "THRU" else "standard.s1p")
        expected = TRACES if kind == "THRU" else ("S11",)
        by_name = {trace.name: trace for trace in traces}
        if set(by_name) != set(expected):
            raise VnaDataError("Трассы файла не соответствуют портам характеристики меры.")
        values = {}
        for name in expected:
            points = by_name[name].complex_points
            if tuple(point[0] for point in points) != grid:
                raise VnaDataError("Не сохранена исходная комплексная сетка характеристики.")
            values[name.lower()] = tuple(complex(re, im) for _, re, im in points)
        return CharacterizationCurve(kind, grid, **values, source_hash=source_hash)
    except VnaDataError:
        raise
    except (UnicodeError, ValueError, ArithmeticError, VnaFileError) as exc:
        raise VnaDataError("Некорректные данные Touchstone характеристики меры.") from exc


def deembed_one_port(measured: CharacterizationCurve, fixture: TwoPort, *,
                     fixture_orientation: str) -> CharacterizationCurve:
    """Remove an F-F fixture from SOL, with fixture port 1 facing the VNA.

    Gamma_std = delta / (S12*S21 + S22*delta), delta = Gamma_m - S11.
    Refuse |tracking| <= 1e-12, |denominator| <= 1e-12 times
    max(1, |tracking| + |S22*delta|), or inverse sensitivity above 1e12.
    For a fixture recorded in the other direction explicitly reverse_two_port
    first; physical orientation cannot be deduced from an S-parameter file.
    """
    if measured.kind == "THRU":
        raise VnaDataError("Однопортовое исключение оснастки применимо только к SOL.")
    if fixture_orientation != "port1_toward_vna":
        raise VnaDataError("Порт 1 характеристики оснастки должен быть обращён к ВАЦ.")
    if fixture.frequencies != measured.frequencies:
        raise VnaDataError("Сетка оснастки должна точно совпадать с замером; интерполяция запрещена.")
    result = []
    for i, gamma in enumerate(measured.s11):
        delta = gamma - fixture.s11[i]
        tracking = fixture.s12[i] * fixture.s21[i]
        feedback = fixture.s22[i] * delta
        denominator = tracking + feedback
        terms = (delta, tracking, feedback, denominator)
        if (any(not math.isfinite(v.real) or not math.isfinite(v.imag) for v in terms)
                or abs(tracking) <= INVERSION_FLOOR
                or abs(denominator) <= INVERSION_FLOOR * max(1.0, abs(tracking) + abs(feedback))
                or abs(tracking / denominator / denominator) > 1 / INVERSION_FLOOR):
            raise VnaDataError(f"Неустойчивое исключение оснастки на частоте {measured.frequencies[i]:g} Гц.")
        result.append(delta / denominator)
    return CharacterizationCurve(measured.kind, measured.frequencies, tuple(result))


def reverse_two_port(data: TwoPort) -> TwoPort:
    """Swap physical ports: S11 <-> S22, S21 <-> S12; no conjugation."""
    return TwoPort(data.frequencies, data.s22, data.s12, data.s21, data.s11)


def deembed_thru(measured: CharacterizationCurve, *, left: TwoPort | None = None,
                 right: TwoPort | None = None, left_orientation: str | None = None,
                 right_orientation: str | None = None) -> CharacterizationCurve:
    """Remove either/both fixtures with the existing exact-grid T-matrix DSP.

    Each orientation names the fixture port facing its own VNA port. The DSP
    expects left port 1 toward VNA and right port 2 toward VNA, so orient first.
    The returned standard preserves the acquisition's physical port order.
    """
    for fixture, orientation in ((left, left_orientation), (right, right_orientation)):
        if (fixture is not None and orientation not in FIXTURE_ORIENTATIONS
                or fixture is None and orientation is not None):
            raise VnaDataError("Для каждой используемой оснастки нужна явная ориентация.")
    if left is not None and left_orientation == "port2_toward_vna":
        left = reverse_two_port(left)
    if right is not None and right_orientation == "port1_toward_vna":
        right = reverse_two_port(right)
    return CharacterizationCurve.from_two_port(remove_fixtures(measured.as_two_port(), left, right))
