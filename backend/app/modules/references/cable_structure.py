"""Structured cable reference (owner 10.10.2026: «наведи порядок в базах коаксиалов, монтажных проводов и сечений»).

Three structures beside the БД.ПРОВ / СПР.КАБ rows the ETK generator, the harness catalogue and the warehouse read
(those rows stay as they are):
- «Сечения AWG ↔ мм²» — one row per conductor size: AWG (or none for a ГОСТ-only size), section, conductor Ø,
  strand pattern. Its whole-AWG rows are the workshop ГОСТ section → AWG map (`gost_awg` entries are kept in step).
- «Монтажные провода» — one row per БД.ПРОВ wire (brand + section): series, the conductor as a reference to a size
  row, insulation, outer Ø, voltage, temperature range. Joined to the БД.ПРОВ row by its mark (`cable_key`), so a
  mirror reload neither drops nor orphans it.
- «Коаксиальные кабели» — a card per cable: construction layers, connectors with their limit, the VNA span narrowed
  per connector, control markers with the acceptance VSWR and attenuation, f_c, notes, sources, confidence.

Pure rules only; storage is `infrastructure.py`, flows are `application.py`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from .domain import ReferenceValidationError, cable_key

# --- numbers ---

_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_LEADING_NUMBER = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)")


def decimal_or_none(value: object) -> Decimal | None:
    """A whole cell that is a number («2,45», «-60») → Decimal; anything else (empty, «4,8×2,34», text) → None."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    text = str(value or "").strip()
    if not _NUMBER.match(text):
        return None
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def leading_int(value: object) -> int | None:
    """«48 (ном.) / 1000 (испыт.)» → 48, «-60» → -60; no leading number → None."""
    match = _LEADING_NUMBER.match(str(value or ""))
    if not match:
        return None
    return int(Decimal(match.group(1).replace(",", ".")).to_integral_value())


def shown(value: Decimal | int | None) -> str:
    """A number as the sheets show it: comma decimal, no trailing zeros («0,5», «12»)."""
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def positive(name: str, value: object, *, required: bool = False) -> Decimal | None:
    number = decimal_or_none(value)
    if number is None:
        if required or str(value or "").strip():
            raise ReferenceValidationError(f"{name}: укажите число больше нуля")
        return None
    if number <= 0:
        raise ReferenceValidationError(f"{name}: укажите число больше нуля")
    return number


# --- «Сечения AWG ↔ мм²» ---

_AWG = re.compile(r"^(\d{1,2}|[1-4]/0)$")


@dataclass(frozen=True)
class WireGauge:
    id: UUID
    awg: str | None  # «28», «1/0»; None — a ГОСТ-only size (0,07 мм²)
    section_mm2: Decimal
    conductor_d_mm: Decimal | None
    strands: str
    position: int

    @property
    def awg_number(self) -> int | None:
        return int(self.awg) if self.awg and self.awg.isdigit() else None

    @property
    def label(self) -> str:
        section = f"{shown(self.section_mm2)} мм²"
        return f"{self.awg} AWG · {section}" if self.awg else section

    @classmethod
    def create(cls, *, awg: str, section_mm2: object, conductor_d_mm: object = "", strands: str = "",
               position: int = 0, id: UUID | None = None) -> WireGauge:
        clean_awg = str(awg or "").strip().upper().removesuffix("AWG").strip()
        if clean_awg and not _AWG.match(clean_awg):
            raise ReferenceValidationError("AWG: целое число (28) или 1/0…4/0; пусто — размер только по ГОСТ")
        if clean_awg.isdigit() and not 1 <= int(clean_awg) <= 40:
            raise ReferenceValidationError("AWG укажите целым числом от 1 до 40")
        section = positive("Сечение, мм²", section_mm2, required=True)
        diameter = positive("Ø жилы, мм", conductor_d_mm)
        return cls(id or uuid4(), str(int(clean_awg)) if clean_awg.isdigit() else (clean_awg or None), section, diameter,
                   str(strands or "").strip(), position)


def section_key(value: Decimal) -> str:
    """The `gost_awg` map key of a section (`domain.normalized_section_key`)."""
    return format(value.normalize(), "f")


def gauge_gost_awg(gauges: Iterable[WireGauge]) -> dict[str, int]:
    """Section → AWG of every whole-AWG size row: what `gost_awg_map` must return for those sections."""
    return {section_key(gauge.section_mm2): gauge.awg_number for gauge in gauges if gauge.awg_number is not None}


def validate_gauges(gauges: Sequence[WireGauge]) -> None:
    awgs = [gauge.awg for gauge in gauges if gauge.awg]
    if len(awgs) != len(set(awgs)):
        raise ReferenceValidationError("Такой AWG уже есть в таблице сечений")
    sections = [gauge.section_mm2 for gauge in gauges]
    if len(sections) != len(set(sections)):
        raise ReferenceValidationError("Такое сечение уже есть в таблице сечений")


# --- «Монтажные провода» ---

