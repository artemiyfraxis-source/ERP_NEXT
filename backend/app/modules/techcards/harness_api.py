"""The harness drawing of a position, kept as its КД and edited in ERP (TECHMAP-GRAPHER embedded with the author's
permission, see THIRD_PARTY_NOTICES.md).

Storage (no tables of its own): ONE row of the КД register per position — the design document (schemaVersion 1, as
`harness.parse_design` / `harness.dump_design` read and write it, unknown fields kept), content type
`application/vnd.fraxis.harness+json`, saved over itself. The version token is a hash of the stored bytes: a save
names the version it was made on and gets 409 when someone saved another one since.

«Ручная сборка» from the drawing (`derive`) is pure: the operations `harness.derive.wire_ops` implies plus the
route's own assembly steps, in blocks from the drawing's manufacturing route, each operation keyed
`harness:<object>:<code>` so the frontend's `rederive` merges it into the master's scheme. Nothing is saved.

Export / import: a `.techmap-project.zip` with `project/snapshot.json` as TECHMAP-GRAPHER writes it (snapshotFormat
4, harness design schemaVersion 1). After an import the ERP copy is the truth.

Library components placed on the drawing (TECHMAP-GRAPHER's project-level `componentSnapshots` / `componentPlacements`:
the pinned template a connector was placed from, which the editor needs to draw it) live inside the design document
under the same two keys — only this harness's rows. The editor's own document model has no such section, so a plain
save carries them over from the stored drawing; the export moves them back to the project level, the import in.

«Отвязать» (unlink) relabels the row to HARNESS_UNLINKED_CONTENT_TYPE: the position no longer reads it, the card lists it
with «Вернуть» (restore), which relabels it back. A harness drawing is never hard-deleted."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
import hashlib
from io import BytesIO
import json
from typing import Annotated, Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, projects_edit_principal, projects_view_principal
from app.shared.stored_files import decode_upload, file_response
from app.workflows.project_kitset import MAX_DOCUMENT_BYTES

from .domain.route import RouteError, RouteOp, parse_route
from .harness import HarnessDesign, dump_design, parse_design, wire_ops
from .harness.derive import ROUTE_MODE_LABELS, ImpliedOp, wire_color, wire_material
from .harness.snapshot import SCHEMA_VERSION
from .kitset_api import (
    HARNESS_CONTENT_TYPE, HARNESS_UNLINKED_CONTENT_TYPE, _document_json, document_kind, techcards_factory,
)
from .kitsets import KitDocument, Kitset, item_id

router = APIRouter(prefix="/api/techcards/kitsets", tags=["Проекты"])

TITLE = "Чертёж жгута"
SNAPSHOT_PATH = "project/snapshot.json"
SNAPSHOT_FORMAT = 4
MAX_EXPORT_BYTES = 30 * 1024 * 1024
FINAL_KEY = "b1"
UNROUTED_KEY = "route:-"
UNROUTED_NAME = "Не в маршруте КД"
COMPONENT_SNAPSHOTS = "componentSnapshots"
COMPONENT_PLACEMENTS = "componentPlacements"
COMPONENT_GRAPH_KEYS = (COMPONENT_SNAPSHOTS, COMPONENT_PLACEMENTS)


class HarnessError(ValueError):
    """A drawing that cannot be read or stored; the message is for the technologist."""


# ── «Ручная сборка» from the drawing (pure) ──────────────────────────────────────────────────────────────────────

# Within a block: cut, then strip, twist, slide the tubes on, terminate, splice, shrink, insert, wrap; the route's own
# assembly steps last. Ties keep the drawing's order (wire by wire).
STAGE = {
    "cutWire": 0, "cutWireStrip": 0, "cutWireCrimp": 0, "cutTut": 0, "stripCable": 1, "coaxStrip": 1, "tin": 2,
    "twist": 3, "insTut": 4, "prsTerm": 5, "solderConn": 5, "splice": 6, "heatTut": 7, "insTerm": 8, "insWrap": 9,
}
ASSEMBLY_STAGE = 10
# The route's own operations that the per-wire operations do not already cover (cut / strip / crimp they do).
ROUTE_ONLY_MODES = frozenset({"assembly", "tin"})
CUT_TYPES = frozenset({"cutWire", "cutWireStrip"})
TERMINAL_TYPES = frozenset({"prsTerm", "insTerm", "solderConn"})


def plain(value: Any) -> Any:
    """Decimals as JSON numbers, tuples as lists."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def origin_key(object_id: str, code: str) -> str:
    """The КД key of an operation (frontend `kdKey`): the drawing object and the operation code."""
    return f"harness:{object_id}:{code}"


