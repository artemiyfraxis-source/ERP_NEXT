"""VSWR norms for an instrument passport: maker spec of the series, else the MIL-PRF-55339 sheet, else none.

Every number lives in backend/data/rf-norms.json with its source; this module only reads and applies it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from .rf_interfaces import RfDetails, parse_interfaces

NORMS_FILE = Path(__file__).resolve().parents[3] / "data" / "rf-norms.json"


@lru_cache(maxsize=1)
def norms_data() -> dict:
    return json.loads(NORMS_FILE.read_text(encoding="utf-8"))


def _num(value: float, digits: int = 3) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",").replace("-", "−")


def ghz_text(value: float) -> str:
    return _num(round(value, 2), 2)


@dataclass(frozen=True)
class Segment:
    to_ghz: float
    a: float
    b: float = 0.0

    @property
    def text(self) -> str:
        return f"{_num(self.a)} + {_num(self.b)}·F" if self.b else _num(self.a)


def loss_db(magnitude: float) -> float:
    """Insertion loss in dB from a linear |S21|."""
    return max(0.0, -20 * math.log10(magnitude)) if magnitude > 0 else math.inf


# A sheet that names one test frequency is judged at the measured point nearest to it, within this distance.
POINT_TOLERANCE_GHZ = 0.05


@dataclass(frozen=True)
class LossSegment:
    to_ghz: float
    a: float = 0.0
    k: float = 0.0           # limit a + k·√F dB, F in GHz

    @property
    def text(self) -> str:
        root = f"{_num(self.k)}·√F" if self.k else ""
        if self.a and root:
            return f"{_num(self.a)} + {root}"
        return root or _num(self.a)


@dataclass(frozen=True)
class LossNorm:
    """Insertion-loss limit of the same source as the VSWR norm: a + k·√F per band, or one value at the test frequency."""
    reference: str
    from_ghz: float = 0.0
    segments: tuple[LossSegment, ...] = ()
    points: tuple[tuple[float, float], ...] = ()   # (GHz, dB maximum)
    source: str = ""

    def limit(self, ghz: float) -> float | None:
        for at, value in self.points:
            if abs(ghz - at) <= POINT_TOLERANCE_GHZ + 1e-9:
                return value
        if self.segments and ghz >= self.from_ghz - 1e-9:
            for segment in self.segments:
                if ghz <= segment.to_ghz + 1e-9:
                    return segment.a + segment.k * math.sqrt(max(ghz, 0.0))
        return None

    @property
    def limit_text(self) -> str:
        parts, low = [], self.from_ghz
        for segment in self.segments:
            parts.append(f"≤ {segment.text} дБ в полосе {ghz_text(low)}–{ghz_text(segment.to_ghz)} ГГц")
            low = segment.to_ghz
        parts += [f"≤ {_num(value)} дБ на {ghz_text(at)} ГГц" for at, value in self.points]
        formula = "; F — частота, ГГц" if any(segment.k for segment in self.segments) else ""
        return "вносимые потери " + "; ".join(parts) + formula

    @property
    def line(self) -> str:
        return f"{self.reference} — {self.limit_text}"


def _loss_norm(reference: str, raw: dict | None) -> LossNorm | None:
    if not raw:
        return None
    segments = tuple(LossSegment(float(s["to_ghz"]), float(s.get("a", 0)), float(s.get("k", 0))) for s in raw.get("segments", ()))
    points = tuple((float(p["ghz"]), float(p["max_db"])) for p in raw.get("points", ()))
    return LossNorm(reference, float(raw.get("from_ghz", 0)), segments, points, raw.get("source", ""))


@dataclass(frozen=True)
class Norm:
    reference: str            # «Планар ADP1A, …» or «MIL-PRF-55339/54B»
    title: str                # what the sheet/group covers
    from_ghz: float
    segments: tuple[Segment, ...]
    url: str = ""
    mating_resource: int | None = None
    maker: bool = False
    loss: LossNorm | None = None

    @property
    def to_ghz(self) -> float:
        return self.segments[-1].to_ghz

    def limit(self, ghz: float) -> float | None:
        if ghz < self.from_ghz - 1e-9:
            return None
        for segment in self.segments:
            if ghz <= segment.to_ghz + 1e-9:
                return segment.a + segment.b * ghz
        return None

    @property
    def limit_text(self) -> str:
        parts, low = [], self.from_ghz
        for segment in self.segments:
            parts.append(f"≤ {segment.text} в полосе {ghz_text(low)}–{ghz_text(segment.to_ghz)} ГГц")
            low = segment.to_ghz
        formula = "; F — частота, ГГц" if any(segment.b for segment in self.segments) else ""
        return "КСВН " + "; ".join(parts) + formula

    @property
    def line(self) -> str:
        return f"{self.reference} ({self.title}) — {self.limit_text}"


@dataclass(frozen=True)
class ItemSpec:
    """Temperature range and mating resource: the item's tag, else the maker's series, else the MIL sheet of the pair."""
    title: str
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    temp_source: str = ""
    mating_resource: int | None = None
    mating_source: str = ""

    @property
    def temperature_line(self) -> str:
        if self.temp_min_c is None or self.temp_max_c is None:
            return ""
        high = _num(self.temp_max_c)
        return f"{_num(self.temp_min_c)}…{'+' if self.temp_max_c > 0 else ''}{high} °C"


def _segments(raw) -> tuple[Segment, ...]:
    return tuple(Segment(float(s["to_ghz"]), float(s["a"]), float(s.get("b", 0))) for s in raw)


def _model_text(name: str, model: str) -> str:
    return f"{name} {model}".upper()


def _maker(name: str, model: str) -> dict | None:
    text = _model_text(name, model)
    return next((entry for entry in norms_data()["makers"]
                 if re.search(rf"(?<![A-Z0-9]){re.escape(entry['match'].upper())}", text)), None)


def _ports(name: str, rf: RfDetails) -> tuple[str, ...] | None:
    ports = rf.interfaces or parse_interfaces(name)
    if len(ports) != 2 or any(p.impedance_ohm is not None and p.impedance_ohm != 50 for p in ports):
        return None
    return tuple(sorted(p.series for p in ports))


def norm_for(name: str, model: str, rf: RfDetails) -> Norm | None:
    """Maker spec of the series if stored, else the MIL-PRF-55339 sheet of the connector pair, else None."""
    ports = _ports(name, rf)
    if ports is None:
        return None
    maker = _maker(name, model)
    if maker:
        for group in maker["groups"]:
            if tuple(sorted(group["ports"])) == ports:
                return Norm(maker["source"], group["title"], float(group["from_ghz"]),
                            _segments(group["segments"]), maker.get("url", ""), group.get("mating_resource"), True,
                            _loss_norm(maker["title"], group.get("loss")))
    sheet = _mil_sheet(ports)
    if sheet is None:
        return None
    return Norm(sheet["sheet"], sheet["title"], float(sheet["from_ghz"]), _segments(sheet["segments"]),
                sheet.get("url", ""), sheet.get("mating_resource"), loss=_loss_norm(sheet["sheet"], sheet.get("loss")))


def _mil_sheet(ports: tuple[str, ...] | None) -> dict | None:
    return next((s for s in norms_data()["mil_adapters"] if ports and tuple(sorted(s["ports"])) == ports), None)


CARD_RESOURCE_SOURCE = "карточка СИ"


def item_spec(name: str, model: str, rf: RfDetails = RfDetails()) -> ItemSpec | None:
    """For the label (`temp_min_c`, `temp_max_c`, `mating_resource`) and the passport, with the source of each.

    The instrument's own «Ресурс сочленений» (RfDetails.mating_resource, PR #913) wins over the series default."""
    spec = _series_spec(name, model, rf)
    own = getattr(rf, "mating_resource", None)  # tolerant until #913 adds the field
    if own:
        spec = replace(spec or ItemSpec(""), mating_resource=own, mating_source=CARD_RESOURCE_SOURCE)
    return spec


