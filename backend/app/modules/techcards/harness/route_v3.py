"""«Маршрут v3» of a harness drawing (pure domain): the graph of route cards a position is made by — cut blanks
(«полуфабрикат»), assemblies and the one final «Готовый жгут» — with the drawing snapshots («слепки») the cards are
drawn from, quantities, operations and the parts each link carries.

Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md),
commit 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22):
- `parse_route_v3` follows `parseRouteV3` (src/Techmap.Client/src/manufacturing-v3/route-v3-model.ts) and
  `parseRouteDrawingCopy` (manufacturing/route-drawing-copy.ts) — the same shape and bounds; the browser copy of
  the same rules is frontend/src/harness/route/route-v3-model.ts;
- `source_refs` / `initial_route` follow `buildRouteSourceItems` (manufacturing/route-source.ts) and
  `createInitialRouteV3`; `generate_route` follows `generateRouteV3` (layout x = 32 + column·304, y = 32 + depth·328).

ERP differences, on purpose:
- the author keeps the route in the browser (localStorage, ADR-0016); here it is a server document per КБ position
  (`techcard_harness_routes`), so a snapshot never holds a copy of the drawing: a source-mode snapshot keeps the
  hidden object IDs (shown over the position's drawing), an isolated one — its own small geometry document;
- every part a route names must exist in the position's drawing (the author's parser does not see the drawing);
- at most one final card, and nothing leaves it (the author's generator makes one; the parser never checked);
- `starter_fragment`: «Построить по чертежу» on a route without snapshots starts from one source snapshot of the
  whole drawing, so the generated route is complete at once (cut cards → «Сборка жгута» → «Готовый жгут»).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Any
from uuid import UUID

ROUTE_VERSION = 1
REF_KINDS = ("wire", "cable", "covering", "connector")
NODE_KINDS = ("semiFinished", "assembly", "final")
OPERATION_KINDS = ("running", "static")
WIRE_ENDS = ("cut", "copper", "tin", "terminal", "sealed", "sealed-pin")
MAX_FRAGMENTS = 500
MAX_TEXT = 512
MAX_HIDDEN_ID = 128
MAX_HIDDEN_IDS = 10_000
MAX_COPY_BYTES = 1024 * 1024          # one isolated geometry document (route-drawing-copy.ts maximumCopyBytes)
MAX_ROUTE_BYTES = 20 * 1024 * 1024    # the whole route — the ceiling of a КД file
FINAL_ID = "route-v3:final"
FINAL_TITLE = "Готовый жгут"
STARTER_ID = "whole-harness"
STARTER_TITLE = "Сборка жгута"
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PREPARED_TITLES = {"wire": "Нарезанные провода", "cable": "Нарезанные кабели", "covering": "Нарезанные оболочки"}
_REF_NAMES = {"wire": "провод", "cable": "кабель", "covering": "оболочка", "connector": "соединитель"}


class RouteError(ValueError):
    """A route that cannot be built; the message is for the technologist."""


@dataclass(frozen=True)
class StoredRoute:
    """The route of one КБ position as stored: its revision (the concurrency token, n+1 per write) and the drawing it
    was built on — the КД row and that drawing's revision; the route is stale once the position's drawing is another
    row or has moved past that revision."""
    project: str
    item_index: str
    revision: int
    drawing_document_id: UUID
    drawing_revision: int
    document: dict[str, Any]
    updated_at: datetime
    updated_by: str


Ref = tuple[str, str]


def ref_key(ref: Mapping[str, Any]) -> Ref:
    return str(ref.get("kind")), str(ref.get("id"))


def ref_name(ref: Ref) -> str:
    return f"{_REF_NAMES.get(ref[0], ref[0])} {ref[1]}"


# ── The drawing as production inputs (route-source.ts) ──────────────────────────────────────────────────────────


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def source_refs(design: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every addressable part of the drawing in `buildRouteSourceItems` order: wires, cables, coverings,
    connectors."""
    topology = design.get("physicalTopology")
    coverings = _items(topology.get("coverings")) if isinstance(topology, Mapping) else []
    refs: list[dict[str, str]] = []
    for kind, items in (("wire", _items(design.get("wires"))), ("cable", _items(design.get("cables"))),
                        ("covering", coverings), ("connector", _items(design.get("connectors")))):
        refs += [{"kind": kind, "id": str(item["id"])} for item in items if isinstance(item.get("id"), str)]
    return refs


