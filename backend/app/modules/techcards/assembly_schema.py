"""One assembly schema for the generator («Типовая») and «Ручная сборка» (owner, 05.10): blocks of semi-finished
products with operations in them. A generator config becomes a manual config with the same operations — every
generator operation keeps its type and fields as they are (wires, lengths, terminals, component norms), and the
manual build makes it with the generator's own typed code — plus the block scheme the generator's ETK shows.

Nothing is converted into library operations: that would lose the lengths, terminals and norms. Issued techcards
are not reissued, old generator configs keep the typed build; this is how a generated assembly opens in the diagram
editor, and the schema a draft from drawings is produced in.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Mapping

from .domain.route import Route, RouteOp, derive_route, parse_route
from .etk_builder import MANUAL_MODE, _chain_results, _decimal, _output_norm, _part_quantity


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _per_unit(op: Mapping[str, Any], config: Mapping[str, Any]) -> Decimal:
    """Pieces of the source block's product one piece of the target takes: the generator's output norm per item
    (computeOutputNorm_ — the batch for a piece product). An operation without one (a cut handing wires on) still
    hands one item's worth as one piece: a block's time is per item, so a larger multiplier would count it twice."""
    norm = _decimal(_output_norm(op, config))
    return norm / _part_quantity(config) if norm > 0 else Decimal(1)


def _route_json(route: Route, ops: list[Mapping[str, Any]], config: Mapping[str, Any], manual: bool) -> dict[str, Any]:
    by_key = {block.key: block for block in route.blocks}
    return {
        "blocks": [{
            "key": block.key, "name": block.name, "ops": list(block.ops), "final": block.final,
            "components": deepcopy(list(block.components)),
        } for block in route.blocks],
        "edges": [{
            "from": edge.source, "to": edge.target,
            "per_unit": _number(edge.per_unit if manual else _per_unit(ops[max(by_key[edge.source].ops) - 1], config)),
        } for edge in route.edges],
    }


def generator_to_assembly(config: Mapping[str, Any]) -> dict[str, Any]:
    """The manual config («Ручная сборка») of a generator config: the same top-level data (wires, twist, tin, strip,
    connections, coax…) and operations, `seq` = position, and `route` — the generator's hand-edited scheme when it
    has one, else the one derived from the operations, blocks named after the products the generator names.
    A manual config comes back as it is. Raises ValueError / RouteError (Russian) when there is nothing to convert."""
    if config.get("mode") == MANUAL_MODE:
        return deepcopy(dict(config))
    raw = config.get("ops")
    ops = [deepcopy(dict(item)) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    if not ops:
        raise ValueError("Нет операций для сборки")
    source = {**config, "ops": ops}
    names = _chain_results(source)
    final_name = str(config.get("assemblyName") or "")
    route_ops = [
        RouteOp(position=position, type=str(op.get("type") or ""), code=str(op.get("opNum") or ""), name=name)
        for position, (op, name) in enumerate(zip(ops, names), start=1)
    ]
    hand_drawn = config.get("route")
    route = parse_route(hand_drawn, route_ops, final_name, manual=True) if hand_drawn else derive_route(route_ops, final_name)
    for position, op in enumerate(ops, start=1):
        op["seq"] = position
    result = deepcopy(dict(config))
    result.update(mode=MANUAL_MODE, ops=ops, route=_route_json(route, ops, source, manual=bool(hand_drawn)))
    return result