def _series_spec(name: str, model: str, rf: RfDetails) -> ItemSpec | None:
    ports = _ports(name, rf)
    maker = _maker(name, model)
    if maker is None:
        sheet = _mil_sheet(ports)
        if sheet is None:
            return None
        source = sheet["sheet"]
        return ItemSpec(sheet["sheet"], sheet.get("temp_min_c"), sheet.get("temp_max_c"), source,
                        sheet.get("mating_resource"), source)
    group = next((g for g in maker["groups"] if ports and tuple(sorted(g["ports"])) == ports), None)
    resource = group.get("mating_resource") if group else None
    source = maker["source"]
    spec = dict(temp_min_c=maker.get("temp_min_c"), temp_max_c=maker.get("temp_max_c"),
                temp_source=maker.get("temp_source", ""), mating_resource=resource,
                mating_source=source if resource else "")
    text = _model_text(name, model)
    for article in maker.get("articles", ()):
        if re.search(rf"(?<![A-Z0-9]){re.escape(article['article'].upper())}(?![A-Z0-9])", text):
            spec.update({key: article[key] for key in spec if key in article})
    return ItemSpec(maker["title"], **spec)


@dataclass(frozen=True)
class Uncertainty:
    title: str
    at_vswr: float
    steps: tuple[tuple[float, float], ...]   # (to_ghz, ±VSWR)
    source: str

    def at(self, ghz: float) -> float:
        return next((value for top, value in self.steps if ghz <= top + 1e-9), self.steps[-1][1])

    @property
    def line(self) -> str:
        parts, low = [], None
        for top, value in self.steps:
            parts.append(f"±{_num(value)} " + (f"до {ghz_text(top)} ГГц" if low is None else f"выше {ghz_text(low)} ГГц"))
            low = top
        return f"{', '.join(parts)} (при КСВН {_num(self.at_vswr)}; {self.source})"


