"""The six VNA bench screens as data (port of the presets of tools/vna-measure/s2vna.ps1 and norms.ps1).

Each screen yields the protocol v2 `Screen` the agent puts on S2VNA: graphs, formats, fixed scales, limit lines and
markers. Limit lines come only from the norm ASSIGNED to the item in backend/data/rf-norms.json (owner 18.11) — VSWR
a + b·F, insertion loss −(a + k·√F) and single test-frequency points — or, for assemblies, from the ETK norm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .domain import ProductionValidationError
from .instrument_norms import CatalogNorm, LossNorm, catalog
from .rf_interfaces import RfDetails, parse_interfaces
from .vna_bench_protocol import LIMIT_SEGMENTS_MAX, LimitSegment, Scale, Screen, ScreenTrace
from .vna_checks import LOAD_LIMIT

FULL_BAND_HZ = (10e6, 18e9)                     # the calibration profile of the adapter screen (СТО, прил. А)
MARKERS_HZ = (1e9, 3e9, 6e9, 12e9, 18e9)
LOSS_CHORDS = 12                                # straight pieces per a + k·√F band: ≤ 0,002 dB stricter than the norm
TEST_POINT_HALF_WIDTH_HZ = 5e7                  # a «tested at X GHz» norm is drawn as a short line around X
INSTRUMENT_BAND_HZ = (1e5, 18e9)                # an ETK band outside S2VNA's range is refused

LimitKind = Literal["swr", "loss", "assembly", "cal"]

# Scales: 10 divisions on every graph, nothing left over from the previous screen (owner 18.11).
SWR_NORM = "norm_top"                                           # 1,00…max(норма×1,15; 1,20)
SWR_ASSEMBLY = {"pdiv": 0.05, "rlev": 1.0, "rpos": 0}           # 1,00–1,50
SWR_FINE = {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}               # 1,00–1,10
LOSS_ADAPTER = {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}          # 0…−0,5 дБ
LOSS_ASSEMBLY = {"pdiv": 0.3, "rlev": 0.0, "rpos": 10}          # 0…−3 дБ
LOSS_FLEX = {"pdiv": 0.03, "rlev": 0.0, "rpos": 10}             # 0…−0,3 дБ
OVERVIEW = {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}               # −50…0 дБ
SMITH = {"pdiv": 1.0}                                           # full scale 1
PHASE = {"pdiv": 36.0, "rlev": 0.0, "rpos": 5}                  # ±180°


@dataclass(frozen=True)
class TraceSpec:
    param: str
    format: str
    scale: dict | str                  # a fixed scale, SWR_NORM or "auto" (autoscale after every sweep)
    limit: LimitKind | None = None
    hidden: bool = False
    memory: bool = False


@dataclass(frozen=True)
class ScreenSpec:
    key: str
    name: str
    purpose: str
    split: int
    full_band: bool                    # False: the band comes from the ETK
    measuring: bool                    # traces 1–4 are S11, S21, S12, S22 and are captured
    traces: tuple[TraceSpec, ...]


def _t(param, fmt, scale, limit=None, *, hidden=False, memory=False) -> TraceSpec:
    return TraceSpec(param, fmt, scale, limit, hidden, memory)


SCREENS: dict[str, ScreenSpec] = {spec.key: spec for spec in (
    ScreenSpec("adapter", "ОТК — переход", "замер переходника", 8, True, True, (
        _t("S11", "SWR", SWR_NORM, "swr"), _t("S21", "MLOG", LOSS_ADAPTER, "loss"),
        _t("S12", "MLOG", LOSS_ADAPTER, "loss"), _t("S22", "SWR", SWR_NORM, "swr"))),
    ScreenSpec("assembly", "ОТК — кабельная сборка", "сборка с двух сторон, полоса и норма по ЭТК", 5, False, False, (
        _t("S11", "SWR", SWR_ASSEMBLY, "assembly"), _t("S21", "MLOG", LOSS_ASSEMBLY),
        _t("S22", "SWR", SWR_ASSEMBLY, "assembly"))),
    ScreenSpec("one_port", "ОТК — один порт", "сборка с одной стороны, на другом конце нагрузка", 3, False, False, (
        _t("S11", "SWR", SWR_ASSEMBLY, "assembly"), _t("S11", "MLOG", OVERVIEW))),
    ScreenSpec("load_check", "Проверка калибровки (нагрузка)", "нагрузка 6650F27 на каждом порту", 3, True, True, (
        _t("S11", "SWR", SWR_FINE, "cal"), _t("S21", "MLOG", LOSS_ADAPTER, hidden=True),
        _t("S12", "MLOG", LOSS_ADAPTER, hidden=True), _t("S22", "SWR", SWR_FINE, "cal"))),
    ScreenSpec("flex", "Проверка кабелей (шевеление)", "запомнить графики, изогнуть кабель, сравнить", 8, True, False, (
        _t("S11", "SWR", SWR_FINE, memory=True), _t("S22", "SWR", SWR_FINE, memory=True),
        _t("S21", "MLOG", LOSS_FLEX, memory=True), _t("S21", "PHAS", PHASE, memory=True))),
    ScreenSpec("engineer", "Подробно (инженер)",
               "все графики: КСВН, потери, Вольперт-Смит, фаза, ГВЗ, возвратные потери", 15, True, True, (
        _t("S11", "SWR", SWR_NORM, "swr"), _t("S21", "MLOG", LOSS_ADAPTER, "loss"), _t("S12", "MLOG", LOSS_ADAPTER, "loss"),
        _t("S22", "SWR", SWR_NORM, "swr"), _t("S11", "SMIT", SMITH), _t("S21", "PHAS", PHASE), _t("S21", "GDEL", "auto"),
        _t("S22", "SMIT", SMITH), _t("S11", "MLOG", OVERVIEW), _t("S21", "MLOG", OVERVIEW), _t("S12", "MLOG", OVERVIEW),
        _t("S22", "MLOG", OVERVIEW))),
)}


@dataclass(frozen=True)
class Line:
    """One straight limit segment: «upper» for VSWR, «lower» for S21/S12 in dB against a loss norm."""
    kind: Literal["upper", "lower"]
    start_hz: float
    stop_hz: float
    start_value: float
    stop_value: float

    def segment(self) -> LimitSegment:
        return LimitSegment(kind=self.kind, start_hz=self.start_hz, stop_hz=self.stop_hz,
                            start_value=self.start_value, stop_value=self.stop_value)


@dataclass(frozen=True)
class ItemLimits:
    swr: tuple[Line, ...] = ()
    loss: tuple[Line, ...] = ()
    title: str = ""


def number_text(value: float, digits: int = 3) -> str:
    """«1,05», «0,005»: trailing zeros cut, decimal comma. Rounded as the bench program printed it (15 significant
    digits, then half away from zero), so the same norm reads the same on the VNA screen and in its file."""
    rounded = Decimal(f"{value:.15g}").quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    text = f"{rounded.copy_abs() if rounded.is_zero() else rounded:f}"
    return (text.rstrip("0").rstrip(".") if "." in text else text).replace(".", ",")


def _ghz(hz: float) -> str:
    return number_text(hz / 1e9, 2)


def loss_lines(loss: LossNorm | None, reference: str, start_hz: float, band_hz: float) -> tuple[tuple[Line, ...], str]:
    """Lower lines for S21/S12: −(a + k·√F). A chord of the convex −k·√F lies above the curve, so the straight pieces
    are slightly stricter than the norm (≤ 0,002 dB with 12 pieces per band)."""
    if loss is None:
        return (), "потери: нормы нет"
    lines, texts, low = [], [], loss.from_ghz
    for segment in loss.segments:
        start, stop = max(low * 1e9, start_hz), min(segment.to_ghz * 1e9, band_hz)
        if stop > start:
            pieces = LOSS_CHORDS if segment.k else 1
            for piece in range(pieces):
                a = start + (stop - start) * piece / pieces
                b = start + (stop - start) * (piece + 1) / pieces
                lines.append(Line("lower", a, b, -(segment.a + segment.k * math.sqrt(a / 1e9)),
                                  -(segment.a + segment.k * math.sqrt(b / 1e9))))
            formula = ([number_text(segment.a)] if segment.a else []) + ([f"{number_text(segment.k)}·√F"] if segment.k else [])
            texts.append(f"≤ {' + '.join(formula)} дБ ({_ghz(start)}–{_ghz(stop)} ГГц)")
        low = segment.to_ghz
    for point in loss.points:
        at = point.ghz * 1e9
        if at < start_hz or at > band_hz:
            continue
        lines.append(Line("lower", max(start_hz, at - TEST_POINT_HALF_WIDTH_HZ), min(band_hz, at + TEST_POINT_HALF_WIDTH_HZ),
                          -point.max_db, -point.max_db))
        texts.append(f"≤ {number_text(point.max_db)} дБ на частоте испытания {_ghz(at)} ГГц")
    if not texts:
        return (), f"потери: норма {reference} не покрывает полосу замера"
    return tuple(lines), f"потери {'; '.join(texts)} — {reference}"


def _foreign_ohm(name: str, rf: RfDetails | None) -> float | None:
    """An end that is not 50 Ω: the card's RF interfaces win, else the name is parsed."""
    ports = (rf.interfaces if rf else ()) or parse_interfaces(name or "")
    return next((p.impedance_ohm for p in ports if p.impedance_ohm is not None and p.impedance_ohm != 50), None)


