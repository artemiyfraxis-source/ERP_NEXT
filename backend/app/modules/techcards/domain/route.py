"""Block scheme («маршрут») of semi-finished products over the ETK operations — Vasily Kostromin's model (04.10).

A block is one semi-finished product: it holds 1..n operations (by their 1-based position in the ETK) and outputs one
piece. An edge says the target block takes `per_unit` pieces of the source block's product (set by hand, default 1).
A valid scheme has exactly one final block (the product), no cycles, every block reaching the final one and every
operation in exactly one block. The scheme is for planning; the operation cards stay as they are.

Without a hand-edited scheme it is derived from the generator operations, one per block, the way the ETK chains its
semi-finished products: a cut starts a branch, TUT pieces (cutTut) wait for the operation that puts them on (insTut /
endCap), every other operation takes all open branches and continues them.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


PREFIX = "Схема полуфабрикатов"
TUT_CUT = "cutTut"
TUT_TAKERS = frozenset({"insTut", "endCap"})
SOURCE_TYPES = frozenset({"cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut", "coaxCutTut"})


class RouteError(ValueError):
    """An invalid scheme; the message is for the technologist."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{PREFIX}: {message}")


@dataclass(frozen=True)
class Block:
    key: str
    name: str
    ops: tuple[int, ...]
    final: bool = False
    components: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    per_unit: Decimal = Decimal(1)


@dataclass(frozen=True)
class Route:
    blocks: tuple[Block, ...]
    edges: tuple[Edge, ...]
    manual: bool = False

    @property
    def final(self) -> Block:
        return next(block for block in self.blocks if block.final)


@dataclass(frozen=True)
class RouteOp:
    """One ETK operation as the scheme sees it: position (1-based), generator type, card code, the name of the
    semi-finished product it hands on and its time norm in minutes per batch."""

    position: int
    type: str = ""
    code: str = ""
    name: str = ""
    minutes: Decimal = Decimal(0)


@dataclass(frozen=True)
class BlockTotals:
    minutes: Decimal          # Σ time norms of the block's operations, per batch
    unit_minutes: Decimal     # per one piece of the block's product
    rolled_minutes: Decimal   # per one piece, with every input: unit + Σ per_unit × input's rolled time
    inputs: Decimal           # input semi-finished pieces per one piece (Σ per_unit of incoming edges)
    rolled_ops: int           # operations in the block and in every block feeding it


def _label(block: Block) -> str:
    return f"«{block.name or block.key}»"


def _ordered(blocks: Sequence[Block], edges: Sequence[Edge], manual: bool) -> Route:
    """Blocks by their first operation number, edges by their blocks' order. Operations of a derived block go
    ascending; a hand-made (manual) block keeps the technologist's order — it IS the order of work inside the
    semi-finished product (ПФ, owner + Vasily 13.10): «05, 03» in one ПФ is allowed, ETK numbers stay as they are."""
    blocks = sorted(
        (Block(item.key, item.name, tuple(item.ops) if manual else tuple(sorted(item.ops)), item.final, item.components)
         for item in blocks),
        key=lambda item: (min(item.ops, default=0), item.key),
    )
    order = {block.key: index for index, block in enumerate(blocks)}
    edges = sorted(edges, key=lambda item: (order.get(item.source, -1), order.get(item.target, -1)))
    return Route(tuple(blocks), tuple(edges), manual)


def derive_route(ops: Sequence[RouteOp], final_name: str = "") -> Route:
    """One block per operation; the last operation is the final block (the ETK's «Изделие»)."""
    if not ops:
        raise RouteError("нет операций")
    last = ops[-1].position
    blocks: list[Block] = []
    edges: list[Edge] = []
    heads: list[str] = []   # products not yet taken by a later operation
    tut: list[str] = []     # TUT pieces waiting for insTut / endCap
    for op in ops:
        key = f"b{op.position}"
        final = op.position == last
        blocks.append(Block(key, (final_name or op.name) if final else op.name, (op.position,), final))
        if op.type == TUT_CUT:
            tut.append(key)
            continue
        if op.type in SOURCE_TYPES or op.code.upper().startswith("CUT_"):
            heads.append(key)
            continue
        inputs = heads + (tut if op.type in TUT_TAKERS else [])
        if op.type in TUT_TAKERS:
            tut = []
        edges.extend(Edge(item, key) for item in inputs)
        heads = [key]
    final_key = f"b{last}"
    edges.extend(Edge(item, final_key) for item in heads + tut if item != final_key)
    route = _ordered(blocks, edges, manual=False)
    validate_route(route, [op.position for op in ops])
    return route


