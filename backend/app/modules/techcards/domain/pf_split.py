"""The generator's ПФ split (owner + Vasily 13.10: «автоматику надо оставлять, генератор Вася оценил»).

The ETK is cut into semi-finished products the way the generator's «Схема полуфабрикатов» draws its cards
(`frontend/src/routeCards.ts` `opStages` / `genCards`): cut wires, printed labels (a ПФ per printer), cut tubes, wires with splices, crimped wires of
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
MARKED = "marked"
# Printing labels and marks is its own task on the printer, apart from cutting the cable and the tubes (owner 20.11:
# «задание на печать маркировок, маркировку и на нарезку почему-то в одном, трубки тоже в одном»).
# A printer is its own operator working in parallel (owner 20.11), so each printer's marks are their own ПФ:
# LABELS («labels») — the Godex markers ZCUT-RT430 ОБ.101 / ZCUT-RT660 ОБ.102: tape labels (PRINT_MARK_LABEL, opKey
# `coaxPrintLabel`), printed and cut marking tubes (PRINT_MARK_TUBE), flag marks printed apart (INS_MARK_AUTO, opKey
# `printMarks`); PRINTED («printed», «Напечатанная маркировка») — the Supvan TP80E ОБ.057: wire marks on a marker tube
# or tape (card PRINT_MARK). Stage keys are a contract: frontend `routeCards.ts` `opStages` mirrors them.
LABELS = "labels"
PRINTED = "printed"
STAGES = (MARKED, WIRES, LABELS, PRINTED, TUBES, CRIMPED, CRIMPED_A, SIDE_A, CRIMPED_B, SIDE_B, FINAL)
# Input-less ПФ made in parallel before the assembly: nothing of the scheme feeds them.
SOURCE_STAGES = frozenset({WIRES, LABELS, PRINTED, TUBES})
PRINT_STAGES = frozenset({LABELS, PRINTED})
CRIMP_STAGES = frozenset({CRIMPED, CRIMPED_A, CRIMPED_B})
WIRE_CUTS = frozenset({"cutWire", "cutWireStrip", "cutWireCrimp", "coaxCut"})
TUBE_CUTS = frozenset({"cutTut", "coaxCutTut"})
# A tube-cut type that prints: tape labels of the coax tabs (opKey `coaxPrintLabel`, card PRINT_MARK_LABEL) and
# printed marking tubes (PRINT_MARK_TUBE, printed and cut on the marker).
LABEL_PRINT_KEY = "coaxPrintLabel"
# The TP80E card (БД.ОП PRINT_MARK «Печать маркировок»); PRINT_MARK_LABEL / PRINT_MARK_TUBE are the Godex ones.
TP80E_CODE = "PRINT_MARK"
# Flag marks printed apart from their mounting («Сложная сборка», insMark with markPhase print, card INS_MARK_AUTO).
FLAG_PRINT_KEY = "printMarks"
# Coax stripping as its own operation prepares the cut cable, not a side (owner 14.10: «зачистка идёт до всего
# остального»): cut, strip A, strip B are one semi-finished product with the cut.
COAX_STRIPS = frozenset({"coaxStrip"})
# The strip of a FAKRA/HFM end (opKey `coaxSideStrip…`, owner 25.10) belongs to its side: the cable is cut whole, then each end is
# «put the parts on -> strip -> assemble» in its own block (a seal can't be slid over a stripped braid). Not «Нарезанный кабель».
SIDE_STRIP_KEY = "coaxSideStrip"
# Cutting coax with stripping on the rotary machine (CUT_COAX+1strip / +2strip).
_COAX_CUT_STRIP = re.compile(r"^CUT_COAX\+\d*STRIP")
# Crimping the ends by hand: terminals and lugs (a double crimp is the same operation), splices.
END_CRIMPS = frozenset({"prsTerm", "splice"})
# The coax centre contact soldered on the cable end — the coax twin of a crimped terminal. When its side is assembled
# later (the housing) it is that side's end preparation: 796К.2 КБ1 (15.10) solders pins A and B one after the other
# before the housings, and pin B in «Сторона В собрана» put that ПФ after ops 7–9 of side A in the work. An angled pin
# soldered inside the housing (no later operation of its side) is the side's assembly itself.
CONTACT_SOLDERS = frozenset({"coaxPin"})
_SIDES = {"A": SIDE_A, "А": SIDE_A, "B": SIDE_B, "В": SIDE_B}
# Generator operations of a side that carry no connector, so a config saved before 16.10 has no `side` for them: the
# cable end stripping, core stripping after the rotor, screen twisting and capping of idle cores, the shrink on the
# jacket cut and the shrink of the sleeve over a connector (their side is in the opKey). Without it «Усадка ТУТ на месте
# разделки А» went to the next side's ПФ (owner 15.10: «Обжатые провода, сторона В» held it), and so did «Усадка ТУТ
# поверх разъёма А1».
_SIDE_KEY = re.compile(r"^(?:stripCable|stripCores|shield|idle|heatJs|heatShr)([AB])")


def _side(op: RouteOp) -> str:
    side = _SIDES.get(op.side.strip().upper(), "")
    if side:
        return side
    by_key = _SIDE_KEY.match(op.key)
    return _SIDES[by_key.group(1)] if by_key else ""


def _cable_strip(op: RouteOp) -> bool:
    return op.type in COAX_STRIPS and not op.key.startswith(SIDE_STRIP_KEY)


def _label_print(op: RouteOp) -> bool:
    return op.type in TUBE_CUTS and (op.key == LABEL_PRINT_KEY or op.code.upper().startswith("PRINT_MARK"))


def _print_stage(op: RouteOp) -> str:
    """The printer's ПФ of a print operation: TP80E (`printed`), a Godex marker (`labels`), "" — no print."""
    if op.type in TUBE_CUTS and op.code.strip().upper() == TP80E_CODE and op.key != LABEL_PRINT_KEY:
        return PRINTED
    if _label_print(op) or (op.type == "insMark" and op.key == FLAG_PRINT_KEY):
        return LABELS
    return ""