def _cable_members(design: Mapping[str, Any]) -> set[str]:
    return {str(wire) for cable in _items(design.get("cables")) for wire in cable.get("memberWireIds") or []}


def initial_route(design: Mapping[str, Any]) -> dict[str, Any]:
    """`createInitialRouteV3`: the cut blanks — wires outside cables, cables, coverings — and nothing else yet."""
    members = _cable_members(design)
    prepared = [ref for ref in source_refs(design)
                if ref["kind"] in ("covering", "cable") or ref["kind"] == "wire" and ref["id"] not in members]
    return {"version": ROUTE_VERSION, "preparedRefs": prepared, "fragments": [], "nodes": [], "edges": [],
            "generated": False, "backgroundOpacity": 0.2}


def starter_fragment(design: Mapping[str, Any], created_at: str) -> dict[str, Any]:
    """ERP: one source-mode snapshot of the whole drawing (nothing hidden) — what «Построить по чертежу» starts from
    when the route has no snapshots yet."""
    refs = source_refs(design)
    topology = design.get("physicalTopology")
    segments = _items(topology.get("segments")) if isinstance(topology, Mapping) else []
    object_ids = list(dict.fromkeys([ref["id"] for ref in refs]
                                    + [str(item["id"]) for item in segments if isinstance(item.get("id"), str)]))
    return {"id": STARTER_ID, "title": STARTER_TITLE, "mode": "source", "refs": refs, "objectIds": object_ids,
            "backgroundOpacity": 0.2, "drawingCopy": {"hiddenObjectIds": []}, "createdAt": created_at}


# ── Generation (generateRouteV3) ────────────────────────────────────────────────────────────────────────────────


