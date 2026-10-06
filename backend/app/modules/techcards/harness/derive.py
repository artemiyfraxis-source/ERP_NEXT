"""What a harness design means for production in OUR terms: the generator operations each wire implies (with the
route block it belongs to), and the check of the drawing against the position's «Данные из листа».

Operation types are the generator's own (`cutWire`, `cutWireStrip`, `prsTerm`, `insTerm`, `splice`, `twist`,
`stripCable`, `coaxStrip`, `cutTut`/`insTut`/`heatTut`, `insWrap`, `solderConn`), so a later step turns them into a
«Ручная сборка» build_config without a second operation model. An operation shared by several wires (a splice,
a pair, a cable, a covering) carries the same `shared` key on each of them — take it once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import re
from typing import Any, Iterable, Mapping, Sequence

from .model import Contact, Covering, HarnessDesign, RouteRow, SheetRow, Side, Wire
from .rules import (
    COVERING_KINDS, HarnessRuleError, blank_cut, covering_length, covering_wires, sheath_strip, strip_steps,
    terminal_name,
)

ROUTE_MODE_OPS = {
    "cut": "cutWire", "cut-strip-from": "cutWireStrip", "cut-strip-to": "cutWireStrip",
    "cut-strip-both": "cutWireStrip", "cut-crimp": "cutWireCrimp", "tin": "tin",
    "strip-from": "stripCable", "strip-to": "stripCable", "strip-both": "stripCable", "assembly": "",
}
ROUTE_MODE_LABELS = {
    "cut": "Резка", "cut-strip-from": "Резка, зачистка А", "cut-strip-to": "Резка, зачистка Б",
    "cut-strip-both": "Резка, зачистка обоих концов", "cut-crimp": "Резка и опрессовка", "tin": "Лужение",
    "strip-from": "Зачистка А", "strip-to": "Зачистка Б", "strip-both": "Зачистка обоих концов", "assembly": "Сборка",
}
SIDE_RU = {"A": "А", "B": "Б"}
BRAID_KINDS = frozenset({"braid", "metal-braid", "nylon"})


@dataclass(frozen=True)
class ImpliedOp:
    type: str                     # generator op type; "" = an assembly step with no generator equivalent
    label: str
    side: str = ""                # "A" | "B" | ""
    shared: str = ""              # "junction:<id>" | "screen:<id>" | "pair:<id>" | "cable:<id>" | "covering:<id>"
    block: str = ""               # route row id the operation belongs to ("" — no route yet)
    data: Mapping[str, Any] = field(default_factory=dict)


def _num(value: Decimal | None) -> Decimal | int | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else value.normalize()


def _ru(value: Decimal | int | None) -> str:
    return "—" if value is None else str(_num(Decimal(value))).replace(".", ",")


def wire_color(design: HarnessDesign, wire: Wire) -> str:
    """The colour shown for a wire: its colour source contact, else the first contact with a colour, else its own
    (buildRouteSourceItems, src/Techmap.Client/src/manufacturing/route-source.ts, TECHMAP-GRAPHER e63befb)."""
    source = wire.raw.get("colorSource") if isinstance(wire.raw, Mapping) else None
    if isinstance(source, Mapping):
        connector = design.connector(str(source.get("connectorId") or ""))
        contact = connector.contact(str(source.get("contactId") or "")) if connector else None
        return (contact.color if contact else "") or wire.color
    if "colorSource" in wire.raw and source is None:
        return wire.color
    for end in (wire.end_a, wire.end_b):
        contact = design.contact_at(end)[1]
        if contact and contact.color:
            return contact.color
    return wire.color


def wire_material(design: HarnessDesign, wire: Wire) -> tuple[str, str, str]:
    """(material name, catalog article, section): the pinned material, else the contact table's «Марка»."""
    contacts = [design.contact_at(end)[1] for end in (wire.end_a, wire.end_b)]
    name = (wire.material.display_name if wire.material else "") or next((c.wire for c in contacts if c and c.wire), "")
    section = next((c.wire_section for c in contacts if c and c.wire_section), "")
    return name, wire.material.source_key if wire.material else "", section


def block_of(design: HarnessDesign, kind: str, item_id: str) -> RouteRow | None:
    """The route row (block) an object is made in: the semi-finished row listing it, else any row listing it."""
    rows = design.route.rows if design.route else ()
    listing = [row for row in rows if (kind, item_id) in row.sources]
    return next((row for row in listing if row.kind == "semiFinished"), listing[0] if listing else None)