def _tape_labels(op: RouteOp) -> bool:
    return op.key == LABEL_PRINT_KEY or op.code.upper().startswith("PRINT_MARK_LABEL")


def _cut_stage(op: RouteOp) -> str:
    code = op.code.upper()
    if printer := _print_stage(op):
        return printer
    if op.type in TUBE_CUTS or code.startswith("CUT_TUT"):
        return TUBES
    if op.type in WIRE_CUTS or _cable_strip(op) or code.startswith("CUT_"):
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
    """The stage of every operation (by its ETK position): wire and cable cuts and coax stripping — cut wires, printing
    labels and marks — the ПФ of its printer (Godex `labels`, TP80E `printed`), TUT and heat-shrink cuts — cut tubes (FAKRA too: each its own input-less ПФ); the end
    preparation of a side before the last side operation — the crimp of that side (`crimpedA` / `crimpedB`, placed
    right before the side: cut, crimp A, side A, crimp B, side B — owner 14.10) when some of it is a hand crimp or a coax centre contact soldered before its housing (all crimped on the cutting machine — no card of
    its own, stripping and tinning stay with the side);
    the end preparation without a side before the first side — the shared crimped wires (`crimped`) when there are
    splices in it, else it prepares the next crimp or side; a side operation — its side; an operation without a side
    (twisting, marking, wrapping) prepares the next crimp or side, and after the last side — the finished product."""
    ordered = sorted(ops, key=lambda op: op.position)
    if any(op.key == "coaxFakraCutStrip" for op in ops):
        covered_labels = any(op.key == "coaxProtectIns" for op in ops)
        last_side = max((op.position for op in ops if _side(op)), default=0)
        return {op.position: _cut_stage(op) if op.type in WIRE_CUTS | TUBE_CUTS else
                FINAL if covered_labels and op.key == "coaxInsLabel" and op.position > last_side else
                MARKED if op.type in {"coaxInsSleeve", "coaxInsTut"} else _side(op) or FINAL for op in ops}

    def assembly(op: RouteOp) -> bool:
        return bool(_side(op)) and not _cut_stage(op) and not _wire_end_prep(op) and op.type not in CONTACT_SOLDERS

    owned = {_side(op) for op in ordered if op.key.startswith(SIDE_STRIP_KEY)}  # the centre contact of such an end is the side's own work
    pins = {op.position for op in ordered if op.type in CONTACT_SOLDERS and _side(op) and _side(op) not in owned and any(
        other.position > op.position and _side(other) == _side(op) and assembly(other) for other in ordered)}

    def end_prep(op: RouteOp) -> bool:
        return _wire_end_prep(op) or op.position in pins

    sided = [op.position for op in ordered if _side(op) and not _cut_stage(op) and not end_prep(op)]
    first_side, last_side = min(sided, default=0), max(sided, default=0)
    prep = [op for op in ordered if not _cut_stage(op) and end_prep(op) and op.position < last_side]
    crimped: dict[int, str] = {}
    for side, stage in ((SIDE_A, CRIMPED_A), (SIDE_B, CRIMPED_B)):
        own = [op for op in prep if _side(op) == side]
        if any(op.type in END_CRIMPS or op.position in pins for op in own):
            crimped.update((op.position, stage) for op in own)
    shared = [op for op in prep if not _side(op) and op.position < first_side]
    if any(op.type in END_CRIMPS for op in shared):
        crimped.update((op.position, CRIMPED) for op in shared)
    result: dict[int, str] = {}
    waiting: list[int] = []
    for op in ordered:
        stage = _cut_stage(op) or crimped.get(op.position) or _side(op)
        if stage and stage not in SOURCE_STAGES:
            result.update((position, stage) for position in waiting)
            waiting = []
        if stage:
            result[op.position] = stage
        else:
            waiting.append(op.position)
    result.update((position, FINAL) for position in waiting)
    return result


