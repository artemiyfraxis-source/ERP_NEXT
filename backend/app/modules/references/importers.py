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
Run twice — the second run changes nothing.

`import_rows(table, uow, rows, …)` (below) loads rows into one table by its natural key through the shared bulk
import (`app/shared/bulk_import.py`): БД.ТЕР / БД.КОАКС / БД.ПРОВ / БД.МАТ / БД.ОБ, the maps «ссылка → поставщик»,
«изготовитель → страна», ГОСТ → AWG and «Сечения AWG ↔ мм²» — see `ROW_TABLES` and docs/api-references.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from app.shared.bulk_import import (
    DUPLICATE_REASON, ImportRateLimiter, ImportReport as RowsReport, ImportRowIn, ImportTable, RowRefused, clean_value,
    row_key, run_import,
)
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import UnitOfWork, _audit, _search_text, _sync_gost_awg, cable_overrides
from .cable_structure import (
    AWG_FIELD, INSULATION_FIELD, MIRRORED_FIELDS, OUTER_FIELD, SECTION_FIELD, TEMP_MAX_FIELD, TEMP_MIN_FIELD,
    VOLTAGE_FIELD, HookupWire, WireGauge, decimal_or_none, leading_int, parse_hookup, refreshed_hookup, section_key,
    shown, validate_gauges,
)
from .cables import CableKind, cable_mark, diameter_index, hookup_series, left_hookups
from .wire_makers import STANDARD_FIELD
from .domain import (
    BASE_COLUMNS, COUNTRY_BY_MANUFACTURER, SUPPLIER_BY_LINK, ReferenceEntry, ReferenceKind, ReferenceValidationError,
    TechOperationRecord, TechOperationTab, cable_key, normalized_manufacturer_key, normalized_section_key,
    normalized_supplier_key, validate_entries,
)
from .technology import (
    _TECH_SCHEMA, _normalized_article, _range_indexes, _resolve_schema, contact_gender, normalize_material,
    pull_force_newtons, schema_display_text, schema_keys, sync_schema_fields, terminal_card_fields,
    terminal_card_indexes, terminal_card_problems,
)

HOOKUP_SEED_VERSION = 1
TYPE_FIELD = "Тип провода"
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


def import_hookups(uow: UnitOfWork, payload: object, actor: Actor, now: datetime, *, dry_run: bool = False,
                   source: str = "api") -> ImportReport:
    """The база монтажных проводов from its parsed file (see the module doc). A file that is not the format is refused
    whole before anything is read (`HookupSeedError`); a bad row is refused with its reason and the rest applies; a
    dry run reports the same and writes nothing. Never deletes, never renames. One audit event per run that changes
    something (`source` — who loads: erp | api | warehouse); a run that changes nothing leaves no event."""
    check_source(source)
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

        if report.changed or report.hookups_created or report.hookups_updated:
            _audit("references.hookup_wires_imported", actor, now,
                   {**report.counts(), "dry_run": dry_run, "source": source}, uow)
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


# --- rows into a table: POST /api/references/import/<table> (owner 12.10.2026: «парсить контакты, разъёмы вручную гемор») ---

IMPORT_SOURCES = ("erp", "api", "warehouse")
IMPORT_EVENT = "references.table_imported"
_AGGREGATE = UUID("00000000-0000-0000-0000-000000000010")
AMBIGUOUS_REASON = "в таблице несколько строк с этим ключом — правьте их в «Базе данных»"
GAUGE_KEY = "AWG или сечение"  # the size's key, filled from awg (else section_mm2) of the row


def check_source(source: str) -> None:
    if source not in IMPORT_SOURCES:
        raise ReferenceValidationError("Источник загрузки: " + ", ".join(IMPORT_SOURCES))


def _cell(name: str, value: Any) -> Any:
    text = clean_value(name, value)
    if text and text.startswith("="):
        raise RowRefused(f"{name}: формулы вводить нельзя")
    return text