def _wire(design: HarnessDesign, wire_id: object):
    return next((wire for wire in design.wires if wire.id == wire_id), None)


def _wire_row(design: HarnessDesign, wire_id: object, length: object = None) -> dict[str, Any]:
    """A wire as the generator's operations list it (`ops[].wires[]`: name, art, length, qty)."""
    wire = _wire(design, wire_id)
    if wire is None:
        return {"name": str(wire_id or ""), "art": "", "qty": 1}
    name, article, _section = wire_material(design, wire)
    row: dict[str, Any] = {"name": name or wire.circuit or wire.id, "art": article, "qty": 1}
    if length is not None:
        row["length"] = plain(length)
    color = wire_color(design, wire)
    if color:
        row["color"] = color
    return row


def _generator_fields(design: HarnessDesign, op: ImpliedOp) -> dict[str, Any]:
    """The generator fields the ETK build reads for this operation type, filled from the drawing where it says them
    (wires with their cut length, connector and terminal, tube / braid material and length). The drawing's own data
    rides along in `kdData`."""
    data = op.data
    kind = op.type
    if kind in CUT_TYPES:
        length = data.get("cut_mm") if data.get("cut_mm") is not None else data.get("length_mm")
        if data.get("cable_id"):
            return {"wires": [{
                "name": data.get("material") or str(data["cable_id"]), "art": data.get("article") or "",
                "qty": 1, "cores": data.get("cores") or 1, **({"length": plain(length)} if length is not None else {}),
            }]}
        fields: dict[str, Any] = {"wires": [_wire_row(design, data.get("wire_id"), length)]}
        if kind == "cutWireStrip":
            sides = list(data.get("strip_sides") or ())
            fields["strip"] = {"a": "A" in sides, "b": "B" in sides}
        return fields
    if kind in TERMINAL_TYPES:
        connector = str(data.get("connector") or "")
        return {
            "ep": {"connName": connector, "connArt": data.get("part_number") or "", "label": connector,
                   "termArt": data.get("terminal_article") or "", "termName": data.get("terminal") or ""},
            "wires": [_wire_row(design, data.get("wire_id"))],
        }
    if kind in {"cutTut", "insTut", "heatTut"}:
        fields = {"tutArt": data.get("article") or "", "tutName": data.get("material") or data.get("name") or "",
                  "tutCount": 1}
        if kind == "cutTut" and data.get("length_mm") is not None:
            fields["tutSegs"] = [{"count": 1, "len": plain(data["length_mm"])}]
        return fields
    if kind == "insWrap":
        return {"wrapArt": data.get("article") or "", "wrapName": data.get("material") or data.get("name") or "",
                "wrapKind": data.get("wrap_kind") or "", "tutCount": 1,
                **({"wrapLen": plain(data["length_mm"])} if data.get("length_mm") is not None else {})}
    if kind == "splice":
        return {"units": 1}
    return {}


def _names(values: list[str]) -> str:
    unique = list(dict.fromkeys(value for value in values if value))
    return ", ".join(unique[:6]) + (" …" if len(unique) > 6 else "")


def _shared_label(design: HarnessDesign, op: ImpliedOp) -> str:
    """Shared operations already name their object (splice, tube…); a cable and a pair get theirs added."""
    kind, _sep, object_id = op.shared.partition(":")
    if kind == "cable":
        cable = next((item for item in design.cables if item.id == object_id), None)
        material = cable.material.display_name if cable is not None and cable.material else ""
        return f"{op.label}, {material or object_id}"
    if kind == "pair":
        pair = next((item for item in design.diff_pairs if item.id == object_id), None)
        names = [(_wire(design, wire_id).circuit or wire_id) if _wire(design, wire_id) else wire_id
                 for wire_id in (pair.wire_ids if pair else ())]
        return f"{op.label} {'/'.join(names)}".strip()
    return op.label


@dataclass
class _Group:
    """One operation of the scheme gathered from the per-wire operations `wire_ops` implies, grouped as the
    generator groups them (the ETK reads one operation per kind with its `wires`): a cut per block and strip
    pattern, a terminal operation per connector, side and terminal, a shared one (splice, pair, cable, tube) once,
    a coax strip per wire."""
    block: str
    stage: int
    object_id: str
    code: str
    label: str
    op: dict[str, Any]
    circuits: list[str] = field(default_factory=list)
    data: list[Any] = field(default_factory=list)


