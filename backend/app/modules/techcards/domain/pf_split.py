"""The generator's ПФ split (owner + Vasily 13.10: «автоматику надо оставлять, генератор Вася оценил»).

The ETK is cut into semi-finished products the way the generator's «Схема полуфабрикатов» draws its cards
(`frontend/src/routeCards.ts` `opStages` / `genCards`): cut wires, cut tubes, wires with splices, crimped wires of
side A, side A assembled, crimped wires of side B, side B assembled, the finished product. Every ETK operation goes to the ПФ of its stage, ops of one stage form one ПФ in ETK
order, and the links between ПФ follow the operations' own links (the existing scheme, `per_unit` included).

Pure: operations (`RouteOp` with type, code, side and the generator's opKey) and the current scheme in, a checked
`Route` out (`generated`). The server applies it when a typical generator tab builds an ETK, the «Предложить разбивку
на ПФ» button shows it, and `workflows/pf_split_existing` moves the issued ETKs onto it.
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Sequence

from .route import Block, Edge, Route, RouteError, RouteOp, _ordered, derive_route, validate_route

WIRES, TUBES, CRIMPED, SIDE_A, SIDE_B, FINAL = "wires", "tubes", "crimped", "A", "B", "final"
# The hand crimp of a side, right before that side (owner 14.10: «резка, обжимка А, монтаж А, обжим Б, монтаж Б»);
# CRIMPED — splices and the like without a side, before the first side.
CRIMPED_A, CRIMPED_B = "crimpedA", "crimpedB"
STAGES = (WIRES, TUBES, CRIMPED, CRIMPED_A, SIDE_A, CRIMPED_B, SIDE_B, FINAL)
CRIMP_STAGES = frozenset({CRIMPED, CRIMPED_A, CRIMPED_B})
WIRE_CUTS = frozenset({"cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut"})
TUBE_CUTS = frozenset({"cutTut", "coaxCutTut"})
# Coax stripping as its own operation prepares the cut cable, not a side (owner 14.10: «зачистка идёт до всего
# остального»): cut, strip A, strip B are one semi-finished product with the cut.
COAX_STRIPS = frozenset({"coaxStrip"})
# Cutting coax with stripping on the rotary machine (CUT_COAX+1strip / +2strip).
_COAX_CUT_STRIP = re.compile(r"^CUT_COAX\+\d*STRIP")
# Crimping the ends by hand: terminals and lugs (a double crimp is the same operation), splices.
END_CRIMPS = frozenset({"prsTerm", "splice"})
_SIDES = {"A": SIDE_A, "А": SIDE_A, "B": SIDE_B, "В": SIDE_B}


def _side(op: RouteOp) -> str:
    return _SIDES.get(op.side.strip().upper(), "")


def _cut_stage(op: RouteOp) -> str:
    code = op.code.upper()
    if op.type in TUBE_CUTS or code.startswith("CUT_TUT"):
        return TUBES
    if op.type in WIRE_CUTS or op.type in COAX_STRIPS or code.startswith("CUT_"):
        return WIRES
    return ""


def _wire_end_prep(op: RouteOp) -> bool:
    """The operation prepares a wire end: strip and cut the cable for the connector, fold the core, tin, crimp a
    terminal or lug, splices with their tube and shrink, TUT on the wire before soldering. Screen twisting, mounting
    into the connector, soldering it and all after — the side's."""
    if op.type in END_CRIMPS or op.type in ("tin", "foldCore"):
        return True
    if op.type == "stripCable":
        return not op.key.startswith("shield")
    return op.key in ("insTut", "insSpl", "heatSpl")


def op_stages(ops: Sequence[RouteOp]) -> dict[int, str]:
    """The stage of every operation (by its ETK position): wire and cable cuts and coax stripping — cut wires, TUT and heat-shrink cuts —
    cut tubes; the end preparation of a side before the last side operation — the crimp of that side (`crimpedA` /
    `crimpedB`, placed right before the side: cut, crimp A, side A, crimp B, side B — owner 14.10) when some of it is
    a hand crimp (all crimped on the cutting machine — no card of its own, stripping and tinning stay with the side);
    the end preparation without a side before the first side — the shared crimped wires (`crimped`) when there are
    splices in it, else it prepares the next crimp or side; a side operation — its side; an operation without a side
    (twisting, marking, wrapping) prepares the next crimp or side, and after the last side — the finished product."""
    ordered = sorted(ops, key=lambda op: op.position)
    sided = [op.position for op in ordered if _side(op) and not _cut_stage(op) and not _wire_end_prep(op)]
    first_side, last_side = min(sided, default=0), max(sided, default=0)
    prep = [op for op in ordered if not _cut_stage(op) and _wire_end_prep(op) and op.position < last_side]
    crimped: dict[int, str] = {}
    for side, stage in ((SIDE_A, CRIMPED_A), (SIDE_B, CRIMPED_B)):
        own = [op for op in prep if _side(op) == side]
        if any(op.type in END_CRIMPS for op in own):
            crimped.update((op.position, stage) for op in own)
    shared = [op for op in prep if not _side(op) and op.position < first_side]
    if any(op.type in END_CRIMPS for op in shared):
        crimped.update((op.position, CRIMPED) for op in shared)
    result: dict[int, str] = {}
    waiting: list[int] = []
    for op in ordered:
        stage = _cut_stage(op) or crimped.get(op.position) or _side(op)
        if stage and stage not in (WIRES, TUBES):
            result.update((position, stage) for position in waiting)
            waiting = []
        if stage:
            result[op.position] = stage
        else:
            waiting.append(op.position)
    result.update((position, FINAL) for position in waiting)
    return result


