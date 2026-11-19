"""Two-port numerics of the VNA bench (port of tools/vna-measure/dsp.ps1): Touchstone 1.x, T-matrix fixture removal,
band cut and the item summary. 50 Ω only; trace order S11, S21, S12, S22.

Fixtures are removed only on the very same frequency grid as the measurement: no interpolation, ever.
"""

from __future__ import annotations

import cmath
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .domain import ProductionValidationError

TRACES = ("S11", "S21", "S12", "S22")
_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
_SINGULAR = 1e-15


class VnaDataError(ProductionValidationError):
    """Measurement data that cannot be used: the message is shown to the operator as is."""


def _finite(value: float) -> bool:
    return not (math.isnan(value) or math.isinf(value))


@dataclass(frozen=True)
class TwoPort:
    """A complete two-port sweep: strictly ascending positive frequencies (Hz) and four complex traces."""

    frequencies: tuple[float, ...]
    s11: tuple[complex, ...]
    s21: tuple[complex, ...]
    s12: tuple[complex, ...]
    s22: tuple[complex, ...]

    def __post_init__(self):
        object.__setattr__(self, "frequencies", tuple(float(f) for f in self.frequencies))
        for name in TRACES:
            object.__setattr__(self, name.lower(), tuple(complex(v) for v in getattr(self, name.lower())))
        count = len(self.frequencies)
        if count == 0:
            raise VnaDataError("Нет частотных точек.")
        for name in TRACES:
            if len(self.trace(name)) != count:
                raise VnaDataError(f"Нет полных данных {name}.")
        previous = -1.0
        for index, frequency in enumerate(self.frequencies):
            if not _finite(frequency) or frequency <= 0 or frequency <= previous:
                raise VnaDataError("Частоты должны быть конечными, положительными и строго возрастающими.")
            previous = frequency
            for name in TRACES:
                value = self.trace(name)[index]
                if not _finite(value.real) or not _finite(value.imag):
                    raise VnaDataError(f"Неконечное значение {name} на частоте {frequency:g}.")

    def trace(self, name: str) -> tuple[complex, ...]:
        if name not in TRACES:
            raise VnaDataError(f"Неизвестная трасса {name}.")
        return getattr(self, name.lower())

    @classmethod
    def from_traces(cls, frequencies: Sequence[float], traces: dict[str, Sequence[complex]]) -> TwoPort:
        return cls(tuple(frequencies), *(tuple(traces.get(name, ())) for name in TRACES))


# ---- Touchstone 1.x ----------------------------------------------------------------------------------------------

VALUES_PER_LINE = {1: 3, 2: 9}   # Touchstone 1.x: frequency + N² complex pairs on one line for 1 and 2 ports


def read_touchstone(content: bytes | str, file_name: str = "", ports: int = 2) -> TwoPort:
    """A two-port Touchstone 1.x file: `# HZ|KHZ|MHZ|GHZ S RI|MA|DB R 50`, `!` comments, one frequency per line with
    9 numbers. The port count comes from the extension (`.s1p` = 1, `.s2p` = 2) or the argument; a one-port file, or
    lines whose count does not match it (an .s1p read as a two-port), are refused — never regrouped into records."""
    extension = re.search(r"\.s(\d+)p$", file_name.lower())
    if extension:
        ports = int(extension[1])
    if ports != 2:
        raise VnaDataError(f"Файл на {ports} порт(а): нужен двухпортовый замер .s2p (S11, S21, S12, S22).")
    expected = VALUES_PER_LINE[ports]
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    scale, fmt, tokens = 0.0, "", []
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            if fmt or tokens:
                raise VnaDataError("Повторная строка формата Touchstone.")
            options = line[1:].strip().upper().split()
            if len(options) != 5 or options[1] != "S" or options[2] not in ("RI", "MA", "DB") or options[3] != "R":
                raise VnaDataError("Нужен Touchstone 1.x: # HZ S RI R 50 (также MA/DB и KHZ/MHZ/GHZ).")
            reference = _float(options[4])
            if options[0] not in _UNITS or reference != 50:
                raise VnaDataError("Поддерживаются только S-параметры 50 Ом с известной единицей частоты.")
            scale, fmt = _UNITS[options[0]], options[2]
            continue
        if not fmt or line.startswith("["):
            raise VnaDataError("Неподдерживаемый формат Touchstone; требуется файл двухпортового замера 1.x.")
        values = line.split()
        if len(values) != expected:
            raise VnaDataError(f"Строка «{line[:60]}»: {len(values)} чисел, для двухпортового замера нужно {expected} "
                               "(частота и S11, S21, S12, S22).")
        tokens.extend(values)
    if not fmt or not tokens or len(tokens) % 9:
        raise VnaDataError("Неполная строка двухпортовых данных Touchstone.")
    frequencies, traces = [], {name: [] for name in TRACES}
    for offset in range(0, len(tokens), 9):
        row = []
        for token in tokens[offset:offset + 9]:
            number = _float(re.sub("[dD]", "E", token))
            if number is None:
                raise VnaDataError(f"Некорректное число в Touchstone: {token}.")
            row.append(number)
        frequencies.append(row[0] * scale)
        for index, name in enumerate(TRACES):
            first, second = row[1 + 2 * index], row[2 + 2 * index]
            if fmt == "RI":
                value = complex(first, second)
            else:
                if fmt == "MA" and first < 0:
                    raise VnaDataError("Отрицательный модуль S-параметра.")
                magnitude = 10 ** (first / 20) if fmt == "DB" else first
                value = cmath.rect(magnitude, second * math.pi / 180)
            traces[name].append(value)
    return TwoPort.from_traces(frequencies, traces)