def validate_coax_finishing(route: Route, ops: Sequence[RouteOp]) -> None:
    """Check newly built/manual routes, never mutate a pinned issued document on read."""
    by_key = {op.key: op for op in ops}
    final = route.final
    colors = [op.position for op in ops if op.type == "coaxConnColor"]
    if colors and list(final.ops[-len(colors):]) != colors:
        raise RouteError("Цветовая маркировка разъёмов должна завершать итоговый ПФ")
    if not {"coaxProtectIns", "coaxInsLabel", "coaxHeat"}.issubset(by_key):
        return
    label, heat = by_key["coaxInsLabel"].position, by_key["coaxHeat"].position
    if label not in final.ops or heat not in final.ops or final.ops.index(heat) != final.ops.index(label) + 1:
        raise RouteError("Наклейка этикеток должна быть в итоговом ПФ непосредственно перед усадкой прозрачной ТУТ")
    later = set(final.ops[final.ops.index(heat) + 1:])
    if any(op.position in later and op.type not in {"coaxVswr", "coaxConnColor"} for op in ops):
        raise RouteError("Наклейку этикеток и усадку прозрачной ТУТ выполняют после сборки разъёмов")
    printed = by_key.get("coaxPrintLabel")
    if printed:
        source = next(block for block in route.blocks if printed.position in block.ops)
        if source.key != final.key and not any(edge.source == source.key and edge.target == final.key for edge in route.edges):
            raise RouteError("Напечатанные этикетки должны поступать в итоговый ПФ")