def vna_model(instrument_text: str, registry=()) -> str:
    """The VNA model from the measurement header, or from the registry by the VNA serial the header names."""
    known = [entry["model"] for entry in norms_data()["vna"]]
    for model in known:
        if re.search(rf"(?<![A-Z0-9]){re.escape(model)}(?![0-9])", instrument_text.upper()):
            return model
    serial = re.search(r"ВАЦ №(\S+)", instrument_text)
    if serial:
        for item in registry:
            if item.serial_number == serial[1]:
                text = _model_text(item.name, item.doc_designation or "")
                return next((model for model in known if model in text), "")
    return ""


def uncertainty_for(model: str) -> Uncertainty | None:
    entry = next((entry for entry in norms_data()["vna"] if entry["model"] == model), None) if model else None
    if entry is None:
        return None
    return Uncertainty(entry["title"], float(entry["at_vswr"]),
                       tuple((float(s["to_ghz"]), float(s["value"])) for s in entry["uncertainty"]), entry["source"])


@dataclass(frozen=True)
class Assessment:
    """Decision against the norm, simple acceptance (w = 0); a margin under the uncertainty is flagged."""
    norm: Norm | None
    status: str                    # "pass" | "fail" | "none" | "no_points"
    start_ghz: float = 0.0
    stop_ghz: float = 0.0
    at_ghz: float = 0.0            # the decisive point: worst margin (pass) or first excess (fail)
    vswr: float = 0.0
    limit: float = 0.0
    last_pass_ghz: float | None = None
    border: bool = False

    @property
    def verdict(self) -> str:
        if self.status == "none":
            return "Норма не установлена — заключение о соответствии не выдаётся"
        if self.status == "no_points":
            return "Нет точек замера в полосе нормы — заключение о соответствии не выдаётся"
        if self.status == "pass":
            text = f"Соответствует норме в полосе {ghz_text(self.start_ghz)}–{ghz_text(self.stop_ghz)} ГГц"
        elif self.last_pass_ghz is None:
            text = "Не соответствует норме"
        else:
            text = f"Не соответствует норме выше {ghz_text(self.last_pass_ghz)} ГГц"
        return text + (" (на границе допуска)" if self.border else "")

    @property
    def result_line(self) -> str:
        if self.status not in ("pass", "fail"):
            return ""
        word = "худший КСВН" if self.status == "pass" else "КСВН"
        return f"{word} {_num(self.vswr, 3)} на {ghz_text(self.at_ghz)} ГГц при норме ≤ {_num(self.limit, 3)}"

    @property
    def short(self) -> str:
        if self.status == "pass":
            return f"соответствует норме до {ghz_text(self.stop_ghz)} ГГц"
        if self.status == "fail":
            return f"не соответствует норме выше {ghz_text(self.last_pass_ghz)} ГГц" if self.last_pass_ghz is not None \
                else "не соответствует норме"
        return "норма не установлена" if self.status == "none" else "нет точек в полосе нормы"


