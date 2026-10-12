"""База монтажных проводов из файла (owner 12.10.2026: «нормальная база монтажных проводов UL1061 UL1571 UL1007
UL3239 НВ-4 600 ПВАМ МГТФ МГШВ МС 16-13 МС 15-11 МК 26-13 ПУГВ, полный набор параметров, остальные марки туда не
заноси»).

`import_hookups(uow, payload, actor, now, dry_run)` takes the parsed `backend/data/cables/hookup-wires.json` (not a
path: the CLI `load_cables --hookups` and a future HTTP import call the same function) and returns an `ImportReport`.

Payload (version 1); P = null | {"value": number | string | array, "source": string}, null = leave what is stored:
    {"version": 1,
     "gauges": [{"awg": str | int | null, "section_mm2": number, "conductor_dia_mm": P, "source": str}],
     "series": [{"key": str, "aliases": [str], "standard": P, "conductor_material": P, "insulation_material": P,
                 "voltage_v": P, "temp_min_c": P, "temp_max_c": P, "colors": P,
                 "sizes": [{"mark": str, "awg": str | int | null, "section_mm2": number, "strands": P,
                            "conductor_dia_mm": P, "insulation_thickness_mm": P, "outer_dia_mm": P,
                            optional "voltage_v" / "temp_min_c" / "temp_max_c" / "colors": P (beat the series)}]}],
     "was": [...]}  — «was» is the file author's change log, ignored here.

A file that is not this format at all is refused whole; a bad gauge, series or size is refused alone with its reason
(`ImportReport.refused`) and the rest applies. Steps, all in one transaction (a dry run rolls it back):
1. sizes «Сечения AWG ↔ мм²»: a size the table lacks is added (the `gost_awg` map kept in step); a size the table has
   with other values is only reported — the table is the owner's.
2. sizes of every series: matched to БД.ПРОВ rows by series (`cables.hookup_series`) + size row (AWG or section),
   not by the mark text («МГТФ 0,2» = «МГТФ 0,20»). A matched row keeps its id and mark; the cells the file gives are
   written (only a changed meaning), the row is marked edited in ERP so a mirror reload keeps it; its structure is
   created or updated. A size no row has becomes a new БД.ПРОВ row with its structure. Nothing is renamed or deleted.
3. report: the БД.ПРОВ marks the shape rule calls монтажный that the база монтажных sends to «прочие».
Run twice — the second run changes nothing."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping
from uuid import UUID

from app.shared.identity import Actor

from .application import UnitOfWork, _audit, _search_text, _sync_gost_awg, cable_overrides
from .cable_structure import (
    AWG_FIELD, INSULATION_FIELD, OUTER_FIELD, SECTION_FIELD, TEMP_MAX_FIELD, TEMP_MIN_FIELD, VOLTAGE_FIELD, HookupWire,
    WireGauge, decimal_or_none, leading_int, parse_hookup, shown, validate_gauges,
)
from .cables import CableKind, cable_mark, diameter_index, hookup_series, left_hookups
from .domain import BASE_COLUMNS, ReferenceValidationError, TechOperationRecord, TechOperationTab, cable_key

HOOKUP_SEED_VERSION = 1
TYPE_FIELD = "Тип провода"
STANDARD_FIELD = "Стандарт / производитель"
STRANDS_FIELD = "Жила: проволок × диаметр, мм"
CORES_FIELD = "Число жил"
COLORS_FIELD = "Цвета"
SOURCE_FIELD = "Источник данных"
_SOURCE_SEPARATOR = "; "
# Colour names a datasheet gives in English → the shop's words.
_COLOR_WORDS = {
    "black": "чёрный", "white": "белый", "red": "красный", "blue": "синий", "light blue": "голубой",
    "green": "зелёный", "yellow": "жёлтый", "orange": "оранжевый", "brown": "коричневый", "violet": "фиолетовый",
    "purple": "фиолетовый", "gray": "серый", "grey": "серый", "pink": "розовый", "natural": "натуральный",
    "clear": "прозрачный", "transparent": "прозрачный", "green/yellow": "жёлто-зелёный", "yellow/green": "жёлто-зелёный",
}


class HookupSeedError(ReferenceValidationError):
    """The file is not the format at all (no version 1, no lists): refused whole."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Файл базы монтажных проводов не принят ({len(errors)}): " + "; ".join(errors[:20])
                         + ("; …" if len(errors) > 20 else ""))


