"""SMA-MF06 «Характеристика мер» on the Planar bench and its consumption by Arinst; pure, no database.

Two ACM-calibrated passes per session (each one the full СТО 11.2/11.4 calibration with confidence test and LOAD
checks): «до 3 ГГц» 2…3000 MHz and «3–6 ГГц» 3000…6000 MHz, both on an exact 1 MHz grid (≤ 4001 points — the ACM
confidence limit). A piece's stored curve is the merged 1 MHz curve cut to its confirmed band. Its even 2 MHz nodes
are the interpolation source for Arinst; the odd 1 MHz points are the measured midpoints that validate linear
interpolation (`arinst_standard_loader.validate_midpoints`), rechecked from the stored bytes on every load.

Male SOL is measured through an F–F fixture characterized in the same pass (same calibration, exact grid) and stored
de-embedded at the fixture's outer female plane; THRU M–M through one F–F fixture on each cable end. Female pieces
mate the male cable ends directly. The workflow (`workflows/vna_bench_api.py`) owns sessions, captures and revisions.
"""

from __future__ import annotations

import math
from dataclasses import asdict

from .arinst_calibration import CalibrationStandard
from .arinst_standard_loader import load_standard, validate_midpoints
from .kit_characterization_data import (CharacterizationCurve, deembed_one_port, deembed_thru, parse_characterization,
                                        reverse_two_port, write_characterization)
from .vna_checks import LOAD_LIMIT, same_grid
from .vna_dsp import TwoPort, VnaDataError
from .vna_sessions import Profile

STEP_HZ = 1e6
JUNCTION_HZ = 3000e6
BANDS = {"low": (2e6, 3000e6), "high": (3000e6, 6000e6)}
BAND_LABELS = {"low": "до 3 ГГц", "high": "3–6 ГГц"}
IF_BANDWIDTH_HZ = 1000
POWER_DBM = 0
# Decided by agent — owner/metrology check: the allocated absolute complex uncertainty of a characterized standard.
# The loader's midpoint residual limit is min(0.001, 10 % of it) = 0.001.
ALLOCATED_UNCERTAINTY = 0.01
PROCESSING_VERSION = "kit-characterization-bench-v1"
TORQUE = "0,56 Н·м"
PREVIEW_POINTS = 121
KIND_NAMES = {"OPEN": "ХХ", "SHORT": "КЗ", "LOAD": "нагрузка", "THRU": "проход"}
GENDER_NAMES = {"male": "вилка", "female": "розетка"}


def band_profile(band: str) -> Profile:
    start, stop = BANDS[band]
    return Profile(start_hz=start, stop_hz=stop, points=round((stop - start) / STEP_HZ) + 1,
                   if_bandwidth_hz=IF_BANDWIDTH_HZ, power_dbm=POWER_DBM, traces=["S11", "S21", "S12", "S22"],
                   vswr_limit=LOAD_LIMIT)


def band_grid(band: str) -> tuple[float, ...]:
    start, stop = BANDS[band]
    return tuple(start + i * STEP_HZ for i in range(round((stop - start) / STEP_HZ) + 1))


def band_of(profile: dict) -> str | None:
    """The pass a session profile belongs to (None: not a characterization grid)."""
    return next((band for band in BANDS if band_profile(band).model_dump(mode="json") == profile), None)


def bands_for(f_min_hz: float, f_max_hz: float) -> tuple[str, ...]:
    """Passes a piece needs: female OPEN/SHORT (≤ 3 GHz) only the first one."""
    return tuple(band for band, (start, stop) in BANDS.items() if f_max_hz > start and f_min_hz < stop)


def fixture_count(kind: str, gender: str) -> int:
    """F–F fixtures between the male cable ends and the piece: none for a female piece, one for male SOL, two for M–M."""
    if gender == "female":
        return 0
    return 2 if kind == "THRU" else 1


def piece_name(kind: str, gender: str) -> str:
    if kind == "THRU":
        return f"Проход {'М–М' if gender == 'male' else 'Р–Р'}"
    return f"{KIND_NAMES[kind].capitalize() if kind != 'LOAD' else 'Нагрузка'} ({GENDER_NAMES[gender]})"


def connection_hint(kind: str, gender: str, label: str, fixtures: list[str]) -> str:
    """Where the operator connects the piece; the reference plane follows from it."""
    tighten = f"SMA затягивайте ключом {TORQUE}"
    if kind == "THRU" and gender == "female":
        return f"{label} — между концами кабелей портов 1 и 2 (сторона 1 — к порту 1). {tighten}"
    if kind == "THRU":
        left, right = (fixtures + ["?", "?"])[:2]
        return (f"Оснастка {left} — на кабель порта 1, {right} — на кабель порта 2, {label} между ними "
                f"(сторона 1 — к порту 1). {tighten}")
    if gender == "female":
        return f"{label} — прямо на конец кабеля порта 1 (вилка SMA); порт 2 свободен. {tighten}"
    return (f"Оснастка {(fixtures or ['?'])[0]} — на конец кабеля порта 1, {label} — на её внешнюю розетку; "
            f"порт 2 свободен. {tighten}")