def assess(measured, norm: Norm | None, uncertainty: Uncertainty | None = None) -> Assessment:
    """Both ports at every measured frequency within the norm's band (and the measurement's)."""
    if norm is None:
        return Assessment(None, "none")
    first, second = measured.points("S11"), measured.points("S22")
    points = [(f / 1e9, max(left, right)) for (f, left), (g, right) in zip(first, second) if f == g]
    judged = [(ghz, value, norm.limit(ghz)) for ghz, value in points if norm.limit(ghz) is not None]
    if not judged:
        return Assessment(norm, "no_points")
    start, stop = judged[0][0], judged[-1][0]
    last_pass = None
    for ghz, value, limit in judged:
        if value > limit:
            border = bool(uncertainty and value - limit < uncertainty.at(ghz))
            return Assessment(norm, "fail", start, stop, ghz, value, limit, last_pass, border)
        last_pass = ghz
    ghz, value, limit = min(judged, key=lambda point: point[2] - point[1])
    border = bool(uncertainty and limit - value < uncertainty.at(ghz))
    return Assessment(norm, "pass", start, stop, ghz, value, limit, None, border)


@dataclass(frozen=True)
class LossAssessment:
    """Insertion loss |S21| against the loss norm, the same simple acceptance (w = 0). The reference data hold no
    insertion-loss uncertainty of the VNA, so the border of the tolerance is not judged."""
    norm: LossNorm | None
    status: str                    # "pass" | "fail" | "none" | "no_points"
    at_ghz: float = 0.0
    loss: float = 0.0
    limit: float = 0.0
    last_pass_ghz: float | None = None

    @property
    def short(self) -> str:
        if self.status == "pass":
            return "соответствует норме"
        if self.status == "fail":
            return f"не соответствует норме выше {ghz_text(self.last_pass_ghz)} ГГц" if self.last_pass_ghz is not None                 else "не соответствует норме"
        return "норма не установлена" if self.status == "none" else "нет точек в полосе нормы"

    @property
    def result_line(self) -> str:
        if self.status not in ("pass", "fail"):
            return ""
        word = "худшие потери" if self.status == "pass" else "потери"
        return f"{word} {_num(self.loss, 3)} дБ на {ghz_text(self.at_ghz)} ГГц при норме ≤ {_num(self.limit, 3)} дБ"


def assess_loss(measured, norm: LossNorm | None) -> LossAssessment:
    """Every measured S21 point the loss norm covers; pass reports the point with the least margin."""
    if norm is None:
        return LossAssessment(None, "none")
    judged = []
    for frequency, magnitude in measured.points("S21"):
        ghz = frequency / 1e9
        limit = norm.limit(ghz)
        if limit is not None:
            judged.append((ghz, loss_db(magnitude), limit))
    if not judged:
        return LossAssessment(norm, "no_points")
    last_pass = None
    for ghz, value, limit in judged:
        if value > limit + 1e-12:
            return LossAssessment(norm, "fail", ghz, value, limit, last_pass)
        last_pass = ghz
    ghz, value, limit = min(judged, key=lambda point: point[2] - point[1])
    return LossAssessment(norm, "pass", ghz, value, limit)