@dataclass(frozen=True)
class Sourced:
    value: object  # Decimal | int | str
    source: str


@dataclass(frozen=True)
class SeedGauge:
    awg: str | None
    section_mm2: Decimal
    conductor_d_mm: Sourced | None
    source: str


@dataclass(frozen=True)
class SeedSize:
    mark: str
    awg: str | None
    section_mm2: Decimal
    strands: Sourced | None
    conductor_dia_mm: Sourced | None
    insulation_thickness_mm: Sourced | None
    outer_dia_mm: Sourced | None
    voltage_v: Sourced | None  # the size's own value, else the series'
    temp_min_c: Sourced | None
    temp_max_c: Sourced | None
    colors: Sourced | None


@dataclass(frozen=True)
class SeedSeries:
    key: str  # the база монтажных series key («МС 16-13»)
    standard: Sourced | None
    conductor_material: Sourced | None
    insulation_material: Sourced | None
    sizes: tuple[SeedSize, ...]


@dataclass(frozen=True)
class HookupSeed:
    gauges: tuple[SeedGauge, ...]
    series: tuple[SeedSeries, ...]
    refused: tuple[dict, ...] = ()  # [{"key": mark | size | path, "reason"}] — left out, the rest goes on

    @property
    def sizes(self) -> int:
        return sum(len(item.sizes) for item in self.series)


# --- validation ---

class _Reader:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def get(self, item: Mapping, name: str, path: str, *, required: bool = True):
        if name not in item:
            if required:
                self.fail(f"{path}.{name}", "нет поля")
            return None
        return item[name]

    def number(self, path: str, raw: object, *, signed: bool = False, whole: bool = False):
        if isinstance(raw, bool):
            number = None
        else:
            number = decimal_or_none(raw) if isinstance(raw, (int, float, str)) else None
        if number is None:
            self.fail(path, "нужно число")
            return None
        if whole and number != number.to_integral_value():
            self.fail(path, "нужно целое число")
            return None
        if not signed and number <= 0:
            self.fail(path, "нужно число больше нуля")
            return None
        return int(number) if whole else number

    def awg(self, path: str, raw: object) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            self.fail(path, "AWG — строка, целое число или null")
            return None
        try:
            return WireGauge.create(awg=str(raw), section_mm2="1").awg
        except ReferenceValidationError as error:
            self.fail(path, str(error))
            return None

    def text(self, path: str, raw: object, *, required: bool = True) -> str:
        if isinstance(raw, str) and raw.strip():
            return " ".join(raw.split())
        if required or raw is not None:
            self.fail(path, "нужна непустая строка")
        return ""

    def sourced(self, item: Mapping, name: str, path: str, kind: str, *, required: bool = True) -> Sourced | None:
        """P: null or {"value", "source"}; `kind` — positive | signed_int | positive_int | text | colors."""
        path = f"{path}.{name}"
        if name not in item:
            if required:
                self.fail(path, "нет поля (null — значение не известно)")
            return None
        raw = item[name]
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or "value" not in raw or "source" not in raw:
            self.fail(path, "нужно null или {\"value\", \"source\"}")
            return None
        source = self.text(f"{path}.source", raw["source"])
        value = raw["value"]
        if kind == "positive":
            parsed = self.number(f"{path}.value", value)
        elif kind == "positive_int":
            parsed = self.number(f"{path}.value", value, whole=True)
        elif kind == "signed_int":
            parsed = self.number(f"{path}.value", value, signed=True, whole=True)
        elif kind == "colors":
            parsed = _colors(value) if isinstance(value, (str, list)) else None
            if not parsed:
                self.fail(f"{path}.value", "нужна строка или список цветов")
        else:  # text: a number is kept as the sheet shows it («7×0,127» is text, 1 is «1»)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parsed = shown(Decimal(str(value)))
            else:
                parsed = self.text(f"{path}.value", value)
        if parsed is None or parsed == "" or not source:
            return None
        return Sourced(parsed, source)


