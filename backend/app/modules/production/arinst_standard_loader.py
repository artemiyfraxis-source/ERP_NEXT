"""Load measured standards onto an Arinst grid before the exact-grid solver.

No extrapolation or ideal model fallback. Interpolation is linear in real and
imaginary parts, and requires finer acquisition evidence for every source interval.
This commissioning policy is numerical evidence, not a physical release gate.

A curve stitched from two acquisitions (calibration passes) declares its join: the
one source interval whose ends come from different passes. Interpolation never
spans it — its midpoints are not validated and targets strictly inside it take only
an exact measured finer sample (the owning pass), otherwise they are refused. The
step between the passes at the join is judged by the caller as a separate check.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from .kit_characterization_data import CharacterizationCurve, frequency_grid
from .vna_dsp import TRACES, VnaDataError

MAX_SPACING_HZ = 2e6
FEMALE_OPEN_SHORT_MAX_HZ = 3e9


@dataclass(frozen=True)
class MidpointValidation:
    source_hash: str
    finer_source_hash: str
    uncertainty: float
    limit: float
    max_residual: float
    midpoint_count: int
    joins: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class LoadedStandard:
    """Integrator maps this to CalibrationStandard; kit/version/band stay external.

    Genders and response follow the selected physical orientation. SHA-256 and
    coefficient_version pin the full canonical characterization, before resampling.
    """

    standard_id: str
    kind: str
    genders: tuple[str, ...]
    revision: int
    sha256: str
    coefficient_version: str
    characterization_source: str
    frequencies: tuple[float, ...]
    response: tuple[complex, ...]


def _source_spacing(curve: CharacterizationCurve) -> None:
    if any(b - a > MAX_SPACING_HZ for a, b in zip(curve.frequencies, curve.frequencies[1:])):
        raise VnaDataError("Шаг исходной характеристики превышает 2 МГц или содержит пропуск.")


def _joins(grid: tuple[float, ...], joins) -> tuple[tuple[float, float], ...]:
    """Each join must be one interval between adjacent source nodes."""
    try:
        checked = tuple((float(a), float(b)) for a, b in joins)
    except (TypeError, ValueError) as exc:
        raise VnaDataError("Некорректный стык проходов характеристики.") from exc
    intervals = set(zip(grid, grid[1:]))
    if any(join not in intervals for join in checked):
        raise VnaDataError("Стык проходов должен совпадать с интервалом исходной сетки.")
    return checked


def _in_join(frequency: float, joins) -> tuple[float, float] | None:
    return next(((a, b) for a, b in joins if a < frequency < b), None)


def _interpolate(grid: tuple[float, ...], values: tuple[complex, ...],
                 target: tuple[float, ...]) -> tuple[complex, ...]:
    if target[0] < grid[0] or target[-1] > grid[-1]:
        raise VnaDataError("Экстраполяция характеристики запрещена, включая продолжение к нулевой частоте.")
    result = []
    for f in target:
        index = bisect_left(grid, f)
        if grid[index] == f:
            result.append(values[index])
            continue
        fraction = (f - grid[index - 1]) / (grid[index] - grid[index - 1])
        # Convex form avoids overflowing a subtraction of opposite large values.
        value = (1 - fraction) * values[index - 1] + fraction * values[index]
        if not math.isfinite(value.real) or not math.isfinite(value.imag):
            raise VnaDataError("Неконечный результат интерполяции характеристики.")
        result.append(value)
    return tuple(result)


def validate_midpoints(source: CharacterizationCurve, finer: CharacterizationCurve, *,
                       uncertainty: float, joins=()) -> MidpointValidation:
    """Compare every finer sample in the source band, including all exact midpoints.

    Finer acquisition must cover the source endpoints and explicitly measure each
    midpoint; interpolating the validation reference would hide missing evidence.
    All four THRU traces are checked, so validation supports either orientation.
    Uncertainty is an allocated absolute complex-response uncertainty, not dB/%.
    Join intervals (two acquisitions) are excluded: validation stays within each pass.
    """
    if (not isinstance(uncertainty, (int, float)) or isinstance(uncertainty, bool)
            or not math.isfinite(uncertainty) or uncertainty <= 0):
        raise VnaDataError("Нужна конечная положительная неопределённость характеристики.")
    _source_spacing(source)
    if source.kind != finer.kind:
        raise VnaDataError("Тип меры проверочного замера не совпадает с характеристикой.")
    grid = source.frequencies
    joins = _joins(grid, joins)
    if finer.frequencies[0] > grid[0] or finer.frequencies[-1] < grid[-1]:
        raise VnaDataError("Проверочный замер не покрывает исходный диапазон.")
    midpoints = tuple(a + (b - a) / 2 for a, b in zip(grid, grid[1:]) if (a, b) not in joins)
    if not set(midpoints).issubset(finer.frequencies):
        raise VnaDataError("В проверочном замере отсутствуют середины исходных интервалов.")
    indexes = [i for i, f in enumerate(finer.frequencies)
               if grid[0] <= f <= grid[-1] and _in_join(f, joins) is None]
    target = tuple(finer.frequencies[i] for i in indexes)
    residual = 0.0
    for trace in TRACES if source.kind == "THRU" else ("S11",):
        prediction = _interpolate(grid, getattr(source, trace.lower()), target)
        actual = getattr(finer, trace.lower())
        residual = max(residual, max(abs(value - actual[i]) for value, i in zip(prediction, indexes)))
    limit = min(0.001, 0.1 * uncertainty)
    if not math.isfinite(residual) or residual > limit:
        raise VnaDataError(f"Невязка интерполяции {residual:g} превышает допуск {limit:g}.")
    return MidpointValidation(source.source_hash, finer.source_hash, uncertainty, limit, residual, len(midpoints), joins)


def _trace(curve: CharacterizationCurve, orientation: str) -> tuple[complex, ...]:
    return curve.s11 if curve.kind != "THRU" else curve.s21 if orientation == "forward" else curve.s12


def resample_characterization(curve: CharacterizationCurve, target_frequencies: Sequence[float], *,
                              orientation: str = "forward", finer_acquisition: CharacterizationCurve | None = None,
                              uncertainty: float | None = None, joins=()) -> tuple[complex, ...]:
    """Return Gamma or forward S21/reverse S12. Exact samples need no interpolation evidence.

    The whole supplied source must satisfy the 2 MHz spacing policy, even if a
    requested subset avoids a gap. Evidence is rechecked against the actual curve
    on each interpolating load, never accepted as a caller-supplied boolean/token.
    A target strictly inside a join takes the finer acquisition's exact sample there
    (it belongs to one pass) and is refused otherwise: never a line across two passes.
    """
    if orientation not in ("forward", "reverse") or curve.kind != "THRU" and orientation != "forward":
        raise VnaDataError("Неверная ориентация калибровочной меры.")
    target = frequency_grid(target_frequencies)
    _source_spacing(curve)
    joins = _joins(curve.frequencies, joins)
    result = list(_interpolate(curve.frequencies, _trace(curve, orientation), target))
    inside = [(i, f, join) for i, f in enumerate(target) if (join := _in_join(f, joins)) is not None]
    if inside:
        exact = (dict(zip(finer_acquisition.frequencies, _trace(finer_acquisition, orientation)))
                 if finer_acquisition is not None and finer_acquisition.kind == curve.kind else {})
        for i, f, (a, b) in inside:
            if f not in exact:
                raise VnaDataError(
                    f"Точка {f / 1e6:.9g} МГц лежит внутри стыка двух калибровок характеристики "
                    f"({a / 1e6:g}–{b / 1e6:g} МГц): интерполяция между проходами запрещена. Выберите сетку "
                    f"с шагом, кратным 1 МГц, или без точек между {a / 1e6:g} и {b / 1e6:g} МГц.")
            result[i] = exact[f]
    if not set(target).issubset(curve.frequencies):
        if finer_acquisition is None:
            raise VnaDataError("Интерполяция требует проверки по более частому независимому замеру.")
        validate_midpoints(curve, finer_acquisition, uncertainty=uncertainty, joins=joins)
    return tuple(result)


def load_standard(curve: CharacterizationCurve, target_frequencies: Sequence[float], *,
                  standard_id: str, genders: Sequence[str], revision: int, sha256: str,
                  characterization_source: str, orientation: str = "forward",
                  finer_acquisition: CharacterizationCurve | None = None,
                  uncertainty: float | None = None, joins=()) -> LoadedStandard:
    """Validate identity/provenance and return measured responses for the exact target grid.

    Genders describe the canonical file's port order, then reverse together with
    a reversed THRU. The integrator must also enforce the confirmed piece band,
    requested mating genders, kit generation and physical commissioning status.
    Female OPEN/SHORT source curves must themselves stop at 3 GHz, so even an
    in-band target cannot borrow an out-of-band interpolation endpoint.
    """
    if not isinstance(standard_id, str) or not standard_id.strip():
        raise VnaDataError("Нет идентификатора физической меры.")
    if not isinstance(characterization_source, str) or not characterization_source.strip():
        raise VnaDataError("Нет источника характеристики меры.")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise VnaDataError("Ревизия характеристики должна быть положительным целым числом.")
    if sha256 != curve.source_hash:
        raise VnaDataError("SHA-256 выбранной ревизии не совпадает с характеристикой.")
    try:
        connectors = tuple(genders)
    except TypeError as exc:
        raise VnaDataError("Нужен список разъёмов физической меры.") from exc
    if (len(connectors) != (2 if curve.kind == "THRU" else 1)
            or any(g not in ("male", "female") for g in connectors)):
        raise VnaDataError("Неверные пол или число разъёмов меры.")
    target = frequency_grid(target_frequencies)
    if (curve.kind in ("OPEN", "SHORT") and connectors == ("female",)
            and max(curve.frequencies[-1], target[-1]) > FEMALE_OPEN_SHORT_MAX_HZ):
        raise VnaDataError("Меры OPEN/SHORT с гнездом SMA допускаются только до 3 ГГц.")
    response = resample_characterization(curve, target, orientation=orientation,
                                         finer_acquisition=finer_acquisition, uncertainty=uncertainty,
                                         joins=joins)
    if orientation == "reverse":
        connectors = connectors[::-1]
    provenance = f"{characterization_source.strip()}; orientation={orientation}"
    return LoadedStandard(standard_id, curve.kind, connectors, revision, sha256,
                          f"{revision}:{sha256}", provenance, target, response)