def _title(stage: str, ops: Sequence[RouteOp]) -> str:
    if stage == WIRES:
        if ops and all(op.type == "coaxCut" or op.type in COAX_STRIPS for op in ops):
            stripped = any(op.type in COAX_STRIPS or _COAX_CUT_STRIP.match(op.code.upper()) for op in ops)
            return "Нарезанный и разделанный кабель" if stripped else "Нарезанный кабель"
        return "Нарезанные и обжатые провода" if any(op.type == "cutWireCrimp" for op in ops) else "Нарезанные провода"
    if stage == TUBES:
        return "Нарезанные трубки"
    if stage in CRIMP_STAGES:
        if not any(op.type == "prsTerm" for op in ops):
            return "Провода со сростками"
        return "Обжатые провода" + {CRIMPED_A: ", сторона А", CRIMPED_B: ", сторона В"}.get(stage, "")
    return {SIDE_A: "Сторона А собрана", SIDE_B: "Сторона В собрана"}.get(stage, "Отделка")


def _op_links(route: Route) -> list[tuple[int, int, Decimal]]:
    """The scheme as links between operations: inside a block each operation feeds the next one in the order of work;
    an edge — the last operation of its source feeds the first one of its target, `per_unit` pieces."""
    by_key = {block.key: block for block in route.blocks}
    links = [(block.ops[index], block.ops[index + 1], Decimal(1)) for block in route.blocks for index in range(len(block.ops) - 1)]
    links += [(by_key[edge.source].ops[-1], by_key[edge.target].ops[0], edge.per_unit)
              for edge in route.edges if by_key[edge.source].ops and by_key[edge.target].ops]
    return links


def generator_split(ops: Sequence[RouteOp], final_name: str = "", current: Route | None = None) -> Route:
    """One ПФ per generator stage, its operations in ETK order; the ПФ of the scheme's final operation is the product.
    Links between ПФ: the operations' links that go forward in the course of assembly (a ПФ by its first operation,
    the product last) — a link back joins nothing, as on the generator's cards; `per_unit` — the largest of the links
    between the two. Side A built feeds the crimp of side B that follows it (the wires go through); cut wires feed the
    crimped wires before the first side even without a link, and a ПФ left without a way on feeds the next one.
    Raises RouteError when the operations make no scheme."""
    if not ops:
        raise RouteError("нет операций")
    base = current or derive_route(ops, final_name)
    stages = op_stages(ops)
    by_position = {op.position: op for op in ops}
    final_block = base.final
    final_stage = stages[final_block.ops[-1]]
    members: dict[str, list[int]] = {}
    for position in sorted(stages):
        members.setdefault(stages[position], []).append(position)
    rank = {stage: (float("inf") if stage == final_stage else min(positions)) for stage, positions in members.items()}
    for crimp, side in ((CRIMPED_A, SIDE_A), (CRIMPED_B, SIDE_B)):  # the crimp of a side goes right before it
        if crimp in rank and side in rank and rank[crimp] > rank[side]:
            rank[crimp] = rank[side] - 0.5
    order = sorted(members, key=lambda stage: (rank[stage], STAGES.index(stage)))
    links: dict[tuple[str, str], Decimal] = {}
    for source, target, per_unit in _op_links(base):
        start, end = stages.get(source), stages.get(target)
        if start and end and start != end and rank[start] < rank[end]:
            links[(start, end)] = max(links.get((start, end), Decimal(0)), per_unit)
    # Crimped wires are made of cut wires even without a link; the crimp of a side after another side takes the wires
    # through that side (its link), and only when nothing feeds it — the cut wires.
    for stage in CRIMP_STAGES & members.keys():
        if WIRES in members and rank[WIRES] < rank[stage] and (stage == CRIMPED or not any(end == stage for _, end in links)):
            links.setdefault((WIRES, stage), Decimal(1))
    for index, stage in enumerate(order[:-1]):
        if not any(start == stage for start, _ in links):
            links[(stage, order[index + 1])] = Decimal(1)
    last_op = by_position.get(final_block.ops[-1])
    blocks = [
        Block(stage, (final_name or (last_op.name if last_op else "")) if stage == final_stage
              else _title(stage, [by_position[position] for position in members[stage] if position in by_position]),
              tuple(members[stage]), stage == final_stage)
        for stage in order
    ]
    edges = [Edge(start, end, per_unit) for (start, end), per_unit in links.items()]
    route = _ordered(blocks, edges, manual=True, generated=True)
    validate_route(route, [op.position for op in ops])
    return route


def same_split(first: Route | None, second: Route | None) -> bool:
    """The two schemes cut the operations into the same ПФ, in the same order of work, linked the same way (block keys,
    names and the source do not matter)."""
    if first is None or second is None:
        return first is second

    def shape(route: Route) -> tuple[frozenset, frozenset]:
        ops = {block.key: block.ops for block in route.blocks}
        return (frozenset((block.ops, block.final) for block in route.blocks),
                frozenset((ops[edge.source], ops[edge.target], edge.per_unit) for edge in route.edges))

    return shape(first) == shape(second)