def _colors(value: object) -> str:
    items = value if isinstance(value, list) else str(value).split(",")
    words = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            return ""
        word = " ".join(item.split())
        words.append(_COLOR_WORDS.get(word.casefold(), word))
    return ", ".join(words)


def hookup_seed(payload: object, series_of: Callable[[str], str | None] = hookup_series) -> HookupSeed:
    """The parsed file → a checked seed. A file that is not the format at all (no version 1, no `gauges` / `series`
    lists) is refused whole (`HookupSeedError`); a bad gauge, series or size is refused alone with its reason
    (`HookupSeed.refused`) and the rest goes on."""
    if not isinstance(payload, Mapping):
        raise HookupSeedError(["файл: нужен объект JSON"])
    errors = []
    if payload.get("version") != HOOKUP_SEED_VERSION:
        errors.append(f"version: ожидается {HOOKUP_SEED_VERSION}")
    for name in ("gauges", "series"):
        if not isinstance(payload.get(name), list):
            errors.append(f"{name}: нужен список")
    if errors:
        raise HookupSeedError(errors)
    refused: list[dict[str, str]] = []

    def refuse(key: str, reader: _Reader) -> None:
        refused.append({"key": key, "reason": "; ".join(reader.errors)})

    gauges: list[SeedGauge] = []
    for index, item in enumerate(payload["gauges"]):
        path, reader = f"gauges[{index}]", _Reader()
        if not isinstance(item, Mapping):
            reader.fail(path, "нужен объект")
            refuse(path, reader)
            continue
        awg = reader.awg(f"{path}.awg", reader.get(item, "awg", path))
        section = reader.number(f"{path}.section_mm2", reader.get(item, "section_mm2", path))
        diameter = reader.sourced(item, "conductor_dia_mm", path, "positive")
        source = reader.text(f"{path}.source", reader.get(item, "source", path))
        key = (f"{awg} AWG · {shown(section)} мм²" if awg else f"{shown(section)} мм²") if section is not None else path
        if section is not None and any(other.section_mm2 == section or (awg and other.awg == awg) for other in gauges):
            reader.fail(path, "такой размер уже есть в файле")
        if reader.errors:
            refuse(key, reader)
        else:
            gauges.append(SeedGauge(awg, section, diameter, source))

    series: list[SeedSeries] = []
    marks: set[str] = set()
    for index, item in enumerate(payload["series"]):
        path, reader = f"series[{index}]", _Reader()
        if not isinstance(item, Mapping):
            reader.fail(path, "нужен объект")
            refuse(path, reader)
            continue
        name = reader.text(f"{path}.key", reader.get(item, "key", path))
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            reader.fail(f"{path}.aliases", "нужен список строк")
            aliases = []
        key = next((found for found in map(series_of, [name, *aliases]) if found), None) if name else None
        if name and key is None:
            reader.fail(f"{path}.key", f"«{name}» — не марка из базы монтажных проводов")
        elif key and any(other.key == key for other in series):
            reader.fail(f"{path}.key", f"серия {key} уже есть в файле")
        common = {field_name: reader.sourced(item, field_name, path, kind) for field_name, kind in (
            ("standard", "text"), ("conductor_material", "text"), ("insulation_material", "text"),
            ("voltage_v", "positive_int"), ("temp_min_c", "signed_int"), ("temp_max_c", "signed_int"), ("colors", "colors"),
        )}
        raw_sizes = reader.get(item, "sizes", path)
        if not isinstance(raw_sizes, list):
            reader.fail(f"{path}.sizes", "нужен список")
            raw_sizes = []
        if reader.errors:  # the series is refused, and every size of it with the reason
            keys = [str(size.get("mark") or "").strip() for size in raw_sizes if isinstance(size, Mapping)]
            for size_key in [key for key in keys if key] or [name or path]:
                refuse(size_key, reader)
            continue
        sizes: list[SeedSize] = []
        for number, size in enumerate(raw_sizes):
            size_path, size_reader = f"{path}.sizes[{number}]", _Reader()
            if not isinstance(size, Mapping):
                size_reader.fail(size_path, "нужен объект")
                refuse(size_path, size_reader)
                continue
            mark = size_reader.text(f"{size_path}.mark", size_reader.get(size, "mark", size_path))
            awg = size_reader.awg(f"{size_path}.awg", size_reader.get(size, "awg", size_path))
            section = size_reader.number(f"{size_path}.section_mm2", size_reader.get(size, "section_mm2", size_path))
            own = {field_name: size_reader.sourced(size, field_name, size_path, kind, required=False) for field_name, kind in (
                ("voltage_v", "positive_int"), ("temp_min_c", "signed_int"), ("temp_max_c", "signed_int"), ("colors", "colors"),
            )}
            values = {field_name: own[field_name] or common[field_name] for field_name in own}
            parts = [size_reader.sourced(size, field_name, size_path, kind) for field_name, kind in (
                ("strands", "text"), ("conductor_dia_mm", "positive"), ("insulation_thickness_mm", "positive"),
                ("outer_dia_mm", "positive"),
            )]
            low, high = values["temp_min_c"], values["temp_max_c"]
            if low is not None and high is not None and low.value >= high.value:
                size_reader.fail(size_path, "температура «от» должна быть ниже «до»")
            if mark and series_of(mark) != key:
                size_reader.fail(f"{size_path}.mark", f"«{mark}» — не марка серии {key}")
            if mark and cable_key(mark) in marks:
                size_reader.fail(f"{size_path}.mark", f"«{mark}» уже есть в файле")
            if any((awg and awg == other.awg) or (section is not None and section == other.section_mm2) for other in sizes):
                size_reader.fail(size_path, "такой размер серии уже есть в файле")
            if size_reader.errors:
                refuse(mark or size_path, size_reader)
                continue
            marks.add(cable_key(mark))
            sizes.append(SeedSize(mark, awg, section, *parts, values["voltage_v"], values["temp_min_c"],
                                  values["temp_max_c"], values["colors"]))
        series.append(SeedSeries(key, common["standard"], common["conductor_material"], common["insulation_material"],
                                 tuple(sizes)))
    return HookupSeed(tuple(gauges), tuple(series), tuple(refused))


