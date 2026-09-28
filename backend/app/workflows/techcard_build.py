"""Enrich an ETK build config with norms owned by the references module."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from app.modules.references.technology import (
    TerminalRecord, fold_wire_section, resolve_terminal, wire_section,
)


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
