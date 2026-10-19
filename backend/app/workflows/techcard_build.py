"""Enrich an ETK build config with norms owned by the references module."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
import re
from typing import Any, Iterable, Mapping

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork, coax_dia_rows
from app.modules.references.cable_structure import HookupWire, WireGauge, decimal_or_none, gauge_gost_awg, section_key
from app.modules.references.cables import hookup_series_rest
from app.modules.references.domain import TechOperationTab, cable_key, gost_awg_map
from app.modules.references.wire_dia import outer_by_series
from app.modules.references.technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION, TerminalRecord, fold_wire_section, matching_terminal_records, resolve_terminal,
    terminal_record_for_section, wire_section,
)


def with_technology(
    config: Mapping[str, Any], references_factory: Callable[[], ReferencesUnitOfWork] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The config «Создать техкарты» builds from, with terminal norms from the references snapshot, and the
    snapshot stamp for the ETK. Without a current snapshot the config goes as is and the stamp is None."""
    if references_factory is None:
        return dict(config), None
    with references_factory() as references_uow:
        metadata = references_uow.tech_operations.metadata()
        if not metadata or metadata["schema_version"] != TECH_SNAPSHOT_SCHEMA_VERSION:
            # The wire Ø come from the cable bases, not from the snapshot: the ETK gets them even without it.
            return enrich_wire_diameters(config, references_uow.cable_structure.hookups(), references_uow.cable_structure.gauges(), [
                record.fields for record in references_uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10_000)
            ]), None
        records = references_uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=10_000)
        # ГОСТ section → AWG from «Сечения AWG ↔ мм²» of the база монтажных проводов (the old СПР.КАБ AWG table is
        # retired, owner 12.10.2026); the stored map only when the sizes table is empty.
        gost_awg = gauge_gost_awg(references_uow.cable_structure.gauges()) or gost_awg_map(references_uow.references.list())
        equipment = [(record.display_text, record.fields) for record in references_uow.tech_operations.list(
            tab=TechOperationTab.EQUIPMENT, query="", limit=10_000,
        )]
        operation_records = [record.fields for record in references_uow.tech_operations.list(
            tab=TechOperationTab.OPERATION, query="", limit=100_000,
        )]
        gauges = references_uow.cable_structure.gauges()
        hookups = references_uow.cable_structure.hookups()
        prov_rows = [record.fields for record in references_uow.tech_operations.list(
            tab=TechOperationTab.WIRE, query="", limit=10_000,
        )]
        coax = isinstance(config.get("coax"), Mapping)
        coax_rows = [record.fields for record in references_uow.tech_operations.list(
            tab=TechOperationTab.COAX, query="", limit=10_000,
        )] if coax else []
        dia_rows = coax_dia_rows(references_uow) if coax else []
    enriched = enrich_terminal_norms(config, (TerminalRecord.from_fields(record.fields) for record in records), gost_awg)
    enriched = enrich_marking(enriched, equipment, operation_records)
    enriched = enrich_linear_timing(enriched, operation_records)
    enriched = enrich_wire_diameters(enriched, hookups, gauges, prov_rows)
    enriched = enrich_coax_hex(enriched, coax_rows)
    enriched = enrich_coax_depth(enriched, coax_rows)
    enriched = enrich_coax_diameters(enriched, dia_rows)
    return enriched, {
        "schema_version": metadata["schema_version"],
        "source_updated_at": metadata["source_updated_at"].isoformat() if metadata["source_updated_at"] else None,
        "imported_at": metadata["imported_at"].isoformat(),
    }


def _millimetres(value: object) -> float | None:
    number = decimal_or_none(value)
    return float(number) if number is not None and number > 0 else None