def generate_route(document: Mapping[str, Any], design: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Every snapshot depends on the latest earlier card holding any of its parts; the cut blanks come first; the
    final card appears only when the snapshots cover every part of the drawing. Card positions: column·304 across,
    depth·328 down. A snapshot taking only part of an earlier snapshot's card is refused (a card is indivisible)."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    owner: dict[Ref, str] = {}
    depths: dict[str, int] = {}
    columns: dict[int, int] = {}

    def add(node: dict[str, Any], parents: list[str]) -> None:
        depth = 1 + max(depths.get(parent, 0) for parent in parents) if parents else 0
        column = columns.get(depth, 0)
        nodes.append({**node, "x": 32 + column * 304, "y": 32 + depth * 328})
        columns[depth] = column + 1
        depths[node["id"]] = depth
        edges.extend({"id": f"edge:{parent}:{node['id']}", "from": parent, "to": node["id"]} for parent in parents)

    prepared = list(document.get("preparedRefs") or [])
    for kind in ("wire", "cable", "covering"):
        refs = [ref for ref in prepared if ref.get("kind") == kind]
        if not refs:
            continue
        node_id = f"prepared:{kind}"
        add({"id": node_id, "kind": "semiFinished", "title": _PREPARED_TITLES[kind], "refs": refs,
             "fragmentIds": []}, [])
        for ref in refs:
            owner[ref_key(ref)] = node_id
    for fragment in document.get("fragments") or []:
        refs = list(fragment.get("refs") or [])
        parents = list(dict.fromkeys(owner[ref_key(ref)] for ref in refs if ref_key(ref) in owner))
        keys = {ref_key(ref) for ref in refs}
        for parent in parents:
            prior = next(node for node in nodes if node["id"] == parent)
            if prior["fragmentIds"] and any(ref_key(ref) not in keys for ref in prior["refs"]):
                raise RouteError(f"«{fragment.get('title')}» использует часть полуфабриката «{prior['title']}». "
                                 "Включите его полный состав в слепок.")
        node_id = f"fragment:{fragment.get('id')}"
        add({"id": node_id, "kind": "assembly" if parents else "semiFinished", "title": fragment.get("title"),
             "refs": refs, "fragmentIds": [fragment.get("id")]}, parents)
        for ref in refs:
            owner[ref_key(ref)] = node_id
    # No fictitious completed harness while some part is still unrepresented.
    every = source_refs(design) if design is not None else []
    captured = {ref_key(ref) for fragment in document.get("fragments") or [] for ref in fragment.get("refs") or []}
    if every and all(ref_key(ref) in captured for ref in every):
        leaves = [node["id"] for node in nodes
                  if node["fragmentIds"] and not any(edge["from"] == node["id"] for edge in edges)]
        add({"id": FINAL_ID, "kind": "final", "title": FINAL_TITLE, "refs": every, "fragmentIds": []}, leaves)
    result = {**document, "nodes": nodes, "edges": edges, "generated": True}
    if "graphEdited" in document:
        result["graphEdited"] = False
    return result


# ── What may be stored (parseRouteV3 + ERP rules) ───────────────────────────────────────────────────────────────


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_TEXT


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _opacity(value: object) -> bool:
    return _number(value) and 0 <= value <= 1


class _Refused(Exception):
    pass


class _Check:
    def __init__(self, parts: set[Ref] | None):
        self.errors: list[str] = []
        self.parts = parts
        self.missing: dict[Ref, None] = {}

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def stop(self, message: str) -> _Refused:
        self.fail(message)
        return _Refused()

    def refs(self, value: object, where: str) -> list[Ref]:
        if not isinstance(value, list) or not all(
            isinstance(ref, Mapping) and ref.get("kind") in REF_KINDS and _text(ref.get("id")) for ref in value
        ):
            raise self.stop(f"{where}: части заданы неверно")
        keys = [ref_key(ref) for ref in value]
        if len(set(keys)) != len(keys):
            raise self.stop(f"{where}: часть указана дважды")
        if self.parts is not None:
            for key in keys:
                if key not in self.parts:
                    self.missing[key] = None
        return keys

    def copy(self, value: object, where: str, *, isolated: bool) -> None:
        """`parseRouteDrawingCopy`, ERP shape: hidden IDs; the geometry document only in an isolated copy."""
        if not isinstance(value, Mapping) or "hiddenObjectIds" not in value or any(
            key not in ("hiddenObjectIds", "document") for key in value
        ):
            raise self.stop(f"{where}: рисунок слепка повреждён")
        hidden = value["hiddenObjectIds"]
        if (not isinstance(hidden, list) or len(hidden) > MAX_HIDDEN_IDS
                or any(not isinstance(item, str) or not item.strip() or len(item) > MAX_HIDDEN_ID for item in hidden)
                or len(set(hidden)) != len(hidden)):
            raise self.stop(f"{where}: скрытые объекты слепка заданы неверно")
        document = value.get("document")
        if not isolated:
            if document is not None:
                raise self.stop(f"{where}: слепок хранит копию всего чертежа — храните только скрытые объекты")
            return
        if not isinstance(document, Mapping) or not isinstance(document.get("wires"), list) \
                or not isinstance(document.get("connectors"), list):
            raise self.stop(f"{where}: у изолированного слепка нет своего рисунка")
        _inspect(document, self, where)
        if len(json.dumps(document, ensure_ascii=False).encode()) > MAX_COPY_BYTES:
            raise self.stop(f"{where}: рисунок изолированного слепка больше 1 МБ")


def _inspect(value: object, check: _Check, where: str) -> None:
    """The bounds of route-drawing-copy.ts `inspect`: depth ≤ 128, ≤ 100 000 values, lists ≤ 10 000, keys ≤ 256, and
    no route nested inside a drawing."""
    count = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100_000 or depth > 128:
            raise check.stop(f"{where}: рисунок слепка слишком большой")
        if isinstance(item, list):
            if len(item) > 10_000:
                raise check.stop(f"{where}: рисунок слепка слишком большой")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, Mapping):
            if "manufacturingRoute" in item:
                raise check.stop(f"{where}: в рисунке слепка вложен маршрут")
            if any(len(str(key)) > 256 for key in item):
                raise check.stop(f"{where}: рисунок слепка повреждён")
            stack.extend((child, depth + 1) for child in item.values())


def _operations(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(op, Mapping) and _text(op.get("id")) and isinstance(op.get("title"), str)
        and op.get("kind") in OPERATION_KINDS
        and all(_number(op.get(name)) and op.get(name) >= 0
                for name in ("lengthMm", "speedMmPerMinute", "employees", "quantity", "minutesEach"))
        for op in value
    )


def _acyclic(node_ids: Iterable[str], edges: list[Mapping[str, Any]]) -> bool:
    following: dict[str, list[str]] = {}
    for edge in edges:
        following.setdefault(edge["from"], []).append(edge["to"])
    state: dict[str, int] = {}  # 1 — on the path, 2 — done
    for start in node_ids:
        if state.get(start):
            continue
        stack = [(start, iter(following.get(start, ())))]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
            elif state.get(child) == 1:
                return False
            elif not state.get(child):
                state[child] = 1
                stack.append((child, iter(following.get(child, ()))))
    return True