def _screen_reference(entry: CatalogNorm) -> str:
    """The short name on the window title: the maker's title for a maker group, else the sheet or type norm."""
    return str(entry.holder["title"]) if entry.level == "maker" and entry.holder else entry.norm.reference


def item_limits(number: str, name: str, rf: RfDetails | None, *, start_hz: float = FULL_BAND_HZ[0],
                stop_hz: float = FULL_BAND_HZ[1]) -> ItemLimits:
    """VSWR and loss lines of the norm assigned to the item (RfDetails.norm_id) inside [start_hz, stop_hz], and the
    window title. Unassigned or unknown — no lines, said in the title. A VSWR segment a + b·F is linear in F, so one
    straight S2VNA segment between its ends is exact."""
    prefix = f"{number} · " if number else ""
    ohm = _foreign_ohm(name, rf)
    if ohm is not None:
        return ItemLimits(title=f"{prefix}{number_text(ohm)} Ом — не для 50 Ом тракта; линий допуска нет")
    norm_id = rf.norm_id if rf else ""
    entry = catalog().get(norm_id) if norm_id else None
    parts, swr, loss, loss_text = [], [], (), "потери: нормы нет"
    if entry is not None:
        norm, reference = entry.norm, _screen_reference(entry)
        texts, low = [], norm.from_ghz
        for segment in norm.segments:
            start, stop = max(low * 1e9, start_hz), min(segment.to_ghz * 1e9, stop_hz)
            if stop > start:
                swr.append(Line("upper", start, stop, segment.a + segment.b * start / 1e9, segment.a + segment.b * stop / 1e9))
                formula = f"{number_text(segment.a)} + {number_text(segment.b)}·F" if segment.b else number_text(segment.a)
                texts.append(f"≤ {formula} ({_ghz(start)}–{_ghz(stop)} ГГц)")
            low = segment.to_ghz
        loss, loss_text = loss_lines(norm.loss, reference, start_hz, stop_hz)
        parts.append(f"КСВН {'; '.join(texts)} — {reference}" if texts else f"норма {reference} не покрывает полосу замера")
    elif norm_id:
        parts.append(f"норма {norm_id} отсутствует в rf-norms.json")
    else:
        parts.append("норма не назначена — линий допуска нет")
    parts.append(loss_text)
    return ItemLimits(tuple(swr), tuple(loss), prefix + " · ".join(parts))


