"""Pure TECHMAP-GRAPHER project export import for the native ETK builder.

A thin adapter: the old keys (`groups`, `connectors`, `pairs`, `warnings`) feed the generator's «из чертежа» button
as before; the full read comes from the harness domain model (`harness/`) and is added under new keys."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from io import BytesIO
import json
import re
from typing import Any, Callable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile

from .harness import SheetRow, match_sheet, parse_design, route_ops, wire_ops
from .harness.derive import ImpliedOp, block_of, covering_ops, end_title, wire_color, wire_material
from .harness.model import HarnessDesign, Side, Wire
from .harness.rules import (
    COVERING_KINDS, HarnessRuleError, blank_cut, covering_length, covering_segments, covering_wires, sheath_strip,
    strip_steps, terminal_name,
)


SNAPSHOT_PATH = "project/snapshot.json"
SNAPSHOT_FORMAT = 4
SOURCE_COMMIT = "e63befb85edb79f0c1635ac8314539ac24d2486a"
MAX_EXPORT_BYTES = 30 * 1024 * 1024


class GrapherImportError(ValueError):
    pass


@dataclass(frozen=True)
class GrapherComponent:
    id: str
    article: str
    name: str


def cut_length(
    length_mm: object,
    correction_from_mm: object = 0,
    correction_to_mm: object = 0,
    rounding_step_mm: object = 1,
) -> Decimal | None:
    if length_mm is None or length_mm == "":
        return None
    length = Decimal(str(length_mm))
    total = length + Decimal(str(correction_from_mm or 0)) + Decimal(str(correction_to_mm or 0))
    step = Decimal(str(rounding_step_mm or 1))
    if total < 0 or step <= 0:
        return None
    return (total / step).to_integral_value(rounding=ROUND_CEILING) * step


def material_tokens(value: object) -> tuple[str, ...]:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"(\d),(\d)", r"\1.\2", text).replace("×", "x").replace("*", "x")
    return tuple(
        token.strip(".-") for token in re.split(r"[^a-zа-я0-9.\-x]+", text)
        if token.strip(".-") and (len(token.strip(".-")) > 1 or any(char.isdigit() for char in token))
    )


def match_component(material: object, components: Sequence[GrapherComponent]) -> str:
    wanted = material_tokens(material)
    if not wanted:
        return ""
    best, score, tied = "", 0, False
    for component in components:
        available = set(material_tokens(f"{component.article} {component.name}"))
        hits = [token for token in wanted if token in available]
        if not any(any(char.isdigit() for char in token) for token in hits):
            continue
        if len(hits) > score:
            best, score, tied = component.id, len(hits), False
        elif len(hits) == score and score:
            tied = True
    return best if score >= min(2, len(wanted)) and not tied else ""


def _end(connectors: Mapping[str, Mapping[str, Any]], value: object) -> tuple[Mapping[str, Any], Mapping[str, Any] | None] | None:
    if not isinstance(value, Mapping) or not value.get("connectorId"):
        return None
    connector = connectors.get(str(value["connectorId"]))
    if connector is None:
        return None
    contact = next((
        item for item in connector.get("contacts", [])
        if isinstance(item, Mapping) and item.get("id") == value.get("contactId")
    ), None)
    return connector, contact


def _end_label(value, raw: object = None) -> str:
    if value is None:
        # Not a contact: a splice, a screen or a free end (a free end used to read «сросток» too).
        endpoint = raw if isinstance(raw, Mapping) else {}
        return "сросток" if endpoint.get("junctionId") else "экран" if endpoint.get("screenId") else "свободный конец"
    connector, contact = value
    label = str(connector.get("designation") or connector.get("partNumber") or "?")
    return f"{label}:{contact.get('number')}" if contact else label


def _material(binding: object, ends: Sequence[object]) -> str:
    if isinstance(binding, Mapping) and binding.get("displayName"):
        return str(binding["displayName"]).strip()
    for value in ends:
        if value is None or value[1] is None:
            continue
        contact = value[1]
        if contact.get("wire") or contact.get("wireSection"):
            return f"{contact.get('wire', '')} {contact.get('wireSection', '')}".strip()
    return ""


def design_groups(design: Mapping[str, Any], components: Sequence[GrapherComponent] = ()) -> dict[str, Any]:
    connectors = {
        str(item.get("id")): item for item in design.get("connectors", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    wires = {
        str(item.get("id")): item for item in design.get("wires", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    cable_members = {
        str(wire_id) for cable in design.get("cables", []) if isinstance(cable, Mapping)
        for wire_id in cable.get("memberWireIds", [])
    }
    groups: dict[tuple, dict[str, Any]] = {}
    warnings: list[str] = []

    def add(item: dict[str, Any]) -> None:
        key = (item["material"], item["length_mm"], item["cores"], item["jacket_mm"])
        group = groups.setdefault(key, {
            "material": item["material"], "length_mm": item["length_mm"],
            "quantity": 0, "cores": item["cores"], "jacket_mm": item["jacket_mm"],
            "correction_from_mm": item["correction_from_mm"],
            "correction_to_mm": item["correction_to_mm"], "cut_mm": item["cut_mm"],
            "ends": [], "source_ids": [],
        })
        group["quantity"] += 1
        group["source_ids"].append(item["id"])
        if len(group["ends"]) < 6:
            group["ends"].append(item["ends"])
        if (
            group["correction_from_mm"] != item["correction_from_mm"]
            or group["correction_to_mm"] != item["correction_to_mm"]
        ):
            group["mixed_corrections"] = True

    for wire_id, wire in wires.items():
        if wire_id in cable_members:
            continue
        ends = (_end(connectors, wire.get("from")), _end(connectors, wire.get("to")))
        length = wire.get("lengthMm")
        if length is None:
            warnings.append(f"Провод {wire.get('circuit') or wire_id} без длины в чертеже")
        add({
            "id": wire_id, "material": _material(wire.get("materialBinding"), ends),
            "length_mm": None if length is None else Decimal(str(length)), "cores": 1,
            "jacket_mm": None,
            "correction_from_mm": Decimal(str(wire.get("endCorrectionFromMm") or 0)),
            "correction_to_mm": Decimal(str(wire.get("endCorrectionToMm") or 0)),
            "cut_mm": cut_length(length, wire.get("endCorrectionFromMm"), wire.get("endCorrectionToMm"), wire.get("cutRoundingStepMm")),
            "ends": f"{_end_label(ends[0], wire.get('from'))} → {_end_label(ends[1], wire.get('to'))}{f' {wire.get("color")}' if wire.get('color') else ''}",
        })

    for cable in design.get("cables", []):
        if not isinstance(cable, Mapping):
            continue
        members = [wires[str(wire_id)] for wire_id in cable.get("memberWireIds", []) if str(wire_id) in wires]
        ends = (
            _end(connectors, members[0].get("from")), _end(connectors, members[0].get("to")),
        ) if members else (None, None)
        strip = cable.get("sheathStrip") if isinstance(cable.get("sheathStrip"), Mapping) else {}
        strip_values = [Decimal(str(value)) for value in (strip.get("fromMm"), strip.get("toMm")) if value is not None]
        jacket = max(strip_values) if strip_values else None
        if len(set(strip_values)) > 1:
            warnings.append(f"Кабель {cable.get('id')}: разделка оболочки различается на концах")
        length = cable.get("lengthMm")
        if length is None:
            warnings.append(f"Кабель {cable.get('id')} без длины в чертеже")
        add({
            "id": str(cable.get("id") or ""), "material": _material(cable.get("materialBinding"), ()),
            "length_mm": None if length is None else Decimal(str(length)), "cores": len(members) or 1,
            "jacket_mm": jacket,
            "correction_from_mm": Decimal(str(cable.get("endCorrectionFromMm") or 0)),
            "correction_to_mm": Decimal(str(cable.get("endCorrectionToMm") or 0)),
            "cut_mm": cut_length(length, cable.get("endCorrectionFromMm"), cable.get("endCorrectionToMm"), cable.get("cutRoundingStepMm")),
            "ends": f"кабель {len(members)} жил: {_end_label(ends[0], members[0].get('from') if members else None)}"
                    f" → {_end_label(ends[1], members[0].get('to') if members else None)}",
        })

    result_groups = []
    for group in groups.values():
        if not group["material"]:
            warnings.append(f"У провода {group['source_ids'][0]} не указан материал")
        group["component_id"] = match_component(group["material"], components)
        result_groups.append(group)
    connector_rows = []
    for connector in connectors.values():
        terminals = sorted({
            str(contact["terminalArticle"]).strip()
            for contact in connector.get("contacts", [])
            if isinstance(contact, Mapping) and contact.get("terminalArticle")
        })
        connector_rows.append({
            "designation": str(connector.get("designation") or ""),
            "part_number": str(connector.get("partNumber") or ""), "terminals": terminals,
        })
    return {
        "groups": result_groups, "connectors": connector_rows,
        "pairs": len(design.get("diffPairs", [])), "warnings": warnings,
    }


def _n(value: Decimal | None) -> Decimal | int | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else value.normalize()


def _plain(value: Any) -> Any:
    """New keys carry JSON numbers (the old keys keep their Decimal → string-or-number shape)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _block(row) -> dict[str, Any] | None:
    return {"id": row.id, "title": row.title, "index": row.index} if row is not None else None