def _title(stage: str, ops: Sequence[RouteOp], printed: bool = False) -> str:
    """`printed` — the scheme has the TP80E ПФ «Напечатанная маркировка» too: the Godex one is named by what it holds."""
    if stage == PRINTED:
        return "Напечатанная маркировка"
    if stage == LABELS:
        if ops and all(_tape_labels(op) for op in ops):
            return "Этикетки"
        if not printed:
            return "Напечатанная маркировка"
        tubes = [op for op in ops if op.code.upper().startswith("PRINT_MARK_TUBE")]
        return "Маркировочные трубки" if len(tubes) == len(ops) else "Этикетки и маркировочные трубки" if tubes else "Этикетки"
    if stage == MARKED:
        if any(op.key in {"coaxInsLabel", "coaxInsTut"} for op in ops):
            return "Кабель с метками и деталями"
        return "Кабель с трубками и деталями" if any(op.key == "coaxProtectIns" for op in ops) else "Кабель с деталями"
    if stage == WIRES:
        if ops and all(op.type == "coaxCut" or _cable_strip(op) for op in ops):
            stripped = any(_cable_strip(op) or _COAX_CUT_STRIP.match(op.code.upper()) for op in ops)
            return "Нарезанный и разделанный кабель" if stripped else "Нарезанный кабель"
        return "Нарезанные и обжатые провода" if any(op.type == "cutWireCrimp" for op in ops) else "Нарезанные провода"
    if stage == TUBES:
        return "Нарезанные трубки"
    if stage in CRIMP_STAGES:
        if ops and all(op.type in CONTACT_SOLDERS for op in ops):
            return "Кабель с центральным контактом" + {CRIMPED_A: ", сторона А", CRIMPED_B: ", сторона В"}.get(stage, "")
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


def _tubes_where_used(links: list[tuple[int, int, Decimal]], ops: Sequence[RouteOp], final_name: str,
                      stages: dict[int, str]) -> list[tuple[int, int, Decimal]]:
    """The links of a stored scheme with every tube cut and label print going where its pieces are put on, as the
    derived scheme takes them («Надеть ТУТ», the cap of a free end, the coax «Монтаж ТУТ», «Наклейка этикеток»), its
    pieces per unit kept. A scheme derived before 16.10 sent the coax tubes into the first operation after the cut
    (796К.2 КБ1: «Пайка центрального контакта А», although «Монтаж ТУТ» takes them)."""
    taken: dict[int, list[int]] = {}
    for source, target, _per_unit in _op_links(derive_route(ops, final_name)):
        if stages.get(source) in {TUBES} | PRINT_STAGES:
            taken.setdefault(source, []).append(target)
    result: list[tuple[int, int, Decimal]] = []
    for source, target, per_unit in links:
        targets = taken.get(source, []) if stages.get(source) in {TUBES} | PRINT_STAGES else []
        result.extend((source, end, per_unit) for end in targets or [target])
    return result


def generator_split(ops: Sequence[RouteOp], final_name: str = "", current: Route | None = None) -> Route:
    """One ПФ per generator stage, its operations in ETK order; the ПФ of the scheme's final operation is the product.
    Links between ПФ: the operations' links that go forward in the course of assembly (a ПФ by its first operation,
    the product last) — a link back joins nothing, as on the generator's cards; `per_unit` — the largest of the links
    between the two. Side A built feeds the crimp of side B that follows it (the wires go through); cut wires feed the
    crimped wires before the first side even without a link, and a ПФ left without a way on feeds the next one. Cut
    wires, labels and cut tubes take nothing: a stored scheme that cut them in one block (FAKRA «Заготовки» before
    20.11) links none of them to another.
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
    for source, target, per_unit in _tubes_where_used(_op_links(base), ops, final_name, stages):
        start, end = stages.get(source), stages.get(target)
        if start and end and start != end and rank[start] < rank[end] and (end not in SOURCE_STAGES or end == final_stage):
            links[(start, end)] = max(links.get((start, end), Decimal(0)), per_unit)
    # Crimped wires are made of cut wires even without a link; the crimp of a side after another side takes the wires
    # through that side (its link), and only when nothing feeds it — the cut wires.
    for stage in CRIMP_STAGES & members.keys():
        if WIRES in members and rank[WIRES] < rank[stage] and (stage == CRIMPED or not any(end == stage for _, end in links)):
            links.setdefault((WIRES, stage), Decimal(1))
    for index, stage in enumerate(order[:-1]):
        if not any(start == stage for start, _ in links):
            later = next((other for other in order[index + 1:] if other not in SOURCE_STAGES or other == final_stage), order[-1])
            links[(stage, later)] = Decimal(1)
    last_op = by_position.get(final_block.ops[-1])
    blocks = [
        Block(stage, (final_name or (last_op.name if last_op else "")) if stage == final_stage
              else _title(stage, [by_position[position] for position in members[stage] if position in by_position],
                          PRINTED in members),
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
