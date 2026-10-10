"""«Ручная сборка» from the harness drawing (pure): the operations `derive.wire_ops` implies plus the route's own
assembly steps, in blocks from the drawing's manufacturing route, each operation keyed `harness:<object>:<code>` so
the frontend's `rederive` merges it into the master's scheme. Nothing is saved."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..domain.route import RouteError, RouteOp, parse_route
from .derive import ROUTE_MODE_LABELS, ImpliedOp, wire_color, wire_material, wire_ops
from .model import HarnessDesign
from .schema import HarnessError, plain

FINAL_KEY = "b1"
UNROUTED_KEY = "route:-"
UNROUTED_NAME = "Не в маршруте КД"

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
