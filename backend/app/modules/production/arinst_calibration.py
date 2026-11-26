"""Exact-grid host SOL and SOL + THRU response normalization for partial VNA data.

SOL convention: m = D + T*gamma/(1 - S*gamma). THRU only normalizes S21;
it does not identify load match, isolation or a full 12-term/two-port error box.
No hardware topology or raw-output semantics are assumed here. Callers must
supply verified raw provenance and characterized standards. This numerical
result alone NEVER authorizes a quality PASS. Persistence, independent checks,
commissioning policy and forward/reverse assembly belong to the workflow.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace

from .vna_checks import KitStandard, kit_response
from .vna_dsp import VnaDataError

ALGORITHM_VERSION = "arinst-sol-thru-response-v1"
_EPS = 1e-12
_MAX_CONDITION = 1e10
_MAX_RESIDUAL = 1e-10


def _magnitude(value: complex) -> float:
    # Unlike abs(complex), hypot returns inf instead of raising on overflow.
    return math.hypot(value.real, value.imag)


def _text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VnaDataError(f"Не указан {label}.")


def _grid(values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(float(v) for v in values)
    if not result or any(not math.isfinite(v) or v <= 0 for v in result):
        raise VnaDataError("Нужна непустая сетка конечных положительных частот.")
    if any(b <= a for a, b in zip(result, result[1:])):
        raise VnaDataError("Частоты должны строго возрастать.")
    return result


def _values(values: Sequence[complex], count: int) -> tuple[complex, ...]:
    result = tuple(complex(v) for v in values)
    if len(result) != count:
        raise VnaDataError("Число комплексных значений не совпадает с сеткой частот.")
    if any(not math.isfinite(_magnitude(v)) for v in result):
        raise VnaDataError("Неконечное комплексное значение.")
    return result


def _band(values: Sequence[float]) -> tuple[float, float]:
    band = tuple(float(v) for v in values)
    if len(band) != 2 or not all(math.isfinite(v) for v in band) or not 0 <= band[0] < band[1]:
        raise VnaDataError("Некорректная полоса частот.")
    return band


def _in_band(frequencies: tuple[float, ...], band: tuple[float, float]) -> None:
    if frequencies[0] < band[0] or frequencies[-1] > band[1]:
        raise VnaDataError("Сетка выходит за подтверждённую полосу прибора, оснастки или меры.")


def _genders(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(values)
    if not 1 <= len(result) <= 2 or any(v not in ("male", "female") for v in result):
        raise VnaDataError("Нужно указать тип разъёма каждой меры: вилка или розетка.")
    return result


def _hash(value: object) -> str:
    def encode(item):
        if isinstance(item, complex):
            return [item.real, item.imag]
        raise TypeError(type(item).__name__)
    return hashlib.sha256(json.dumps(value, default=encode, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CalibrationContext:
    """Exact reuse key. connection_id changes after reconnect/restart.

    usable_band_hz is the already validated instrument/fixture intersection;
    standard_genders describe the standard connectors mating at the DUT planes,
    in instrument port order (not the genders of the instrument connectors).
    kit_version must change for ANY selected kit definition/physical item change.
    """

    instrument_uid: str
    firmware: str
    profile_fingerprint: str
    if_bandwidth_hz: float
    kit_id: str
    kit_version: str
    fixture_fingerprint: str
    reference_plane: str
    orientation: str
    connection_id: str
    standard_genders: tuple[str, ...]
    usable_band_hz: tuple[float, float]

    def __post_init__(self):
        for name, label in (("instrument_uid", "идентификатор прибора"), ("firmware", "номер прошивки"),
                            ("profile_fingerprint", "отпечаток профиля"), ("kit_id", "идентификатор набора"),
                            ("kit_version", "номер версии набора"), ("fixture_fingerprint", "отпечаток оснастки"),
                            ("reference_plane", "идентификатор плоскости отсчёта"),
                            ("connection_id", "идентификатор подключения")):
            _text(getattr(self, name), label)
        if self.orientation not in ("forward", "reverse"):
            raise VnaDataError("Неизвестная ориентация подключения.")
        if not math.isfinite(self.if_bandwidth_hz) or self.if_bandwidth_hz <= 0:
            raise VnaDataError("Полоса ПЧ должна быть конечной и положительной.")
        object.__setattr__(self, "standard_genders", _genders(self.standard_genders))
        object.__setattr__(self, "usable_band_hz", _band(self.usable_band_hz))


@dataclass(frozen=True)
class Acquisition:
    """Instrument-port S11 and optional S21; never fabricated S12/S22.

    raw_semantics_evidence identifies the commissioning evidence establishing
    that these data are uncorrected. A COMP format flag is not such evidence.
    evidence_id identifies an immutable raw acquisition artifact in the caller.
    """

    context: CalibrationContext
    frequencies: tuple[float, ...]
    s11: tuple[complex, ...]
    evidence_id: str
    s21: tuple[complex, ...] | None = None
    correction_origin: str = "unknown"
    raw_semantics_evidence: str = ""
    calibration_id: str | None = None
    grid_confirmed: bool = False

    def __post_init__(self):
        object.__setattr__(self, "frequencies", _grid(self.frequencies))
        object.__setattr__(self, "s11", _values(self.s11, len(self.frequencies)))
        if self.s21 is not None:
            object.__setattr__(self, "s21", _values(self.s21, len(self.frequencies)))
            if len(self.context.standard_genders) != 2:
                raise VnaDataError("Для передачи нужны две плоскости подключения.")
        _text(self.evidence_id, "идентификатор исходных данных")
        _in_band(self.frequencies, self.context.usable_band_hz)
        if self.correction_origin not in ("unknown", "raw", "instrument", "host"):
            raise VnaDataError("Неизвестное происхождение коррекции.")
        if self.correction_origin == "host" and not self.calibration_id:
            raise VnaDataError("Нет ссылки на применённую калибровку.")
        if self.correction_origin != "host" and self.calibration_id is not None:
            raise VnaDataError("Данные уже содержат ссылку на коррекцию.")
        if not isinstance(self.grid_confirmed, bool):
            raise VnaDataError("Подтверждение сетки частот должно быть явным.")

    @property
    def content_hash(self) -> str:
        """Canonical numeric payload hash; original wire-byte hashes live in evidence_id's artifact."""
        return _hash(asdict(self))

    @property
    def quality_eligible(self) -> bool:
        """Numerics alone cannot certify hardware, uncertainty or verification."""
        return False


