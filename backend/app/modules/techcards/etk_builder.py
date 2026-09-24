"""Build a project ETK from a SUPPLYMAP generator config and operation cards."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable, Mapping, Sequence

from .operation_cards import OperationCardTemplate


ETK_VERSION = 1
NO_COMPONENTS = {
    "SDR_tin", "INS_TUT", "PRS_SLEEVE_coax", "TWIST", "HEAT_TYT_short",
    "HEAT_TYT_long", "HEAT_TTK_short", "HEAT_TTK_long", "PRS_MHF",
    "PRS_MHF_LK", "PRS_IDC", "PRS_CAP", "PRS_COPPER", "INS_NEYLON",
    "INS_ПМЛ", "INS_MARK", "INS_CAP", "INS_lock", "INS_гофра_разрез",
    "INS_гофра_неразрез", "INS_стяжки", "INS_tape", "MOLD_hand", "MOLD_auto",
    "PLASM", "PRS_CAP_COAX", "INS_END_CAP", "PREP_FOLD",
}
PLACEHOLDERS = {
    "index": "{{INDEX}}", "name": "{{NAME}}", "wire_name": "{{WIRE_NAME}}",
    "wire_art": "{{WIRE_ART}}", "wire_qty": "{{WIRE_QTY}}", "length": "{{LENGTH}}",
    "semifinished": "{{SEMIFINISHED}}", "result": "{{RESULT}}",
    "term_name_a": "{{TERM_A_NAME}}", "term_art_a": "{{TERM_A_ART}}",
    "term_qty_a": "{{TERM_A_QTY}}", "conn_name_a": "{{CONN_A_NAME}}",
    "conn_art_a": "{{CONN_A_ART}}", "conn_qty_a": "{{CONN_A_QTY}}",
    "term_name_b": "{{TERM_B_NAME}}", "term_art_b": "{{TERM_B_ART}}",
    "term_qty_b": "{{TERM_B_QTY}}", "conn_name_b": "{{CONN_B_NAME}}",
    "conn_art_b": "{{CONN_B_ART}}", "conn_qty_b": "{{CONN_B_QTY}}",
    "op_num": "{{OP_NUM}}", "t_prep": "{{T_PREP}}", "t_op": "{{T_OP}}",
    "t_machine": "{{T_MACHINE}}", "tolerance": "{{ДОПУСК}}", "length_kd": "[L КД]",
}


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0").strip().replace(",", "."))
    except InvalidOperation:
        return Decimal(0)


def _number(value: object, default: Decimal = Decimal(0)) -> Decimal:
    number = _decimal(value)
    return number if number else default


def _format(value: object, places: int = 4) -> str:
    number = _decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(number, "f").rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def _part_quantity(config: Mapping[str, Any]) -> Decimal:
    quantity = _decimal(config.get("partQty"))
    return quantity if quantity > 0 else Decimal(1)


def _wires(op: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    routed = op.get("wires")
    source = routed if isinstance(routed, list) else config.get("wires")
    return [dict(item) for item in source or [] if isinstance(item, Mapping)]


def _kind(op: Mapping[str, Any]) -> str:
    return {"prsTerm": "prs", "insTerm": "ins", "solderConn": "sdr"}.get(str(op.get("type")), "")


def _endpoint(op: Mapping[str, Any]) -> dict[str, Any]:
    value = op.get("ep")
    return dict(value) if isinstance(value, Mapping) else {}


def _side_label(op: Mapping[str, Any]) -> str:
    return "В" if op.get("side") == "B" else "А"


def _location(op: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    endpoint = _endpoint(op)
    label = str(endpoint.get("label") or endpoint.get("connName") or "")
    return label if config.get("bigAssembly") and label else f"ст. {_side_label(op)}"


def _term_count(op: Mapping[str, Any], config: Mapping[str, Any]) -> int:
    count = sum(
        int(_number(wire.get("qty"), Decimal(1)) * _number(wire.get("cores"), Decimal(1)))
        for wire in _wires(op, config)
    )
    endpoint = _endpoint(op)
    if endpoint.get("double"):
        per_terminal = max(2, int(_number(endpoint.get("perTerm"), Decimal(2))))
        return (count + per_terminal - 1) // per_terminal
    return count


def _wire_name(wire: Mapping[str, Any]) -> str:
    label = str(wire.get("art") or wire.get("name") or "").strip()
    length = _decimal(wire.get("length"))
    return " ".join(part for part in (label, f"{_format(length)}мм" if length else "") if part)


def _combined_wire_data(wires: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not wires:
        return {}
    if len(wires) == 1:
        return dict(wires[0])
    return {
        "name": "\n".join(str(item.get("name") or "") for item in wires),
        "art": "\n".join(str(item.get("art") or "") for item in wires),
        "qty": sum(_number(item.get("qty"), Decimal(1)) for item in wires),
        "length": "\n".join(str(item.get("length") or "") for item in wires),
    }


def _suffix(op: Mapping[str, Any]) -> str:
    operation_type = op.get("type")
    if operation_type == "cutWireStrip":
        return "разделка"
    if operation_type == "cutWireCrimp":
        return "обжим"
    if operation_type == "cutTut":
        return str(op.get("tutArt") or "ТУТ")
    if operation_type == "insTut":
        return "на жгут" if op.get("harness") else "на сростки" if op.get("spliceWeld") else "под пайку"
    if operation_type == "heatTut":
        return "на жгуте" if op.get("harness") else "на сростках" if op.get("spliceWeld") else "на пайке"
    if _kind(op):
        endpoint = _endpoint(op)
        return str(endpoint.get("connName") or endpoint.get("connArt") or "")
    return ""


def _result_name(
    op: Mapping[str, Any], config: Mapping[str, Any], previous: str,
) -> str:
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    wires = _wires(op, config)
    endpoint = _endpoint(op)
    if kind == "prs":
        terminal = str(endpoint.get("termArt") or endpoint.get("termName") or "")
        sources = list(dict.fromkeys(str(item) for item in op.get("wireSources") or [] if item))
        if op.get("side") == "B" and previous and len(sources) <= 1:
            return f"{previous}; {_location(op, config)}: обжат терм. {terminal}".strip()
        return f"Заготовка: {len(wires)} пров. с обжатым терм. {terminal} ({_location(op, config)})"
    if kind == "ins":
        connector = str(endpoint.get("connArt") or endpoint.get("connName") or "")
        return f"Разъём {connector} смонтирован ({_location(op, config)}, {len(wires)} пров.)"
    if kind == "sdr":
        connector = str(endpoint.get("connArt") or endpoint.get("connName") or "")
        return f"Разъём {connector} припаян ({_location(op, config)})"
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        cut = "; ".join(filter(None, (_wire_name(item) for item in wires)))
        terminal = str(endpoint.get("termArt") or endpoint.get("termName") or "")
        return f"{cut}; {_location(op, config)}: обжат терм. {terminal}" if terminal else cut
    if operation_type == "cutTut":
        material = str(op.get("tutArt") or op.get("tutName") or "ТУТ")
        segments = op.get("tutSegs") if isinstance(op.get("tutSegs"), list) else []
        if any(item.get("len") for item in segments if isinstance(item, Mapping)):
            parts = [f"{item.get('count', 0)}×{item.get('len')}мм" for item in segments if item.get("len")]
            return f"{material}: {' + '.join(parts)}"
        count = int(_decimal(op.get("tutCount")))
        return f"{material}{f', {count} отрезков' if count else ''}"
    if operation_type == "insTut":
        if previous:
            return f"{previous} ({'термоусадка надета' if op.get('harness') else 'ТУТ надет'})"
        return "ТУТ надет на провода"
    if operation_type == "heatTut":
        if op.get("harness") and previous.endswith(" (термоусадка надета)"):
            return previous.removesuffix(" (термоусадка надета)") + " (термоусадка усажена)"
        return f"{previous} ({'термоусадка усажена' if op.get('harness') else 'ТУТ усажен'})" if previous else "Усадка ТУТ"
    if operation_type == "stripCable":
        return f"{previous} (концы разделаны)" if previous else f"Концы разделаны ({op.get('tutCount') or 0} конц.)"
    labels = {
        "twist": "со свивкой", "tin": "с лужением", "insMark": "маркирован",
        "seal": "загерметизирован", "insWrap": "в оплётке" if op.get("wrapKind") == "braid" else "в защитной ленте",
    }
    return f"{previous} ({labels[operation_type]})" if previous and operation_type in labels else previous


def _group_cut_components(wires: Sequence[Mapping[str, Any]], quantity: Decimal) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, Any]] = {}
    for wire in wires:
        key = str(wire.get("art") or wire.get("name") or "").strip()
        row = grouped.setdefault(key, {"art": key, "name": str(wire.get("name") or ""), "meters": Decimal(0)})
        row["meters"] += _number(wire.get("qty"), Decimal(1)) * _decimal(wire.get("length")) * quantity / 1000
    return [{"art": row["art"], "name": row["name"], "norm": _format(row["meters"]), "unit": "М."} for row in grouped.values()]


def _components(op: Mapping[str, Any], config: Mapping[str, Any], code: str) -> list[dict[str, str]]:
    if code in NO_COMPONENTS:
        return []
    operation_type = str(op.get("type") or "")
    quantity = _part_quantity(config)
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        rows = _group_cut_components(_wires(op, config), quantity)
        endpoint = _endpoint(op)
        terminal = str(endpoint.get("termArt") or endpoint.get("termName") or "")
        if terminal:
            count = sum((_number(wire.get("qty"), Decimal(1)) for wire in _wires(op, config)), Decimal(0))
            rows.append({
                "art": terminal, "name": str(endpoint.get("termName") or ""),
                "norm": _format(count * quantity), "unit": "Шт.",
            })
        return rows
    if operation_type == "cutTut":
        count = _decimal(op.get("tutCount")) * quantity
        return [{"art": str(op.get("tutArt") or ""), "name": str(op.get("tutName") or op.get("tutArt") or ""), "norm": _format(count), "unit": "Шт."}]
    if operation_type == "insWrap":
        meters = _decimal(op.get("wrapLen")) * quantity / 1000
        return [{"art": str(op.get("wrapArt") or ""), "name": str(op.get("wrapName") or op.get("wrapArt") or ""), "norm": _format(meters), "unit": "М."}]
    if operation_type == "insMark":
        return [{"art": str(item.get("art") or item.get("name") or ""), "name": str(item.get("name") or ""), "norm": _format(_number(item.get("qty"), Decimal(1)) * quantity), "unit": "Шт."} for item in op.get("marks") or []]
    endpoint = _endpoint(op)
    kind = _kind(op)
    if kind == "prs":
        return [{"art": str(endpoint.get("termArt") or endpoint.get("termName") or ""), "name": str(endpoint.get("termName") or ""), "norm": _format(_term_count(op, config) * quantity), "unit": "Шт."}]
    if kind in {"ins", "sdr"}:
        return [{"art": str(endpoint.get("connArt") or endpoint.get("connName") or ""), "name": str(endpoint.get("connName") or ""), "norm": _format(quantity), "unit": "Шт."}]
    return []


def _time(op: Mapping[str, Any], config: Mapping[str, Any], result_name: str) -> list[dict[str, str]]:
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    wires = _wires(op, config)
    quantity = _part_quantity(config)
    operation_minutes = _decimal(op.get("tOp")) / 60
    setup_minutes = _decimal(op.get("tPrep")) / 60
    if (operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"} or kind) and wires:
        units = lambda wire: _number(wire.get("qty"), Decimal(1)) * (_number(wire.get("cores"), Decimal(1)) if kind else 1)
        if kind in {"ins", "sdr"} or (kind == "prs" and op.get("side") == "B"):
            return [{"name": result_name, "norm": _format(operation_minutes * sum(map(units, wires)) * quantity + setup_minutes, 2)}]
        setup_per_wire = setup_minutes / len(wires)
        terminal = str(_endpoint(op).get("termArt") or _endpoint(op).get("termName") or "") if operation_type == "cutWireCrimp" or kind else ""
        return [{
            "name": f"{_wire_name(wire)}{f', обжатый терминал {terminal}' if terminal else ''}",
            "norm": _format((operation_minutes * units(wire) * quantity if operation_minutes else units(wire) * quantity) + setup_per_wire, 2),
        } for wire in wires]
    twist = config.get("twist") if isinstance(config.get("twist"), Mapping) else {}
    config_wires = config.get("wires") if isinstance(config.get("wires"), list) else []
    twist_count = Decimal(1)
    if twist.get("mode") == "pairs" and isinstance(twist.get("pairs"), list):
        twist_count = sum((
            min(
                _number(config_wires[pair[0]].get("qty"), Decimal(1)),
                _number(config_wires[pair[1]].get("qty"), Decimal(1)),
            )
            for pair in twist["pairs"]
            if isinstance(pair, list) and len(pair) >= 2
            and 0 <= pair[0] < len(config_wires) and 0 <= pair[1] < len(config_wires)
        ), Decimal(0)) or Decimal(1)
    counters = {
        "twist": twist_count, "tin": sum((_number(item.get("qty"), Decimal(1)) for item in wires), Decimal(0)) or 1,
        "cutTut": _number(op.get("tutCount"), Decimal(1)), "insTut": _number(op.get("tutCount"), Decimal(1)),
        "endCap": _number(op.get("tutCount"), Decimal(1)), "foldCore": _number(op.get("tutCount"), Decimal(1)),
        "stripCable": _number(op.get("tutCount"), Decimal(1)),
        "heatTut": _number(op.get("tutCount"), Decimal(1)), "insWrap": _number(op.get("tutCount"), Decimal(1)),
        "insMark": sum((_number(item.get("qty"), Decimal(1)) for item in op.get("marks") or []), Decimal(0)) or 1,
        "seal": _number(op.get("sealPlaces"), Decimal(1)), "splice": _number(op.get("units"), Decimal(1)),
        "insHousing": _number(op.get("units"), Decimal(1)),
    }
    count = counters.get(operation_type)
    norm = operation_minutes * count * quantity + setup_minutes if count is not None else operation_minutes
    return [{"name": result_name, "norm": _format(norm, 2)}]


def _cut_tolerance(wire_data: Mapping[str, Any]) -> str:
    length = _decimal(str(wire_data.get("length") or "").split("\n", 1)[0])
    if not length:
        return ""
    tolerance = max(1, int((length * Decimal(7) / 1000).to_integral_value(rounding="ROUND_CEILING")))
    return f"(+/-){tolerance}мм"


def _pins(value: object) -> list[int]:
    result: list[int] = []
    for token in re.split(r"[,;\s]+", str(value or "")):
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        for pin in range(min(start, end), min(max(start, end), min(start, end) + 199) + 1):
            if pin not in result:
                result.append(pin)
    return result


def _fields(op: Mapping[str, Any], config: Mapping[str, Any], wire_data: Mapping[str, Any]) -> dict[str, Any]:
    operation_type = str(op.get("type") or "")
    fields: dict[str, Any] = {}
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        fields["cutTolerance"] = _cut_tolerance(wire_data)
        first_length = str(wire_data.get("length") or "").split("\n", 1)[0]
        fields["lengthKd"] = f"{first_length}мм" if first_length else ""
    strip = config.get("strip") if operation_type == "cutWire" else op.get("strip") if operation_type in {"cutWireStrip", "cutWireCrimp"} else None
    if isinstance(strip, Mapping):
        fields["strip"] = {"a": bool(strip.get("a")), "b": bool(strip.get("b")), "lenA": strip.get("lenA") or "", "lenB": strip.get("lenB") or ""}
    if operation_type == "twist":
        twist = config.get("twist") if isinstance(config.get("twist"), Mapping) else {}
        wires = config.get("wires") or []
        pairs = []
        for index, pair in enumerate(twist.get("pairs") or []):
            names = [str(wires[item].get("art") or wires[item].get("name") or "") for item in pair if 0 <= item < len(wires)]
            if names:
                pairs.append(f"{index + 1}) {' + '.join(names)}")
        fields["twist"] = {"pitch": twist.get("pitch") or "", "pairs": "\n".join(pairs), "count": len(pairs) or 1}
    wires = _wires(op, config)
    if _kind(op) or operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        pin_values = op.get("pins") if isinstance(op.get("pins"), list) else []
        fields["wires"] = [{
            "name": str(wire.get("name") or ""), "art": str(wire.get("art") or ""),
            "qty": float(_number(wire.get("qty"), Decimal(1))), "length": wire.get("length") or "",
            "pins": _pins(pin_values[index]) if _kind(op) and index < len(pin_values) else [],
        } for index, wire in enumerate(wires)]
        if any(item["pins"] for item in fields["wires"]):
            fields["pinout"] = True
    terminal_norm = op.get("terminalNorm")
    if isinstance(terminal_norm, Mapping):
        for source, target in (("applicator", "applicator"), ("crimpHeight", "crimpHeight"), ("pullForce", "pullForce"), ("bySection", "crimpBySection")):
            if source in terminal_norm:
                fields[target] = deepcopy(terminal_norm[source])
    return fields


def _placeholder_map(
    op: Mapping[str, Any], config: Mapping[str, Any], previous: str, result: str,
    wire_data: Mapping[str, Any], fields: Mapping[str, Any],
) -> dict[str, str]:
    endpoint = _endpoint(op)
    endpoint_side = {
        "termName": endpoint.get("termName") or "", "termArt": endpoint.get("termArt") or "",
        "termQty": _term_count(op, config), "connName": endpoint.get("connName") or "",
        "connArt": endpoint.get("connArt") or "", "connQty": _term_count(op, config),
    }
    side_a = endpoint_side if endpoint and op.get("side") != "B" else config.get("sideA") or {}
    side_b = endpoint_side if endpoint and op.get("side") == "B" else config.get("sideB") or {}
    values = {
        "index": config.get("assemblyIndex") or "", "name": config.get("assemblyName") or "",
        "wire_name": wire_data.get("name") or "", "wire_art": wire_data.get("art") or "",
        "wire_qty": wire_data.get("qty") or "", "length": wire_data.get("length") or "",
        "semifinished": previous, "result": result, "op_num": op.get("opNum") or "",
        "t_prep": op.get("tPrep") or "", "t_op": op.get("tOp") or "", "t_machine": op.get("tMachine") or "",
        "tolerance": fields.get("cutTolerance") or "", "length_kd": fields.get("lengthKd") or "",
    }
    for suffix, side in (("a", side_a), ("b", side_b)):
        for field in ("termName", "termArt", "termQty", "connName", "connArt", "connQty"):
            values[f"{re.sub(r'(?<!^)(?=[A-Z])', '_', field).lower()}_{suffix}"] = side.get(field) or ""
    return {PLACEHOLDERS[key]: str(value) for key, value in values.items()}


def _resolve_card(card: OperationCardTemplate, replacements: Mapping[str, str], fields: Mapping[str, Any]) -> dict[str, Any]:
    def substitute(value: object) -> str:
        text = str(value or "")
        for token, replacement in replacements.items():
            text = text.replace(token, replacement)
        twist = fields.get("twist")
        if isinstance(twist, Mapping):
            text = text.replace("‹шаг›", f"{twist.get('pitch')} мм" if twist.get("pitch") else "по КД")
            text = text.replace("‹провода›", str(twist.get("pairs") or "по КД").replace("\n", "; "))
        return text

    steps = []
    for step in card.steps:
        text = substitute(step.text)
        tolerance = substitute(step.tolerance)
        if step.kind == "Контроль" and tolerance in {"", "-", "—"}:
            lowered = text.casefold()
            if fields.get("crimpHeight") is not None and re.search(r"datasheet|высота обжима|геометр", lowered):
                tolerance = f"{fields.get('crimpHeight')} мм".strip()
            elif fields.get("pullForce") is not None and re.search(r"pull[\s-]?test|разрыв|усилие обрыва", lowered):
                tolerance = f"{fields.get('pullForce')} Н".strip()
        if re.search(r"тестовый\s*рез", text.casefold()) and not tolerance:
            tolerance = str(fields.get("cutTolerance") or "")
        steps.append({"number": step.number, "kind": step.kind, "text": text, "tolerance": "" if tolerance in {"-", "—"} else tolerance})
    tooling = []
    applicator_set = False
    for tool in card.tooling:
        program = substitute(tool.program)
        free_program = not program or (fields.get("cutCrimp") and program in {"-", "—"})
        if not applicator_set and fields.get("applicator") and tool.section == "Оборудование" and free_program:
            program = str(fields["applicator"])
            applicator_set = True
        tooling.append({
            "section": tool.section, "number": tool.number, "name": substitute(tool.name),
            "inventory_number": tool.inventory_number, "program": program,
            "parameters": substitute(tool.parameters), "norm": tool.norm, "unit": tool.unit,
        })
    return {"name": card.name, "safety": card.safety, "defects": card.defects, "steps": steps, "tooling": tooling}


def _topology(config: Mapping[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    splice = next((item for item in config.get("ops") or [] if item.get("type") == "splice"), None)

    def node(identifier: str, connector: str, side: str = "") -> dict[str, Any]:
        if identifier not in by_id:
            by_id[identifier] = {"id": identifier, "conn": connector, "side": side, "depth": 0}
            nodes.append(by_id[identifier])
        return by_id[identifier]

    for op in config.get("ops") or []:
        if _kind(op) not in {"prs", "ins", "sdr"}:
            continue
        endpoint = _endpoint(op)
        connector = str(endpoint.get("connArt") or endpoint.get("connName") or "")
        if not connector:
            continue
        identifier = connector
        if op.get("side") == "B" and identifier in by_id and by_id[identifier]["side"] == "A":
            identifier += " (В)"
        node(identifier, connector, str(op.get("side") or ""))
        per_edge: dict[tuple[str, str], float] = {}
        sources = op.get("wireSources") if isinstance(op.get("wireSources"), list) else []
        for index, wire in enumerate(_wires(op, config)):
            source = str(sources[index] or "") if index < len(sources) else ""
            if source and source != identifier:
                node(source, source)
                pair = (source, identifier)
            elif not source and splice:
                hub = node("Сростки", f"Сростки ×{op.get('units') or splice.get('units') or 1}")
                hub["kind"] = "splice"
                pair = ("Сростки", identifier) if op.get("side") == "B" else (identifier, "Сростки")
            else:
                continue
            edge = edges.setdefault(pair, {"from": pair[0], "to": pair[1], "wires": [], "lengths": [], "count": 0})
            per_edge[pair] = per_edge.get(pair, 0) + float(_number(wire.get("qty"), Decimal(1)))
            edge["count"] = max(edge["count"], per_edge[pair])
            name = str(wire.get("art") or wire.get("name") or "")
            if name not in edge["wires"]:
                edge["wires"].append(name)
                edge["lengths"].append(float(_decimal(wire.get("length"))))
    for _ in nodes:
        moved = False
        for edge in edges.values():
            next_depth = by_id[edge["from"]]["depth"] + 1
            if by_id[edge["to"]]["depth"] < next_depth:
                by_id[edge["to"]]["depth"] = next_depth
                moved = True
        if not moved:
            break
    return {
        "nodes": nodes,
        "edges": [{
            "from": edge["from"], "to": edge["to"], "wires": edge["wires"],
            "count": edge["count"],
            "length": edge["lengths"][0] if edge["lengths"] and len(set(edge["lengths"])) == 1 else 0,
        } for edge in edges.values()],
    }


def build_etk(
    config: Mapping[str, Any], cards: Sequence[OperationCardTemplate], *,
    operation_library_commit: str,
    technology_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an immutable document snapshot suitable for ProjectTechcard.rebuild."""
    raw_operations = config.get("ops")
    if not isinstance(raw_operations, list):
        raise ValueError("Нет операций для сборки")
    cards_by_id = {card.source_id: card for card in cards}
    operations = [deepcopy(item) for item in raw_operations if isinstance(item, Mapping)]
    warnings: list[str] = []
    built: list[dict[str, Any]] = []
    previous_result = ""
    previous_norm = ""
    tut_cuts: list[dict[str, Any]] = []

    for index, op in enumerate(operations, start=1):
        card = cards_by_id.get(str(op.get("templateId") or ""))
        code = card.code if card else str(op.get("opNum") or "")
        if card is None:
            missing_card = code or op.get("templateId") or "шаблон не выбран"
            warnings.append(
                f"Операция {index} ({op.get('type') or ''}): нет карточки {missing_card}"
            )
        wires = _wires(op, config)
        wire_data = _combined_wire_data(wires) if op.get("type") in {"cutWire", "cutWireStrip", "cutWireCrimp"} else {}
        result = _result_name(op, config, previous_result)
        if op.get("type") == "cutTut":
            tut_cuts.append({"name": result, "norm": _decimal(op.get("tutCount")) * _part_quantity(config)})
        auxiliary = None
        if op.get("type") in {"insTut", "endCap"}:
            auxiliary = {"name": "; ".join(item["name"] for item in tut_cuts), "norm": _format(sum((item["norm"] for item in tut_cuts), Decimal(0)))}
            tut_cuts = []
        fields = _fields(op, config, wire_data)
        if op.get("type") == "cutWireCrimp":
            fields["cutCrimp"] = True
        terminal_norm = op.get("terminalNorm")
        if isinstance(terminal_norm, Mapping):
            missing_sections = [
                str(item.get("section") or "сечение не определено")
                for item in terminal_norm.get("bySection") or []
                if isinstance(item, Mapping) and not item.get("found")
            ]
            if missing_sections:
                terminal = str(_endpoint(op).get("termArt") or _endpoint(op).get("termName") or "")
                warnings.append(
                    f"Операция {index} ({code}): нет нормы терминала {terminal} "
                    f"для {', '.join(missing_sections)}"
                )
        replacements = _placeholder_map(op, config, previous_result, result, wire_data, fields)
        components = _components(op, config, code)
        semis = [] if code.startswith("CUT_") or not previous_result else [{"name": previous_result, "norm": previous_norm, "unit": "Шт."}]
        if auxiliary and auxiliary["name"]:
            semis.append({**auxiliary, "unit": "Шт."})
        quantity = _part_quantity(config)
        is_last = index == len(operations)
        result_norm = _format(quantity) if is_last or _kind(op) in {"ins", "sdr"} or op.get("type") in {"twist", "heatTut", "insWrap", "insMark", "seal"} else ""
        result_rows = [{"name": str(config.get("assemblyName") or result) if is_last else result, "norm": result_norm, "unit": "Шт."}]
        suffix = _suffix(op)
        built.append({
            "position": index, "code": code, "type": str(op.get("type") or ""),
            "side": str(op.get("side") or ""), "connector": suffix or str(op.get("side") or index),
            "connector_article": str(_endpoint(op).get("connArt") or _endpoint(op).get("connName") or ""),
            "template_id": str(op.get("templateId") or ""), "title": card.title if card else "",
            "card": _resolve_card(card, replacements, fields) if card else None,
            "components": components, "semis": semis, "result": result_rows,
            "result_label": "Изделие" if is_last else "Полуфабрикат",
            "time": _time(op, config, result_rows[0]["name"]),
            "times": {"operation_seconds": str(op.get("tOp") or ""), "setup_seconds": str(op.get("tPrep") or ""), "machine_seconds": str(op.get("tMachine") or "")},
            "fields": fields,
        })
        if op.get("type") not in {"cutTut", "endCap", "foldCore", "stripCable"} and not (op.get("type") == "insTut" and not op.get("harness")):
            previous_result = result
            previous_norm = _format(quantity) if _kind(op) in {"ins", "sdr"} or op.get("side") == "B" or op.get("type") in {"twist", "heatTut", "insWrap", "insMark", "seal"} else ""

    return {
        "schema_version": ETK_VERSION,
        "kit": {
            "project": str(config.get("projectNo") or ""), "item": str(config.get("itemId") or ""),
            "index": str(config.get("assemblyIndex") or ""), "name": str(config.get("assemblyName") or ""),
            "part_quantity": _format(_part_quantity(config)), "mode": str(config.get("mode") or "wire"),
        },
        "operations": built,
        "topology": _topology(config),
        "warnings": warnings,
        "build_config": deepcopy(dict(config)),
        "source": {
            "operation_library_commit": operation_library_commit,
            **({"technology_snapshot": deepcopy(dict(technology_source))} if technology_source else {}),
        },
    }
