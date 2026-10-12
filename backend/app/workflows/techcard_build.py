"""Enrich an ETK build config with norms owned by the references module."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any, Iterable, Mapping

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab, gost_awg_map
from app.modules.references.technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION, TerminalRecord, fold_wire_section, gost_awg_from_wire_table, matching_terminal_records,
    resolve_terminal, terminal_record_for_section, wire_section,
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
            return dict(config), None
        records = references_uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=10_000)
        # SUPPLYMAP gostAwgMap_: the map comes from the СПР.КАБ AWG table; the stored map only when it is not loaded.
        gost_awg = gost_awg_from_wire_table(record.fields for record in references_uow.tech_operations.list(
            tab=TechOperationTab.KAB_AWG, query="", limit=1_000,
        )) or gost_awg_map(references_uow.references.list())
        equipment = [(record.display_text, record.fields) for record in references_uow.tech_operations.list(
            tab=TechOperationTab.EQUIPMENT, query="", limit=10_000,
        )]
        operation_records = [record.fields for record in references_uow.tech_operations.list(
            tab=TechOperationTab.OPERATION, query="", limit=100_000,
        )]
    enriched = enrich_terminal_norms(config, (TerminalRecord.from_fields(record.fields) for record in records), gost_awg)
    enriched = enrich_marking(enriched, equipment, operation_records)
    return enriched, {
        "schema_version": metadata["schema_version"],
        "source_updated_at": metadata["source_updated_at"].isoformat() if metadata["source_updated_at"] else None,
        "imported_at": metadata["imported_at"].isoformat(),
    }


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


def enrich_terminal_norms(
    config: Mapping[str, Any], records: Iterable[TerminalRecord],
    gost_awg: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return a copied generator config with terminal norms resolved per wire section.

    `gost_awg` is the workshop ГОСТ section → AWG map (SUPPLYMAP СПР.КАБ).
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