def end_title(design: HarnessDesign, wire: Wire, side: Side) -> str:
    end = wire.end(side)
    if end.kind == "contact":
        connector, contact = design.contact_at(end)
        return f"{(connector.designation if connector else '') or 'Соединитель'}:{contact.number if contact else '?'}"
    if end.kind == "junction":
        return next((j.label for j in design.junctions if j.id == end.junction_id), end.junction_id)
    if end.kind == "screen":
        screen = next((s for s in design.screens if s.id == end.screen_id), None)
        return f"экран {(screen.label if screen else '') or end.screen_id}"
    return "свободный конец"


def covering_ops(design: HarnessDesign, covering: Covering) -> list[ImpliedOp]:
    """Heat-shrink: cut, slide on, shrink; braid / nylon sleeve: «insWrap» braid; tape / band: «insWrap» tape."""
    topology = design.topology
    length = covering_length(covering, topology.dimensions) if topology else covering.length_mm
    block = block_of(design, "covering", covering.id)
    data = {
        "covering_id": covering.id, "name": covering.name, "kind": covering.kind,
        "material": covering.material.display_name if covering.material else "",
        "article": covering.material.source_key if covering.material else "", "length_mm": _num(length),
    }
    common = {"shared": f"covering:{covering.id}", "block": block.id if block else "", "data": data}
    if covering.kind == "heat-shrink":
        return [ImpliedOp("cutTut", f"Резка ТУТ — {covering.name}", **common),
                ImpliedOp("insTut", f"Надеть ТУТ — {covering.name}", **common),
                ImpliedOp("heatTut", f"Усадка ТУТ — {covering.name}", **common)]
    wrap = "braid" if covering.kind in BRAID_KINDS else "tape"
    return [ImpliedOp("insWrap", f"Монтаж: {COVERING_KINDS.get(covering.kind, covering.name)} {covering.name}",
                      shared=common["shared"], block=common["block"], data={**data, "wrap_kind": wrap})]


def _terminal_strip(design: HarnessDesign, wire: Wire, side: Side) -> Decimal | None:
    for row in design.route.rows if design.route else ():
        for requirement in row.terminal_requirements:
            if requirement.wire_id == wire.id and requirement.side == side:
                return requirement.strip_length_mm
    return None