def _op_view(op: ImpliedOp) -> dict[str, Any]:
    return {"type": op.type, "label": op.label, "side": op.side, "shared": op.shared, "block": op.block,
            "data": dict(op.data)}


END_LABELS = {"contact": "Контакт", "junction": "Сросток", "screen": "Экран", "free": "Свободный конец"}


def _end_view(design: HarnessDesign, wire: Wire, side: Side) -> dict[str, Any]:
    end = wire.end(side)
    profile = wire.strip(side)
    view: dict[str, Any] = {
        "type": end.kind, "type_label": END_LABELS[end.kind], "title": end_title(design, wire, side),
        "connector_id": end.connector_id, "connector": "", "part_number": "", "contact_id": end.contact_id,
        "contact_number": None, "contact_name": "", "contact_type": "", "circuit": "", "terminal_article": "",
        "terminal": "", "terminal_l_plus_mm": None, "terminal_l_minus_mm": None, "terminal_details": None,
        "junction_id": end.junction_id, "screen_id": end.screen_id, "screen_side": end.screen_side,
        "correction_mm": _n(wire.correction(side)), "strip_profile": profile.pin.display_name if profile else "",
    }
    connector, contact = design.contact_at(end)
    if connector is not None:
        view.update(connector=connector.designation or connector.part_number, part_number=connector.part_number)
    if contact is not None:
        snapshot = contact.terminal
        view.update(
            contact_number=contact.number, contact_name=contact.name, contact_type=contact.contact_type,
            circuit=contact.circuit, terminal_article=contact.terminal_article,
            terminal=terminal_name(contact) if contact.terminal_article else "",
            terminal_l_plus_mm=_n(snapshot.l_plus_mm) if snapshot else None,
            terminal_l_minus_mm=_n(snapshot.l_minus_mm) if snapshot else None,
            terminal_details={"manufacturer": snapshot.manufacturer, "series": snapshot.series,
                              "description": snapshot.description, "article": snapshot.article} if snapshot else None,
        )
    return view