def plane(kind: str, gender: str, fixtures: list[str]) -> str:
    if gender == "female":
        return "Плоскости соединения вилок SMA тестовых кабелей (АКМ-калибровка)"
    if kind == "THRU":
        return f"Внешние розетки оснастки {fixtures[0]} (порт 1) и {fixtures[1]} (порт 2), оснастка исключена"
    return f"Внешняя розетка оснастки {fixtures[0]} на кабеле порта 1, оснастка исключена"


def snap(data: TwoPort, band: str, what: str) -> TwoPort:
    """The sweep on the planned grid: equal within max(1 Hz, 1e-9·f), then the exact planned frequencies are used."""
    grid = band_grid(band)
    probe = TwoPort(grid, *([0j] * len(grid) for _ in range(4)))
    same_grid(data, probe, what)
    return TwoPort(grid, data.s11, data.s21, data.s12, data.s22)


def oriented(fixture: TwoPort, *, reversed_: bool) -> TwoPort:
    return reverse_two_port(fixture) if reversed_ else fixture


def piece_curve(kind: str, gender: str, raw: TwoPort, fixtures: list[tuple[TwoPort, bool]]) -> CharacterizationCurve:
    """The piece itself on the pass grid. Fixture tuples: (its own capture in this pass, reversed).

    Not reversed: the left fixture's side 1 and the right fixture's side 2 face the VNA cables — the way each was
    characterized between the cable ends. Ill-conditioned removal is refused point by point (T2)."""
    if len(fixtures) != fixture_count(kind, gender):
        raise VnaDataError("Число оснасток не соответствует мере")
    if kind == "THRU":
        measured = CharacterizationCurve.from_two_port(raw)
        if not fixtures:
            return measured
        (left, left_reversed), (right, right_reversed) = fixtures
        return deembed_thru(measured, left=left, right=right,
                            left_orientation="port2_toward_vna" if left_reversed else "port1_toward_vna",
                            right_orientation="port1_toward_vna" if right_reversed else "port2_toward_vna")
    measured = CharacterizationCurve(kind, raw.frequencies, raw.s11)
    if not fixtures:
        return measured
    fixture, reversed_ = fixtures[0]
    return deembed_one_port(measured, oriented(fixture, reversed_=reversed_), fixture_orientation="port1_toward_vna")


def merge(parts: dict[str, CharacterizationCurve], f_min_hz: float, f_max_hz: float) -> CharacterizationCurve:
    """One 1 MHz curve: «до 3 ГГц» up to and including 3000 MHz, «3–6 ГГц» above it; cut to the piece band so that it
    ends on a 2 MHz node (an odd point count), never extended."""
    kinds = {curve.kind for curve in parts.values()}
    if len(kinds) != 1 or set(parts) != set(bands_for(f_min_hz, f_max_hz)):
        raise VnaDataError("Нет замеров меры во всех нужных диапазонах")
    kind = kinds.pop()
    names = ("s11", "s21", "s12", "s22") if kind == "THRU" else ("s11",)
    points = []
    for band in ("low", "high"):
        curve = parts.get(band)
        if curve is None:
            continue
        for index, frequency in enumerate(curve.frequencies):
            if band == "high" and frequency <= JUNCTION_HZ and "low" in parts:
                continue
            if f_min_hz <= frequency <= f_max_hz:
                points.append((frequency, tuple(getattr(curve, name)[index] for name in names)))
    if len(points) % 2 == 0:
        points.pop()
    if len(points) < 3:
        raise VnaDataError("Полоса меры не перекрывает сетку характеристики")
    traces = {name: tuple(p[1][i] for p in points) for i, name in enumerate(names)}
    return CharacterizationCurve(kind, tuple(p[0] for p in points), **traces)


def decimate(curve: CharacterizationCurve) -> CharacterizationCurve:
    """The 2 MHz interpolation source: every other point of the merged curve."""
    names = ("s11", "s21", "s12", "s22") if curve.kind == "THRU" else ("s11",)
    return CharacterizationCurve(curve.kind, curve.frequencies[::2], **{n: getattr(curve, n)[::2] for n in names})