def wire_ops(design: HarnessDesign, wire: Wire) -> tuple[list[ImpliedOp], list[str]]:
    """Generator operations one wire implies, in production order, plus Russian notes on what was assumed."""
    notes: list[str] = []
    label = wire.circuit or wire.id
    cable = design.cable_of(wire.id)
    block = block_of(design, "cable", cable.id) if cable else block_of(design, "wire", wire.id)
    block_id = block.id if block else ""
    ops: list[ImpliedOp] = []

    # 1. Cut. A core of a multicore cable is cut with its cable.
    if cable is not None:
        try:
            cut = blank_cut(cable)
            strip = sheath_strip(cable)
        except HarnessRuleError as error:
            notes.append(f"Кабель {cable.id}: {error}")
            cut, strip = None, None
        common = {"shared": f"cable:{cable.id}", "block": block_id}
        ops.append(ImpliedOp("cutWire", "Резка кабеля", data={
            "cable_id": cable.id, "cores": len(cable.member_wire_ids),
            "material": cable.material.display_name if cable.material else "",
            "article": cable.material.source_key if cable.material else "",
            "length_mm": _num(cable.length_mm), "correction_a_mm": _num(cable.correction_a_mm),
            "correction_b_mm": _num(cable.correction_b_mm), "cut_mm": _num(cut.cut_mm) if cut else None,
        }, **common))
        if strip is not None:
            ops.append(ImpliedOp("stripCable", "Разделка оболочки кабеля", data={
                "cable_id": cable.id, "sheath_a_mm": _num(strip.a_mm), "sheath_b_mm": _num(strip.b_mm),
                "sheath_cut_a_mm": _num(strip.cut_a_mm), "sheath_cut_b_mm": _num(strip.cut_b_mm),
            }, **common))
    else:
        strip_sides = [side for side in ("A", "B") if wire.end(side).kind != "free" and wire.strip(side) is None]
        material, article, section = wire_material(design, wire)
        try:
            cut = blank_cut(wire)
        except HarnessRuleError as error:
            notes.append(f"Провод {label}: {error}")
            cut = None
        if cut is not None and cut.cut_mm is None:
            notes.append(f"Провод {label}: нет длины в чертеже — длина резки не посчитана")
        ops.append(ImpliedOp("cutWireStrip" if strip_sides else "cutWire",
                             "Резка с разделкой" if strip_sides else "Резка", block=block_id, data={
            "wire_id": wire.id, "material": material, "article": article, "section": section,
            "length_mm": _num(wire.length_mm), "correction_a_mm": _num(wire.correction_a_mm),
            "correction_b_mm": _num(wire.correction_b_mm), "rounding_step_mm": _num(wire.rounding_step_mm),
            "cut_mm": _num(cut.cut_mm) if cut else None, "strip_sides": strip_sides,
        }))

    # 2. Ends.
    for side in ("A", "B"):
        end = wire.end(side)
        profile = wire.strip(side)
        if profile is not None:
            ops.append(ImpliedOp("coaxStrip", f"Разделка коаксиала ({SIDE_RU[side]})", side=side, block=block_id, data={
                "wire_id": wire.id, "profile": profile.pin.display_name, "source_key": profile.pin.source_key,
                "layers": [{"name": f"L{layer.index}", "length_mm": _num(layer.length_mm), "step_mm": _num(step),
                            "diameter_mm": _num(layer.diameter_mm)} for layer, step in strip_steps(profile.layers)],
            }))
        if end.kind == "contact":
            connector, contact = design.contact_at(end)
            if connector is None or contact is None:
                notes.append(f"Провод {label}, конец {SIDE_RU[side]}: контакт не найден — операции конца не выведены")
                continue
            ops.extend(_contact_ops(design, wire, side, connector.designation, connector.part_number, contact, block_id, notes))
        elif end.kind == "junction":
            junction = next((j for j in design.junctions if j.id == end.junction_id), None)
            ops.append(ImpliedOp("splice", f"Сращивание: {junction.label if junction else end.junction_id}", side=side,
                                 shared=f"junction:{end.junction_id}", block=block_id, data={
                "junction_id": end.junction_id, "wire_ids": list(junction.wire_ids) if junction else [wire.id],
                "units": 1,
            }))
        elif end.kind == "screen":
            screen = next((s for s in design.screens if s.id == end.screen_id), None)
            ops.append(ImpliedOp("splice", f"Подпайка к экрану {(screen.label if screen else '') or end.screen_id}",
                                 side=side, shared=f"screen:{end.screen_id}", block=block_id, data={
                "screen_id": end.screen_id, "target": "screen",
                "wire_ids": list(screen.wire_ids) if screen else [], "drain_wire_id": wire.id,
            }))

    # 3. Pair twist, coverings.
    for pair in design.diff_pairs:
        if wire.id in pair.wire_ids:
            ops.append(ImpliedOp("twist", "Свивка пары", shared=f"pair:{pair.id}", block=block_id,
                                 data={"pair_id": pair.id, "wire_ids": list(pair.wire_ids)}))
    if design.topology is not None:
        for covering in design.topology.coverings:
            if wire.id in covering_wires(design.topology, covering):
                ops.extend(covering_ops(design, covering))
    return ops, notes


def _contact_ops(
    design: HarnessDesign, wire: Wire, side: Side, designation: str, part_number: str, contact: Contact,
    block_id: str, notes: list[str],
) -> list[ImpliedOp]:
    base = {"wire_id": wire.id, "connector": designation, "part_number": part_number,
            "contact_number": contact.number, "contact_name": contact.name, "circuit": contact.circuit}
    if not contact.terminal_article:
        if wire.strip(side) is not None:
            return []
        notes.append(f"Провод {wire.circuit or wire.id}, конец {SIDE_RU[side]} ({designation}:{contact.number}): "
                     "терминал не задан — выведена пайка, проверь")
        return [ImpliedOp("solderConn", f"Пайка разъёма ({SIDE_RU[side]})", side=side, block=block_id, data=base)]
    snapshot = contact.terminal
    terminal = {
        "terminal_article": contact.terminal_article, "terminal": terminal_name(contact),
        "terminal_l_plus_mm": _num(snapshot.l_plus_mm) if snapshot else None,
        "terminal_l_minus_mm": _num(snapshot.l_minus_mm) if snapshot else None,
        "allowance_mm": _num(wire.correction(side)), "strip_length_mm": _num(_terminal_strip(design, wire, side)),
    }
    return [
        ImpliedOp("prsTerm", f"Опрессовка терминала ({SIDE_RU[side]})", side=side, block=block_id, data={**base, **terminal}),
        ImpliedOp("insTerm", f"Монтаж терминала ({SIDE_RU[side]})", side=side, block=block_id,
                  data={**base, "terminal": terminal["terminal"]}),
    ]