def enrich_wire_diameters(
    config: Mapping[str, Any], hookups: Iterable[HookupWire], gauges: Iterable[WireGauge],
    prov_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Copy of the config whose wires carry `outerDiameter` / `conductorDiameter` (mm, numbers) of their БД.ПРОВ
    record: the outer Ø from the монтажный провод (else the БД.ПРОВ cell «Наружный диаметр, мм»), the conductor Ø
    from the «Сечения AWG ↔ мм²» row of that wire (a БД.ПРОВ cable without a монтажный record — by its «AWG», else its
    «Сечение, мм2»). The record is found by the wire's article, then its name, ignoring case, spaces, hyphens; a
    BOM name with more words after the mark («UL1007 24AWG красный») — by its longest leading words (two at least)
    that are a mark. A wire of a series of the база монтажных without its own record is read as the generator reads
    it (`tcGenLogic` «Ø изоляции по маркам»): the series in the name, its AWG («24AWG») or section («0,2») → the size
    row, the Ø of that series and size. Nothing found — no key (the ETK shows «—»), never a number from nowhere."""
    enriched = deepcopy(dict(config))
    gauges = list(gauges)
    hookups = list(hookups)
    by_gauge = {gauge.id: gauge for gauge in gauges}
    by_awg = {gauge.awg: gauge for gauge in gauges if gauge.awg}
    by_section = {section_key(gauge.section_mm2): gauge for gauge in gauges}
    by_mark = {wire.mark_key: wire for wire in hookups}
    series_outer = outer_by_series(hookups)
    prov = {cable_key(row.get("Марка")): row for row in prov_rows if cable_key(row.get("Марка"))}

    def known(label: object) -> str:
        words = str(label or "").split()
        for count in range(len(words), 1, -1):
            key = cable_key(" ".join(words[:count]))
            if key in by_mark or key in prov:
                return key
        key = cable_key(label)
        return key if key in by_mark or key in prov else ""

    def row_gauge(row: Mapping[str, Any]) -> WireGauge | None:
        awg = re.match(r"\s*(\d+)", str(row.get("AWG") or ""))
        section = decimal_or_none(row.get("Сечение, мм2"))
        return ((by_awg.get(str(int(awg.group(1)))) if awg else None)
                or (by_section.get(section_key(section)) if section is not None and section > 0 else None))

    def by_record(key: str) -> dict[str, float | None]:
        record, row = by_mark.get(key), prov.get(key)
        outer = _millimetres(record.outer_d_mm) if record is not None else None
        if outer is None and row is not None:
            outer = _millimetres(row.get("Наружный диаметр, мм"))
        gauge = by_gauge.get(record.gauge_id) if record is not None else row_gauge(row) if row is not None else None
        return {"outerDiameter": outer, "conductorDiameter": _millimetres(gauge.conductor_d_mm) if gauge else None}

    def by_series(label: object) -> dict[str, float | None]:
        found = hookup_series_rest(str(label or ""))
        if found is None:
            return {}
        series, rest = found
        awg = re.search(r"(\d+)\s*AWG", str(label or "").upper())
        if awg:
            candidates = [by_awg.get(str(int(awg.group(1))))]
        else:
            # «ПуГВ 1х0,75»: the number before «х» counts the cores, not the section.
            numbers = re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"\d+\s*[ХX×*]\s*(?=\d)", " ", rest))
            candidates = [by_section.get(section_key(Decimal(number.replace(",", ".")))) for number in numbers]
        sizes = [gauge for gauge in candidates if gauge is not None]
        gauge = next((item for item in sizes if (series, item.id) in series_outer), sizes[0] if sizes else None)
        if gauge is None:
            return {}
        return {"outerDiameter": _millimetres(series_outer.get((series, gauge.id))),
                "conductorDiameter": _millimetres(gauge.conductor_d_mm)}

    def diameters(wire: Mapping[str, Any]) -> dict[str, float]:
        labels = (wire.get("art"), wire.get("name"))
        found = next((by_record(key) for key in map(known, labels) if key), None)
        if found is None:
            found = next((item for item in map(by_series, labels) if item), {})
        return {name: value for name, value in found.items() if value is not None}

    lists = [enriched.get("wires")] + [op.get("wires") for op in enriched.get("ops") or [] if isinstance(op, dict)]
    for wires in lists:
        for wire in wires if isinstance(wires, list) else []:
            if isinstance(wire, dict):
                wire.update(diameters(wire))
    return enriched


# Маркировка по КД (владелец 12.10.2026): оборудование видов меток из «Оборудования» (поле «Для базы») и нормы БД.ОП
# их отдельных шагов. Хлястик печатает флажковый маркиратор (в базе с моделью — ZCUT-RT430, «Монтаж маркировки»),
# надписи на трубках — принтер трубок, отрезки — автомат резки термоусаживаемых трубок, усадка — термовоздушная станция
# (её же ставит БД.ОП усадки HEAT_TYT_*). Ключи — etk_marking.EQUIPMENT_DEFAULTS / TIME_ROWS.
MARK_EQUIPMENT = {
    "flagPrinter": re.compile(r"маркиратор[^/]*(флаж|флаш)", re.IGNORECASE),
    "tubePrinter": re.compile(r"принтер[^/]*трубок", re.IGNORECASE),
    "tubeCutter": re.compile(r"резки[^/]*термоусаж[^/]*трубок", re.IGNORECASE),
    "heater": re.compile(r"термовоздушн", re.IGNORECASE),
}
MARK_NORMS = {"flagPrint": "INS_MARK_AUTO", "tubePrint": "PRINT_MARK", "tubeCut": "CUT_TUT_short", "heat": "HEAT_TYT_short"}


def _equipment_pick(pattern: re.Pattern[str], equipment: Iterable[tuple[str, Mapping[str, Any]]]) -> dict[str, str] | None:
    """Первая запись «Оборудования» по виду; запись с моделью («ZCUT-RT430/…») раньше безымянной («/Маркиратор…»)."""
    found = [(name.strip(), fields) for name, fields in equipment if pattern.search(name or "")]
    found.sort(key=lambda item: item[0].startswith("/"))
    if not found:
        return None
    name, fields = found[0]
    return {"name": name, "inventory": str(fields.get("Инв. номер") or "").strip()}


def enrich_marking(
    config: Mapping[str, Any], equipment: Iterable[tuple[str, Mapping[str, Any]]], operations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Copy of the config whose marking-by-KD operations (insMark with a mark list) carry `markKit`: the equipment
    of each mark kind and the БД.ОП norms of its own steps (None — not in the references)."""
    enriched = deepcopy(dict(config))
    ops = [item for item in enriched.get("ops") or [] if isinstance(item, dict) and item.get("type") == "insMark"
           and any(isinstance(mark, Mapping) and (mark.get("text") or mark.get("target")) for mark in item.get("marks") or [])]
    if not ops:
        return enriched
    equipment = tuple(equipment)
    norms: dict[str, dict[str, str]] = {}
    for fields in operations:
        code = str(fields.get("opNumber") or "").strip()
        if code and code not in norms:
            norms[code] = {"code": code, "operation_seconds": str(fields.get("tOp") or ""),
                           "setup_seconds": str(fields.get("tPrep") or "")}
    kit = {
        "equipment": {key: _equipment_pick(pattern, equipment) for key, pattern in MARK_EQUIPMENT.items()},
        "norms": {key: norms.get(code) or {"code": code} for key, code in MARK_NORMS.items()},
    }
    for op in ops:
        op["markKit"] = deepcopy(kit)
    return enriched


# «Погонная» операция БД.ОП (владелец 15.10.2026): время резки считается по узлам станка, как в расчётном листе цеха —
# на штуку L / «Скорость проката» + «Скорость работы инструмента» × «Кол-во операций инструмента». «Скорость проката»
# подписана в листе «сек/м», но делится на неё длина, т. е. это подача в м/с; ключ БД не переименовывается.
# Циклограмма (владелец 18.10): время — большее из ручных работ (взять + работы + снять ПФ) и машины, как в листе.
_LINEAR_COLUMNS = {
    "rollSpeed": "скорость проката", "toolSeconds": "скорость работы инструмента", "toolOps": "кол-во операций инструмента",
}
_MANUAL_COLUMNS = {"takeSeconds": "время ручных работ для взятия", "putSeconds": "время ручных работ для снятия"}


def _column(fields: Mapping[str, Any], prefix: str) -> str:
    for key, value in fields.items():
        if str(key).strip().lower().startswith(prefix):
            return str(value or "").strip()
    return ""


def operation_linear_timing(fields: Mapping[str, Any]) -> dict[str, str] | None:
    """Узлы станка строки БД.ОП, по которым ЭТК считает время резки; None — тип не «Погонный» или подача не задана
    (> 0): тогда время, как раньше, из «Время операции»."""
    if not _column(fields, "тип операции").lower().startswith("погон"):
        return None
    timing = {name: _column(fields, prefix) for name, prefix in _LINEAR_COLUMNS.items()}
    roll = decimal_or_none(timing["rollSpeed"])
    if roll is None or roll <= 0:
        return None
    manual = {name: _column(fields, prefix) for name, prefix in _MANUAL_COLUMNS.items()}
    # «Время ручных работ» без «для взятия / для снятия» — сами работы.
    manual["manualSeconds"] = next((str(value or "").strip() for key, value in fields.items()
                                    if str(key).strip().lower().startswith("время ручных работ")
                                    and not any(word in str(key).lower() for word in ("взяти", "сняти"))), "")
    return {**timing, **{name: value for name, value in manual.items() if value}}


def enrich_linear_timing(config: Mapping[str, Any], operations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Copy of the config whose operations of a «Погонная» БД.ОП record (by `opNum`) carry `linearTiming` — the
    record's node speeds; an operation whose record lost them drops the key."""
    enriched = deepcopy(dict(config))
    timings: dict[str, dict[str, str] | None] = {}
    for fields in operations:
        code = str(fields.get("opNumber") or fields.get("Номер") or "").strip()
        if code and code not in timings:
            timings[code] = operation_linear_timing(fields)
    for op in enriched.get("ops") or []:
        if not isinstance(op, dict):
            continue
        op.pop("linearTiming", None)
        timing = timings.get(str(op.get("opNum") or "").strip())
        if timing:
            op["linearTiming"] = timing
    return enriched


def coax_hex_cell(fields: Mapping[str, Any]) -> str:
    """HEX матрицы обжима втулки экрана из строки БД.КОАКС: столбец, чьё имя начинается с «HEX» (свой столбец
    владельца «HEX обжима, мм»); пусто — не задан."""
    for key, value in fields.items():
        if str(key).strip().casefold().startswith("hex") and str(value or "").strip():
            return str(value).strip()
    return ""


def enrich_coax_hex(config: Mapping[str, Any], coax_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Copy of the config whose coax ends (`coax.sideA` / `sideB`) carry `crimpHex` — the HEX of the БД.КОАКС row of that
    connector on that cable (the end's `wire`, else the assembly's cable). No row of the pair — the connector's rows on
    other cables only when they all give one HEX; otherwise no key, and the ETK says «HEX не задан — по чертежу разъёма»."""
    enriched = deepcopy(dict(config))
    coax = enriched.get("coax")
    if not isinstance(coax, dict):
        return enriched
    rows = list(coax_rows)

    def folded(value: object) -> str:
        return " ".join(str(value or "").casefold().split())

    cable = coax.get("cableArt") or coax.get("cableName") or ""
    for key in ("sideA", "sideB"):
        end = coax.get(key)
        if not isinstance(end, dict):
            continue
        end.pop("crimpHex", None)
        article = folded(end.get("article"))
        if not article:
            continue
        mine = [fields for fields in rows if folded(fields.get("coaxArticle") or fields.get("Артикул")) == article]
        wire = cable_key(end.get("wire") or cable)
        pair = [fields for fields in mine if wire and cable_key(fields.get("coaxWire") or fields.get("Провод")) == wire]
        values = {coax_hex_cell(fields) for fields in (pair or mine)}
        if len(values) == 1 and (value := next(iter(values))):
            end["crimpHex"] = value
    return enriched


COAX_DEPTH_COLUMN = "Заглубление контакта, мм"


def enrich_coax_depth(config: Mapping[str, Any], coax_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Copy of the config whose coax ends carry `contactDepth` — «Заглубление контакта, мм» of the connector (owner
    19.10.2026: the distance from the body's front face to the contact, «0,38…1,14»). It belongs to the connector, not to
    the pair, so every row of the article must give the same value; none or a disagreement — no key, nothing is drawn."""
    enriched = deepcopy(dict(config))
    coax = enriched.get("coax")
    if not isinstance(coax, dict):
        return enriched
    rows = list(coax_rows)
    for key in ("sideA", "sideB"):
        end = coax.get(key)
        if not isinstance(end, dict):
            continue
        end.pop("contactDepth", None)
        article = " ".join(str(end.get("article") or "").casefold().split())
        if not article:
            continue
        values = {str(fields.get(COAX_DEPTH_COLUMN) or "").strip() for fields in rows
                  if " ".join(str(fields.get("coaxArticle") or fields.get("Артикул") or "").casefold().split()) == article}
        if len(values) == 1 and (value := next(iter(values))):
            end["contactDepth"] = value
    return enriched


COAX_END_DIAMETERS = ("d1", "d2", "d3")


def enrich_coax_diameters(config: Mapping[str, Any], dia_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Copy of the config whose coax ends (`coax.sideA` / `sideB`) carry `d1`–`d3` of the assembly's CABLE only (owner
    16.10.2026: «разъём тянет L1–L3, а провод — свои данные»): the D-table row (СПР.КАБ · коаксиал and the cable's other
    names, `coax_dia_rows`) of `cableArt`, else `cableName` — the generator's `coaxCableDiaLookup_` key, the last row of
    a key wins as in `coaxDiaFromRecords`. A cable without a row — no D on the end (the ETK shows «—»), never the D a
    saved config took from the connector row."""
    enriched = deepcopy(dict(config))
    coax = enriched.get("coax")
    if not isinstance(coax, dict):
        return enriched
    index: dict[str, Mapping[str, Any]] = {}
    for row in dia_rows:
        first = next(iter(row.values()), "")
        if key := cable_key(first):
            index[key] = row
    row = index.get(cable_key(coax.get("cableArt") or coax.get("cableName") or ""))
    for side in ("sideA", "sideB"):
        end = coax.get(side)
        if not isinstance(end, dict):
            continue
        for name in COAX_END_DIAMETERS:
            value = str((row or {}).get(name.upper()) or "").strip()
            if value:
                end[name] = value
            else:
                end.pop(name, None)
    return enriched


def enrich_terminal_norms(
    config: Mapping[str, Any], records: Iterable[TerminalRecord],
    gost_awg: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return a copied generator config with terminal norms resolved per wire section.

    `gost_awg` is the workshop ГОСТ section → AWG map («Сечения AWG ↔ мм²»).
    """
    enriched = deepcopy(dict(config))
    terminal_records = tuple(records)
    operations = enriched.get("ops")
    if not isinstance(operations, list):
        return enriched

    for operation in operations:
        if not isinstance(operation, dict) or operation.get("type") not in {"prsTerm", "cutWireCrimp"}:
            continue
        # A cut that crimps both ends («Обе стороны») carries side В as `epB`: its norm goes to `terminalNormB`.
        sides = (("ep", "terminalNorm"), ("epB", "terminalNormB")) if operation.get("type") == "cutWireCrimp" else (("ep", "terminalNorm"),)
        for endpoint_key, norm_key in sides:
            _enrich_side(operation, operation.get(endpoint_key), norm_key, terminal_records, gost_awg)
    return enriched


def _enrich_side(
    operation: dict[str, Any], endpoint: object, norm_key: str, terminal_records: tuple[TerminalRecord, ...],
    gost_awg: Mapping[str, int] | None,
) -> None:
    if not isinstance(endpoint, Mapping):
        return
    article = str(endpoint.get("termArt") or endpoint.get("termName") or "").strip()
    wires = operation.get("wires")
    if not article or not isinstance(wires, list):
        return
    labels = tuple(str(wire.get("art") or wire.get("name") or "") for wire in wires if isinstance(wire, Mapping))
    sections = tuple(wire_section(label, gost_awg) for label in labels)
    if endpoint.get("fold"):
        # A core folded under the contact is crimped as a doubled cross-section.
        sections = tuple(fold_wire_section(section) for section in sections)
    resolution = resolve_terminal(terminal_records, article, sections)
    # The ETK names each contact's norms per end: which wires a section row crimps and the contact's strip length.
    candidates = matching_terminal_records(terminal_records, article)
    by_label = {section.label: section for section in sections if section.label}

    def section_strip(label: str) -> str:
        record = terminal_record_for_section(candidates, by_label[label]) if label in by_label else None
        return record.strip.strip() if record else ""
    operation[norm_key] = {
        "applicator": resolution.applicator,
        "crimpHeight": resolution.crimp_height,
        "pullForce": resolution.pull_force,
        "bySection": [
            {
                "section": item.section,
                "crimpHeight": item.crimp_height,
                "pullForce": item.pull_force,
                "applicator": item.applicator,
                "rangeOnly": item.range_only,
                "found": item.found,
                "wires": list(dict.fromkeys(
                    label for label, section in zip(labels, sections) if label and section.label == item.section
                )),
                "strip": section_strip(item.section),
            }
            for item in resolution.by_section
        ],
    }