def parse_route_v3(value: object, design: Mapping[str, Any] | None = None) -> list[str]:
    """Every reason the route must not be stored, in Russian; [] — it may. `design` — the position's drawing: every
    part the route names must exist in it. Like the author's parser, a damaged route is refused whole, never
    silently trimmed."""
    check = _Check({ref_key(ref) for ref in source_refs(design)} if design is not None else None)
    try:
        _parse(value, check)
    except _Refused:
        return check.errors
    if check.missing:
        names = ", ".join(ref_name(key) for key in list(check.missing)[:10])
        more = f" и ещё {len(check.missing) - 10}" if len(check.missing) > 10 else ""
        check.fail(f"в чертеже нет частей, на которые ссылается маршрут: {names}{more}")
    return check.errors


def _parse(v: object, check: _Check) -> None:
    if not isinstance(v, Mapping) or v.get("version") != ROUTE_VERSION:
        raise check.stop("это не маршрут v3")
    if len(json.dumps(v, ensure_ascii=False).encode()) > MAX_ROUTE_BYTES:
        raise check.stop("маршрут больше 20 МБ")
    for key in ("fragments", "preparedRefs", "nodes", "edges"):
        if not isinstance(v.get(key), list):
            raise check.stop(f"маршрут повреждён: нет списка {key}")
    if len(v["fragments"]) > MAX_FRAGMENTS:
        raise check.stop(f"слепков больше {MAX_FRAGMENTS}")
    if not isinstance(v.get("generated"), bool) or ("graphEdited" in v and not isinstance(v["graphEdited"], bool)):
        raise check.stop("маршрут повреждён: признаки построения заданы неверно")
    if not _opacity(v.get("backgroundOpacity")):
        raise check.stop("прозрачность фона — число от 0 до 1")
    check.refs(v["preparedRefs"], "Заготовки")
    artwork = v.get("wireArtwork")
    if artwork is not None:
        if not isinstance(artwork, list) or any(
            not isinstance(item, Mapping) or not _text(item.get("wireId"))
            or item.get("color") is not None and not (isinstance(item["color"], str) and _COLOR.match(item["color"]))
            or item.get("start") is not None and item["start"] not in WIRE_ENDS
            or item.get("end") is not None and item["end"] not in WIRE_ENDS
            for item in artwork
        ) or len({item["wireId"] for item in artwork}) != len(artwork):
            raise check.stop("вид проводов маршрута задан неверно")
        check.refs([{"kind": "wire", "id": item["wireId"]} for item in artwork], "Вид проводов")

    fragment_ids: set[str] = set()
    for f in v["fragments"]:
        title = f.get("title") if isinstance(f, Mapping) else None
        where = f"Слепок «{title}»" if _text(title) else "Слепок"
        if (not isinstance(f, Mapping) or not _text(f.get("id")) or f["id"] in fragment_ids or not _text(title)
                or f.get("mode") not in ("source", "isolated") or not _opacity(f.get("backgroundOpacity"))
                or not _text(f.get("createdAt"))):
            raise check.stop(f"{where}: повреждён или повторяет id другого слепка")
        objects = f.get("objectIds")
        if not isinstance(objects, list) or not objects or any(not _text(item) for item in objects):
            raise check.stop(f"{where}: не выбраны объекты")
        check.refs(f.get("refs"), where)
        check.copy(f.get("drawingCopy"), where, isolated=False)
        if f.get("isolatedDrawingCopy") is not None:
            check.copy(f["isolatedDrawingCopy"], where, isolated=True)
        elif f["mode"] == "isolated":
            raise check.stop(f"{where}: у изолированного слепка нет своего рисунка")
        fragment_ids.add(f["id"])

    node_ids: set[str] = set()
    finals: list[str] = []
    for node in v["nodes"]:
        title = node.get("title") if isinstance(node, Mapping) else None
        where = f"Карточка «{title}»" if _text(title) else "Карточка"
        if (not isinstance(node, Mapping) or not _text(node.get("id")) or node["id"] in node_ids or not _text(title)
                or node.get("kind") not in NODE_KINDS or not _number(node.get("x")) or not _number(node.get("y"))):
            raise check.stop(f"{where}: повреждена или повторяет id другой карточки")
        if not isinstance(node.get("fragmentIds"), list) or any(
            not isinstance(item, str) or item not in fragment_ids for item in node["fragmentIds"]
        ):
            raise check.stop(f"{where}: ссылается на несуществующий слепок")
        check.refs(node.get("refs"), where)
        if node.get("rawRefs") is not None:
            check.refs(node["rawRefs"], where)
        if node.get("operatorConfirmed") is not None and not isinstance(node["operatorConfirmed"], bool):
            raise check.stop(f"{where}: отметка подтверждения задана неверно")
        drawing = node.get("drawing")
        if drawing is not None:
            if not isinstance(drawing, Mapping) or not _opacity(drawing.get("backgroundOpacity")):
                raise check.stop(f"{where}: рисунок карточки повреждён")
            if drawing.get("drawingCopy") is not None:
                check.copy(drawing["drawingCopy"], where, isolated=False)
            if drawing.get("isolatedDrawingCopy") is not None:
                check.copy(drawing["isolatedDrawingCopy"], where, isolated=True)
        for name, label in (("width", "ширина"), ("height", "высота"), ("quantity", "количество")):
            if node.get(name) is not None and not (_number(node[name]) and node[name] > 0):
                raise check.stop(f"{where}: {label} — положительное число")
        inputs = node.get("inputNodeIds")
        if inputs is not None and (not isinstance(inputs, list) or any(not _text(item) for item in inputs)):
            raise check.stop(f"{where}: входы заданы неверно")
        if node.get("operations") is not None and not _operations(node["operations"]):
            raise check.stop(f"{where}: операции заданы неверно (время — неотрицательные числа)")
        node_ids.add(node["id"])
        if node["kind"] == "final":
            finals.append(node["id"])

    edge_ids: set[str] = set()
    for edge in v["edges"]:
        if (not isinstance(edge, Mapping) or not _text(edge.get("id")) or edge["id"] in edge_ids
                or not _text(edge.get("from")) or not _text(edge.get("to"))
                or edge["from"] not in node_ids or edge["to"] not in node_ids or edge["from"] == edge["to"]):
            raise check.stop("связь карточек повреждена, повторяет id или ведёт к несуществующей карточке")
        if edge.get("refs") is not None:
            check.refs(edge["refs"], "Связь карточек")
        edge_ids.add(edge["id"])
    if not _acyclic(node_ids, v["edges"]):
        raise check.stop("связи карточек образуют цикл")
    if len(finals) > 1:
        raise check.stop("у маршрута больше одной карточки «Готовый жгут»")
    if finals and any(edge["from"] == finals[0] for edge in v["edges"]):
        raise check.stop("из карточки «Готовый жгут» не может выходить связь")