def _cut(item) -> tuple[Decimal | None, Decimal | None, str]:
    try:
        cut = blank_cut(item)
    except HarnessRuleError as error:
        return None, None, str(error)
    return cut.unrounded_mm, cut.cut_mm, ""


def _wire_view(design: HarnessDesign, wire: Wire, match: Callable[[str], str], notes: list[str]) -> dict[str, Any]:
    material, article, section = wire_material(design, wire)
    cable = design.cable_of(wire.id)
    unrounded, cut_mm, _error = _cut(wire)
    ops, op_notes = wire_ops(design, wire)
    notes.extend(note for note in op_notes if note not in notes)
    topology = design.topology
    return {
        "id": wire.id, "circuit": wire.circuit, "color": wire_color(design, wire), "material": material,
        "material_article": article, "section": section, "component_id": match(material),
        "cable_id": cable.id if cable else "",
        "length_mm": _n(wire.length_mm), "correction_from_mm": _n(wire.correction_a_mm),
        "correction_to_mm": _n(wire.correction_b_mm), "rounding_step_mm": _n(wire.rounding_step_mm),
        "unrounded_mm": _n(unrounded), "cut_mm": _n(cut_mm),
        "label": f"{end_title(design, wire, 'A')} → {end_title(design, wire, 'B')}",
        "from": _end_view(design, wire, "A"), "to": _end_view(design, wire, "B"),
        "block": _block(block_of(design, "cable", cable.id) if cable else block_of(design, "wire", wire.id)),
        "covering_ids": [c.id for c in topology.coverings if wire.id in covering_wires(topology, c)] if topology else [],
        "ops": [_op_view(op) for op in ops],
        "route_ops": [_op_view(op) for op in route_ops(design, wire)],
    }