def norm_top_pdiv(swr: tuple[Line, ...] | list[Line]) -> float:
    """SWR scale of an adapter screen: top = max(norm × 1,15; 1,20) rounded up to 0,05; without a norm 1,00–1,20."""
    norm = max((max(line.start_value, line.stop_value) for line in swr), default=0.0)
    top = max(norm * 1.15, 1.20)
    top = math.ceil(round(top * 20, 6)) / 20
    return round((top - 1.0) / 10, 4)


def _scale(spec: TraceSpec, swr: tuple[Line, ...]) -> Scale | str:
    if spec.scale == "auto":
        return "auto"
    if spec.scale == SWR_NORM:
        return Scale(pdiv=norm_top_pdiv(swr), rlev=1.0, rpos=0)
    return Scale(**spec.scale)


def _band(spec: ScreenSpec, band_hz: tuple[float, float] | None) -> tuple[float, float]:
    if spec.full_band:
        return FULL_BAND_HZ
    if band_hz is None:
        raise ProductionValidationError(f"Для экрана «{spec.name}» нужна полоса по ЭТК.")
    low, high = band_hz
    if not (math.isfinite(low) and math.isfinite(high)) or low < INSTRUMENT_BAND_HZ[0] or high > INSTRUMENT_BAND_HZ[1] or high <= low:
        raise ProductionValidationError("Полоса вне диапазона ВАЦ 0,1–18000 МГц.")
    return low, high