def route_ops(design: HarnessDesign, wire: Wire) -> list[ImpliedOp]:
    """The operations the drawing's own route puts in the wire's block, with their БД.ОП codes."""
    cable = design.cable_of(wire.id)
    block = block_of(design, "cable", cable.id) if cable else block_of(design, "wire", wire.id)
    if block is None:
        return []
    return [
        ImpliedOp(ROUTE_MODE_OPS.get(op.mode, ""), (op.operation.display_name if op.operation else "")
                  or ROUTE_MODE_LABELS.get(op.mode, op.mode), block=block.id,
                  side="A" if op.mode.endswith("-from") else "B" if op.mode.endswith("-to") else "",
                  data={"code": op.operation.source_key if op.operation else "", "mode": op.mode, "note": op.note})
        for op in block.operations
    ]


# --- «Данные из листа» -------------------------------------------------------------------------------------------
SHEET_TYPES = {
    "wire": ("провод", "кабель", "коакс"), "connector": ("соед", "разъ", "колод", "корпус", "вилк", "розет"),
    "terminal": ("терм", "контакт", "пин"), "covering": ("тут", "термо", "оплет", "оплёт", "трубк", "лент", "нейлон", "изол"),
}
UNITS = {"wire": "м", "covering": "м", "connector": "шт", "terminal": "шт"}
KIND_RU = {"wire": "Провод", "connector": "Соединитель", "terminal": "Терминал", "covering": "Оболочка"}


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е").replace("×", "x").replace("х", "x").replace("*", "x")
    return re.sub(r"[\s\"'«»“”]+", "", re.sub(r"(\d),(\d)", r"\1.\2", text))


def _tokens(text: str) -> set[str]:
    text = re.sub(r"(\d),(\d)", r"\1.\2", text.lower().replace("ё", "е").replace("×", "x").replace("х", "x"))
    return {t.strip(".-") for t in re.split(r"[^a-zа-я0-9.\-x]+", text) if len(t.strip(".-")) > 1 or t.isdigit()}


@dataclass
class SheetItem:
    kind: str                     # wire | connector | terminal | covering
    name: str
    article: str
    alt_article: str
    quantity: Decimal
    item_ids: list[str]


@dataclass(frozen=True)
class SheetMatch:
    kind: str
    name: str
    article: str
    unit: str
    quantity: Decimal | None      # in the drawing, per one product
    item_ids: tuple[str, ...]
    sheet_id: str = ""
    sheet_quantity: Decimal | None = None
    matched_by: str = ""          # article | kd | name | tokens
    status: str = "ok"            # ok | quantity | not_in_sheet | not_in_drawing


def sheet_items(design: HarnessDesign) -> list[SheetItem]:
    """What the drawing consumes for one product: wire/cable metres by cut length, connectors, terminals on wired
    contacts, covering metres."""
    items: dict[tuple[str, str], SheetItem] = {}

    def add(kind: str, name: str, article: str, alt: str, quantity: Decimal, item_id: str) -> None:
        key = (kind, _norm(article or name))
        item = items.setdefault(key, SheetItem(kind, name, article, alt, Decimal(0), []))
        item.quantity += quantity
        item.item_ids.append(item_id)

    def metres(cut: Decimal | None, length: Decimal | None) -> Decimal:
        value = cut if cut is not None else length
        return (value or Decimal(0)) / 1000

    for wire in design.wires:
        if design.cable_of(wire.id) is not None:
            continue
        name, article, _section = wire_material(design, wire)
        try:
            cut = blank_cut(wire).cut_mm
        except HarnessRuleError:
            cut = None
        add("wire", name, article, "", metres(cut, wire.length_mm), wire.id)
    for cable in design.cables:
        try:
            cut = blank_cut(cable).cut_mm
        except HarnessRuleError:
            cut = None
        add("wire", cable.material.display_name if cable.material else "", cable.material.source_key if cable.material else "",
            "", metres(cut, cable.length_mm), cable.id)
    wired = {(end.connector_id, end.contact_id) for wire in design.wires for end in (wire.end_a, wire.end_b) if end.kind == "contact"}
    for connector in design.connectors:
        add("connector", connector.template_name or connector.designation, connector.part_number,
            connector.template_article, Decimal(1), connector.id)
        for contact in connector.contacts:
            if contact.terminal_article and (connector.id, contact.id) in wired:
                add("terminal", contact.terminal.description if contact.terminal else "", terminal_name(contact),
                    contact.terminal_article, Decimal(1), f"{connector.id}:{contact.id}")
    if design.topology is not None:
        for covering in design.topology.coverings:
            length = covering_length(covering, design.topology.dimensions)
            add("covering", (covering.material.display_name if covering.material else "") or covering.name,
                covering.material.source_key if covering.material else "", "", metres(None, length), covering.id)
    return list(items.values())