def _cable_views(design: HarnessDesign, match: Callable[[str], str], notes: list[str]) -> list[dict[str, Any]]:
    views = []
    for index, cable in enumerate(design.cables, start=1):
        material = cable.material.display_name if cable.material else ""
        label = f"Кабель {index}" + (f" · {material}" if material else "")
        unrounded, cut_mm, error = _cut(cable)
        if error:
            notes.append(f"{label}: {error}")
        try:
            strip = sheath_strip(cable)
        except HarnessRuleError as strip_error:
            notes.append(f"{label}: {strip_error}")
            strip = None
        views.append({
            "id": cable.id, "label": label, "material": material,
            "material_article": cable.material.source_key if cable.material else "", "component_id": match(material),
            "member_wire_ids": list(cable.member_wire_ids), "cores": len(cable.member_wire_ids),
            "length_mm": _n(cable.length_mm), "correction_from_mm": _n(cable.correction_a_mm),
            "correction_to_mm": _n(cable.correction_b_mm), "rounding_step_mm": _n(cable.rounding_step_mm),
            "unrounded_mm": _n(unrounded), "cut_mm": _n(cut_mm),
            "sheath_strip": None if strip is None else {
                "from_mm": _n(strip.a_mm), "to_mm": _n(strip.b_mm),
                "cut_from_mm": _n(strip.cut_a_mm), "cut_to_mm": _n(strip.cut_b_mm),
            },
            "block": _block(block_of(design, "cable", cable.id)),
        })
    return views


def _covering_views(design: HarnessDesign, match: Callable[[str], str], notes: list[str]) -> list[dict[str, Any]]:
    topology = design.topology
    if topology is None:
        return []

    def node(node_id: str) -> str:
        connector = design.connector(topology.node_connector.get(node_id, ""))
        return connector.designation if connector else ""

    views = []
    for covering in topology.coverings:
        length = covering_length(covering, topology.dimensions)
        if length is None:
            notes.append(f"Оболочка {covering.name}: длина не задана — нужен размер на чертеже или ручная длина")
        if covering.material is None:
            notes.append(f"Оболочка {covering.name}: материал не назначен")
        spans = []
        for span in covering.spans:
            first = topology.pipe_segments.get(span.segment_id, (span.segment_id,))
            nodes = topology.segment_nodes.get(first[0]) if first else None
            if nodes is None:
                notes.append(f"Оболочка {covering.name}: участок {span.segment_id} не найден в топологии")
            spans.append({"segment_id": span.segment_id, "from": _n(span.start), "to": _n(span.stop),
                          "from_connector": node(nodes[0]) if nodes else "",
                          "to_connector": node(nodes[1]) if nodes else ""})
        wire_ids = covering_wires(topology, covering)
        material = covering.material.display_name if covering.material else ""
        views.append({
            "id": covering.id, "name": covering.name, "kind": covering.kind,
            "kind_label": COVERING_KINDS.get(covering.kind, covering.kind), "material": material,
            "material_article": covering.material.source_key if covering.material else "",
            "component_id": match(material), "color": covering.color,
            "length_mode": covering.length_mode or "auto", "length_mm": _n(length), "spans": spans,
            "segment_ids": covering_segments(topology, covering), "wire_ids": wire_ids,
            "cable_ids": sorted({cable.id for w in wire_ids if (cable := design.cable_of(w)) is not None}),
            "block": _block(block_of(design, "covering", covering.id)),
            "ops": [_op_view(op) for op in covering_ops(design, covering)],
        })
    return views