def evidence(curve: CharacterizationCurve) -> tuple[dict, str]:
    """Validation of the merged curve: the 2 MHz source interpolates every measured 1 MHz midpoint within the limit."""
    checks, reason, details = dict(grid=True, band=True, finite=True), "", {}
    try:
        source = decimate(curve)
        midpoints = validate_midpoints(source, curve, uncertainty=ALLOCATED_UNCERTAINTY)
        checks["midpoints"] = True
        details = dict(source_sha256=midpoints.source_hash, finer_sha256=midpoints.finer_source_hash,
                       uncertainty=midpoints.uncertainty, limit=midpoints.limit, max_residual=midpoints.max_residual,
                       midpoint_count=midpoints.midpoint_count, source_spacing_hz=2 * STEP_HZ)
    except VnaDataError as error:
        checks["midpoints"], reason = False, str(error)
    return dict(valid=all(checks.values()), checks=checks, midpoints=details, reason=reason,
                uncertainty=ALLOCATED_UNCERTAINTY, processing_version=PROCESSING_VERSION), reason


def content(curve: CharacterizationCurve) -> bytes:
    return write_characterization(curve)


def preview(curve: CharacterizationCurve) -> list[list[float]]:
    """≤ 121 points for the screen: [MHz, |Γ| or |S21| dB, phase °]."""
    values = curve.s21 if curve.kind == "THRU" else curve.s11
    step = max(1, math.ceil(len(values) / PREVIEW_POINTS))
    indexes = list(range(0, len(values), step))
    if indexes[-1] != len(values) - 1:
        indexes.append(len(values) - 1)
    rows = []
    for i in indexes:
        value = values[i]
        magnitude = abs(value)
        level = 20 * math.log10(magnitude) if curve.kind == "THRU" and magnitude > 0 else magnitude
        rows.append([round(curve.frequencies[i] / 1e6, 3), round(level, 5),
                     round(math.degrees(math.atan2(value.imag, value.real)), 2)])
    return rows


def summary(curve: CharacterizationCurve) -> str:
    values = curve.s21 if curve.kind == "THRU" else curve.s11
    magnitudes = [abs(v) for v in values]
    if curve.kind == "THRU":
        loss = [-20 * math.log10(m) if m > 0 else math.inf for m in magnitudes]
        return f"потери {min(loss):.3f}…{max(loss):.3f} дБ".replace(".", ",")
    return f"|Γ| {min(magnitudes):.4f}…{max(magnitudes):.4f}".replace(".", ",")


# ---- Arinst: server-owned standards from the active generation --------------------------------------------------------

def band_text(f_min_hz: float, f_max_hz: float) -> str:
    return f"{f_min_hz / 1e6:g}–{f_max_hz / 1e6:g} МГц".replace(".", ",")


KIT_BAND_NOTE = "Меры набора охарактеризованы до 6 ГГц (ХХ/КЗ «розетка» — до 3 ГГц)"


def arinst_standard(piece: dict, revision: dict, target: tuple[float, ...], *, kit_id: str, kit_version: str,
                    source: str, orientation: str = "forward") -> CalibrationStandard:
    """A measured standard resampled onto the exact Arinst grid; refused outside the stored curve, never ideal.

    piece: id, piece_label, kind, gender, gender2, f_min_hz, f_max_hz; revision: revision, sha256, content.
    """
    curve = parse_characterization(revision["content"], kind=piece["kind"], source_hash=revision["sha256"])
    low, high = curve.frequencies[0], curve.frequencies[-1]
    if target[0] < low or target[-1] > high:
        raise VnaDataError(f"Сетка Arinst {band_text(target[0], target[-1])} выходит за характеристику меры "
                           f"{piece['piece_label']} ({band_text(low, high)}). {KIT_BAND_NOTE}")
    source_curve = decimate(curve)
    genders = (piece["gender"], piece["gender2"]) if piece["kind"] == "THRU" else (piece["gender"],)
    loaded = load_standard(source_curve, target, standard_id=str(piece["id"]), genders=genders,
                           revision=revision["revision"], sha256=source_curve.source_hash,
                           characterization_source=f"{source}; {piece['piece_label']} r{revision['revision']} "
                                                   f"sha256={revision['sha256']}",
                           orientation=orientation, finer_acquisition=curve, uncertainty=ALLOCATED_UNCERTAINTY)
    return CalibrationStandard(str(piece["id"]), kit_id, kit_version, piece["kind"], loaded.genders,
                               (piece["f_min_hz"], piece["f_max_hz"]),
                               coefficient_version=f"{revision['revision']}:{revision['sha256']}",
                               characterization_source=loaded.characterization_source,
                               frequencies=loaded.frequencies, response=loaded.response)


def standard_json(standard: CalibrationStandard) -> dict:
    """The snapshot form `vna_bench.arinst_solve` reads back (complex as [re, im])."""
    data = asdict(standard)
    data["response"] = [[v.real, v.imag] for v in standard.response]
    data["frequencies"] = list(standard.frequencies)
    data["genders"], data["band_hz"] = list(standard.genders), list(standard.band_hz)
    return data