def _group_key(wire, op: ImpliedOp) -> tuple[tuple, str, str]:
    """(group, КД object, operation code) of one implied operation."""
    if op.shared:
        return ("shared", op.shared, op.type), op.shared.split(":", 1)[1], op.type
    code = f"{op.type}.{op.side}" if op.side else op.type
    if op.type in CUT_TYPES:
        sides = "".join(sorted(op.data.get("strip_sides") or ()))
        code = f"{op.type}.{sides}" if sides else op.type
        return ("cut", op.block, code), op.block or "-", code
    if op.type in TERMINAL_TYPES:
        end = wire.end(op.side) if op.side else None
        connector = end.connector_id if end is not None and end.kind == "contact" else ""
        terminal = str(op.data.get("terminal_article") or op.data.get("terminal") or "")
        return ("end", connector or wire.id, code, terminal, op.block), connector or wire.id, code
    return ("wire", wire.id, code), wire.id, code


def _implied(design: HarnessDesign) -> tuple[list[tuple[str, int, dict[str, Any]]], list[str]]:
    """(block id, stage, operation) for the operations the drawing implies, plus the route's own assembly steps;
    and the Russian notes of what was assumed."""
    notes: list[str] = []
    groups: dict[tuple, _Group] = {}
    for wire in design.wires:
        ops, wire_notes = wire_ops(design, wire)
        notes.extend(note for note in wire_notes if note not in notes)
        for op in ops:
            if not op.type:
                continue
            key, object_id, code = _group_key(wire, op)
            group = groups.get(key)
            if group is None:
                group = groups[key] = _Group(
                    op.block, STAGE.get(op.type, ASSEMBLY_STAGE), object_id, code,
                    _shared_label(design, op) if op.shared else op.label,
                    {"type": op.type, "side": op.side, "tPrep": "", "tOp": "", **_generator_fields(design, op)},
                )
            elif not op.shared:
                group.op["wires"] = [*group.op.get("wires", []), *_generator_fields(design, op).get("wires", [])]
            if not op.shared:
                group.circuits.append(wire.circuit or wire.id)
            if not op.shared or not group.data:
                group.data.append(plain(dict(op.data)))
    # A connector side with several terminals, or with wires in several blocks: the КД key tells the groups apart.
    terminals: dict[tuple, set] = {}
    blocks: dict[tuple, set] = {}
    for key in groups:
        if key[0] == "end":
            terminals.setdefault(key[1:3], set()).add(key[3])
            blocks.setdefault(key[1:4], set()).add(key[4])
    result: list[tuple[str, int, dict[str, Any]]] = []
    for key, group in groups.items():
        code, label = group.code, group.label
        if key[0] == "end":
            if len(terminals[key[1:3]]) > 1:
                code += f"/{key[3]}"
            if len(blocks[key[1:4]]) > 1:
                code += f"@{key[4] or '-'}"
            label = f"{label} — {group.op.get('ep', {}).get('connName') or key[1]}"
        elif key[0] in {"cut", "wire"}:
            label = f"{label}: {_names(group.circuits)}"
        result.append((group.block, group.stage, {
            **group.op, "label": label, "kdData": group.data, "origin": origin_key(group.object_id, code),
        }))
    for row in design.route.rows if design.route else ():
        for step in row.operations:
            if step.mode not in ROUTE_ONLY_MODES:
                continue
            label = (step.operation.display_name if step.operation else "") or ROUTE_MODE_LABELS.get(step.mode, step.mode)
            result.append((row.id, STAGE.get(step.mode, ASSEMBLY_STAGE), {
                "type": "customOp", "label": label, "qty": "1", "tPrep": "", "tOp": "",
                "opNum": step.operation.source_key if step.operation else "",
                **({"note": step.note.strip()} if step.note.strip() else {}),
                "steps": [], "tooling": [], "origin": origin_key(step.id, step.mode),
            }))
    return result, notes