@dataclass(frozen=True)
class CalibrationStandard:
    """Characterized gamma (SOL) or S21 (THRU) samples, on an exact grid.

    An empty response is an uncharacterized catalog record, never an ideal
    standard. Physical standard_id, kit_id and coefficient provenance are
    provided by the workflow; catalog entries are templates only.
    """

    standard_id: str
    kit_id: str
    kit_version: str
    kind: str
    genders: tuple[str, ...]
    band_hz: tuple[float, float]
    coefficient_version: str = ""
    characterization_source: str = ""
    frequencies: tuple[float, ...] = ()
    response: tuple[complex, ...] = ()

    def __post_init__(self):
        for name, label in (("standard_id", "идентификатор меры"), ("kit_id", "идентификатор набора"),
                            ("kit_version", "номер версии набора")):
            _text(getattr(self, name), label)
        if self.kind not in ("OPEN", "SHORT", "LOAD", "THRU"):
            raise VnaDataError("Неизвестный тип калибровочной меры.")
        object.__setattr__(self, "genders", _genders(self.genders))
        if len(self.genders) != (2 if self.kind == "THRU" else 1):
            raise VnaDataError("Число разъёмов не соответствует типу меры.")
        object.__setattr__(self, "band_hz", _band(self.band_hz))
        if self.frequencies or self.response:
            object.__setattr__(self, "frequencies", _grid(self.frequencies))
            object.__setattr__(self, "response", _values(self.response, len(self.frequencies)))
            _in_band(self.frequencies, self.band_hz)
            _text(self.coefficient_version, "версия коэффициентов меры")
            _text(self.characterization_source, "источник характеристики меры")
        else:
            object.__setattr__(self, "frequencies", ())
            object.__setattr__(self, "response", ())

    def with_model(self, model: KitStandard, frequencies: Sequence[float], *,
                   coefficient_version: str, characterization_source: str) -> CalibrationStandard:
        """Sample an explicitly supplied S2VNA-unit model; never infer coefficients."""
        if model.type != self.kind:
            raise VnaDataError("Модель не соответствует типу меры.")
        values = (model.delay, model.loss, model.z0, *model.c, *model.l)
        if (len(model.c) != 4 or len(model.l) != 4 or not all(math.isfinite(v) for v in values)
                or model.delay < 0 or model.loss < 0 or model.z0 <= 0):
            raise VnaDataError("Некорректные коэффициенты модели меры.")
        grid = _grid(frequencies)
        _in_band(grid, self.band_hz)
        try:
            response = tuple(kit_response(model, f) for f in grid)
        except (ArithmeticError, ValueError) as exc:
            raise VnaDataError("Невозможно рассчитать характеристику меры.") from exc
        return replace(self, frequencies=grid, response=response, coefficient_version=coefficient_version,
                       characterization_source=characterization_source)