def screen(key: str, *, item: ItemLimits | None = None, band_hz: tuple[float, float] | None = None,
           assembly_vswr: float | None = None) -> Screen:
    """The v2 `Screen` of one bench screen. `item` — the lines of an adapter (`item_limits`, cut at the chosen upper
    frequency), drawn and titled only on the screens that judge an item; `band_hz` and `assembly_vswr` — the ETK band
    and VSWR norm of an assembly. A graph without a norm gets no lines; markers outside the swept band are left out."""
    spec = SCREENS.get(key)
    if spec is None:
        raise ProductionValidationError(f"Нет экрана «{key}».")
    start, stop = _band(spec, band_hz)
    item = item or ItemLimits()
    by_kind: dict[str, tuple[Line, ...]] = {"swr": item.swr, "loss": item.loss, "cal": (Line("upper", start, stop, LOAD_LIMIT, LOAD_LIMIT),)}
    title = item.title if any(trace.limit in ("swr", "loss") for trace in spec.traces) else ""
    if any(trace.limit == "assembly" for trace in spec.traces):
        title = ""
        by_kind["assembly"] = ()
        if assembly_vswr is not None:
            if not math.isfinite(assembly_vswr) or assembly_vswr <= 1:
                raise ProductionValidationError("Норма КСВН — число больше 1.")
            by_kind["assembly"] = (Line("upper", start, stop, assembly_vswr, assembly_vswr),)
            title = f"КСВН сборки ≤ {number_text(assembly_vswr)} по ЭТК"
    traces = []
    for index, trace in enumerate(spec.traces, start=1):
        lines = by_kind[trace.limit] if trace.limit else ()
        if len(lines) > LIMIT_SEGMENTS_MAX:
            raise ProductionValidationError(f"График {index}: линий допуска {len(lines)}, S2VNA принимает до {LIMIT_SEGMENTS_MAX}.")
        traces.append(ScreenTrace(param=trace.param, format=trace.format, scale=_scale(trace, item.swr), hidden=trace.hidden,
                                  memory=trace.memory, limits=[line.segment() for line in lines]))
    markers = [hz for hz in MARKERS_HZ if start <= hz <= stop]
    return Screen(split=spec.split, traces=traces, markers_hz=markers, title=title[:256])