def chain_route(ops: Sequence[RouteOp], final_name: str = "") -> Route:
    """One block per operation in list order, each feeding the next; the last one is the product. The start of a
    hand-assembled scheme («Ручная сборка»): it is the technologist's own, so production follows it (manual)."""
    if not ops:
        raise RouteError("нет операций")
    last = ops[-1].position
    blocks = [
        Block(f"b{op.position}", (final_name or op.name) if op.position == last else op.name, (op.position,),
              op.position == last)
        for op in ops
    ]
    edges = [Edge(f"b{source.position}", f"b{target.position}") for source, target in zip(ops, ops[1:])]
    route = _ordered(blocks, edges, manual=True)
    validate_route(route, [op.position for op in ops])
    return route


def _position(value: object) -> int:
    if isinstance(value, bool):
        raise RouteError(f"«{value}» — укажите номер операции")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise RouteError(f"«{value}» — укажите номер операции") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise RouteError(f"«{value}» — укажите номер операции")
    return int(number)


def _per_unit(value: object, source: str, target: str) -> Decimal:
    if value is None or value == "":
        return Decimal(1)
    if isinstance(value, bool):
        number = Decimal(-1)
    else:
        try:
            number = Decimal(str(value).strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            number = Decimal(-1)
    if not number.is_finite() or number <= 0:
        raise RouteError(f"количество на стрелке {source} → {target} должно быть числом больше нуля")
    return number


def _components(value: object, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RouteError(f"комплектующие блока {label} должны быть списком")
    result: list[Any] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(deepcopy(dict(item)))
        elif isinstance(item, (str, int)) and not isinstance(item, bool) and str(item).strip():
            result.append(str(item).strip())
        elif item not in (None, ""):
            raise RouteError(f"комплектующие блока {label}: непонятная строка «{item}»")
    return tuple(result)


def parse_route(
    raw: object, ops: Sequence[RouteOp], final_name: str = "", *, manual: bool = True,
) -> Route:
    """A hand-edited scheme (`build_config.route`, or a stored `document.route`) checked against the operations.
    A block without a name is named after the product of its last operation; extra keys (totals) are ignored."""
    if not isinstance(raw, Mapping):
        raise RouteError("схема должна быть объектом с блоками и стрелками")
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise RouteError("нет ни одного блока")
    names = {op.position: op.name for op in ops}
    blocks: list[Block] = []
    for index, item in enumerate(raw_blocks, start=1):
        if not isinstance(item, Mapping):
            raise RouteError(f"блок №{index} должен быть объектом")
        key = str(item.get("key") or "").strip()
        if not key:
            raise RouteError(f"у блока №{index} нет ключа")
        raw_ops = item.get("ops")
        if raw_ops is None:
            raw_ops = []
        if not isinstance(raw_ops, list):
            raise RouteError(f"операции блока «{key}» должны быть списком номеров")
        positions = tuple(_position(value) for value in raw_ops)
        final = bool(item.get("final"))
        name = str(item.get("name") or "").strip()
        if not name and positions:
            # The product of the block is what its last operation hands on: the last by the hand-made order.
            name = (final_name if final else "") or names.get(positions[-1] if manual else max(positions), "")
        blocks.append(Block(key, name, positions, final, _components(item.get("components"), f"«{name or key}»")))
    raw_edges = raw.get("edges")
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise RouteError("стрелки схемы должны быть списком")
    labels = {block.key: _label(block) for block in blocks}
    edges: list[Edge] = []
    for index, item in enumerate(raw_edges, start=1):
        if not isinstance(item, Mapping):
            raise RouteError(f"стрелка №{index} должна быть объектом")
        source, target = str(item.get("from") or "").strip(), str(item.get("to") or "").strip()
        if not source or not target:
            raise RouteError(f"у стрелки №{index} не указано, из какого блока в какой она ведёт")
        per_unit = _per_unit(item.get("per_unit"), labels.get(source, f"«{source}»"), labels.get(target, f"«{target}»"))
        edges.append(Edge(source, target, per_unit))
    route = _ordered(blocks, edges, manual)
    validate_route(route, [op.position for op in ops])
    return route


def _topological(route: Route) -> list[str]:
    """Kahn order of the block keys; a cycle raises with the blocks it runs through."""
    by_key = {block.key: block for block in route.blocks}
    incoming = {block.key: 0 for block in route.blocks}
    outgoing: dict[str, list[str]] = {block.key: [] for block in route.blocks}
    for edge in route.edges:
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = deque(block.key for block in route.blocks if not incoming[block.key])
    order: list[str] = []
    while ready:
        key = ready.popleft()
        order.append(key)
        for target in outgoing[key]:
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    if len(order) == len(route.blocks):
        return order
    # Every block left over has a left-over input: walking inputs back must come round to a block already passed.
    stuck = set(by_key) - set(order)
    path = [next(block.key for block in route.blocks if block.key in stuck)]
    while path.count(path[-1]) < 2:
        path.append(next(edge.source for edge in route.edges if edge.target == path[-1] and edge.source in stuck))
    cycle = list(reversed(path[path.index(path[-1]):]))
    raise RouteError("схема зациклена — " + " → ".join(_label(by_key[key]) for key in cycle))


def validate_route(route: Route, positions: Sequence[int]) -> None:
    """One final block, every operation in exactly one block, edges between known blocks, no cycle, every block
    reaching the final one."""
    if not route.blocks:
        raise RouteError("нет ни одного блока")
    by_key: dict[str, Block] = {}
    for block in route.blocks:
        if block.key in by_key:
            raise RouteError(f"ключ блока «{block.key}» повторяется")
        by_key[block.key] = block
        if not block.ops:
            raise RouteError(f"в блоке {_label(block)} нет операций")
    known = set(positions)
    owner: dict[int, Block] = {}
    for block in route.blocks:
        for position in block.ops:
            if position not in known:
                raise RouteError(f"операции {position} нет в техкарте (операций {len(known)})")
            if position in owner:
                raise RouteError(
                    f"операция {position} входит в несколько блоков — {_label(owner[position])} и {_label(block)}"
                )
            owner[position] = block
    missing = [str(position) for position in positions if position not in owner]
    if len(missing) == 1:
        raise RouteError(f"операция {missing[0]} не входит ни в один блок")
    if missing:
        raise RouteError(f"операции {', '.join(missing)} не входят ни в один блок")
    finals = [block for block in route.blocks if block.final]
    if not finals:
        raise RouteError("не отмечен итоговый блок (изделие)")
    if len(finals) > 1:
        raise RouteError("итоговый блок должен быть один, отмечено: " + ", ".join(_label(block) for block in finals))
    pairs: set[tuple[str, str]] = set()
    for edge in route.edges:
        for key in (edge.source, edge.target):
            if key not in by_key:
                raise RouteError(f"стрелка ведёт из неизвестного блока «{key}»" if key == edge.source
                                 else f"стрелка ведёт в неизвестный блок «{key}»")
        source, target = by_key[edge.source], by_key[edge.target]
        if edge.source == edge.target:
            raise RouteError(f"стрелка из блока {_label(source)} в него же")
        if (edge.source, edge.target) in pairs:
            raise RouteError(f"стрелка {_label(source)} → {_label(target)} повторяется")
        pairs.add((edge.source, edge.target))
        if edge.per_unit <= 0:
            raise RouteError(f"количество на стрелке {_label(source)} → {_label(target)} должно быть больше нуля")
        if source.final:
            raise RouteError(f"из итогового блока {_label(source)} не может выходить стрелка")
    _topological(route)
    reached = {finals[0].key}
    queue = deque(reached)
    while queue:
        key = queue.popleft()
        for edge in route.edges:
            if edge.target == key and edge.source not in reached:
                reached.add(edge.source)
                queue.append(edge.source)
    lost = [block for block in route.blocks if block.key not in reached]
    if lost:
        names = ", ".join(_label(block) for block in lost)
        raise RouteError(f"блок {names} не доходит до итогового блока" if len(lost) == 1
                         else f"блоки {names} не доходят до итогового блока")


def route_totals(route: Route, minutes: Mapping[int, Decimal], quantity: Decimal) -> dict[str, BlockTotals]:
    """Per block: its time, time per piece, rolled-up time per piece and input pieces; output is one piece."""
    quantity = quantity if quantity > 0 else Decimal(1)
    by_key = {block.key: block for block in route.blocks}
    incoming: dict[str, list[Edge]] = {block.key: [] for block in route.blocks}
    for edge in route.edges:
        incoming[edge.target].append(edge)
    totals: dict[str, BlockTotals] = {}
    upstream: dict[str, set[int]] = {}
    for key in _topological(route):
        block = by_key[key]
        own = sum((minutes.get(position, Decimal(0)) for position in block.ops), Decimal(0))
        unit = own / quantity
        edges = incoming[key]
        upstream[key] = set(block.ops).union(*(upstream[edge.source] for edge in edges))
        totals[key] = BlockTotals(
            minutes=own,
            unit_minutes=unit,
            rolled_minutes=unit + sum((edge.per_unit * totals[edge.source].rolled_minutes for edge in edges), Decimal(0)),
            inputs=sum((edge.per_unit for edge in edges), Decimal(0)),
            rolled_ops=len(upstream[key]),
        )
    return totals


def _number(value: Decimal, places: int = 4) -> int | float:
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(rounded) if rounded == rounded.to_integral_value() else float(rounded)


def route_json(route: Route, totals: Mapping[str, BlockTotals]) -> dict[str, Any]:
    """The `document.route` shape (ETK version 3)."""
    return {
        "source": "manual" if route.manual else "derived",
        "final": route.final.key,
        "blocks": [{
            "key": block.key,
            "name": block.name,
            "ops": list(block.ops),
            "final": block.final,
            "components": deepcopy(list(block.components)),
            "outputs": 1,
            "inputs": _number(totals[block.key].inputs),
            "minutes": _number(totals[block.key].minutes),
            "unit_minutes": _number(totals[block.key].unit_minutes),
            "rolled_minutes": _number(totals[block.key].rolled_minutes),
            "rolled_ops": totals[block.key].rolled_ops,
        } for block in route.blocks],
        "edges": [{"from": edge.source, "to": edge.target, "per_unit": _number(edge.per_unit)} for edge in route.edges],
    }


# ── ПФ documents (owner + Vasily 13.10): the ETK is cut into semi-finished products; a ПФ document is one block of
# the scheme, not an entity of its own — computed from the document every time, nothing is stored.

def _batch(document: Mapping[str, Any]) -> Decimal:
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    kit = document.get("kit") if isinstance(document.get("kit"), Mapping) else {}
    for value in (config.get("partQty"), kit.get("part_quantity")):
        number = _decimal_or_none(value)
        if number is not None and number > 0:
            return number
    return Decimal(1)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _sum_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows of one article (or name, without an article) and unit added up; a row without a number stays its own."""
    result: list[dict[str, Any]] = []
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        article, name = str(row.get("art") or "").strip(), str(row.get("name") or "").strip()
        unit = str(row.get("unit") or "").strip()
        norm = _decimal_or_none(row.get("norm"))
        key = (article or name, unit)
        same = index.get(key) if norm is not None and (article or name) else None
        if same is not None:
            same["norm"] = _text(_decimal_or_none(same["norm"]) + norm)
            continue
        item = deepcopy(dict(row))
        if norm is not None:
            item["norm"] = _text(norm)
            if article or name:
                index[key] = item
        result.append(item)
    return result


def _text(value: Decimal) -> str:
    number = _number(value)
    return str(number).replace(".", ",") if isinstance(number, float) else str(number)


def pf_documents(document: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The ПФ documents of an ETK — one per block of its `route` (pass a document with its route attached:
    `etk_builder.attach_route`), in the scheme's order, numbered ПФ-1… Each one: its operations in the order of work
    (`ops` — ETK positions, the order of `block.ops`), the components all of them take (one article added up), the
    semi-finished pieces that come from outside the ПФ (incoming edges: the source ПФ, per one piece and per batch),
    what it hands on (the result of its last operation) and its time norm. No route or no operations — empty."""
    if not isinstance(document, Mapping):
        return []
    route = document.get("route") if isinstance(document.get("route"), Mapping) else {}
    raw_blocks = [item for item in route.get("blocks") or [] if isinstance(item, Mapping) and item.get("key")]
    operations = [item for item in document.get("operations") or [] if isinstance(item, Mapping)]
    if not raw_blocks or not operations:
        return []
    batch = _batch(document)
    number = {str(block["key"]): index for index, block in enumerate(raw_blocks, start=1)}
    names = {str(block["key"]): str(block.get("name") or "") for block in raw_blocks}
    result: list[dict[str, Any]] = []
    for block in raw_blocks:
        key = str(block["key"])
        positions = [_position(value) for value in block.get("ops") or []]
        own = [operations[position - 1] for position in positions if 0 < position <= len(operations)]
        components = _sum_rows([row for operation in own for row in operation.get("components") or []
                                if isinstance(row, Mapping)])
        inputs = []
        for edge in route.get("edges") or []:
            if not isinstance(edge, Mapping) or str(edge.get("to") or "") != key or str(edge.get("from") or "") not in number:
                continue
            source = str(edge["from"])
            per_unit = _decimal_or_none(edge.get("per_unit")) or Decimal(1)
            inputs.append({"block": source, "number": number[source], "name": names[source],
                           "per_unit": _number(per_unit), "norm": _text(per_unit * batch), "unit": "шт"})
        inputs.sort(key=lambda item: item["number"])
        last = own[-1] if own else {}
        result.append({
            "number": number[key], "key": key, "name": str(block.get("name") or ""),
            "final": bool(block.get("final")), "ops": positions,
            "components": components, "inputs": inputs,
            "output": [deepcopy(dict(row)) for row in last.get("result") or [] if isinstance(row, Mapping)],
            "minutes": block.get("minutes", 0), "unit_minutes": block.get("unit_minutes", 0),
        })
    return result