# --- import ---

@dataclass
class ImportReport:
    """The shape of the shared bulk import report: created / updated (what → [old, new]) / unchanged / refused, key =
    the БД.ПРОВ mark; plus the size table and the marks the база монтажных sends from монтажные to «прочие»."""
    dry_run: bool
    created: list[str] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)  # [{"key": mark, "changes": {what: [old, new]}}]
    unchanged: int = 0
    refused: list[dict] = field(default_factory=list)  # [{"key", "reason"}]
    moved_out_of_montage: list[str] = field(default_factory=list)
    gauges_added: list[str] = field(default_factory=list)
    gauge_mismatches: list[str] = field(default_factory=list)  # reported only, the size table is not changed
    gauges_in_file: int = 0
    sizes_in_file: int = 0
    rows_matched: int = 0
    hookups_created: int = 0
    hookups_updated: int = 0

    @property
    def changed(self) -> int:
        return len(self.gauges_added) + len(self.created) + len(self.updated)

    def counts(self) -> dict[str, int]:
        return {"gauges_in_file": self.gauges_in_file, "gauges_added": len(self.gauges_added),
                "gauge_mismatches": len(self.gauge_mismatches), "sizes_in_file": self.sizes_in_file,
                "rows_matched": self.rows_matched, "created": len(self.created), "updated": len(self.updated),
                "unchanged": self.unchanged, "refused": len(self.refused), "hookups_created": self.hookups_created,
                "hookups_updated": self.hookups_updated, "moved_out_of_montage": len(self.moved_out_of_montage)}

    def as_dict(self) -> dict:
        return {"dry_run": self.dry_run, "created": list(self.created),
                "updated": [{"key": item["key"], "changes": dict(item["changes"])} for item in self.updated],
                "unchanged": self.unchanged, "refused": [dict(item) for item in self.refused],
                "moved_out_of_montage": list(self.moved_out_of_montage), "gauges_added": list(self.gauges_added),
                "gauge_mismatches": list(self.gauge_mismatches), "counts": self.counts()}

    def changes_of(self, key: str) -> dict:
        entry = next((item for item in self.updated if item["key"] == key), None)
        if entry is None:
            entry = {"key": key, "changes": {}}
            self.updated.append(entry)
        return entry["changes"]