# ── Against the current drawing ─────────────────────────────────────────────────────────────────────────────────


def _named_refs(document: Mapping[str, Any]) -> list[Ref]:
    found: list[Ref] = []
    groups = [document.get("preparedRefs") or []]
    groups += [fragment.get("refs") or [] for fragment in document.get("fragments") or []]
    for node in document.get("nodes") or []:
        groups += [node.get("refs") or [], node.get("rawRefs") or []]
    groups += [edge.get("refs") or [] for edge in document.get("edges") or []]
    for group in groups:
        found += [ref_key(ref) for ref in group if isinstance(ref, Mapping)]
    found += [("wire", str(item.get("wireId"))) for item in document.get("wireArtwork") or []
              if isinstance(item, Mapping)]
    return list(dict.fromkeys(found))


def missing_refs(document: Mapping[str, Any], design: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The parts the route names that the drawing no longer has (all of them when there is no drawing)."""
    parts = {ref_key(ref) for ref in source_refs(design)} if design is not None else set()
    return [{"kind": kind, "id": ref_id} for kind, ref_id in _named_refs(document) if (kind, ref_id) not in parts]


def is_complete(document: Mapping[str, Any], design: Mapping[str, Any] | None) -> bool:
    """Exactly one «Готовый жгут», it holds every part of the drawing and the route names no part the drawing lacks —
    the route a techcard may be fed from."""
    if design is None or missing_refs(document, design):
        return False
    finals = [node for node in document.get("nodes") or [] if node.get("kind") == "final"]
    if len(finals) != 1:
        return False
    held = {ref_key(ref) for ref in finals[0].get("refs") or []}
    every = [ref_key(ref) for ref in source_refs(design)]
    return bool(every) and all(key in held for key in every)