def _scheme(design: HarnessDesign, used: set[str]) -> tuple[list[dict[str, Any]], list[tuple[str, str]], str]:
    """Blocks (route rows that got operations, plus «Не в маршруте КД») in the route's order, edges between them —
    through the rows that got none — and the key of the final block (the one row nothing depends on)."""
    rows = list(design.route.rows) if design.route else []
    if not rows:
        return [{"key": FINAL_KEY, "name": "", "final": True}], [], FINAL_KEY
    by_id = {row.id: row for row in rows}
    after: dict[str, list[str]] = {row.id: [] for row in rows}
    for row in rows:
        for source in row.depends_on:
            if source in after and row.id not in after[source]:
                after[source].append(row.id)
    sinks = [row.id for row in rows if not after[row.id]]
    final = sinks[0] if len(sinks) == 1 else ""
    kept = [row for row in rows if row.id in used or row.id == final]
    blocks = [{
        "key": f"route:{row.id}", "name": (row.title or row.index).strip(), "final": row.id == final,
        **({"kind": row.kind} if row.id != final and row.kind in {"semiFinished", "assembly"} else {}),
        **({"quantity": plain(row.quantity)} if row.quantity is not None and row.quantity > 0 and row.quantity != 1 else {}),
        **({"refs": list(dict.fromkeys(item_id for _kind, item_id in row.sources))} if row.sources else {}),
    } for row in kept]
    kept_ids = {row.id for row in kept}
    edges: list[tuple[str, str]] = []
    for row in kept:
        # The nearest kept rows downstream, passing through rows that got no operations.
        targets: list[str] = []
        queue, visited = deque(after[row.id]), set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            if current in kept_ids:
                targets.append(current)
            else:
                queue.extend(after.get(current, ()))
        edges.extend((f"route:{row.id}", f"route:{target}") for target in targets if target in by_id)
    final_key = f"route:{final}" if final else ""
    if "" in used:
        blocks.insert(0, {"key": UNROUTED_KEY, "name": UNROUTED_NAME, "final": False, "kind": "semiFinished"})
        if final_key:
            edges.append((UNROUTED_KEY, final_key))
    return blocks, edges, final_key


