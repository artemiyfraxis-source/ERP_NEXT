"""Enrich an ETK build config with norms owned by the references module."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, Iterable, Mapping

from app.modules.references.application import UnitOfWork as ReferencesUnitOfWork
from app.modules.references.domain import TechOperationTab, gost_awg_map
from app.modules.references.technology import (
    TECH_SNAPSHOT_SCHEMA_VERSION, TerminalRecord, fold_wire_section, gost_awg_from_wire_table, resolve_terminal,
    wire_section,
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
        endpoint = operation.get("ep")
        if not isinstance(endpoint, Mapping):
            continue
        article = str(endpoint.get("termArt") or endpoint.get("termName") or "").strip()
        wires = operation.get("wires")
        if not article or not isinstance(wires, list):
            continue
        sections = tuple(
            wire_section(str(wire.get("art") or wire.get("name") or ""), gost_awg)
            for wire in wires if isinstance(wire, Mapping)
        )
        if endpoint.get("fold"):
            # A core folded under the contact is crimped as a doubled cross-section.
            sections = tuple(fold_wire_section(section) for section in sections)
        resolution = resolve_terminal(terminal_records, article, sections)
        operation["terminalNorm"] = {
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
                }
                for item in resolution.by_section
            ],
        }
    return enriched