_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _float(token: str) -> float | None:
    """An invariant-culture number; NaN, ∞, digit separators and decimal commas are refused."""
    if not _NUMBER.match(token):
        return None
    value = float(token)
    return value if _finite(value) else None


def write_touchstone(data: TwoPort, comments: Iterable[str] = ()) -> str:
    """`# HZ S RI R 50` with full double precision; every comment line is prefixed with `!`, so no text becomes data."""
    lines = [f"! {line}" for comment in comments for line in re.split(r"\r?\n", comment)]
    lines.append("# HZ S RI R 50")
    for index, frequency in enumerate(data.frequencies):
        row = [_g17(frequency)]
        for name in TRACES:
            value = data.trace(name)[index]
            row += [_g17(value.real), _g17(value.imag)]
        lines.append(" ".join(row))
    return "\n".join(lines) + "\n"


def _g17(value: float) -> str:
    return format(value, ".17g")


# ---- T-matrix fixture removal ----------------------------------------------------------------------------------
# A 2×2 matrix is the flat tuple (a, b, c, d) of rows [a, b] and [c, d].

Matrix = tuple[complex, complex, complex, complex]


def to_t(s11: complex, s21: complex, s12: complex, s22: complex) -> Matrix:
    if abs(s21) < _SINGULAR:
        raise VnaDataError("Нельзя преобразовать в T-матрицу: S21 близок к нулю.")
    return (s12 - s11 * s22 / s21, s11 / s21, -s22 / s21, 1 / s21)


def from_t(matrix: Matrix) -> tuple[complex, complex, complex, complex]:
    """(S11, S21, S12, S22) of a T-matrix."""
    a, b, c, d = matrix
    if abs(d) < _SINGULAR:
        raise VnaDataError("Вырожденная T-матрица.")
    return (b / d, 1 / d, (a * d - b * c) / d, -c / d)


def multiply(left: Matrix, right: Matrix) -> Matrix:
    a, b, c, d = left
    e, f, g, h = right
    return (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h)


def invert(matrix: Matrix) -> Matrix:
    a, b, c, d = matrix
    determinant = a * d - b * c
    if abs(determinant) < _SINGULAR:
        raise VnaDataError("Нельзя вычесть оснастку: вырожденная T-матрица.")
    return (d / determinant, -b / determinant, -c / determinant, a / determinant)


def _point(data: TwoPort, index: int) -> tuple[complex, complex, complex, complex]:
    return tuple(data.trace(name)[index] for name in TRACES)


def remove_fixtures(data: TwoPort, left: TwoPort | None = None, right: TwoPort | None = None) -> TwoPort:
    """The DUT between two fixtures: T_dut = T_left⁻¹ · T_measured · T_right⁻¹ at every point of the same grid.

    Orientation: the left fixture has port 1 toward VNA port 1 and port 2 toward the DUT; the right fixture has port 1
    toward the DUT and port 2 toward VNA port 2 — its file must be measured (or reversed) in that orientation."""
    for fixture in (left, right):
        if fixture is None:
            continue
        if len(fixture.frequencies) != len(data.frequencies):
            raise VnaDataError("Сетка частот оснастки не совпадает с замером.")
        if fixture.frequencies != data.frequencies:
            raise VnaDataError("Сетка частот оснастки не совпадает с замером; интерполяция запрещена.")
    if left is None and right is None:
        return data
    traces = {name: [] for name in TRACES}
    for index in range(len(data.frequencies)):
        matrix = to_t(*_point(data, index))
        if left is not None:
            matrix = multiply(invert(to_t(*_point(left, index))), matrix)
        if right is not None:
            matrix = multiply(matrix, invert(to_t(*_point(right, index))))
        for name, value in zip(TRACES, from_t(matrix)):
            traces[name].append(value)
    return TwoPort.from_traces(data.frequencies, traces)


# ---- band and summary --------------------------------------------------------------------------------------------

def select_band(data: TwoPort, fmax_hz: float) -> TwoPort:
    """The measured points up to the chosen upper frequency; nothing is resampled."""
    if not _finite(fmax_hz) or fmax_hz <= 0:
        raise VnaDataError("Верхняя частота должна быть конечной и положительной.")
    indexes = [i for i, frequency in enumerate(data.frequencies) if frequency <= fmax_hz]
    if not indexes:
        raise VnaDataError("В выбранном диапазоне нет частотных точек.")
    return TwoPort.from_traces([data.frequencies[i] for i in indexes],
                               {name: [data.trace(name)[i] for i in indexes] for name in TRACES})


def vswr(reflection: complex) -> float:
    magnitude = abs(reflection)
    return math.inf if magnitude >= 1 else (1 + magnitude) / (1 - magnitude)


def db(value: complex) -> float:
    magnitude = abs(value)
    return -math.inf if magnitude <= 0 else 20 * math.log10(magnitude)


@dataclass(frozen=True)
class Summary:
    vswr1: float
    vswr2: float
    loss_db: float     # the worse of S21 and S12 (СТО ФРАК-002-2026 п. 13.1)
    fmax_hz: float


def summary(data: TwoPort) -> Summary:
    vswr1 = vswr2 = 1.0
    loss = -math.inf
    for index in range(len(data.frequencies)):
        vswr1 = max(vswr1, vswr(data.s11[index]))
        vswr2 = max(vswr2, vswr(data.s22[index]))
        magnitude = min(abs(data.s21[index]), abs(data.s12[index]))
        loss = max(loss, -20 * math.log10(magnitude) if magnitude > 0 else math.inf)
    return Summary(vswr1, vswr2, loss, data.frequencies[-1])