_HOOKUP_LABELS = (
    ("series", "серия"), ("gauge_id", "сечение"), ("insulation", "изоляция"), ("outer_d_mm", "Ø наружный, мм"),
    ("voltage_v", "напряжение, В"), ("temp_min_c", "темп. от, °C"), ("temp_max_c", "темп. до, °C"),
    ("conductor_material", "материал жилы"), ("insulation_thickness_mm", "толщина изоляции, мм"),
)


def _value_text(value: object, gauges: Mapping) -> str:
    if value is None:
        return ""
    if isinstance(value, UUID):
        return gauges[value].label if value in gauges else str(value)
    return shown(value) if isinstance(value, (Decimal, int)) and not isinstance(value, bool) else str(value)


def _gauge_step(seed: HookupSeed, uow: UnitOfWork, report: ImportReport) -> list[WireGauge]:
    gauges = uow.cable_structure.gauges()
    report.gauges_in_file = len(seed.gauges)
    for item in seed.gauges:
        label = f"{item.awg} AWG · {shown(item.section_mm2)} мм²" if item.awg else f"{shown(item.section_mm2)} мм²"
        by_awg = next((gauge for gauge in gauges if item.awg and gauge.awg == item.awg), None)
        by_section = next((gauge for gauge in gauges if gauge.section_mm2 == item.section_mm2), None)
        diameter = item.conductor_d_mm.value if item.conductor_d_mm else None
        found = by_awg or by_section
        if found is None:
            gauge = WireGauge.create(awg=item.awg or "", section_mm2=item.section_mm2, conductor_d_mm=diameter or "",
                                     position=max((gauge.position for gauge in gauges), default=-1) + 1)
            validate_gauges([*gauges, gauge])
            uow.cable_structure.save_gauge(gauge)
            _sync_gost_awg(uow, None, gauge)
            gauges.append(gauge)
            report.gauges_added.append(label)
            continue
        differs = []
        if item.awg and found.awg != item.awg:
            differs.append(f"в таблице AWG {found.awg or '—'}")
        if found.section_mm2 != item.section_mm2:
            differs.append(f"в таблице {shown(found.section_mm2)} мм²")
        if diameter is not None and found.conductor_d_mm != diameter:
            differs.append(f"Ø жилы в таблице {shown(found.conductor_d_mm) or '—'}, в файле {shown(diameter)}")
        if differs:
            report.gauge_mismatches.append(f"{label}: " + "; ".join(differs))
    return gauges


def _size_gauge(size: SeedSize, gauges: list[WireGauge]) -> WireGauge | None:
    if size.awg:
        by_awg = next((gauge for gauge in gauges if gauge.awg == size.awg), None)
        if by_awg is not None:
            return by_awg
    return next((gauge for gauge in gauges if gauge.section_mm2 == size.section_mm2), None)


def _merged_sources(cell: str, sources: list[str]) -> str:
    text = str(cell or "").strip()
    fresh = []
    for source in sources:
        if source not in text and source not in fresh:
            fresh.append(source)
    return _SOURCE_SEPARATOR.join(part for part in [text, *fresh] if part)