def _folded(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


# БД.ТЕР by the contact protocol v5.2 (owner 12.10.2026): the applicator, pneumatics, ОТР, «С зачисткой» and mm² are
# the technologist's — a load never writes them (and mm² is never computed from AWG); the rest is kept as the maker
# writes it, only the units are brought to the base's: pull force in N, AWG without «#», «Металл / Покрытие».
_TECHNOLOGIST_KEYS = frozenset({"terApplicator", "mm2From", "mm2To"})
_TECHNOLOGIST_COLUMN = re.compile(r"^(аппликатор|applicator|пневм|отр$|с зачисткой)|мм2|мм²|mm2|mm²")
TECHNOLOGIST_REASON = "заполняет технолог в «Базе данных» — загрузка контактов это поле не пишет"


def technologist_field(name: str) -> bool:
    return name in _TECHNOLOGIST_KEYS or bool(_TECHNOLOGIST_COLUMN.search(_folded(name)))


def _ter_cell(name: str, value: Any) -> Any:
    text = _cell(name, value)
    if text is None:
        return None
    if technologist_field(name):
        raise RowRefused(f"{name}: {TECHNOLOGIST_REASON}")
    folded = _folded(name)
    if name in ("terPullForceMin", "terPullForceMax") or folded.startswith("усилие обрыва"):
        return pull_force_newtons(text)
    if name in ("awgFrom", "awgTo") or (folded.startswith(("от", "до")) and folded.endswith("awg")):
        return text.replace("#", "").strip()
    if name == "terMaterial" or folded.startswith("материал"):
        return normalize_material(text)
    if name == "terGender":
        gender = contact_gender(text)
        if text and not gender:
            raise RowRefused(f"terGender: Male или Female, а не «{text}»")
        return gender
    return text


class _Target:
    """Writes through the unit of work the import runs in; what is not committed is dropped when it closes (a dry
    run, or nothing changed), so `rollback` has nothing to do. The audit event carries the load's `source`."""

    def __init__(self, uow: UnitOfWork, source: str) -> None:
        self.uow = uow
        self.source = source

    def flush(self) -> None:
        """Writes kept until every row was seen (a map is stored once per call)."""

    def audit(self, event: AuditEvent) -> None:
        self.flush()
        self.uow.audit.add(replace(event, details={**event.details, "source": self.source}))

    def commit(self) -> None:
        self.uow.commit()

    def rollback(self) -> None:
        pass


# БД.ТЕР / БД.КОАКС / БД.ОБ / БД.ПРОВ / БД.МАТ

@dataclass(frozen=True)
class _TechTable:
    tab: TechOperationTab
    label: str
    key_fields: tuple[str, ...]
    # Match forms of a key, strictest first: a row matches the stored rows of the first form any of them shares.
    forms: Callable[[tuple[str, ...]], tuple[tuple[str, object], ...]]
    virtual: str = ""  # a key that is the record's display text, not a field (БД.ОБ «Для базы» = baseValue)
    cell: Callable[[str, Any], Any] = _cell  # a row value as stored (БД.ТЕР: units of the base, technologist's fields)
    card_columns: tuple[str, ...] = ()  # sheet columns a load may bring before any row has them (БД.КОАКС card)

    @property
    def sheet_columns(self) -> bool:
        """ОБ / ТЕР / КОАКС: fields are the schema values plus the sheet's own columns; ПРОВ / МАТ: the sheet header."""
        return self.tab not in BASE_COLUMNS

    def schema_fields(self) -> tuple[str, ...]:
        if not self.sheet_columns:
            return BASE_COLUMNS[self.tab]
        fields = _TECH_SCHEMA[self.tab.value][0]
        derived = sorted(schema_keys(self.tab.value) - {item.key for item in fields})
        stored = tuple(item.key for item in fields if item.store or item.key == self.virtual)
        return stored + tuple(derived)


def _ter_forms(values: tuple[str, ...]) -> tuple[tuple[str, object], ...]:
    # The generator's terminal lookup (technology.matching_terminal_records): exact, then without spaces . / ( ) _ -;
    # a stored row is found by its REEL article or by its BAG one (`_TechTarget` indexes both).
    article = values[0]
    normalized = _normalized_article(article)
    return (("exact", article.casefold().strip()),) + ((("normalized", normalized),) if len(normalized) >= 3 else ())


# The coax connector card (owner 13.10.2026, the 311 connectors of gsm-repiteri.ru): БД.КОАКС sheet columns a load may
# bring before any row has them. «Тип пина» / «Тип экрана» are the sheet's own (the generator reads them); the site's
# words for the pin and the shield go to «Центральный контакт» / «Крепление экрана».
COAX_CARD_COLUMNS = (
    "Тип пина", "Тип экрана", "Серия", "Пол", "Полярность", "Исполнение", "Монтаж", "Центральный контакт",
    "Крепление экрана", "Волновое сопротивление, Ом", "Частота до, ГГц", "Материал корпуса", "Покрытие", "Габариты",
    "Наименование", "Ссылка", "Примечание", "Источник данных",
)

TECH_TABLES: dict[str, _TechTable] = {
    "ter": _TechTable(TechOperationTab.TERMINAL, "БД.ТЕР — контакты, терминалы и разъёмы", ("terArticle",), _ter_forms,
                      cell=_ter_cell),
    "coax": _TechTable(TechOperationTab.COAX, "БД.КОАКС — коаксиальные разъёмы", ("coaxArticle", "coaxWire"),
                       lambda values: (("pair", (_folded(values[0]), cable_key(values[1]))),),
                       card_columns=COAX_CARD_COLUMNS),
    "prov": _TechTable(TechOperationTab.WIRE, "БД.ПРОВ — кабели и провода", ("Марка",),
                       lambda values: (("mark", cable_key(values[0])),)),
    "mat": _TechTable(TechOperationTab.MATERIAL, "БД.МАТ — материалы", ("Наименование",),
                      lambda values: (("name", _folded(values[0])),)),
    "ob": _TechTable(TechOperationTab.EQUIPMENT, "БД.ОБ — станки и оборудование", ("baseValue",),
                     lambda values: (("base", _folded(values[0])),), virtual="baseValue"),
}


def _sheet_columns(table: _TechTable, records: Sequence[TechOperationRecord]) -> list[str]:
    if not table.sheet_columns:
        return list(BASE_COLUMNS[table.tab])
    keys = schema_keys(table.tab.value)
    return list(dict.fromkeys([*(name for record in records for name in record.fields if name not in keys),
                               *table.card_columns]))


def _schema_values(tab: str, fields: Mapping[str, str]) -> dict[str, str]:
    schema_fields = _TECH_SCHEMA[tab][0]
    keys = schema_keys(tab)
    columns = [name for name in fields if name not in keys]
    resolved = _resolve_schema(schema_fields, columns)
    return {item.key: str((fields[columns[resolved[item.key]]] if resolved[item.key] >= 0 else fields.get(item.key)) or "")
            .strip() for item in schema_fields}


class _TechTarget(_Target):
    def __init__(self, uow: UnitOfWork, source: str, table: _TechTable, records: Sequence[TechOperationRecord],
                 key_values: Mapping[str, tuple[str, ...]], now: datetime) -> None:
        super().__init__(uow, source)
        self.table, self.key_values, self.now = table, key_values, now
        self.columns = _sheet_columns(table, records)
        self.index: dict[tuple[str, object], list[TechOperationRecord]] = {}
        for record in records:
            values = tuple(str(self._current(record).get(name) or "") for name in table.key_fields)
            keyed = [values] if all(values) else []
            if table.tab == TechOperationTab.TERMINAL:  # the same terminal in BAG packaging is the same row
                bag = str(record.fields.get("terArticleBag") or terminal_card_fields(record.fields).get("terArticleBag")
                          or "")
                keyed += [(bag,)] if bag.strip() else []
            for key_values_of_row in keyed:
                for form in table.forms(key_values_of_row):
                    rows = self.index.setdefault(form, [])
                    if all(item.id != record.id for item in rows):
                        rows.append(record)
        self.matched: dict[str, TechOperationRecord] = {}
        self.ambiguous: set[str] = set()
        self.duplicates: set[str] = set()
        self.created: set[tuple[str, object]] = set()

    def _current(self, record: TechOperationRecord) -> dict[str, str]:
        fields = dict(record.fields)
        if self.table.virtual:
            fields[self.table.virtual] = record.display_text
        return fields

    def existing(self, keys):
        found, taken = {}, {}
        for key in keys:
            matches: list[TechOperationRecord] = []
            for form in self.table.forms(self.key_values[key]):
                if form in self.index:
                    matches = self.index[form]
                    break
            if len(matches) > 1:
                self.ambiguous.add(key)
            elif matches:
                record = matches[0]
                if record.id in taken:  # «ZHR-5» and «ZHR 5» in one load: one row
                    self.duplicates.add(key)
                    continue
                taken[record.id] = key
                self.matched[key] = record
                found[key] = self._current(record)
        return found

    def _schema(self, fields: dict[str, str], given: set[str]) -> dict[str, str]:
        """ОБ / ТЕР / КОАКС: a schema value given (terStep) is written to the sheet column it is read from too, so a
        later edit of the row never takes it back; a sheet column given moves its schema value (as edit_record)."""
        if not self.table.sheet_columns:
            return fields
        tab = self.table.tab.value
        keys = schema_keys(tab)
        columns = [name for name in fields if name not in keys]
        resolved = _resolve_schema(_TECH_SCHEMA[tab][0], columns)
        result = dict(fields)
        if self.table.tab == TechOperationTab.TERMINAL:
            resolved.update(_range_indexes(columns))
            resolved.update(terminal_card_indexes(columns))
            self._gender(result, given, columns, resolved)
        for name in sorted(given & keys):
            index = resolved.get(name, -1)
            if index < 0:
                continue
            column = columns[index]
            if column in given and fields[column] != fields[name]:
                raise RowRefused(f"{name} и «{column}» — одно и то же поле, а значения разные")
            result[column] = fields[name]
        synced = sync_schema_fields(tab, result)
        if self.table.tab == TechOperationTab.TERMINAL and (problems := terminal_card_problems(synced)):
            raise RowRefused("; ".join(problems))  # the CHECKs of migration 0115 would refuse the whole load
        return synced

    @staticmethod
    def _gender(fields: dict[str, str], given: set[str], columns: list[str], resolved: Mapping[str, int]) -> None:
        """terGender is read from «Тип контакта» (terType): a gender given alone becomes the type when the type says
        otherwise; given with a type that says otherwise, the row is refused."""
        if "terGender" not in given:
            return
        index = resolved.get("terType", -1)
        column = columns[index] if index >= 0 else ""
        # the type as this row leaves it: given here (the key or its column), else the stored one
        current = fields.get("terType") if "terType" in given or not column else fields.get(column)
        if contact_gender(current) == fields["terGender"]:
            return
        if "terType" in given or column in given:
            raise RowRefused(f"terGender и «{column or 'terType'}» расходятся: {fields['terGender']} ≠ {current}")
        fields["terType"] = fields["terGender"]
        if column:
            fields[column] = fields["terGender"]

    def create(self, key, fields):
        if key in self.ambiguous:
            raise RowRefused(AMBIGUOUS_REASON)
        forms = set(self.table.forms(self.key_values[key]))
        if key in self.duplicates or forms & self.created:
            raise RowRefused(DUPLICATE_REASON)
        stored = {name: "" for name in self.columns}  # every column of the base, so the row is editable on screen
        stored.update(fields)
        stored = self._schema(stored, set(fields))
        tab = self.table.tab.value
        display = (schema_display_text(tab, stored) if self.table.sheet_columns else "") \
            or str(fields.get(self.table.key_fields[0]) or "")
        sort = _TECH_SCHEMA[tab][3] if self.table.sheet_columns else None
        sort_key = sort(_schema_values(tab, {**stored, self.table.virtual: display} if self.table.virtual else stored)) \
            if sort else display
        if self.table.virtual:
            stored.pop(self.table.virtual, None)
        try:
            record = TechOperationRecord.create(tab=self.table.tab, display_text=display, search_text="",
                                                source_sheet="", sort_key=sort_key, fields=stored, edited_in_erp=True)
        except ReferenceValidationError as error:
            raise RowRefused(str(error)) from error
        self.uow.tech_operations.save(record)
        self.created |= forms

    def update(self, key, changes):
        record = self.matched[key]
        fields = self._schema({**record.fields, **changes}, set(changes))
        display = (schema_display_text(self.table.tab.value, fields) if self.table.sheet_columns else "") \
            or record.display_text
        updated = replace(record, fields=fields, display_text=display, edited_in_erp=True)
        updated = replace(updated, normalized_search=_search_text(updated))
        wire = None
        mark_key = cable_key(cable_mark(record))
        if self.table.tab == TechOperationTab.WIRE and mark_key and set(changes) & set(MIRRORED_FIELDS):
            stored = self.uow.cable_structure.hookup(mark_key)
            if stored is not None:  # a structured монтажный wire follows its cells (as edit_record)
                try:
                    wire = refreshed_hookup(stored, cable_mark(updated), updated.fields, set(changes),
                                            self.uow.cable_structure.gauges())
                except ReferenceValidationError as error:
                    raise RowRefused(str(error)) from error
        self.uow.tech_operations.save(updated)
        if wire is not None:
            self.uow.cable_structure.save_hookup(wire, self.now)


# «Ссылка → поставщик» / «Изготовитель → страна» / ГОСТ → AWG

_MAPS: dict[str, tuple[ReferenceKind, Callable[[str], str], str]] = {
    "suppliers": (ReferenceKind.SUPPLIER, normalized_supplier_key, "Ссылка → поставщик"),
    "countries": (ReferenceKind.COUNTRY, normalized_manufacturer_key, "Изготовитель → страна"),
    "gost_awg": (ReferenceKind.GOST_AWG, normalized_section_key, "ГОСТ → AWG (сечение ГОСТ, мм² → AWG)"),
}
MAP_FIELDS = ("key", "value")


def _map_value(name: str, value: Any) -> Any:
    text = _cell(name, value)
    return str(int(text)) if name == "value" and isinstance(text, str) and text.isdigit() else text


class _MapTarget(_Target):
    """Upsert into one map: a key not in it is added after the others, a known one gets the new value, nothing is
    removed. «Изготовитель → страна» compares with what the screen shows (saved rows + the seed); «ссылка →
    поставщик» never saved shows its seed, which the first write stores, as the first save on screen does."""

    def __init__(self, uow: UnitOfWork, source: str, kind: ReferenceKind, normalize: Callable[[str], str]) -> None:
        super().__init__(uow, source)
        self.kind, self.normalize = kind, normalize
        self.entries = list(uow.references.list())
        self.saved = {entry.normalized_key: entry for entry in self.entries if entry.kind == kind}
        never_saved = not any(entry.kind in (ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY) for entry in self.entries)
        supplier_map = kind in (ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY)
        self.materialize = SUPPLIER_BY_LINK if never_saved and supplier_map else ()
        seed = SUPPLIER_BY_LINK if kind == ReferenceKind.SUPPLIER and never_saved else (
            COUNTRY_BY_MANUFACTURER if kind == ReferenceKind.COUNTRY else ())
        self.seed = {normalize(name): (name, value) for name, value in seed}
        self.gauges = {section_key(gauge.section_mm2): gauge for gauge in uow.cable_structure.gauges()} \
            if kind == ReferenceKind.GOST_AWG else {}
        self.duplicates: set[str] = set()
        self.dirty = False

    def existing(self, keys):
        found, taken = {}, set()
        for key in keys:
            try:
                normalized = self.normalize(key)
            except ReferenceValidationError:
                continue  # refused with the reason when it is written
            if normalized in taken:
                self.duplicates.add(key)
                continue
            taken.add(normalized)
            if normalized in self.saved:
                found[key] = {"key": self.saved[normalized].key, "value": self.saved[normalized].value}
            elif normalized in self.seed:
                found[key] = {"key": self.seed[normalized][0], "value": self.seed[normalized][1]}
        return found

    def _put(self, key: str, value: str) -> None:
        if key in self.duplicates:
            raise RowRefused(DUPLICATE_REASON)
        try:
            normalized = self.normalize(key)
            current = self.saved.get(normalized)
            name = current.key if current else self.seed.get(normalized, (key,))[0]
            entry = ReferenceEntry.create(self.kind, name, value, current.position if current else 0)
        except ReferenceValidationError as error:
            raise RowRefused(str(error)) from error
        gauge = self.gauges.get(normalized)
        if gauge is not None and gauge.awg_number is not None and str(gauge.awg_number) != entry.value:
            raise RowRefused(f"в «Сечения AWG ↔ мм²» {shown(gauge.section_mm2)} мм² = {gauge.awg} AWG — ГОСТ → AWG "
                             "следует таблице сечений, поменяйте там")
        self.saved[normalized] = replace(entry, id=current.id) if current else entry
        self.dirty = True

    def create(self, key, fields):
        self._put(key, str(fields.get("value") or ""))

    def update(self, key, changes):
        self._put(key, str(changes.get("value") or ""))

    def flush(self) -> None:
        if not self.dirty:
            return
        own = list(self.saved.values())
        if self.materialize:  # the supplier seed the screen shows is stored with the first write
            seeded = [self.saved.get(self.normalize(name)) if self.kind == ReferenceKind.SUPPLIER else None
                      for name, _value in self.materialize]
            seeded = [found or ReferenceEntry.create(ReferenceKind.SUPPLIER, name, value, 0)
                      for found, (name, value) in zip(seeded, self.materialize)]
            seeded_keys = {entry.normalized_key for entry in seeded}
            own = seeded + [entry for entry in own if entry.kind != ReferenceKind.SUPPLIER
                            or entry.normalized_key not in seeded_keys]
        positions: dict[ReferenceKind, int] = {}
        placed = []
        for entry in own:
            placed.append(replace(entry, position=positions.get(entry.kind, 0)))
            positions[entry.kind] = positions.get(entry.kind, 0) + 1
        entries = tuple([entry for entry in self.entries if entry.kind not in positions] + placed)
        validate_entries(entries)
        self.uow.references.replace(entries)


# «Сечения AWG ↔ мм²»

GAUGE_FIELDS = (GAUGE_KEY, "awg", "section_mm2", "conductor_d_mm")


def _gauge_value(name: str, value: Any) -> Any:
    text = clean_value(name, value)
    if not text or name == GAUGE_KEY:
        return text
    if name == "awg":
        try:
            return WireGauge.create(awg=text, section_mm2="1").awg or ""
        except ReferenceValidationError as error:
            raise RowRefused(f"awg: {error}") from error
    number = decimal_or_none(text)
    if number is None or number <= 0:
        raise RowRefused(f"{name}: нужно число больше нуля")
    return shown(number)


def _gauge_rows(rows: Sequence[ImportRowIn]) -> tuple[list[ImportRowIn], dict[str, tuple[str | None, Decimal | None]]]:
    """Each row gets its key: «20 AWG» when it gives an AWG, else «0,5 мм²» (a key sent by the caller is replaced)."""
    keyed, sizes = [], {}
    for row in rows:
        fields = {name: value for name, value in row.fields.items() if name != GAUGE_KEY}
        try:
            awg = _gauge_value("awg", fields.get("awg")) or None
            section = decimal_or_none(_gauge_value("section_mm2", fields.get("section_mm2")) or None)
        except RowRefused:
            awg, section = None, None  # the framework refuses the row with the field's reason
        label = f"{awg} AWG" if awg else f"{shown(section)} мм²" if section is not None else ""
        if label:
            fields[GAUGE_KEY] = label
            sizes[label] = (awg, section)
        keyed.append(ImportRowIn(fields=fields or {"awg": None}, old=row.old))
    return keyed, sizes


class _GaugeTarget(_Target):
    """A size is matched by its AWG; a row without an AWG — by the section (of a size row without an AWG too). The
    ГОСТ → AWG map follows every size written (as `save_gauge`)."""

    def __init__(self, uow: UnitOfWork, source: str, sizes: Mapping[str, tuple[str | None, Decimal | None]]) -> None:
        super().__init__(uow, source)
        self.sizes = sizes
        self.gauges = uow.cable_structure.gauges()
        self.matched: dict[str, WireGauge] = {}
        self.duplicates: set[str] = set()

    @staticmethod
    def _fields(gauge: WireGauge, key: str) -> dict[str, str]:
        return {GAUGE_KEY: key, "awg": gauge.awg or "", "section_mm2": shown(gauge.section_mm2),
                "conductor_d_mm": shown(gauge.conductor_d_mm)}

    def existing(self, keys):
        found, taken = {}, set()
        for key in keys:
            awg, section = self.sizes[key]
            gauge = next((item for item in self.gauges if awg and item.awg == awg), None) or next((
                item for item in self.gauges if section is not None and item.section_mm2 == section
                and not (awg and item.awg)), None)
            if gauge is None:
                continue
            if gauge.id in taken:
                self.duplicates.add(key)
                continue
            taken.add(gauge.id)
            self.matched[key] = gauge
            found[key] = self._fields(gauge, key)
        return found

    def _save(self, before: WireGauge | None, values: Mapping[str, str]) -> None:
        others = [item for item in self.gauges if before is None or item.id != before.id]
        try:
            gauge = WireGauge.create(
                id=before.id if before else None, awg=values.get("awg") or "",
                section_mm2=values.get("section_mm2") or "", conductor_d_mm=values.get("conductor_d_mm") or "",
                position=before.position if before else max((item.position for item in self.gauges), default=-1) + 1,
            )
            validate_gauges([*others, gauge])
        except ReferenceValidationError as error:
            raise RowRefused(str(error)) from error
        self.uow.cable_structure.save_gauge(gauge)
        _sync_gost_awg(self.uow, before, gauge)
        self.gauges = [*others, gauge]

    def create(self, key, fields):
        if key in self.duplicates:
            raise RowRefused(DUPLICATE_REASON)
        self._save(None, fields)

    def update(self, key, changes):
        before = self.matched[key]
        self._save(before, {**self._fields(before, key), **changes})


# The tables

ROW_TABLES = (*TECH_TABLES, *_MAPS, "wire-gauges")
IMPORT_TABLES = (*ROW_TABLES, "hookups")
MAP_TABLES = tuple(_MAPS)


def unknown_table(table: str) -> LookupError:
    return LookupError(f"Нет таблицы «{table}» для загрузки; есть: " + ", ".join(IMPORT_TABLES))


def _table(name: str, key_fields: tuple[str, ...], fields: Sequence[str], normalize) -> ImportTable:
    return ImportTable(name=name, key_fields=key_fields, fields=frozenset(fields), event_type=IMPORT_EVENT,
                       aggregate_id=_AGGREGATE, normalize=normalize)


def _key_values(table: ImportTable, rows: Sequence[ImportRowIn]) -> dict[str, tuple[str, ...]]:
    """Each row's key string (`row_key`) → its key values, so a target matches by the values, not the joined text."""
    values = {}
    for row in rows:
        try:
            key = tuple(str(table.normalize(name, row.fields.get(name)) or "") for name in table.key_fields)
        except (RowRefused, ValueError):
            continue
        values[row_key(table, dict(zip(table.key_fields, key)))] = key
    return values


def import_rows(table: str, uow: UnitOfWork, rows: Sequence[ImportRowIn], actor: Actor, now: datetime, *,
                dry_run: bool, source: str = "api", limiter: ImportRateLimiter | None = None) -> RowsReport:
    """Rows into one table (`ROW_TABLES`) by its natural key, through `run_import`: created / updated field by field /
    unchanged / refused with the reason; nothing deleted; a БД row written is marked edited in ERP, so a mirror reload
    keeps it. One audit event `references.table_imported` (with `source`) per call that changes something."""
    check_source(source)
    if table not in ROW_TABLES:
        raise unknown_table(table)
    with uow:
        if table in TECH_TABLES:
            spec = TECH_TABLES[table]
            records = uow.tech_operations.list(tab=spec.tab, query="", limit=100_000)
            import_table = _table(table, spec.key_fields, [*spec.schema_fields(), *_sheet_columns(spec, records)],
                                  spec.cell)
            target: _Target = _TechTarget(uow, source, spec, records, _key_values(import_table, rows), now)
        elif table in _MAPS:
            kind, normalize, _label = _MAPS[table]
            import_table = _table(table, ("key",), MAP_FIELDS, _map_value)
            target = _MapTarget(uow, source, kind, normalize)
        else:
            rows, sizes = _gauge_rows(rows)
            import_table = _table(table, (GAUGE_KEY,), GAUGE_FIELDS, _gauge_value)
            target = _GaugeTarget(uow, source, sizes)
        return run_import(import_table, rows, target, actor=actor, now=now, dry_run=dry_run, limiter=limiter)


def import_schema(table: str, uow: UnitOfWork) -> dict:
    """What a table's load accepts: the key and every field name (БД.ОБ / ТЕР / КОАКС — the schema values the
    generator reads, then the sheet's own columns as «База данных» shows them)."""
    if table == "hookups":
        return {"table": table, "label": "База монтажных проводов", "key": ["марка БД.ПРОВ (серия + размер)"],
                "fields": [], "format": f"hookup-wires.json, version {HOOKUP_SEED_VERSION}"}
    if table in TECH_TABLES:
        spec = TECH_TABLES[table]
        with uow:
            records = uow.tech_operations.list(tab=spec.tab, query="", limit=100_000)
        schema = list(spec.schema_fields())
        columns = [name for name in _sheet_columns(spec, records) if name not in schema] if spec.sheet_columns else []
        return {"table": table, "label": spec.label, "key": list(spec.key_fields), "fields": schema + columns,
                "schema_fields": schema if spec.sheet_columns else [], "sheet_columns": columns}
    if table in _MAPS:
        return {"table": table, "label": _MAPS[table][2], "key": ["key"], "fields": list(MAP_FIELDS)}
    if table == "wire-gauges":
        return {"table": table, "label": "Сечения AWG ↔ мм²", "key": ["awg", "section_mm2"],
                "fields": list(GAUGE_FIELDS[1:]), "note": "ключ — AWG; строка без AWG ищется по сечению"}
    raise unknown_table(table)