def _ordered_blocks(blocks: list[dict[str, Any]], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn order of the block keys; ties — in the route's order."""
    place = {block["key"]: index for index, block in enumerate(blocks)}
    waiting = {key: 0 for key in place}
    for _source, target in edges:
        waiting[target] += 1
    ready = [key for key in place if not waiting[key]]
    order: list[str] = []
    while ready:
        ready.sort(key=place.__getitem__)
        key = ready.pop(0)
        order.append(key)
        for source, target in edges:
            if source == key:
                waiting[target] -= 1
                if not waiting[target]:
                    ready.append(target)
    return order + [key for key in place if key not in order]


def assembly_from_harness(
    design: HarnessDesign, *, index: str = "", name: str = "", part_qty: object = 1,
) -> tuple[dict[str, Any], list[str]]:
    """The «Ручная сборка» build_config of a harness drawing and the Russian notes about it. Blocks follow the
    drawing's manufacturing route (row → block, «зависит от» → arrow, the row nothing depends on → final block);
    objects the route does not list go to «Не в маршруте КД». A route that does not make a valid scheme — one
    block with every operation and a note. Nothing is saved: the frontend merges it into the master's scheme."""
    implied, notes = _implied(design)
    if not implied:
        raise HarnessError("В чертеже нет проводов с операциями — схему сборки не из чего вывести")
    known = {row.id for row in design.route.rows} if design.route else set()
    implied = [(block if block in known else "", stage, op) for block, stage, op in implied]
    blocks, edges, final_key = _scheme(design, {block for block, _stage, _op in implied})
    keyed = [(f"route:{block}" if block else (UNROUTED_KEY if known else FINAL_KEY), stage, op)
             for block, stage, op in implied]
    config = _config(blocks, edges, keyed, index=index, name=name, part_qty=part_qty)
    try:
        if not final_key and known:
            raise RouteError("в маршруте КД нет одной итоговой строки, от которой ничего не зависит")
        _check(config)
    except RouteError as error:
        notes.append(f"Маршрут чертежа не сложился в схему ({error}) — все операции в одном блоке")
        flat = [{"key": FINAL_KEY, "name": "", "final": True}]
        config = _config(flat, [], [(FINAL_KEY, stage, op) for _key, stage, op in keyed],
                         index=index, name=name, part_qty=part_qty)
    return config, notes


def _config(
    blocks: list[dict[str, Any]], edges: list[tuple[str, str]], keyed: list[tuple[str, int, dict[str, Any]]], *,
    index: str, name: str, part_qty: object,
) -> dict[str, Any]:
    by_block: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
    for order, (key, stage, op) in enumerate(keyed):
        by_block.setdefault(key, []).append((stage, order, op))
    ops: list[dict[str, Any]] = []
    positions: dict[str, list[int]] = {}
    for key in _ordered_blocks(blocks, edges):
        for _stage, _order, op in sorted(by_block.get(key, ())):
            ops.append({**op, "seq": len(ops) + 1})
            positions.setdefault(key, []).append(len(ops))
    route_blocks = [{
        "key": block["key"], **({"name": block["name"]} if block["name"] else {}),
        "ops": positions.get(block["key"], []), "final": block["final"],
        **{field: block[field] for field in ("kind", "quantity", "refs") if field in block},
    } for block in blocks if positions.get(block["key"]) or block["final"]]
    present = {block["key"] for block in route_blocks}
    return {
        "mode": "manual", "assemblyIndex": index, "assemblyName": name, "partQty": plain(part_qty), "ops": ops,
        "route": {
            "blocks": route_blocks,
            "edges": [{"from": source, "to": target, "per_unit": 1}
                      for source, target in edges if source in present and target in present],
        },
    }


def _check(config: Mapping[str, Any]) -> None:
    """The scheme as the ETK build reads it (RouteError in Russian when it does not hold)."""
    ops = [RouteOp(position=op["seq"], type=str(op.get("type") or ""), code=str(op.get("opNum") or ""),
                   name=str(op.get("label") or "")) for op in config["ops"]]
    parse_route(config["route"], ops, str(config.get("assemblyName") or ""), manual=True)


# ── Document ⇄ JSON, export / import (pure) ──────────────────────────────────────────────────────────────────────


def normalized(content: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The design document as stored: read leniently, written back with every field it had."""
    if not isinstance(content, Mapping):
        raise HarnessError("Чертёж жгута должен быть объектом JSON")
    version = content.get("schemaVersion")
    if version is not None and version != SCHEMA_VERSION:
        raise HarnessError(f"Поддерживается только схема чертежа {SCHEMA_VERSION}, в документе — {version}")
    design, warnings = parse_design(content)
    return dump_design(design), warnings


def encode(document: Mapping[str, Any]) -> bytes:
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HarnessError("Чертёж жгута больше 20 МБ")
    return content


def decode(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError("Сохранённый чертёж жгута повреждён — не JSON") from error
    if not isinstance(value, dict):
        raise HarnessError("Сохранённый чертёж жгута повреждён — не объект")
    return value


def carry_component_graph(document: Mapping[str, Any], stored: Mapping[str, Any] | None) -> dict[str, Any]:
    """The document to store: a save that does not name the library placements (the editor's autosave — its model has
    no such section) keeps those of the stored drawing; a save that names them (placing a library component) wins."""
    result = dict(document)
    for key in COMPONENT_GRAPH_KEYS:
        if key not in result and stored is not None and isinstance(stored.get(key), list):
            result[key] = deepcopy(stored[key])
    return result


def _rows(value: object) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def version_of(content: bytes) -> str:
    """The version token: what the client loaded, it names on save."""
    return hashlib.sha256(content).hexdigest()[:32]


def _utc(value: datetime) -> str:
    return value.isoformat()


def export_zip(design: Mapping[str, Any], *, project: str, index: str, name: str, quantity: object,
               harness_id: str, created: datetime, updated: datetime) -> bytes:
    """`.techmap-project.zip` of one harness, shaped as TECHMAP-GRAPHER's project export (snapshotFormat 4,
    SqliteProjectExportService.cs of e63befb): the ERP project number is the project designation, the position
    index is the harness designation, ids are stable per project / КД row."""
    run = plain(quantity) if isinstance(quantity, Decimal) else quantity
    content = {key: value for key, value in design.items() if key not in COMPONENT_GRAPH_KEYS}
    placements = [{**row, "harnessId": harness_id} for row in _rows(design.get(COMPONENT_PLACEMENTS))]
    snapshot = {
        "snapshotFormat": SNAPSHOT_FORMAT,
        "project": {
            "projectId": str(uuid5(NAMESPACE_URL, f"fraxis-erp:project:{project}")), "designation": project,
            "increment": 1, "name": name or item_id(project, index), "batchQuantity": run, "status": "active",
            "revision": 1, "createdUtc": _utc(created), "updatedUtc": _utc(updated),
        },
        "harnesses": [{
            "harnessId": harness_id, "designation": index, "quantity": run, "sortOrder": 0,
            "createdUtc": _utc(created), "updatedUtc": _utc(updated), "documents": [],
            "design": {"schemaVersion": SCHEMA_VERSION, "content": content},
        }],
        "attachments": [], "pinnedCharacteristics": [],
        COMPONENT_SNAPSHOTS: _rows(design.get(COMPONENT_SNAPSHOTS)), COMPONENT_PLACEMENTS: placements,
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(SNAPSHOT_PATH, json.dumps(snapshot, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def design_from_export(content: bytes, file_name: str, *, project: str, index: str) -> dict[str, Any]:
    """The design document of this position from a TECHMAP-GRAPHER export (`.techmap-project.zip`, or its
    snapshot.json): the harness designated as the position (index or full product id), else the only one."""
    if not content:
        raise HarnessError("Файл пустой")
    if len(content) > MAX_EXPORT_BYTES:
        raise HarnessError("Файл больше 30 МБ")
    try:
        if file_name.lower().endswith(".json"):
            payload = content
        else:
            with ZipFile(BytesIO(content)) as archive:
                try:
                    payload = archive.read(SNAPSHOT_PATH)
                except KeyError as error:
                    raise HarnessError(f"В архиве нет {SNAPSHOT_PATH} — это не экспорт TECHMAP-GRAPHER") from error
        snapshot = json.loads(payload.decode("utf-8"))
    except (BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError("Не удалось разобрать экспорт TECHMAP-GRAPHER") from error
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("harnesses"), list):
        raise HarnessError("Это не экспорт TECHMAP-GRAPHER: нет списка жгутов")
    harnesses = [item for item in snapshot["harnesses"] if isinstance(item, Mapping)]
    if not harnesses:
        raise HarnessError("В экспорте нет ни одного жгута")
    wanted = {index.strip().upper(), item_id(project, index).upper()}
    named = [item for item in harnesses if str(item.get("designation") or "").strip().upper() in wanted]
    if len(named) == 1:
        harness = named[0]
    elif len(harnesses) == 1:
        harness = harnesses[0]
    else:
        listed = ", ".join(str(item.get("designation") or "без обозначения") for item in harnesses)
        raise HarnessError(f"В экспорте {len(harnesses)} жгута(ов) ({listed}), жгута «{index}» среди них нет")
    design = harness.get("design")
    if not isinstance(design, Mapping):
        raise HarnessError(f"У жгута «{harness.get('designation') or index}» в экспорте нет чертежа")
    if design.get("schemaVersion") is not None and design.get("schemaVersion") != SCHEMA_VERSION:
        raise HarnessError(f"Поддерживается только схема чертежа {SCHEMA_VERSION}, в экспорте — {design.get('schemaVersion')}")
    inner = design.get("content")
    result = dict(inner) if isinstance(inner, Mapping) else dict(design)
    # The library placements of this harness and the snapshots they pin (project-level in the export) go inside.
    if not any(key in result for key in COMPONENT_GRAPH_KEYS):
        harness_id = str(harness.get("harnessId") or "")
        placements = [row for row in _rows(snapshot.get(COMPONENT_PLACEMENTS))
                      if harness_id and str(row.get("harnessId") or "") == harness_id]
        used = {str(row.get("snapshotId") or "") for row in placements}
        if placements:
            result[COMPONENT_PLACEMENTS] = placements
            result[COMPONENT_SNAPSHOTS] = [row for row in _rows(snapshot.get(COMPONENT_SNAPSHOTS))
                                           if str(row.get("snapshotId") or "") in used]
    return result


# ── HTTP ──────────────────────────────────────────────────────────────────────────────────────────────────────────


class SavePayload(BaseModel):
    design: dict[str, Any]
    # The version the edit was made on; None — the drawing is new (no harness КД at this position yet).
    version: str | None = Field(default=None, max_length=64)


class DerivePayload(BaseModel):
    # The drawing as it is in the editor (unsaved); None — the saved one.
    design: dict[str, Any] | None = None


class UnlinkPayload(BaseModel):
    # The version on screen; None — unlink whatever is stored (the card does not hold a version).
    version: str | None = Field(default=None, max_length=64)


class RestorePayload(BaseModel):
    document_id: UUID


class ImportPayload(BaseModel):
    file_name: str = Field(min_length=1, max_length=400)
    content_b64: str = Field(min_length=1, max_length=42_000_000)
    version: str | None = Field(default=None, max_length=64)


def _position(uow, project: str, index: str) -> tuple[Kitset, str]:
    kitset = uow.kitsets.get(project)
    if kitset is None:
        raise HTTPException(status_code=404, detail={"message": f"Карточка набора «{project}» не найдена", "gone": True})
    wanted = str(index or "").strip().upper()
    assembly = kitset.assembly(wanted)
    if not wanted or assembly is None:
        raise HTTPException(status_code=404, detail=f"В наборе «{kitset.project}» нет сборки {wanted or '(пусто)'}")
    return kitset, assembly.index


def _current(uow, project: str, index: str) -> tuple[KitDocument, bytes] | None:
    """The harness КД of the position (the latest, should an older one have been attached as a file too)."""
    rows = [document for document in uow.kitsets.documents(project)
            if document.item_index.upper() == index.upper() and document.content_type == HARNESS_CONTENT_TYPE]
    for document in sorted(rows, key=lambda item: item.uploaded_at, reverse=True):
        found = uow.kitsets.document_content(project, document.id)
        if found is not None:
            return found
    return None


def _view(found: tuple[KitDocument, bytes] | None, warnings: list[str] | None = None) -> dict[str, Any]:
    if found is None:
        return {"document": None, "design": None, "version": None, "warnings": warnings or []}
    document, content = found
    design = decode(content)
    if warnings is None:
        warnings = parse_design(design)[1]
    return {"document": _document_json(document), "design": design, "version": version_of(content), "warnings": warnings}


def _when(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _save(request: Request, project: str, index: str, design: Mapping[str, Any], version: str | None,
          principal: Principal, now: datetime, event: str) -> dict[str, Any]:
    try:
        document, warnings = normalized(design)
        content = encode(document)
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        current = _current(uow, kitset.project, index)
        file_name = f"{item_id(kitset.project, index)} — {TITLE}"
        if current is not None and any(key not in document for key in COMPONENT_GRAPH_KEYS):
            try:
                kept = carry_component_graph(document, decode(current[1]))
                content = encode(kept) if kept != document else content
            except HarnessError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        if current is None:
            if version:
                raise HTTPException(status_code=409, detail=(
                    "Чертёж жгута отвязан от позиции или удалён, пока вы его правили — обновите страницу"
                ))
            saved = KitDocument(
                id=uuid4(), project=kitset.project, item_index=index, file_name=file_name,
                content_type=HARNESS_CONTENT_TYPE, size=len(content), uploaded_at=now, uploaded_by=principal.actor.name,
            )
            uow.kitsets.add_document(saved, content)
        else:
            stored, stored_content = current
            if version != version_of(stored_content):
                raise HTTPException(status_code=409, detail=(
                    f"Чертёж жгута уже сохранил {stored.uploaded_by} {_when(stored.uploaded_at)} — "
                    "обновите страницу и повторите правку"
                ))
            changed = uow.kitsets.replace_content(
                kitset.project, stored.id, file_name=file_name, content_type=HARNESS_CONTENT_TYPE, content=content,
                uploaded_at=now, uploaded_by=principal.actor.name, expected_uploaded_at=stored.uploaded_at,
            )
            if not changed:
                raise HTTPException(status_code=409, detail="Чертёж жгута только что сохранили — обновите страницу и повторите правку")
            saved = KitDocument(
                id=stored.id, project=kitset.project, item_index=index, file_name=file_name,
                content_type=HARNESS_CONTENT_TYPE, size=len(content), uploaded_at=now, uploaded_by=principal.actor.name,
            )
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=principal.actor.id, event_type=event, actor=principal.actor, occurred_at=now,
            details={"project": kitset.project, "item_index": index, "file": file_name, "size": str(len(content))},
        ))
        uow.commit()
    return {**_view((saved, content), warnings), "created": current is None}


@router.get("/{project}/harness/{index}")
def harness(
    project: str, index: str, request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> dict:
    """Чертёж жгута позиции: документ КД, сам чертёж, версия для сохранения. Нет — `document: null`."""
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        found = _current(uow, kitset.project, index)
    try:
        return _view(found)
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/{project}/harness/{index}")
def save_harness(
    project: str, index: str, payload: SavePayload, request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Сохранить чертёж поверх себя (одна строка КД на позицию). Версия — та, что пришла с чтением; чужое сохранение
    после неё — 409. Чертежа ещё нет — создаётся (версия не нужна)."""
    return _save(request, project, index, payload.design, payload.version, principal, now,
                 "techcards.kitset_harness_saved")


@router.post("/{project}/harness/{index}/derive")
def derive_harness(
    project: str, index: str, request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
    payload: DerivePayload | None = None,
) -> dict:
    """«Ручная сборка» из чертежа (переданного или сохранённого): блоки по маршруту КД, операции с ключом КД.
    Ничего не сохраняется — экран сливает её со схемой мастера (`rederive`)."""
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        assembly = kitset.assembly(index)
        found = None if payload is not None and payload.design is not None else _current(uow, kitset.project, index)
    try:
        if payload is not None and payload.design is not None:
            content, warnings = normalized(payload.design)
        elif found is None:
            raise HTTPException(status_code=404, detail=f"У сборки {index} нет чертежа жгута")
        else:
            content, warnings = decode(found[1]), []
        design, read_warnings = parse_design(content)
        config, notes = assembly_from_harness(
            design, index=index, name=assembly.name if assembly else "", part_qty=assembly.run if assembly else 1,
        )
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"build_config": config, "warnings": list(dict.fromkeys([*warnings, *read_warnings, *notes]))}