# Catalog facts only, not physical kit identities or calibration coefficients.
# https://kroks.ru/shop/attenyuatory/kalibrovochnye-nabory/calibration-kit-sma-mf06/
# Manufacturer listing checked 2026-11-23: female OPEN/SHORT are limited to 3 GHz.
SMA_MF06_STANDARDS = tuple(
    CalibrationStandard(f"SMA-MF06:{kind}:{gender}", "SMA-MF06", "catalog-2026-11-23", kind,
                        (gender,) * (2 if kind == "THRU" else 1),
                        (0.0, 3e9 if gender == "female" and kind in ("OPEN", "SHORT") else 6e9))
    for kind in ("OPEN", "SHORT", "LOAD", "THRU") for gender in ("male", "female")
)


@dataclass(frozen=True)
class StandardMeasurement:
    standard: CalibrationStandard
    measured: Acquisition


@dataclass(frozen=True)
class Calibration:
    """Solver output, including diagnostics and ordered standard/raw content hashes.

    Three standards exactly determine three terms. A small fitting residual is
    a numerical check, NOT an independent accuracy/uncertainty verification.
    """

    context: CalibrationContext
    frequencies: tuple[float, ...]
    directivity: tuple[complex, ...]
    source_match: tuple[complex, ...]
    reflection_tracking: tuple[complex, ...]
    condition_numbers: tuple[float, ...]
    residuals: tuple[float, ...]
    standard_hashes: tuple[str, ...]
    measurement_hashes: tuple[str, ...]
    standard_ids: tuple[str, ...]
    measurement_ids: tuple[str, ...]
    raw_semantics_evidence: str
    transmission_tracking: tuple[complex, ...] | None = None
    algorithm_version: str = ALGORITHM_VERSION

    def __post_init__(self):
        object.__setattr__(self, "frequencies", _grid(self.frequencies))
        count = len(self.frequencies)
        for name in ("directivity", "source_match", "reflection_tracking"):
            object.__setattr__(self, name, _values(getattr(self, name), count))
        if self.transmission_tracking is not None:
            object.__setattr__(self, "transmission_tracking", _values(self.transmission_tracking, count))
        _in_band(self.frequencies, self.context.usable_band_hz)
        _text(self.raw_semantics_evidence, "подтверждение отсутствия коррекции")
        if self.algorithm_version != ALGORITHM_VERSION:
            raise VnaDataError("Неподдерживаемая версия алгоритма калибровки.")
        for name, maximum in (("condition_numbers", _MAX_CONDITION), ("residuals", _MAX_RESIDUAL)):
            values = tuple(float(v) for v in getattr(self, name))
            if len(values) != count or any(not math.isfinite(v) or not 0 <= v <= maximum for v in values):
                raise VnaDataError("Некорректная диагностика решения калибровки.")
            object.__setattr__(self, name, values)
        for name in ("standard_hashes", "measurement_hashes"):
            hashes = tuple(getattr(self, name))
            if len(hashes) != (4 if self.transmission_tracking is not None else 3):
                raise VnaDataError("Неполная история калибровочных мер.")
            for value in hashes:
                if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise VnaDataError("Некорректный хеш калибровочных данных.")
            object.__setattr__(self, name, hashes)
        for name in ("standard_ids", "measurement_ids"):
            identifiers = tuple(getattr(self, name))
            if len(identifiers) != len(self.standard_hashes) or len(set(identifiers)) != len(identifiers):
                raise VnaDataError("Неполные или повторяющиеся ссылки на калибровочные данные.")
            for value in identifiers:
                _text(value, "идентификатор калибровочных данных")
            object.__setattr__(self, name, identifiers)

    @property
    def method(self) -> str:
        return "sol+thru_response" if self.transmission_tracking is not None else "sol"

    @property
    def calibration_id(self) -> str:
        return _hash(asdict(self))

    @property
    def quality_eligible(self) -> bool:
        return False