SOURCE_KINDS_RU = {"wire": "провод", "cable": "кабель", "covering": "оболочка", "connector": "соединитель"}


def _route_views(design: HarnessDesign) -> list[dict[str, Any]]:
    by_id = {wire.id: wire for wire in design.wires}
    coverings = {c.id: c.name for c in design.topology.coverings} if design.topology else {}

    def source_label(kind: str, item_id: str) -> str:
        if kind == "wire":
            return f"провод {(by_id[item_id].circuit if item_id in by_id else '') or item_id}"
        if kind == "covering":
            return coverings.get(item_id, item_id)
        if kind == "connector":
            connector = design.connector(item_id)
            return (connector.designation if connector else "") or item_id
        return f"{SOURCE_KINDS_RU.get(kind, kind)} {item_id}"

    return [{
        "id": row.id, "kind": row.kind,
        "kind_label": {"semiFinished": "Полуфабрикат", "assembly": "Сборка"}.get(row.kind, row.kind),
        "role": row.role, "index": row.index, "title": row.title, "quantity": _n(row.quantity),
        "reserve": _n(row.reserve), "operation_time_min": _n(row.operation_time_min), "comment": row.comment,
        "prepared": row.prepared, "depends_on": list(row.depends_on),
        "sources": [{"kind": kind, "id": item_id, "label": source_label(kind, item_id)} for kind, item_id in row.sources],
        "operations": [{
            "id": op.id, "code": op.operation.source_key if op.operation else "",
            "name": op.operation.display_name if op.operation else "", "mode": op.mode, "note": op.note,
        } for op in row.operations],
        "terminal_requirements": [{
            "wire_id": req.wire_id, "end": "from" if req.side == "A" else "to",
            "terminal_article": req.terminal_article, "strip_length_mm": _n(req.strip_length_mm),
        } for req in row.terminal_requirements],
    } for row in (design.route.rows if design.route else ())]