def _cells(fields: Mapping[str, str], series: SeedSeries, size: SeedSize) -> dict[str, str]:
    """БД.ПРОВ cells to write: only the values the file gives, only where the cell means something else («48 (ном.)
    / 1000 (испыт.)» is 48 V and stays as typed). Sources the cell does not name yet are appended."""
    def cell(name: str) -> str:
        return str(fields.get(name) or "").strip()

    wanted: dict[str, str] = {}
    for name, given in ((STANDARD_FIELD, series.standard), (STRANDS_FIELD, size.strands), (COLORS_FIELD, size.colors),
                        (INSULATION_FIELD, series.insulation_material)):
        if given is not None and cell(name) != given.value:
            wanted[name] = str(given.value)
    if size.outer_dia_mm is not None and decimal_or_none(cell(OUTER_FIELD)) != size.outer_dia_mm.value:
        wanted[OUTER_FIELD] = shown(size.outer_dia_mm.value)
    for name, given in ((VOLTAGE_FIELD, size.voltage_v), (TEMP_MIN_FIELD, size.temp_min_c), (TEMP_MAX_FIELD, size.temp_max_c)):
        if given is not None and leading_int(cell(name)) != given.value:
            wanted[name] = shown(given.value)
    sources = [given.source for given in (
        series.standard, series.conductor_material, series.insulation_material, size.strands, size.conductor_dia_mm,
        size.insulation_thickness_mm, size.outer_dia_mm, size.voltage_v, size.temp_min_c, size.temp_max_c, size.colors,
    ) if given is not None]
    merged = _merged_sources(cell(SOURCE_FIELD), sources)
    if merged != cell(SOURCE_FIELD):
        wanted[SOURCE_FIELD] = merged
    return wanted


def _hookup(mark: str, base: HookupWire | None, parsed, gauge: WireGauge, series: SeedSeries, size: SeedSize) -> HookupWire:
    """The structure: what is stored (or, not structured yet, what the row says), the file's values over it."""
    def given(value: Sourced | None, current):
        return current if value is None else value.value

    return HookupWire.create(
        id=base.id if base else None, mark=mark, series=series.key, gauge_id=gauge.id,
        insulation=given(series.insulation_material, base.insulation if base else parsed.insulation),
        outer_d_mm=given(size.outer_dia_mm, base.outer_d_mm if base else parsed.outer_d_mm),
        voltage_v=given(size.voltage_v, base.voltage_v if base else parsed.voltage_v),
        temp_min_c=given(size.temp_min_c, base.temp_min_c if base else parsed.temp_min_c),
        temp_max_c=given(size.temp_max_c, base.temp_max_c if base else parsed.temp_max_c),
        conductor_material=given(series.conductor_material, base.conductor_material if base else ""),
        insulation_thickness_mm=given(size.insulation_thickness_mm, base.insulation_thickness_mm if base else None),
    )


def import_hookups(uow: UnitOfWork, payload: object, actor: Actor, now: datetime, *, dry_run: bool = False) -> ImportReport:
    """The база монтажных проводов from its parsed file (see the module doc). A file that is not the format is refused
    whole before anything is read (`HookupSeedError`); a bad row is refused with its reason and the rest applies; a
    dry run reports the same and writes nothing. Never deletes, never renames. One audit event per run."""
    seed = hookup_seed(payload)
    report = ImportReport(dry_run=dry_run, sizes_in_file=seed.sizes, refused=[dict(item) for item in seed.refused])
    with uow:
        gauges = _gauge_step(seed, uow, report)
        by_id = {gauge.id: gauge for gauge in gauges}
        records = list(uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10_000))
        diameters = uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)
        dia_keys = frozenset(diameter_index(diameters))
        overrides = cable_overrides(uow.references.list())
        hookups = {wire.mark_key: wire for wire in uow.cable_structure.hookups()}

        index: dict[tuple[str, UUID], list[TechOperationRecord]] = {}
        for record in records:
            mark = cable_mark(record)
            key = cable_key(mark)
            if not key:
                continue
            if key not in overrides and left_hookups(record.fields, mark, dia_keys):
                report.moved_out_of_montage.append(mark)
            series = hookup_series(mark)
            if series is None:
                continue
            stored = hookups.get(key)
            gauge = by_id.get(stored.gauge_id) if stored else parse_hookup(mark, record.fields, gauges).gauge
            if gauge is not None:
                index.setdefault((series, gauge.id), []).append(record)
        known_keys = {cable_key(cable_mark(record)) for record in records}

        for series in seed.series:
            for size in series.sizes:
                gauge = _size_gauge(size, gauges)
                if gauge is None:
                    report.refused.append({"key": size.mark, "reason": "размера нет в таблице сечений"})
                    continue
                matched = index.get((series.key, gauge.id), [])
                if not matched:
                    if cable_key(size.mark) in known_keys:
                        report.refused.append({"key": size.mark,
                                               "reason": "в БД.ПРОВ есть строка с этой маркой другого размера"})
                        continue
                    _create(series, size, gauge, by_id, uow, now, report)
                    continue
                done: set[str] = set()
                for record in matched:
                    _update(record, series, size, gauge, by_id, hookups, overrides, done, uow, now, report)

        _audit("references.hookup_wires_imported", actor, now, {**report.counts(), "dry_run": dry_run}, uow)
        if not dry_run:
            uow.commit()
    return report