def _raw(acquisition: Acquisition) -> None:
    if acquisition.correction_origin != "raw" or acquisition.calibration_id is not None:
        raise VnaDataError("Коррекция разрешена только для подтверждённых исходных данных без коррекции.")
    _text(acquisition.raw_semantics_evidence, "подтверждение отсутствия коррекции")
    if not acquisition.grid_confirmed:
        raise VnaDataError("Фактическая сетка частот не подтверждена.")


def _same_setup(reference: Acquisition | Calibration, acquisition: Acquisition) -> None:
    _raw(acquisition)
    if reference.context != acquisition.context:
        raise VnaDataError("Не совпадают прибор, профиль, набор, оснастка, ориентация или подключение калибровки.")
    if reference.frequencies != acquisition.frequencies:
        raise VnaDataError("Сетка частот не совпадает с калибровкой; интерполяция и экстраполяция запрещены.")
    if reference.raw_semantics_evidence != acquisition.raw_semantics_evidence:
        raise VnaDataError("Изменилось подтверждение отсутствия коррекции прибора.")


def _standard(measurement: StandardMeasurement, reference: Acquisition) -> None:
    _same_setup(reference, measurement.measured)
    standard, context = measurement.standard, reference.context
    if not standard.response:
        raise VnaDataError("Нет характеристики меры; идеальная мера не подставляется автоматически.")
    if standard.kit_id != context.kit_id or standard.kit_version != context.kit_version:
        raise VnaDataError("Мера принадлежит другому набору или версии его определения.")
    genders = context.standard_genders if standard.kind == "THRU" else context.standard_genders[:1]
    if standard.genders != genders:
        raise VnaDataError("Разъёмы меры не соответствуют плоскостям подключения.")
    if standard.frequencies != reference.frequencies:
        raise VnaDataError("Характеристика меры имеет другую сетку; интерполяция и экстраполяция запрещены.")
    _in_band(reference.frequencies, standard.band_hz)


def _divide(numerator: complex, denominator: complex, scale: float = 1.0) -> complex:
    if not all(math.isfinite(v) for v in (_magnitude(numerator), _magnitude(denominator), scale)):
        raise VnaDataError("Численное переполнение при коррекции.")
    if _magnitude(denominator) <= _EPS * max(1.0, scale):
        raise VnaDataError("Вырожденная коррекция: знаменатель близок к нулю.")
    result = numerator / denominator
    if not math.isfinite(_magnitude(result)):
        raise VnaDataError("Неконечный результат коррекции.")
    return result


def _solve(measured: Sequence[complex], actual: Sequence[complex]):
    """Pivoted 3x3 Gauss-Jordan solve, infinity-norm condition and backward residual.

    No optional numeric dependency: solve [1, gamma, m*gamma] [a,b,c] = m.
    The identity block yields the inverse for an explicit condition check.
    Thresholds are numerical safeguards, not a metrological uncertainty budget.
    """
    matrix = [[1 + 0j, g, m * g] for m, g in zip(measured, actual)]
    norm = max(sum(_magnitude(v) for v in row) for row in matrix)
    if not math.isfinite(norm):
        raise VnaDataError("Переполнение системы калибровки.")
    rows = [row[:] + [complex(i == j) for j in range(3)] + [measured[i]]
            for i, row in enumerate(matrix)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda i: _magnitude(rows[i][col]))
        if _magnitude(rows[pivot][col]) <= _EPS * norm:
            raise VnaDataError("Вырожденная или плохо обусловленная система калибровки.")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        divisor = rows[col][col]
        rows[col] = [v / divisor for v in rows[col]]
        for i in range(3):
            if i != col:
                factor = rows[i][col]
                rows[i] = [v - factor * w for v, w in zip(rows[i], rows[col])]
    condition = norm * max(sum(_magnitude(v) for v in row[3:6]) for row in rows)
    if not math.isfinite(condition) or condition > _MAX_CONDITION:
        raise VnaDataError("Плохо обусловленная система калибровки.")
    a, b, c = (row[6] for row in rows)
    residual = max(_magnitude(sum(v * x for v, x in zip(row, (a, b, c))) - m)
                   for row, m in zip(matrix, measured)) / max(1.0, max(map(_magnitude, measured)))
    if not math.isfinite(residual) or residual > _MAX_RESIDUAL:
        raise VnaDataError("Слишком большая невязка калибровки.")
    tracking = b + a * c
    _divide(1, tracking)
    # Check the original rational model too: a small linear residual can hide a pole.
    for m, g in zip(measured, actual):
        predicted = a + _divide(tracking * g, 1 - c * g, _magnitude(c * g))
        if _magnitude(predicted - m) > _MAX_RESIDUAL * max(1.0, _magnitude(m)):
            raise VnaDataError("Не подтверждена модель калибровочных мер.")
    return a, c, tracking, condition, residual