def _type_fits(kind: str, sheet_type: str) -> bool:
    lowered = sheet_type.lower()
    return not lowered or any(marker in lowered for marker in SHEET_TYPES[kind])


def _find_row(item: SheetItem, rows: Sequence[SheetRow]) -> tuple[SheetRow | None, str]:
    for article in (item.article, item.alt_article):
        key = _norm(article)
        if not key:
            continue
        for row in rows:
            if key == _norm(row.article):
                return row, "article"
        for row in rows:
            if key == _norm(row.kd_article):
                return row, "kd"
    name = _norm(item.name)
    if name:
        for row in rows:
            if _type_fits(item.kind, row.type) and name in (_norm(row.name), _norm(row.article)):
                return row, "name"
    # Last resort, as the generator's material match: the most shared tokens including a number, unique only.
    wanted = _tokens(f"{item.article} {item.name}")
    best, score, tied = None, 0, False
    for row in rows:
        if not _type_fits(item.kind, row.type):
            continue
        hits = wanted & _tokens(f"{row.article} {row.kd_article} {row.name}")
        if not any(any(ch.isdigit() for ch in token) for token in hits):
            continue
        if len(hits) > score:
            best, score, tied = row, len(hits), False
        elif len(hits) == score:
            tied = True
    return (best, "tokens") if best is not None and score >= 2 and not tied else (None, "")


def match_sheet(design: HarnessDesign, rows: Iterable[SheetRow]) -> tuple[list[SheetMatch], list[str]]:
    """Each drawing item against «Данные из листа» (article, then «по КД» article, then name), with Russian warnings:
    in the drawing but not in the sheet, in the sheet but not in the drawing, quantity differs."""
    rows = list(rows)
    matches: list[SheetMatch] = []
    warnings: list[str] = []
    by_row: dict[str, list[tuple[SheetItem, str]]] = {}
    for item in sheet_items(design):
        row, how = _find_row(item, rows)
        if row is None:
            matches.append(SheetMatch(item.kind, item.name, item.article, UNITS[item.kind], item.quantity,
                                      tuple(item.item_ids), status="not_in_sheet"))
            warnings.append(f"{KIND_RU[item.kind]} {item.article or item.name or '?'} есть в чертеже, но нет в листе")
            continue
        by_row.setdefault(row.id, []).append((item, how))
    for row in rows:
        found = by_row.get(row.id)
        if not found:
            matches.append(SheetMatch("", row.name, row.article, "", None, (), row.id, row.quantity, status="not_in_drawing"))
            warnings.append(f"{row.type or 'Позиция'} {row.article or row.name} есть в листе, но нет в чертеже")
            continue
        kind = found[0][0].kind
        total = sum((item.quantity for item, _how in found), Decimal(0))
        unit = UNITS[kind]
        shown = total.quantize(Decimal("0.001")) if unit == "м" else total
        differs = row.quantity is not None and (
            abs(shown - row.quantity) > Decimal("0.0005") if unit == "м" else shown != row.quantity)
        if differs:
            warnings.append(f"{row.type or KIND_RU[kind]} {row.article or row.name}: в чертеже {_ru(shown)} {unit}, "
                            f"в листе {_ru(row.quantity)}")
        for item, how in found:
            matches.append(SheetMatch(item.kind, item.name, item.article, unit, item.quantity, tuple(item.item_ids),
                                      row.id, row.quantity, how, "quantity" if differs else "ok"))
    return matches, warnings