@router.get("/{project}/harness/{index}/export")
def export_harness(
    project: str, index: str, request: Request,
    _principal: Annotated[Principal, Depends(projects_view_principal)],
) -> Response:
    """Чертёж как `.techmap-project.zip` — открыть в TECHMAP-GRAPHER."""
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        assembly = kitset.assembly(index)
        found = _current(uow, kitset.project, index)
    if found is None:
        raise HTTPException(status_code=404, detail=f"У сборки {index} нет чертежа жгута")
    document, content = found
    try:
        design = decode(content)
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    archive = export_zip(
        design, project=kitset.project, index=index, name=assembly.name if assembly else "",
        quantity=assembly.run if assembly else 1, harness_id=str(document.id),
        created=document.uploaded_at, updated=document.uploaded_at,
    )
    return file_response(archive, "application/zip", f"{item_id(kitset.project, index)}.techmap-project.zip")


@router.post("/{project}/harness/{index}/import")
def import_harness(
    project: str, index: str, payload: ImportPayload, request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Чертёж из экспорта TECHMAP-GRAPHER (`.techmap-project.zip`) — ложится в КД позиции поверх прежнего (с той же
    проверкой версии, что и сохранение). Дальше правда — копия в ERP."""
    content = decode_upload(payload.content_b64.split(",", 1)[-1], "Файл передан не в base64")
    try:
        design = design_from_export(content, payload.file_name, project=project, index=index)
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _save(request, project, index, design, payload.version, principal, now, "techcards.kitset_harness_imported")


def _audit(uow, principal: Principal, now: datetime, event: str, project: str, index: str, document: KitDocument) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=principal.actor.id, event_type=event, actor=principal.actor, occurred_at=now,
        details={"project": project, "item_index": index, "file": document.file_name, "document_id": str(document.id)},
    ))


@router.post("/{project}/harness/{index}/unlink")
def unlink_harness(
    project: str, index: str, request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    payload: UnlinkPayload | None = None,
) -> dict:
    """«Отвязать» чертёж от позиции: строка КД и сам чертёж остаются (отвязанный, с «Вернуть» в карточке), позиция его
    больше не читает — ни редактор, ни «Предложить из КД». Версия с экрана, если передана, должна совпасть."""
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        current = _current(uow, kitset.project, index)
        if current is None:
            raise HTTPException(status_code=404, detail=f"У сборки {index} нет чертежа жгута")
        stored, content = current
        if payload is not None and payload.version and payload.version != version_of(content):
            raise HTTPException(status_code=409, detail=(
                f"Чертёж жгута уже сохранил {stored.uploaded_by} {_when(stored.uploaded_at)} — обновите страницу"
            ))
        if not uow.kitsets.relabel_document(
            kitset.project, stored.id, from_type=stored.content_type, to_type=HARNESS_UNLINKED_CONTENT_TYPE,
        ):
            raise HTTPException(status_code=409, detail="Чертёж жгута только что изменили — обновите страницу")
        _audit(uow, principal, now, "techcards.kitset_harness_unlinked", kitset.project, index, stored)
        uow.commit()
    return {"document": _document_json(replace(stored, content_type=HARNESS_UNLINKED_CONTENT_TYPE))}


@router.post("/{project}/harness/{index}/restore")
def restore_harness(
    project: str, index: str, payload: RestorePayload, request: Request,
    principal: Annotated[Principal, Depends(projects_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """«Вернуть» отвязанный чертёж позиции. У позиции уже есть чертёж — 409: два чертежа у одной позиции не бывает,
    сначала отвязывают тот."""
    with techcards_factory(request)() as uow:
        kitset, index = _position(uow, project, index)
        document = uow.kitsets.document(kitset.project, payload.document_id)
        if (document is None or document.item_index.upper() != index.upper()
                or document_kind(document) != "harness_unlinked"):
            raise HTTPException(status_code=404, detail=f"У сборки {index} нет такого отвязанного чертежа жгута")
        if _current(uow, kitset.project, index) is not None:
            raise HTTPException(status_code=409, detail=(
                f"У сборки {index} уже есть чертёж жгута — сначала отвяжите его, потом верните этот"
            ))
        if not uow.kitsets.relabel_document(
            kitset.project, document.id, from_type=document.content_type, to_type=HARNESS_CONTENT_TYPE,
        ):
            raise HTTPException(status_code=409, detail="Чертёж жгута только что изменили — обновите страницу")
        _audit(uow, principal, now, "techcards.kitset_harness_restored", kitset.project, index, document)
        found = _current(uow, kitset.project, index)
        uow.commit()
    try:
        return _view(found)
    except HarnessError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