def solve_sol(standards: Sequence[StandardMeasurement], *,
              thru: StandardMeasurement | None = None) -> Calibration:
    """Solve OPEN/SHORT/LOAD, optionally add explicitly limited THRU normalization.

    THRU transfer model is m21 = tracking21*S21. Mismatch/isolation remain
    uncorrected; neither DUT reversal nor a 12-term solve is inferred.
    """
    standards = tuple(standards)
    if len(standards) != 3 or {m.standard.kind for m in standards} != {"OPEN", "SHORT", "LOAD"}:
        raise VnaDataError("Нужны ровно три разные меры: холостой ход, короткое замыкание и нагрузка.")
    standards = tuple(sorted(standards, key=lambda m: ("OPEN", "SHORT", "LOAD").index(m.standard.kind)))
    reference = standards[0].measured
    all_standards = standards + ((thru,) if thru is not None else ())
    if len({m.standard.standard_id for m in all_standards}) != len(all_standards):
        raise VnaDataError("Идентификаторы физических мер должны различаться.")
    if len({m.measured.evidence_id for m in all_standards}) != len(all_standards):
        raise VnaDataError("Для каждой меры нужны отдельные исходные данные.")
    for measurement in all_standards:
        _standard(measurement, reference)
    terms = [_solve([m.measured.s11[i] for m in standards], [m.standard.response[i] for m in standards])
             for i in range(len(reference.frequencies))]
    transmission = None
    if thru is not None:
        if thru.standard.kind != "THRU" or thru.measured.s21 is None:
            raise VnaDataError("Для нормировки передачи нужны мера прохода и измеренная трасса S21.")
        transmission = tuple(_divide(m, actual) for m, actual in zip(thru.measured.s21, thru.standard.response))
        for value in transmission:
            _divide(1, value)
    return Calibration(reference.context, reference.frequencies,
                       *(tuple(row[i] for row in terms) for i in range(5)),
                       tuple(_hash(asdict(m.standard)) for m in all_standards),
                       tuple(m.measured.content_hash for m in all_standards),
                       tuple(m.standard.standard_id for m in all_standards),
                       tuple(m.measured.evidence_id for m in all_standards),
                       reference.raw_semantics_evidence, transmission)


def apply_calibration(calibration: Calibration, measured: Acquisition) -> Acquisition:
    """Correct once on the exact setup/grid; preserve the measured partial channels."""
    _same_setup(calibration, measured)
    s11 = []
    for m, d, s, t in zip(measured.s11, calibration.directivity,
                           calibration.source_match, calibration.reflection_tracking):
        delta = m - d
        _divide(1, t)
        s11.append(_divide(delta, t + s * delta, _magnitude(t) + _magnitude(s * delta)))
    s21 = None
    if measured.s21 is not None:
        if calibration.transmission_tracking is None:
            raise VnaDataError("SOL не корректирует передачу; нужна отдельная нормировка по мере прохода.")
        s21 = tuple(_divide(m, t) for m, t in zip(measured.s21, calibration.transmission_tracking))
    return replace(measured, s11=tuple(s11), s21=s21, correction_origin="host",
                   calibration_id=calibration.calibration_id)
