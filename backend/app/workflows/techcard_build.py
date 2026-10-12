"""Enrich an ETK build config with norms owned by the references module."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, Iterable, Mapping

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.cable_structure import gauge_gost_awg
from app.modules.references.domain import TechOperationTab, gost_awg_map
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
            return dict(config), None
        records = references_uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=10_000)
        # ГОСТ section → AWG from «Сечения AWG ↔ мм²» of the база монтажных проводов (the old СПР.КАБ AWG table is
        # retired, owner 12.10.2026); the stored map only when the sizes table is empty.
        gost_awg = gauge_gost_awg(references_uow.cable_structure.gauges()) or gost_awg_map(references_uow.references.list())
    enriched = enrich_terminal_norms(config, (TerminalRecord.from_fields(record.fields) for record in records), gost_awg)
    return enriched, {
        "schema_version": metadata["schema_version"],
        "source_updated_at": metadata["source_updated_at"].isoformat() if metadata["source_updated_at"] else None,
        "imported_at": metadata["imported_at"].isoformat(),
    }


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