def harness_view(
    content: Mapping[str, Any], components: Sequence[GrapherComponent] = (), sheet: Sequence[SheetRow] = (),
) -> dict[str, Any]:
    """New keys of one harness: the full design read through the harness model."""
    design, warnings = parse_design(content)

    def match(material: str) -> str:
        return match_component(material, components)

    notes = list(warnings)
    by_id = {wire.id: wire for wire in design.wires}
    route = design.route
    matches, sheet_warnings = match_sheet(design, sheet) if sheet else ([], [])
    return _plain({
        "format": {"schema_version": design.schema_version, "writer_contract": design.writer_contract},
        "wires": [_wire_view(design, wire, match, notes) for wire in design.wires],
        "junctions": [{
            "id": j.id, "label": j.label, "wire_ids": list(j.wire_ids),
            "ending_wire_ids": [
                w for w in j.wire_ids if w in by_id and j.id in (by_id[w].end_a.junction_id, by_id[w].end_b.junction_id)
            ],
            "circuits": sorted({by_id[w].circuit for w in j.wire_ids if w in by_id} - {""}),
        } for j in design.junctions],
        "screens": [{
            "id": s.id, "label": s.label, "wire_ids": list(s.wire_ids), "terminal_side": s.terminal_side,
            "drain_wire_ids": [w.id for w in design.wires if s.id in (w.end_a.screen_id, w.end_b.screen_id)],
        } for s in design.screens],
        "diff_pairs": [{
            "id": p.id, "wire_ids": list(p.wire_ids),
            "circuits": [(by_id[w].circuit if w in by_id else "") or w for w in p.wire_ids],
            "step": _n(p.step), "amplitude": _n(p.amplitude), "variant": p.variant,
        } for p in design.diff_pairs],
        # The source has no separate twist entity: a diff pair is drawn twisted (editor/drawing-twisted-pair.ts).
        "twisted_groups": [
            {"id": p.id, "kind": "pair", "label": "Витая пара", "wire_ids": list(p.wire_ids)} for p in design.diff_pairs
        ],
        "cables": _cable_views(design, match, notes),
        "strip_profiles": [{
            "wire_id": wire.id, "end": "from" if side == "A" else "to", "circuit": wire.circuit,
            "profile": profile.pin.display_name, "source_key": profile.pin.source_key,
            "layers": [{"name": f"L{layer.index}", "index": layer.index, "length_mm": _n(layer.length_mm),
                        "step_mm": _n(step), "diameter_mm": _n(layer.diameter_mm)}
                       for layer, step in strip_steps(profile.layers)],
        } for wire in design.wires for side, profile in (("A", wire.strip_a), ("B", wire.strip_b)) if profile],
        "coverings": _covering_views(design, match, notes),
        "route_status": route.status if route else "",
        "route_status_label": {"draft": "Черновик", "completed": "Готов"}.get(route.status, route.status) if route else "",
        "route_rows": _route_views(design),
        "connector_details": [{
            "id": c.id, "designation": c.designation, "part_number": c.part_number, "library": c.library,
            "template_code": c.template_code, "template_name": c.template_name,
            "template_article": c.template_article, "template_version": c.template_version,
            "contacts": [{
                "id": k.id, "number": k.number, "name": k.name, "contact_type": k.contact_type, "circuit": k.circuit,
                "terminal_article": k.terminal_article, "terminal": terminal_name(k) if k.terminal_article else "",
                "terminal_l_plus_mm": _n(k.terminal.l_plus_mm) if k.terminal else None,
                "terminal_l_minus_mm": _n(k.terminal.l_minus_mm) if k.terminal else None,
                "wire": k.wire, "wire_section": k.wire_section, "color": k.color, "status": k.status,
            } for k in c.contacts],
        } for c in design.connectors],
        "sheet_match": [{
            "kind": m.kind, "name": m.name, "article": m.article, "unit": m.unit, "quantity": _n(m.quantity),
            "item_ids": list(m.item_ids), "sheet_id": m.sheet_id, "sheet_quantity": _n(m.sheet_quantity),
            "matched_by": m.matched_by, "status": m.status,
        } for m in matches],
        "sheet_warnings": sheet_warnings,
        "import_warnings": notes,
    })