def _update(record: TechOperationRecord, series: SeedSeries, size: SeedSize, gauge: WireGauge, by_id: Mapping,
            hookups: dict[str, HookupWire], overrides: Mapping[str, CableKind], done: set[str], uow: UnitOfWork,
            now: datetime, report: ImportReport) -> None:
    mark = cable_mark(record)
    key = cable_key(mark)
    if overrides.get(key) not in (None, CableKind.WIRE):
        report.refused.append({"key": mark, "reason": "перенесён вручную в другой список — не тронут"})
        return
    report.rows_matched += 1
    changes: dict[str, list[str]] = {}
    if key not in done:  # twins of one mark share one structure
        done.add(key)
        stored = hookups.get(key)
        wire = _hookup(mark, stored, parse_hookup(mark, record.fields, list(by_id.values())), gauge, series, size)
        for name, label in _HOOKUP_LABELS:
            old = getattr(stored, name) if stored else None
            new_text = _value_text(getattr(wire, name), by_id)
            if (stored is not None and old != getattr(wire, name)) or (stored is None and new_text):
                changes[f"структура: {label}"] = [_value_text(old, by_id), new_text]
        if stored is None or changes:
            uow.cable_structure.save_hookup(wire, now)
            hookups[key] = wire
            if stored is None:
                report.hookups_created += 1
            else:
                report.hookups_updated += 1
    cells = _cells(record.fields, series, size)
    if cells:
        changes.update({name: [str(record.fields.get(name) or ""), value] for name, value in cells.items()})
        updated = replace(record, fields={**record.fields, **cells}, edited_in_erp=True)
        uow.tech_operations.save(replace(updated, normalized_search=_search_text(updated)))
    if changes:
        report.changes_of(mark).update(changes)
    else:
        report.unchanged += 1


def _create(series: SeedSeries, size: SeedSize, gauge: WireGauge, by_id: Mapping, uow: UnitOfWork, now: datetime,
            report: ImportReport) -> None:
    """A size no БД.ПРОВ row has: a new row (edited in ERP — a mirror reload keeps it) and its structure."""
    fields = {name: "" for name in BASE_COLUMNS[TechOperationTab.WIRE]}
    fields.update({"Марка": size.mark, TYPE_FIELD: "монтажный", SECTION_FIELD: shown(size.section_mm2),
                   AWG_FIELD: size.awg or gauge.awg or "", CORES_FIELD: "1"})
    fields.update(_cells(fields, series, size))
    record = TechOperationRecord.create(tab=TechOperationTab.WIRE, display_text=size.mark, search_text="",
                                        source_sheet="", sort_key=size.mark, fields=fields, edited_in_erp=True)
    uow.tech_operations.save(record)
    wire = _hookup(size.mark, None, parse_hookup(size.mark, fields, list(by_id.values())), gauge, series, size)
    uow.cable_structure.save_hookup(wire, now)
    report.created.append(size.mark)
    report.hookups_created += 1
