"""Norms for an instrument passport: the norm ASSIGNED to the item by id from the catalog, else none (owner 18.11).

Every number lives in backend/data/rf-norms.json with its source; this module only reads and applies it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from .rf_interfaces import RfDetails

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


# A test frequency outside the measured span by more than this is «no point»; inside, the nearest measured point is used.
TEST_FREQUENCY_TOLERANCE_GHZ = 0.05
# Without a VNA loss uncertainty (model unknown) an excess smaller than this is reported as a border, not a failure.
LOSS_BORDER_FALLBACK_DB = 0.05


def _bands_text(segments, from_ghz: float, start: float, stop: float, unit: str = "") -> str:
    """«≤ 1,06 до 20 ГГц; ≤ 1,1 до 26,5 ГГц» — only the segments the measured range [start, stop] touches."""
    parts, low = [], from_ghz
    for segment in segments:
        if segment.to_ghz > start + 1e-9 and low < stop - 1e-9:
            span = f"до {ghz_text(segment.to_ghz)}" if not parts and low <= 1e-9 or parts else                 f"в полосе {ghz_text(low)}–{ghz_text(segment.to_ghz)}"
            parts.append(f"≤ {segment.text}{unit} {span} ГГц")
        low = segment.to_ghz
    return "; ".join(parts)


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
class LossPoint:
    """A sheet that tests at one frequency («tested at X GHz») is judged only there."""
    ghz: float
    max_db: float
    k: float = 0.0           # the sheet's k·√F, kept for the text

    @property
    def text(self) -> str:
        value = f"{_num(self.k)}·√F = {_num(self.max_db)}" if self.k else _num(self.max_db)
        return f"≤ {value} дБ на частоте испытания {ghz_text(self.ghz)} ГГц"


@dataclass(frozen=True)
class LossNorm:
    """Insertion-loss limit of the same source as the VSWR norm: a + k·√F per band, or values at the test frequency."""
    reference: str
    from_ghz: float = 0.0
    segments: tuple[LossSegment, ...] = ()
    points: tuple[LossPoint, ...] = ()
    source: str = ""

    def limit(self, ghz: float) -> float | None:
        """The banded limit; point norms are judged by `assess_loss` at their test frequency only."""
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
        parts += [point.text for point in self.points]
        formula = "; F — частота, ГГц" if any(s.k for s in self.segments) or any(p.k for p in self.points) else ""
        return "вносимые потери " + "; ".join(parts) + formula

    @property
    def line(self) -> str:
        return f"{self.reference} — {self.limit_text}"

    def brief(self, start: float, stop: float) -> str:
        """«≤ 0,2 дБ до 32 ГГц» for the measured range; test-frequency norms only when that frequency was measured."""
        parts = [_bands_text(self.segments, self.from_ghz, start, stop, " дБ")] if self.segments else []
        parts += [point.text for point in self.points if start - 0.5 <= point.ghz <= stop + 0.5]
        return "; ".join(filter(None, parts))


def _loss_norm(reference: str, raw: dict | None) -> LossNorm | None:
    if not raw:
        return None
    segments = tuple(LossSegment(float(s["to_ghz"]), float(s.get("a", 0)), float(s.get("k", 0))) for s in raw.get("segments", ()))
    points = []
    for point in raw.get("points", ()):
        ghz, k = float(point["ghz"]), float(point.get("k", 0))
        points.append(LossPoint(ghz, float(point["max_db"]) if "max_db" in point else k * math.sqrt(ghz), k))
    return LossNorm(raw.get("reference", reference), float(raw.get("from_ghz", 0)), segments, tuple(points), raw.get("source", ""))


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
        return f"{self.reference} — {self.limit_text}"

    def brief(self, start: float, stop: float) -> str:
        """«РЭ Планар ADP1A, табл. 2.6 — КСВН ≤ 1,06 до 20 ГГц; потери ≤ 0,2 дБ до 32 ГГц»: the bands that apply only."""
        text = f"{self.reference} — КСВН {_bands_text(self.segments, self.from_ghz, start, stop) or self.limit_text[5:]}"
        loss = self.loss.brief(start, stop) if self.loss else ""
        if loss:
            other = f" ({self.loss.reference})" if self.loss.reference not in ("", self.reference) else ""
            text += f"; потери{other} {loss}"
        return text


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


@dataclass(frozen=True)
class CatalogNorm:
    """One assignable norm of rf-norms.json: a maker group, a ТУ/IEC/ESCC type norm or a MIL-PRF-55339 sheet."""
    id: str
    level: str                                   # maker | tu | iec | escc | mil
    norm: Norm
    port_pairs: tuple[tuple[str, str], ...]      # sorted series pairs the norm covers
    ends: tuple[tuple[str, str], ...] = ()       # sorted «SERIES штырь|гнездо» pairs when the source names the genders
    angle: bool | None = None                    # True/False when the source tells straight from right-angle
    holder: dict | None = None                   # maker entry, type norm or sheet: temperature, resource, articles
    group: dict | None = None                    # the maker's group (resource)


def _pair(ports) -> tuple[str, str]:
    return tuple(sorted(ports))


def _pairs(raw: dict) -> tuple[tuple[str, str], ...]:
    return tuple(_pair(p) for p in raw.get("port_pairs", [raw.get("ports", ())]))


def _ends(raw: dict) -> tuple[tuple[str, str], ...]:
    return tuple(_pair(pair) for pair in raw.get("ends", ()))


@lru_cache(maxsize=1)
def catalog() -> dict[str, CatalogNorm]:
    """Every assignable norm by id; an id repeated in the file is an error, not a silent override."""
    data, found = norms_data(), []
    for maker in data["makers"]:
        for group in maker["groups"]:
            found.append(CatalogNorm(group["id"], "maker", Norm(
                group.get("reference", group.get("source", maker["source"])), group["title"], float(group["from_ghz"]), _segments(group["segments"]),
                group.get("url", maker.get("url", "")), group.get("mating_resource"), True,
                _loss_norm(maker["title"], group.get("loss"))), _pairs(group), _ends(group), holder=maker, group=group))
    for entry in data.get("type_norms", ()):
        found.append(CatalogNorm(entry["id"], entry["level"], Norm(
            entry["reference"], entry["title"], float(entry["from_ghz"]), _segments(entry["segments"]), entry.get("url", ""),
            entry.get("mating_resource"), loss=_loss_norm(entry["reference"], entry.get("loss"))),
            _pairs(entry), _ends(entry), entry.get("angle"), holder=entry))
    for sheet in data["mil_adapters"]:
        found.append(CatalogNorm(sheet["id"], "mil", Norm(
            sheet["sheet"], sheet["title"], float(sheet["from_ghz"]), _segments(sheet["segments"]), sheet.get("url", ""),
            sheet.get("mating_resource"), loss=_loss_norm(sheet["sheet"], sheet.get("loss"))),
            _pairs(sheet), _ends(sheet), holder=sheet))
    result = {}
    for entry in found:
        if entry.id in result:
            raise ValueError(f"Норма {entry.id} повторяется в rf-norms.json")
        result[entry.id] = entry
    return result


def norm_by_id(norm_id: str) -> Norm | None:
    entry = catalog().get(norm_id or "")
    return entry.norm if entry else None


def norm_for(name: str, model: str, rf: RfDetails) -> Norm | None:
    """The norm ASSIGNED to the item (RfDetails.norm_id), never derived at run time (owner 18.11): unassigned or an id
    missing from the catalog gives None — «норма не назначена», no verdict. `name`/`model` stay for the callers."""
    return norm_by_id(getattr(rf, "norm_id", ""))


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
    """Temperature and resource of the assigned norm's source; a maker's article tag beats its series data."""
    entry = catalog().get(getattr(rf, "norm_id", "") or "")
    if entry is None:
        return None
    holder = entry.holder or {}
    if entry.level != "maker":
        source = entry.norm.reference
        temp = holder.get("temp_min_c") is not None
        resource = holder.get("mating_resource")
        return ItemSpec(source, holder.get("temp_min_c"), holder.get("temp_max_c"), source if temp else "",
                        resource, source if resource else "")
    resource = (entry.group or {}).get("mating_resource")
    source = holder["source"]
    spec = dict(temp_min_c=holder.get("temp_min_c"), temp_max_c=holder.get("temp_max_c"),
                temp_source=holder.get("temp_source", ""), mating_resource=resource,
                mating_source=source if resource else "")
    text = _model_text(name, model)
    for article in holder.get("articles", ()):
        if re.search(rf"(?<![A-Z0-9]){re.escape(article['article'].upper())}(?![A-Z0-9])", text):
            spec.update({key: article[key] for key in spec if key in article})
    return ItemSpec(holder["title"], **spec)


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


def resolve_vna(texts, registry=()):
    """(model, registry item) of the VNA that measured: the model from any header/file-name text or the item; the item
    by the serial the header names («ВАЦ №…», «VNA serial=…»), else the only registry entry of that model."""
    known = [entry["model"] for entry in norms_data()["vna"]]
    joined = " ".join(texts)
    serial = re.search(r"(?:ВАЦ №|VNA serial=)([A-Za-z0-9-]+)", joined)
    item = next((entry for entry in registry if serial and entry.serial_number == serial[1]), None)
    model = next((m for m in known if re.search(rf"(?<![A-Z0-9]){re.escape(m)}(?![0-9])", joined.upper())), "")
    if not model and item is not None:
        model = next((m for m in known if m in _model_text(item.name, item.doc_designation or "")), "")
    if item is None and model:
        same = [entry for entry in registry if model in _model_text(entry.name, entry.doc_designation or "")]
        item = same[0] if len(same) == 1 else None
    return model, item


def uncertainty_for(model: str) -> Uncertainty | None:
    entry = next((entry for entry in norms_data()["vna"] if entry["model"] == model), None) if model else None
    if entry is None:
        return None
    return Uncertainty(entry["title"], float(entry["at_vswr"]),
                       tuple((float(s["to_ghz"]), float(s["value"])) for s in entry["uncertainty"]), entry["source"])


REPEAT_CONNECTIONS = 3  # «3 подключения с поворотом 120°» (owner decision 18.11)
REPEAT_TEXT = "нужен повторный замер (3 подключения с поворотом 120°)"


BELOW_ACCURACY = "ниже точности ВАЦ — не оценивается"


def _decide(judged, allowance, repeated: bool):
    """One rule for VSWR and loss (owner decision 18.11): a point within ±U of the limit is «на границе допуска» and only
    triggers the 3-connection repeat; after the repeat the worst connection is judged by simple acceptance (≤ limit).

    Returns (status, first non-pass point, last pass before it, peak point)."""
    first_bad, last_pass, clear_fail, border = None, None, False, False
    for point in judged:
        ghz, value, limit = point
        allowed = 0.0 if repeated else allowance(ghz)
        if value > limit + allowed + 1e-12:
            clear_fail = True
        elif abs(value - limit) <= allowed + 1e-12 and allowed > 0:
            border = True
        elif value <= limit:
            if first_bad is None:
                last_pass = ghz
            continue
        else:  # above the limit with no stated uncertainty: simple acceptance
            clear_fail = True
        if first_bad is None:
            first_bad = point
    status = "fail" if clear_fail else "border" if border else "pass"
    return status, first_bad, last_pass, max(judged, key=lambda point: point[1])


def _resolvable(judged, allowance, margin):
    """Points whose limit the VNA can resolve: a limit within its own uncertainty (`margin(limit) ≤ 2·U` for loss,
    `limit − 1 ≤ U` for VSWR) is not judged (owner decision 18.11)."""
    return [point for point in judged if margin(point) > allowance(point[0]) + 1e-12]


@dataclass(frozen=True)
class Assessment:
    """Decision against the norm: pass below limit − U; «на границе допуска» within ±U until repeated; fail above."""
    norm: Norm | None
    status: str                    # "pass" | "border" | "fail" | "below_accuracy" | "none" | "no_points"
    start_ghz: float = 0.0
    stop_ghz: float = 0.0
    at_ghz: float = 0.0            # the first point that is not a clean pass (border/fail)
    vswr: float = 0.0
    limit: float = 0.0
    last_pass_ghz: float | None = None
    uncertainty: float | None = None
    peak_ghz: float = 0.0          # the worst measured value (what the passport quotes as «КСВН ≤ …»)
    peak: float = 0.0
    repeated: bool = False

    @property
    def first_bad_ghz(self) -> float | None:
        return self.at_ghz if self.status in ("border", "fail") else None

    @property
    def verdict(self) -> str:
        if self.status == "none":
            return "Норма не назначена — заключение о соответствии не выдаётся"
        if self.status == "no_points":
            return "Нет точек замера в полосе нормы — заключение о соответствии не выдаётся"
        if self.status == "below_accuracy":
            return "Норма ниже точности ВАЦ — заключение о соответствии не выдаётся"
        if self.status == "pass":
            return f"Соответствует норме в полосе {ghz_text(self.start_ghz)}–{ghz_text(self.stop_ghz)} ГГц"
        above = f" выше {ghz_text(self.last_pass_ghz)} ГГц" if self.last_pass_ghz is not None else ""
        if self.status == "border":
            return f"На границе допуска{above} — {REPEAT_TEXT}"
        return f"Не соответствует норме{above}"

    @property
    def result_line(self) -> str:
        return self.brief

    @property
    def brief(self) -> str:
        """«КСВН ≤ 1,035 (13,04 ГГц)»; a border or a failure names its point, limit and, when known, the uncertainty."""
        if self.status == "pass":
            return f"КСВН ≤ {_num(self.peak, 3)} ({ghz_text(self.peak_ghz)} ГГц)"
        if self.status in ("border", "fail"):
            text = f"КСВН {_num(self.vswr, 3)} на {ghz_text(self.at_ghz)} ГГц при норме ≤ {_num(self.limit, 3)}"
            return text + (f", погрешность ±{_num(self.uncertainty)}" if self.uncertainty else "")
        if self.status == "below_accuracy":
            return f"КСВН {_num(self.peak, 3)} (норма {_num(self.limit, 3)} {BELOW_ACCURACY})"
        return ""

    @property
    def short(self) -> str:
        if self.status == "pass":
            return f"соответствует норме до {ghz_text(self.stop_ghz)} ГГц"
        above = f" выше {ghz_text(self.last_pass_ghz)} ГГц" if self.last_pass_ghz is not None else ""
        if self.status == "border":
            return f"на границе допуска{above} — {REPEAT_TEXT}"
        if self.status == "fail":
            return f"не соответствует норме{above}"
        if self.status == "below_accuracy":
            return f"норма {BELOW_ACCURACY}"
        return "норма не назначена" if self.status == "none" else "нет точек в полосе нормы"


def assess(measured, norm: Norm | None, uncertainty: Uncertainty | None = None, *, repeated: bool = False) -> Assessment:
    """Both ports at every measured frequency within the norm's band (and the measurement's). Without a known VNA the
    uncertainty is unknown and the decision is simple acceptance."""
    if norm is None:
        return Assessment(None, "none")
    first, second = measured.points("S11"), measured.points("S22")
    points = [(f / 1e9, max(left, right)) for (f, left), (g, right) in zip(first, second) if f == g]
    judged = [(ghz, value, norm.limit(ghz)) for ghz, value in points if norm.limit(ghz) is not None]
    if not judged:
        return Assessment(norm, "no_points")

    def allowance(ghz: float) -> float:
        return uncertainty.at(ghz) if uncertainty else 0.0

    resolvable = _resolvable(judged, allowance, lambda point: point[2] - 1)
    if not resolvable:
        ghz, value, limit = max(judged, key=lambda point: point[1])
        return Assessment(norm, "below_accuracy", judged[0][0], judged[-1][0], ghz, value, limit, None,
                          allowance(ghz) or None, ghz, value)
    judged = resolvable
    status, bad, last_pass, peak = _decide(judged, allowance, repeated)
    ghz, value, limit = bad or peak
    return Assessment(norm, status, judged[0][0], judged[-1][0], ghz, value, limit, last_pass,
                      uncertainty.at(ghz) if uncertainty else None, peak[0], peak[1], repeated)


@dataclass(frozen=True)
class LossUncertainty:
    """Transmission-magnitude uncertainty of the VNA in dB, per band (from the maker's manual)."""
    steps: tuple[tuple[float, float], ...]   # (to_ghz, ±dB)
    source: str

    def at(self, ghz: float) -> float:
        return next((value for top, value in self.steps if ghz <= top + 1e-9), self.steps[-1][1])

    @property
    def line(self) -> str:
        parts, low = [], None
        for top, value in self.steps:
            parts.append(f"±{_num(value)} дБ " + (f"до {ghz_text(top)} ГГц" if low is None else f"выше {ghz_text(low)} ГГц"))
            low = top
        return f"{', '.join(parts)} ({self.source})"


def loss_uncertainty_for(model: str) -> LossUncertainty | None:
    entry = next((entry for entry in norms_data()["vna"] if entry["model"] == model), None) if model else None
    raw = entry.get("loss_uncertainty") if entry else None
    if not raw:
        return None
    return LossUncertainty(tuple((float(s["to_ghz"]), float(s["value_db"])) for s in raw["steps"]), raw["source"])


@dataclass(frozen=True)
class LossAssessment:
    """Insertion loss |S21| against the loss norm by the same rule as VSWR; with the VNA unknown the uncertainty is
    LOSS_BORDER_FALLBACK_DB (owner decision 18.11)."""
    norm: LossNorm | None
    status: str                    # "pass" | "border" | "fail" | "below_accuracy" | "none" | "no_points"
    at_ghz: float = 0.0
    loss: float = 0.0
    limit: float = 0.0
    last_pass_ghz: float | None = None
    uncertainty_db: float | None = None
    peak_ghz: float = 0.0
    peak: float = 0.0
    repeated: bool = False

    @property
    def first_bad_ghz(self) -> float | None:
        return self.at_ghz if self.status in ("border", "fail") else None

    @property
    def short(self) -> str:
        above = f" выше {ghz_text(self.last_pass_ghz)} ГГц" if self.last_pass_ghz is not None else ""
        if self.status == "pass":
            return "соответствует норме"
        if self.status == "border":
            return f"на границе допуска{above} — {REPEAT_TEXT}"
        if self.status == "fail":
            return f"не соответствует норме{above}"
        if self.status == "below_accuracy":
            return f"норма {BELOW_ACCURACY}"
        if self.status == "no_points":
            return "нет точек замера в полосе нормы" if self.norm and self.norm.segments else \
                "нет точки замера на частоте испытания"
        return "норма не назначена"

    @property
    def brief(self) -> str:
        if self.status == "pass":
            return f"потери ≤ {_num(self.peak, 3)} дБ ({ghz_text(self.peak_ghz)} ГГц)"
        if self.status in ("border", "fail"):
            text = f"потери {_num(self.loss, 3)} дБ на {ghz_text(self.at_ghz)} ГГц при норме ≤ {_num(self.limit, 3)} дБ"
            return text + (f", погрешность ±{_num(self.uncertainty_db)} дБ" if self.uncertainty_db is not None else "")
        if self.status == "below_accuracy":
            return f"потери {_num(self.peak, 3)} дБ (норма {_num(self.limit, 3)} дБ {BELOW_ACCURACY})"
        return ""

    @property
    def result_line(self) -> str:
        return self.brief


def _judged_loss(measured, norm: LossNorm):
    points = [(frequency / 1e9, loss_db(magnitude)) for frequency, magnitude in measured.points("S21")]
    judged = [(ghz, value, limit) for ghz, value in points if (limit := norm.limit(ghz)) is not None]
    if points:
        low, high = points[0][0], points[-1][0]
        for point in norm.points:
            if low - TEST_FREQUENCY_TOLERANCE_GHZ <= point.ghz <= high + TEST_FREQUENCY_TOLERANCE_GHZ:
                ghz, value = min(points, key=lambda item: abs(item[0] - point.ghz))
                judged.append((ghz, value, point.max_db))
    return sorted(judged)


def assess_loss(measured, norm: LossNorm | None, uncertainty: LossUncertainty | None = None, *,
                repeated: bool = False) -> LossAssessment:
    """Every measured S21 point the banded norm covers, and the nearest point to each test frequency."""
    if norm is None:
        return LossAssessment(None, "none")
    judged = _judged_loss(measured, norm)
    if not judged:
        return LossAssessment(norm, "no_points")

    def allowance(ghz: float) -> float:
        return uncertainty.at(ghz) if uncertainty else LOSS_BORDER_FALLBACK_DB

    resolvable = _resolvable(judged, allowance, lambda point: point[2] / 2)  # limit ≤ 2·U: not judged
    if not resolvable:
        ghz, value, limit = max(judged, key=lambda point: point[1])
        return LossAssessment(norm, "below_accuracy", ghz, value, limit, None, allowance(ghz), ghz, value)
    judged = resolvable
    status, bad, last_pass, peak = _decide(judged, allowance, repeated)
    ghz, value, limit = bad or peak
    return LossAssessment(norm, status, ghz, value, limit, last_pass, uncertainty.at(ghz) if uncertainty else None,
                          peak[0], peak[1], repeated)


_ORDER = {"fail": 5, "border": 4, "pass": 3, "below_accuracy": 2, "no_points": 1, "none": 0}


def worst(assessments):
    """Across the repeated connections the worst result decides: the worst status, then the lowest first non-pass point,
    then the highest peak."""
    return max(assessments, key=lambda a: (_ORDER[a.status], -(a.first_bad_ghz if a.first_bad_ghz is not None else 1e9),
                                           a.peak))