MARK_FIELD = "Марка"
SECTION_FIELD = "Сечение, мм2"
AWG_FIELD = "AWG"
OUTER_FIELD = "Наружный диаметр, мм"
INSULATION_FIELD = "Изоляция"
VOLTAGE_FIELD = "Напряжение, В"
TEMP_MIN_FIELD = "Темп. от, °C"
TEMP_MAX_FIELD = "Темп. до, °C"
# БД.ПРОВ cells a hook-up wire's structure stands for: a save writes them back, an edit of them updates the structure.
MIRRORED_FIELDS = (SECTION_FIELD, AWG_FIELD, OUTER_FIELD, INSULATION_FIELD, VOLTAGE_FIELD, TEMP_MIN_FIELD, TEMP_MAX_FIELD)

_MARK_AWG = re.compile(r"(\d{1,2})\s*AWG", re.IGNORECASE)
_SIZE_TAIL = re.compile(r"\s+(?:\d{1,2}\s*AWG|\d+\s*[хx×]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:мм2|мм²|мм)?)\s*$", re.IGNORECASE)
_SECTION_TAIL = re.compile(r"(?:^|\s|[хx×])(\d+(?:[.,]\d+)?)\s*(?:мм2|мм²)?\s*$", re.IGNORECASE)


def wire_series(mark: str) -> str:
    """The brand/series of a wire mark without its size: «UL1061 28AWG» → «UL1061», «НВ-4 0,20» → «НВ-4»,
    «ПуГВнг(А)-LS 1х1,0» → «ПуГВнг(А)-LS». A mark that is only a size, or has none, stays whole."""
    text = " ".join(str(mark or "").split())
    for _ in range(2):
        stripped = _SIZE_TAIL.sub("", text)
        if not stripped or stripped == text:
            break
        text = stripped
    return text


@dataclass(frozen=True)
class HookupWire:
    id: UUID
    mark: str
    mark_key: str
    series: str
    gauge_id: UUID
    insulation: str = ""
    outer_d_mm: Decimal | None = None
    voltage_v: int | None = None
    temp_min_c: int | None = None
    temp_max_c: int | None = None
    conductor_material: str = ""  # «медь луженая»; '' — not known yet
    insulation_thickness_mm: Decimal | None = None

    @classmethod
    def create(cls, *, mark: str, series: str, gauge_id: UUID, insulation: str = "", outer_d_mm: object = None,
               voltage_v: object = None, temp_min_c: object = None, temp_max_c: object = None,
               conductor_material: str = "", insulation_thickness_mm: object = None,
               id: UUID | None = None) -> HookupWire:
        clean_mark = str(mark or "").strip()
        key = cable_key(clean_mark)
        if not key:
            raise ReferenceValidationError("Марка провода обязательна")
        clean_series = " ".join(str(series or "").split())
        if not clean_series:
            raise ReferenceValidationError("Серия провода обязательна (ПВАМ, МГТФ, UL1007…)")
        voltage = _int_or_none("Напряжение, В", voltage_v)
        if voltage is not None and voltage <= 0:
            raise ReferenceValidationError("Напряжение, В: укажите число больше нуля")
        low, high = _int_or_none("Темп. от, °C", temp_min_c), _int_or_none("Темп. до, °C", temp_max_c)
        if low is not None and high is not None and low >= high:
            raise ReferenceValidationError("Температура «от» должна быть ниже «до»")
        return cls(id or uuid4(), clean_mark, key, clean_series, gauge_id, " ".join(str(insulation or "").split()),
                   positive("Наружный Ø, мм", outer_d_mm), voltage, low, high, " ".join(str(conductor_material or "").split()),
                   positive("Толщина изоляции, мм", insulation_thickness_mm))