def project_catalog(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Pinned characteristics, component snapshots and placements of the export (snapshotFormat 4, as written by
    src/Techmap.Infrastructure.Sqlite/SqliteProjectExportService.cs of TECHMAP-GRAPHER e63befb)."""
    def rows(key: str) -> list[Mapping[str, Any]]:
        value = snapshot.get(key)
        return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    warnings = [
        f"В экспорте нет раздела «{label}» (старый экспорт) — раздел пуст"
        for key, label in (("pinnedCharacteristics", "закреплённые характеристики"),
                           ("componentSnapshots", "снимки компонентов"),
                           ("componentPlacements", "размещения компонентов"))
        if key not in snapshot
    ]
    if snapshot.get("snapshotFormat") not in (None, SNAPSHOT_FORMAT):
        warnings.append(
            f"Формат экспорта {snapshot.get('snapshotFormat')} не знаком (ERP знает {SNAPSHOT_FORMAT}) — прочитано что понятно"
        )
    components = {str(item.get("snapshotId") or ""): item for item in rows("componentSnapshots")}
    return {
        "pinned": [{
            "source_kind": str(item.get("sourceKind") or ""), "record_key": str(item.get("sourceRecordKey") or ""),
            "version": str(item.get("sourceVersion") or ""), "name": str(item.get("characteristicName") or ""),
            "value": str(item.get("characteristicValue") or ""), "unit": str(item.get("unit") or ""),
        } for item in rows("pinnedCharacteristics")],
        "components": [{
            "snapshot_id": snapshot_id, "code": str(item.get("code") or ""), "name": str(item.get("name") or ""),
            "version": item.get("sourceVersion"),
            "articles": [str(b.get("articleKey") or "") for b in item.get("articleBindings") or [] if isinstance(b, Mapping)],
        } for snapshot_id, item in components.items()],
        "placements": [{
            "harness_id": str(item.get("harnessId") or ""), "article_key": str(item.get("articleKey") or ""),
            "entity_type": str(item.get("entityType") or ""), "snapshot_id": str(item.get("snapshotId") or ""),
            "component_code": str(components.get(str(item.get("snapshotId") or ""), {}).get("code") or ""),
        } for item in rows("componentPlacements")],
    }, warnings


def import_snapshot(
    snapshot: Mapping[str, Any], components: Sequence[GrapherComponent] = (), sheet: Sequence[SheetRow] = (),
) -> dict[str, Any]:
    if not isinstance(snapshot.get("project"), Mapping) or not isinstance(snapshot.get("harnesses"), list):
        raise GrapherImportError("Это не экспорт TECHMAP-GRAPHER: нет project/harnesses")
    harnesses = []
    for harness in snapshot["harnesses"]:
        if not isinstance(harness, Mapping):
            continue
        design = harness.get("design") if isinstance(harness.get("design"), Mapping) else {}
        if design.get("schemaVersion") is not None and int(design["schemaVersion"]) != 1:
            raise GrapherImportError(
                f"Жгут {harness.get('designation')}: поддерживается только схема чертежа 1"
            )
        content = design.get("content") if isinstance(design.get("content"), Mapping) else design
        harnesses.append({
            "designation": str(harness.get("designation") or ""),
            "quantity": int(harness.get("quantity") or 0),
            **design_groups(content, components),
            "id": str(harness.get("harnessId") or ""),
            **harness_view(content, components, sheet),
        })
    project = snapshot["project"]
    catalog, warnings = project_catalog(snapshot)
    return {
        "project": {
            "designation": str(project.get("designation") or ""),
            "name": str(project.get("name") or ""),
        },
        "harnesses": harnesses,
        "format": {"snapshot_format": snapshot.get("snapshotFormat"), "source_commit": SOURCE_COMMIT},
        "catalog": catalog,
        "import_warnings": warnings,
    }


def read_export(
    content: bytes, filename: str, components: Sequence[GrapherComponent] = (), sheet: Sequence[SheetRow] = (),
) -> dict[str, Any]:
    if not content:
        raise GrapherImportError("Файл пустой")
    if len(content) > MAX_EXPORT_BYTES:
        raise GrapherImportError("Файл больше 30 МБ")
    try:
        if filename.lower().endswith(".json"):
            payload = content
        else:
            with ZipFile(BytesIO(content)) as archive:
                try:
                    payload = archive.read(SNAPSHOT_PATH)
                except KeyError as error:
                    raise GrapherImportError(f"В архиве нет {SNAPSHOT_PATH}") from error
        snapshot = json.loads(payload.decode("utf-8"))
    except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GrapherImportError("Не удалось разобрать экспорт TECHMAP-GRAPHER") from error
    if not isinstance(snapshot, Mapping):
        raise GrapherImportError("Корень snapshot.json должен быть объектом")
    return import_snapshot(snapshot, components, sheet)