def _int_or_none(name: str, value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    number = decimal_or_none(value)
    if number is None or number != number.to_integral_value():
        raise ReferenceValidationError(f"{name}: укажите целое число")
    return int(number)


@dataclass(frozen=True)
class HookupParse:
    """What a БД.ПРОВ row says about its structure. `gauge` None → not parsable; `problem` says why."""
    series: str
    gauge: WireGauge | None
    insulation: str
    outer_d_mm: Decimal | None
    voltage_v: int | None
    temp_min_c: int | None
    temp_max_c: int | None
    problem: str = ""


def match_gauge(mark: str, fields: Mapping[str, str], gauges: Sequence[WireGauge]) -> tuple[WireGauge | None, str]:
    """The size row of a wire, first rule that fits: the AWG cell, the AWG in the mark («28AWG»), the section cell,
    the section at the end of the mark («0,20», «1х1,0», «0,75мм2»). AWG wins over the section (UL1015 26AWG says
    0,13 мм² — the size row of 26 AWG is 0,12 мм² by ГОСТ)."""
    by_awg = {gauge.awg: gauge for gauge in gauges if gauge.awg}
    by_section = {gauge.section_mm2: gauge for gauge in gauges}
    awg_cell = str(fields.get(AWG_FIELD) or "").strip()
    mark_awg = _MARK_AWG.search(mark)
    awg = awg_cell if awg_cell.isdigit() else (mark_awg.group(1) if mark_awg else "")
    if awg:
        awg = str(int(awg))
        return (by_awg[awg], "") if awg in by_awg else (None, f"{awg} AWG нет в таблице сечений")
    section = decimal_or_none(fields.get(SECTION_FIELD))
    if section is None:
        tail = _SECTION_TAIL.search(mark)
        section = decimal_or_none(tail.group(1)) if tail else None
    if section is None or section <= 0:
        return None, "не указаны ни AWG, ни сечение"
    gauge = by_section.get(section)
    return (gauge, "") if gauge else (None, f"сечения {shown(section)} мм² нет в таблице сечений")


def parse_hookup(mark: str, fields: Mapping[str, str], gauges: Sequence[WireGauge]) -> HookupParse:
    gauge, problem = match_gauge(mark, fields, gauges)
    low, high = leading_int(fields.get(TEMP_MIN_FIELD)), leading_int(fields.get(TEMP_MAX_FIELD))
    if low is not None and high is not None and low >= high:
        low = high = None
    voltage = leading_int(fields.get(VOLTAGE_FIELD))
    outer = decimal_or_none(fields.get(OUTER_FIELD))
    return HookupParse(
        series=wire_series(mark), gauge=gauge, insulation=" ".join(str(fields.get(INSULATION_FIELD) or "").split()),
        outer_d_mm=outer if outer is not None and outer > 0 else None,
        voltage_v=voltage if voltage is not None and voltage > 0 else None,
        temp_min_c=low, temp_max_c=high, problem=problem,
    )


def hookup_from_parse(mark: str, parsed: HookupParse) -> HookupWire | None:
    if parsed.gauge is None or not parsed.series:
        return None
    return HookupWire.create(
        mark=mark, series=parsed.series, gauge_id=parsed.gauge.id, insulation=parsed.insulation,
        outer_d_mm=parsed.outer_d_mm, voltage_v=parsed.voltage_v, temp_min_c=parsed.temp_min_c,
        temp_max_c=parsed.temp_max_c,
    )


def written_back_fields(fields: Mapping[str, str], wire: HookupWire, gauge: WireGauge,
                        gauges: Sequence[WireGauge]) -> dict[str, str]:
    """БД.ПРОВ cells to change so the row says what the structure says. A cell already meaning the same value stays
    as typed («48 (ном.) / 1000 (испыт.)» is 48 V, «2,45» is 2,45 мм): only a changed value is written."""
    parsed = parse_hookup(wire.mark, fields, gauges)
    changes: dict[str, str] = {}
    if parsed.gauge is None or parsed.gauge.id != gauge.id:
        changes[SECTION_FIELD] = shown(gauge.section_mm2)
        changes[AWG_FIELD] = gauge.awg or ""
    if parsed.outer_d_mm != wire.outer_d_mm:
        changes[OUTER_FIELD] = shown(wire.outer_d_mm)
    if parsed.insulation != wire.insulation:
        changes[INSULATION_FIELD] = wire.insulation
    if parsed.voltage_v != wire.voltage_v:
        changes[VOLTAGE_FIELD] = shown(wire.voltage_v)
    if (parsed.temp_min_c, parsed.temp_max_c) != (wire.temp_min_c, wire.temp_max_c):
        changes[TEMP_MIN_FIELD] = shown(wire.temp_min_c)
        changes[TEMP_MAX_FIELD] = shown(wire.temp_max_c)
    return {name: value for name, value in changes.items() if str(fields.get(name) or "").strip() != value}


def refreshed_hookup(wire: HookupWire, mark: str, fields: Mapping[str, str], edited: set[str],
                     gauges: Sequence[WireGauge]) -> HookupWire:
    """A БД.ПРОВ edit of the mirrored cells moves the structure with it; a size the table does not know keeps the
    old size row (the structure never loses its conductor)."""
    parsed = parse_hookup(mark, fields, gauges)
    values: dict[str, Any] = {
        "mark": mark, "series": wire.series, "gauge_id": wire.gauge_id, "insulation": wire.insulation,
        "outer_d_mm": wire.outer_d_mm, "voltage_v": wire.voltage_v, "temp_min_c": wire.temp_min_c,
        "temp_max_c": wire.temp_max_c, "conductor_material": wire.conductor_material,
        "insulation_thickness_mm": wire.insulation_thickness_mm,
    }
    if SECTION_FIELD in edited and AWG_FIELD not in edited:
        # the section was changed on its own: it decides, not the AWG cell left from before
        section = decimal_or_none(fields.get(SECTION_FIELD))
        by_section = next((gauge for gauge in gauges if gauge.section_mm2 == section), None)
        if by_section is not None:
            values["gauge_id"] = by_section.id
    elif edited & {SECTION_FIELD, AWG_FIELD} and parsed.gauge is not None:  # a renamed mark keeps its size row
        values["gauge_id"] = parsed.gauge.id
    if OUTER_FIELD in edited:
        values["outer_d_mm"] = parsed.outer_d_mm
    if INSULATION_FIELD in edited:
        values["insulation"] = parsed.insulation
    if VOLTAGE_FIELD in edited:
        values["voltage_v"] = parsed.voltage_v
    if edited & {TEMP_MIN_FIELD, TEMP_MAX_FIELD}:
        values["temp_min_c"], values["temp_max_c"] = parsed.temp_min_c, parsed.temp_max_c
    return HookupWire.create(id=wire.id, **values)


# --- «Коаксиальные кабели» ---

LAYERS = ("core", "dielectric", "shield", "jacket")
LAYER_LABELS = {"core": "Жила", "dielectric": "Диэлектрик", "shield": "Экран", "jacket": "Оболочка"}
# Card parameters a technologist, buyer or tester asks for; filled only from the seed file, never guessed. The number is
# what a calculation reads; `param_notes` keeps the datasheet wording («50 ±2», «>165 (EZ); >100 (Smiths)»).
PARAMS = (
    "impedance_ohm", "velocity_pct", "capacitance_pf_m", "shielding_db", "bend_static_mm", "bend_dynamic_mm",
    "temp_min_c", "temp_max_c", "voltage_max_v", "max_power_w", "max_power_at_mhz", "weight_g_m",
)
_SIGNED_PARAMS = {"temp_min_c", "temp_max_c"}


@dataclass(frozen=True)
class CoaxLayer:
    layer: str
    material: str
    diameter_mm: Decimal | None


@dataclass(frozen=True)
class CoaxConnector:
    family: str
    max_ghz: Decimal | None
    max_note: str
    span_to_ghz: Decimal | None  # the assembly's VNA span with this connector, when it narrows the cable's


@dataclass(frozen=True)
class CoaxMarker:
    freq_mhz: Decimal | None  # None — the «Peak» marker (max VSWR over the span)
    vswr_max: Decimal | None  # acceptance norm proposed for the shop's VNA check, not a datasheet value
    note: str = ""
    vswr_ref: Decimal | None = None  # the VSWR a reference assembly's datasheet guarantees, to settle a disputed reject


# Attenuation as the datasheet publishes it (owner 10.10.2026): the value in its unit, per reference length, at a
# temperature, nominal / typical / max, with its source; dB/m is derived from it so an assembly's loss can be computed
# later. A point the datasheet does not publish (a marker between two published ones) is `interpolated`, with its basis.
ATTENUATION_UNITS = {
    "dB/m": Decimal(1), "dB/100m": Decimal(100), "dB/km": Decimal(1000), "dB/100ft": Decimal("30.48"),
    "dB/ft": Decimal("0.3048"),
}
ATTENUATION_KINDS = ("nominal", "typical", "max")
_UNIT_ALIASES = {
    "db/m": "dB/m", "дб/м": "dB/m", "db/100m": "dB/100m", "дб/100м": "dB/100m", "db/km": "dB/km", "дб/км": "dB/km",
    "db/100ft": "dB/100ft", "дб/100фут": "dB/100ft", "db/ft": "dB/ft",
}


@dataclass(frozen=True)
class CoaxAttenuation:
    freq_mhz: Decimal
    kind: str  # nominal | typical | max
    value: Decimal  # as published, in `unit`
    unit: str
    ref_length_m: Decimal  # the length the unit refers to (100 ft = 30,48 м)
    temp_c: Decimal | None
    loss_db_m: Decimal  # derived: value / ref_length_m
    derived: bool  # the dB/m is computed from another unit
    interpolated: bool  # not published at this frequency: interpolated / extrapolated / by the maker's formula
    source: str = ""
    basis: str = ""  # how the value was obtained («интерполировано (лог-лог) между 400 и 1000 МГц»), the datasheet type


def attenuation_kind(raw: object) -> str:
    """«typical (EZ, по M17/133)» → typical, «nominal (max ≤115 %)» → nominal, «max» → max."""
    text = _text(raw).casefold()
    return next((kind for kind in ATTENUATION_KINDS if text.startswith(kind)), text or "nominal")


def attenuation_point(name: str, freq: Decimal, kind: str, value: object, unit: object, *, temp_c: object = None,
                      interpolated: bool = False, source: str = "", ref_length_m: object = None,
                      basis: str = "") -> CoaxAttenuation:
    clean_unit = _UNIT_ALIASES.get(re.sub(r"\s", "", str(unit or "dB/m")).casefold())
    if clean_unit is None:
        raise ReferenceValidationError(f"«{name}»: единица затухания «{unit}» — нужна dB/m, dB/100m, dB/km, dB/100ft или dB/ft")
    if kind not in ATTENUATION_KINDS:
        raise ReferenceValidationError(f"«{name}»: затухание «{kind}» — номинальное, типовое или максимальное")
    number = positive(f"«{name}», затухание", value, required=True)
    length = positive(f"«{name}», длина для затухания", ref_length_m) or ATTENUATION_UNITS[clean_unit]
    return CoaxAttenuation(
        freq_mhz=freq, kind=kind, value=number, unit=clean_unit, ref_length_m=length,
        temp_c=decimal_or_none(temp_c), loss_db_m=(number / length).quantize(Decimal("0.0001")),
        derived=clean_unit != "dB/m" or length != 1, interpolated=bool(interpolated), source=_text(source),
        basis=_text(basis),
    )


# Another name a coax cable is known by (owner 12.10.2026, «Коаксиальные кабели: алиасы + полное название»): the short
# mark and its spellings («RG316», «RG1.37»), a supplier's full name («Кабель RG-316 50 Ом»), a supplier's article
# («1951», with the supplier and the page). The key is the generator's cable key; it is unique across all cables.
ALIAS_KINDS = ("mark", "full_name", "supplier_article")


@dataclass(frozen=True)
class CoaxAlias:
    alias: str
    alias_key: str
    kind: str  # mark | full_name | supplier_article
    supplier: str = ""
    url: str = ""


@dataclass(frozen=True)
class CoaxCard:
    name: str
    name_key: str
    family: str
    layers: tuple[CoaxLayer, ...]
    connectors: tuple[CoaxConnector, ...]
    markers: tuple[CoaxMarker, ...]
    attenuation: tuple[CoaxAttenuation, ...]
    span_from_mhz: Decimal | None
    span_to_ghz: Decimal | None
    span_note: str
    fc_ghz: Decimal | None  # cut-off of the first higher mode (TE11): physics, not the rated limit
    notes: str
    sources: tuple[str, ...]
    confidence: str
    verified: bool
    changes: tuple[str, ...]
    params: Mapping[str, Decimal] = field(default_factory=dict)
    position: int = 0
    id: UUID = field(default_factory=uuid4)
    fc_note: str = ""  # how f_c was obtained («calc (D 1.52, d 0.51, Vp 69.5 %)»)
    max_operating_ghz: Decimal | None = None  # the rated maximum operating frequency (datasheet), ≠ f_c
    max_operating_note: str = ""
    param_notes: Mapping[str, str] = field(default_factory=dict)
    # From the supplier file (`coax-articles.json`), not the card file: the card load never changes them.
    full_name: str = ""
    aliases: tuple[CoaxAlias, ...] = ()


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _freq_mhz(item: Mapping[str, Any]) -> Decimal | None | str:
    raw = item.get("freq_mhz", item.get("freq"))
    if isinstance(raw, str) and raw.strip().casefold() in {"peak", "пик"}:
        return "peak"
    if raw is None and item.get("freq_ghz") is not None:
        ghz = decimal_or_none(item.get("freq_ghz"))
        return ghz * 1000 if ghz is not None else None
    return decimal_or_none(raw)


def _change_text(change: object) -> str:
    """A verifier's change {field, was, now, why} → one readable line; a string stays as it is."""
    if isinstance(change, Mapping):
        head = _text(change.get("field"))
        body = f"было «{_text(change.get('was'))}», стало «{_text(change.get('now'))}»"
        why = _text(change.get("why"))
        return f"{head}: {body}" + (f" — {why}" if why else "")
    return _text(change)


def card_from_seed(item: Mapping[str, Any], position: int) -> CoaxCard:
    """One cable of the seed file (`backend/data/cables/coax-cables.json`) → a validated card. One cable per entry:
    a combined entry («RG-142 / RG-400») is refused, the file must split it."""
    name = _text(item.get("name"))
    if not name:
        raise ReferenceValidationError(f"Кабель №{position + 1}: нет названия")
    if " / " in name:
        raise ReferenceValidationError(f"«{name}»: одна строка — один кабель, разделите запись")
    construction = item.get("construction") or {}
    layers = tuple(
        CoaxLayer(layer, _text((construction.get(layer) or {}).get("material") or (construction.get(layer) or {}).get("type")),
                  positive(f"«{name}», {LAYER_LABELS[layer]} Ø", (construction.get(layer) or {}).get("d_mm")))
        for layer in LAYERS if construction.get(layer)
    )
    span = item.get("vna_span") or {}
    narrowing = {cable_key(_text(rule.get("connector") or rule.get("family"))): rule
                 for rule in span.get("narrowing") or [] if isinstance(rule, Mapping)}
    span_notes = [_text(rule) for rule in span.get("narrowing") or [] if isinstance(rule, str)]
    connectors = []
    for connector in item.get("connectors") or []:
        family = _text(connector.get("family"))
        if not family:
            continue
        rule = narrowing.pop(cable_key(family), None)
        connectors.append(CoaxConnector(
            family, positive(f"«{name}», {family}, ГГц", connector.get("max_ghz")),
            _text(connector.get("note") or connector.get("note_ru")),
            positive(f"«{name}», {family}, предел сборки", rule.get("to_ghz")) if rule else None,
        ))
    span_notes += [f"{_text(rule.get('connector') or rule.get('family'))}: до {_text(rule.get('to_ghz'))} ГГц"
                   for rule in narrowing.values()]
    if _text(span.get("note")):
        span_notes.insert(0, _text(span.get("note")))
    markers, seen = [], set()
    attenuation: dict[tuple[Decimal, str, str], CoaxAttenuation] = {}  # (freq, kind, source): two sources may publish one freq
    default_temp = item.get("attenuation_temp_c", 20)
    for point in item.get("attenuation") or []:
        freq = _freq_mhz(point)
        if not isinstance(freq, Decimal) or freq <= 0:
            raise ReferenceValidationError(f"«{name}»: у точки затухания нет частоты")
        given = [(kind, point.get(kind)) for kind in ATTENUATION_KINDS if point.get(kind) is not None]
        for kind, value in given or [(attenuation_kind(point.get("kind")), point.get("value"))]:
            if (freq, kind, _text(point.get("source"))) in attenuation:
                raise ReferenceValidationError(f"«{name}»: затухание на {shown(freq)} МГц повторяется")
            attenuation[(freq, kind, _text(point.get("source")))] = attenuation_point(
                name, freq, kind, value, point.get("unit"), temp_c=point.get("temp_c", default_temp),
                interpolated=bool(point.get("interpolated")), source=_text(point.get("source")),
                ref_length_m=point.get("ref_length_m"), basis=_text(point.get("basis")),
            )
    for marker in item.get("markers") or []:
        freq = _freq_mhz(marker)
        if freq is None:
            raise ReferenceValidationError(f"«{name}»: маркер без частоты")
        if freq != "peak" and freq <= 0:
            raise ReferenceValidationError(f"«{name}»: частота маркера должна быть больше нуля")
        key = "peak" if freq == "peak" else freq
        if key in seen:
            raise ReferenceValidationError(f"«{name}»: маркер {freq} повторяется")
        seen.add(key)
        vswr = decimal_or_none(marker.get("vswr_max"))
        reference = decimal_or_none(marker.get("vswr_ref_assembly"))
        if (vswr is not None and vswr < 1) or (reference is not None and reference < 1):
            raise ReferenceValidationError(f"«{name}»: КСВН не может быть меньше 1")
        loss = marker.get("attenuation_db_m")
        # A marker's own dB/m becomes a dB/m point unless the file publishes one at that frequency; with a basis
        # («интерполировано…», «по формуле…») it is marked as not published.
        if isinstance(freq, Decimal) and loss not in (None, "") and not any(key[0] == freq for key in attenuation):
            basis = _text(marker.get("attenuation_basis_ru") or marker.get("attenuation_basis"))
            attenuation[(freq, "nominal", "")] = attenuation_point(
                name, freq, "nominal", loss, "dB/m", temp_c=default_temp, source=_text(marker.get("source")),
                interpolated=bool(basis) and not basis.casefold().startswith("по даташиту"), basis=basis)
        markers.append(CoaxMarker(None if freq == "peak" else freq, vswr,
                                  _text(marker.get("note") or marker.get("vswr_check_ru")), reference))
    for point in item.get("extra_attenuation") or []:  # dB/m at a frequency that is not a marker
        freq = _freq_mhz(point)
        if isinstance(freq, Decimal) and not any(key[0] == freq for key in attenuation):
            attenuation[(freq, "nominal", "")] = attenuation_point(
                name, freq, "nominal", point.get("attenuation_db_m"), "dB/m", temp_c=default_temp)
    params: dict[str, Decimal] = {}
    for key in PARAMS:
        value = decimal_or_none((item.get("params") or {}).get(key, item.get(key)))
        if value is None:
            continue
        if key not in _SIGNED_PARAMS and value <= 0:
            raise ReferenceValidationError(f"«{name}», {key}: укажите число больше нуля")
        params[key] = value
    if "velocity_pct" in params and params["velocity_pct"] > 100:
        raise ReferenceValidationError(f"«{name}»: коэффициент укорочения больше 100 %")
    if {"temp_min_c", "temp_max_c"} <= set(params) and params["temp_min_c"] >= params["temp_max_c"]:
        raise ReferenceValidationError(f"«{name}»: температура «от» должна быть ниже «до»")
    span_from = positive(f"«{name}», начало диапазона", span.get("from_mhz"))
    span_to = positive(f"«{name}», конец диапазона", span.get("to_ghz"))
    if span_from is not None and span_to is not None and span_from >= span_to * 1000:
        raise ReferenceValidationError(f"«{name}»: начало диапазона выше конца")
    confidence = _text(item.get("confidence"))
    return CoaxCard(
        name=name, name_key=cable_key(name), family=_text(item.get("family")), layers=layers,
        connectors=tuple(connectors), markers=tuple(markers),
        attenuation=tuple(sorted(attenuation.values(), key=lambda point: (point.freq_mhz, point.kind))),
        span_from_mhz=span_from, span_to_ghz=span_to,
        span_note="; ".join(note for note in span_notes if note), fc_ghz=positive(f"«{name}», f_c", item.get("fc_ghz")),
        notes=str(item.get("notes_ru") or item.get("notes") or "").strip(),
        sources=tuple(_text(source) for source in item.get("sources") or [] if _text(source)),
        confidence=confidence, verified=bool(item.get("verified", False)),
        changes=tuple(_change_text(change) for change in item.get("changed") or [] if _change_text(change)),
        params=params, position=position, fc_note=_text(item.get("fc_note") or item.get("fc_basis")),
        max_operating_ghz=positive(f"«{name}», рабочая частота", item.get("max_operating_ghz")),
        max_operating_note=_text(item.get("max_operating_note")),
        param_notes={_text(key): _text(value) for key, value in (item.get("param_notes") or {}).items() if _text(value)},
    )


def cards_from_seed(document: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[CoaxCard]:
    """The seed file: a list of cables, or {"verified": bool, "cables": [...]} — `verified` marks every card whose
    entry does not say otherwise. Names are unique by the generator's cable key."""
    items = document.get("cables", []) if isinstance(document, Mapping) else document
    verified = bool(document.get("verified", False)) if isinstance(document, Mapping) else False
    cards = [card_from_seed({"verified": verified, **item}, position) for position, item in enumerate(items)]
    keys = [card.name_key for card in cards]
    duplicates = sorted({card.name for card in cards if keys.count(card.name_key) > 1})
    if duplicates:
        raise ReferenceValidationError("Кабель повторяется в файле: " + ", ".join(duplicates))
    return cards


def same_card(stored: CoaxCard, seed: CoaxCard) -> bool:
    """The stored card already says what the file says (its id, full name and aliases are not the file's). Attenuation
    is compared as a set: points of one frequency and kind from two sources have no order of their own."""
    def points(card: CoaxCard) -> list:
        return sorted(card.attenuation, key=lambda point: (point.freq_mhz, point.kind, point.source))

    seed = replace(seed, id=stored.id, full_name=stored.full_name, aliases=stored.aliases)
    return replace(seed, attenuation=()) == replace(stored, attenuation=()) and points(seed) == points(stored)


def card_for(key: str, cards: Mapping[str, CoaxCard]) -> tuple[CoaxCard | None, bool]:
    """The card of a cable row: by its exact key, then by one of a card's aliases (another name of the same cable —
    exact too), else the family card («RG-58» for «RG-58 A/U», `cables.family_dia` rule — a non-digit after the
    prefix), which is only a suggestion. Returns (card, exact)."""
    if key in cards:
        return cards[key], True
    by_alias = next((card for card in cards.values() if any(alias.alias_key == key for alias in card.aliases)), None)
    if by_alias is not None:
        return by_alias, True
    best = ""
    for candidate in cards:
        if len(candidate) >= 3 and len(candidate) > len(best) and key.startswith(candidate) and not key[len(candidate):][:1].isdigit():
            best = candidate
    return (cards[best], False) if best else (None, False)


# --- «Коаксиальные кабели»: full names and aliases from the supplier file ---

ALIAS_MAX = 500  # coax_cable_aliases.alias / alias_key
URL_MAX = 1000


@dataclass(frozen=True)
class CoaxNames:
    """What the supplier file gives one card: its full name and its other names."""
    full_name: str = ""
    aliases: tuple[CoaxAlias, ...] = ()


@dataclass(frozen=True)
class ArticlesPlan:
    names: dict[str, CoaxNames]  # card name key → names; every card, an empty CoaxNames when the file has none
    no_card: tuple[str, ...]  # marks of the file no card is named as: their names wait until the card exists
    refused: tuple[str, ...]  # «alias»: why it is not loaded
    family_only: int  # offers of the family only («у поставщика RG-58 без суффикса») — never an alias


def _alias(text: object, kind: str, supplier: object = "", url: object = "") -> CoaxAlias:
    clean = _text(text)
    return CoaxAlias(clean, cable_key(clean), kind, _text(supplier), _text(url))


def names_from_articles(document: Mapping[str, Any], cards: Sequence[CoaxCard]) -> ArticlesPlan:
    """The supplier file (`backend/data/cables/coax-articles.json`: cables[{mark, aliases[], offers[{supplier, article,
    full_name, url, family_only}]}]) → the full name and aliases of each card.

    An entry belongs to the card named as its mark or as one of its spellings — never to a family card («RG-58 A/U»
    is not «RG-58»). Its aliases: the mark and its spellings, then the full name and the article of every offer of
    this very cable (a family-only offer is not one). The full name of a card is the first such offer's. An alias
    that is only the card's own key is dropped as a repeat. Refused, with the reason: an alias that is the name of
    another card, or one two cables of the file both claim (an article of «5D-FB» and of «5D-FB Cu» — while only one
    of them has a card, it is that card's)."""
    by_key = {card.name_key: card for card in cards}
    entries = [entry for entry in document.get("cables") or [] if isinstance(entry, Mapping)]
    owners: list[str | None] = []
    candidates: list[list[CoaxAlias]] = []
    full_names: list[str] = []
    family_only = 0
    for entry in entries:
        spellings = [_alias(entry.get("mark"), "mark")] + [_alias(item, "mark") for item in entry.get("aliases") or []]
        owners.append(next((alias.alias_key for alias in spellings if alias.alias_key in by_key), None))
        found = [alias for alias in spellings if alias.alias_key]
        first_name = ""
        for offer in entry.get("offers") or []:
            if not isinstance(offer, Mapping):
                continue
            if offer.get("family_only"):
                family_only += 1
                continue
            name = _alias(offer.get("full_name"), "full_name", offer.get("supplier"), offer.get("url"))
            article = _alias(offer.get("article"), "supplier_article", offer.get("supplier"), offer.get("url"))
            found += [alias for alias in (name, article) if alias.alias_key]
            first_name = first_name or name.alias
        candidates.append(found)
        full_names.append(first_name)
    claims: dict[str, set[str | None]] = {}
    for owner, found in zip(owners, candidates):
        for alias in found:
            claims.setdefault(alias.alias_key, set()).add(owner)
    names: dict[str, CoaxNames] = {key: CoaxNames() for key in by_key}
    refused: dict[str, str] = {}
    no_card: list[str] = []
    for entry, owner, found, full_name in zip(entries, owners, candidates, full_names):
        if owner is None:
            no_card.append(_text(entry.get("mark")) or "(без марки)")
            continue
        kept = list(names[owner].aliases)
        for alias in found:
            if alias.alias_key == owner or any(item.alias_key == alias.alias_key for item in kept):
                continue
            problem = _alias_problem(alias, owner, by_key, claims)
            if problem:
                refused.setdefault(alias.alias, problem)
                continue
            kept.append(alias)
        names[owner] = CoaxNames(names[owner].full_name or full_name, tuple(kept))
    return ArticlesPlan(names, tuple(no_card), tuple(f"«{alias}»: {why}" for alias, why in refused.items()), family_only)


def _alias_problem(alias: CoaxAlias, owner: str, by_key: Mapping[str, CoaxCard], claims: Mapping[str, set]) -> str:
    if alias.alias_key in by_key:
        return f"это название другого кабеля «{by_key[alias.alias_key].name}»"
    with_cards = sorted(key for key in claims[alias.alias_key] if key in by_key)
    if len(with_cards) > 1:
        return "так названы разные кабели: " + ", ".join(by_key[key].name for key in with_cards)
    if len(alias.alias) > ALIAS_MAX or len(alias.url) > URL_MAX:
        return f"длиннее {ALIAS_MAX} знаков (ссылка — {URL_MAX})"
    if alias.kind == "supplier_article" and not alias.supplier:
        return "у артикула не указан поставщик"
    return ""


def validate_aliases(names: Mapping[str, CoaxNames], cards: Sequence[CoaxCard]) -> None:
    """One cable per alias key, and never the name of another cable — what the loader refuses before writing (the
    UNIQUE key in the base refuses the first case too)."""
    by_key = {card.name_key: card for card in cards}
    seen: dict[str, str] = {}
    for owner, item in names.items():
        for alias in item.aliases:
            if alias.kind not in ALIAS_KINDS or not alias.alias_key:
                raise ReferenceValidationError(f"«{alias.alias}»: вид алиаса — марка, полное название или артикул")
            if alias.alias_key in by_key and alias.alias_key != owner:
                raise ReferenceValidationError(f"«{alias.alias}» — это название другого кабеля «{by_key[alias.alias_key].name}»")
            if seen.setdefault(alias.alias_key, owner) != owner:
                raise ReferenceValidationError(f"«{alias.alias}» уже есть у кабеля «{by_key[seen[alias.alias_key]].name}»")


def alias_dia_rows(index: Mapping[str, Mapping[str, str]], cards: Iterable[CoaxCard],
                   diameters: Sequence[str] = ("D1", "D2", "D3")) -> list[dict[str, str]]:
    """One more D-table row per name of a cable that has none of its own: named as that name (the first column, as
    `coaxDiaFromRecords` reads it), with the D1–D3 of the cable — of the row named as the card, else of the first of
    its aliases that has one. `index` — cable key → the D-table row the generator uses. A name with a row of its own
    keeps it: every old key finds what it found before."""
    rows: list[dict[str, str]] = []
    for card in cards:
        names = [(card.name, card.name_key)] + [(alias.alias, alias.alias_key) for alias in card.aliases]
        source = next((index[key] for _name, key in names if key in index), None)
        if source is None:
            continue
        rows += [{"Кабель": name, **{column: str(source.get(column) or "") for column in diameters}}
                 for name, key in names if key not in index]
    return rows


# --- `load_cables --coax-vocabulary`: the БД.ПРОВ coax cells in the words of the coax vocabulary ---

VOCABULARY_SOURCE = "БД.ПРОВ"
PART_SEPARATOR = " / "  # «ПТФЭ (диэлектрик) / ФЭП (оболочка)»: one part per layer in the «Изоляция» cell


@dataclass(frozen=True)
class VocabularyCell:
    mark: str
    mark_key: str
    field: str
    old: str
    new: str


def vocabulary_cells(document: Mapping[str, Any]) -> list[VocabularyCell]:
    """The `map` of `coax-vocabulary.json`, БД.ПРОВ entries → one cell per (cable, column): the old text and the
    vocabulary term. The parts of one cell (`part`: диэлектрик, оболочка) are joined in the file's order with « / »,
    as the cell keeps them. An entry with nothing old and nothing new (an empty cell) changes nothing. The entry's
    `note` (sizes, «по данным продавца») stays in the file."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in document.get("map") or []:
        if not isinstance(entry, Mapping) or _text(entry.get("source")) != VOCABULARY_SOURCE:
            continue
        mark, column = _text(entry.get("cable")), _text(entry.get("field"))
        old, new = _text(entry.get("old")), _text(entry.get("new"))
        if not cable_key(mark) or not column or not (old or new):
            continue
        cell = grouped.setdefault((cable_key(mark), column), {"mark": mark, "old": [], "new": []})
        cell["old"].append(old)
        cell["new"].append(new)
    return [VocabularyCell(cell["mark"], key, column, PART_SEPARATOR.join(item for item in cell["old"] if item),
                           PART_SEPARATOR.join(item for item in cell["new"] if item))
            for (key, column), cell in grouped.items()]


def vocabulary_changes(fields: Mapping[str, str], cells: Iterable[VocabularyCell]) -> tuple[dict[str, str], list[str]]:
    """The cells of one БД.ПРОВ row to rewrite: a cell that still holds the old text gets the term; one that already
    holds the term stays; one changed since (by hand, by the mirror) is not touched and is listed."""
    changes: dict[str, str] = {}
    problems: list[str] = []
    for cell in cells:
        if cell.field not in fields:
            problems.append(f"{cell.mark}: нет столбца «{cell.field}»")
            continue
        current = _text(fields.get(cell.field))
        if current == cell.new:
            continue
        if current == cell.old:
            changes[cell.field] = cell.new
        else:
            problems.append(f"{cell.mark}, {cell.field}: в ячейке «{current}», в словаре было «{cell.old}» — не изменено")
    return changes, problems
