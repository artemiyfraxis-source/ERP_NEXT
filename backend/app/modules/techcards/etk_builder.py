"""Build a project ETK from a SUPPLYMAP generator config and operation cards."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable, Mapping, Sequence

from .domain.route import (
    Block, RouteError, RouteOp, chain_route, derive_route, parse_route, route_json, route_totals,
)
from .operation_cards import OperationCardTemplate
from .project_cards import CUSTOM_OP


# 2: result/semi-finished rows with quantities (SUPPLYMAP etkResult_/etkSemis_), combined cards carry the defects of
#    every transition they perform.
# 3: `route` — the block scheme of semi-finished products (domain/route.py); nothing else changed. An older document
#    gets its route derived on read (`document_route`), so a version-2 ETK is not stale: `refresh` rebuilds only
#    documents below ETK_REBUILD_VERSION and the route alone makes no new revision.
ETK_VERSION = 3
ETK_REBUILD_VERSION = 2
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
        return "обжим + лужение" if op.get("tin") else "обжим"
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
        tinned = f"; {TINNED_END}" if op.get("tin") else ""
        return f"{cut}; {_location(op, config)}: обжат терм. {terminal}{tinned}" if terminal else cut
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


CUT_TYPES = {"cutWire", "cutWireStrip", "cutWireCrimp"}
PIECE_TYPES = {"twist", "tin", "heatTut", "insWrap", "insMark", "seal"}
TUT_TYPES = {"cutTut", "insTut", "endCap", "foldCore", "stripCable"}


def _row(name: str, norm: object) -> dict[str, str]:
    return {"name": name, "norm": str(norm or ""), "unit": "Шт."}


def _terminal(op: Mapping[str, Any]) -> str:
    endpoint = _endpoint(op)
    return str(endpoint.get("termArt") or endpoint.get("termName") or "")


# Резка с обжимом и лужением (CUT_WIRE+1crimp+tin): второй конец провода станок лудит сам.
TINNED_END = "другой конец облужен"


def _wire_result_name(wire: Mapping[str, Any], terminal: str, tinned: bool = False) -> str:
    """SUPPLYMAP buildWireResultName_ (without the connector tail, which ETK never passes)."""
    name = f"{_wire_name(wire)}, обжатый терминал {terminal}" if terminal else _wire_name(wire)
    return f"{name}, {TINNED_END}" if terminal and tinned else name


def _double_crimp_rows(op: Mapping[str, Any], config: Mapping[str, Any], wires: Sequence[Mapping[str, Any]]) -> list[str]:
    """SUPPLYMAP doubleCrimpRows_: one row per terminal, 2 (or perTerm) routed wires in it."""
    per = max(2, int(_number(_endpoint(op).get("perTerm"), Decimal(2))))
    label = f"обжим {per} проводов" if per > 2 else "двойной обжим"
    return [
        f"{' + '.join(_wire_name(wire) for wire in wires[start:start + per])}, обж. терм. {_terminal(op)} "
        f"({label}, {_location(op, config)})"
        for start in range(0, len(wires), per)
    ]


def _per_wire(wire: Mapping[str, Any], quantity: Decimal) -> str:
    return _format(_number(wire.get("qty"), Decimal(1)) * quantity)


def _single_name(config: Mapping[str, Any], result: str, is_last: bool) -> str:
    """The name of the one piece an operation hands on: the product itself after the last operation."""
    return str(config.get("assemblyName") or result) if is_last else result


def _result_rows(
    op: Mapping[str, Any], config: Mapping[str, Any], result: str, is_last: bool, single: str | None = None,
) -> list[dict[str, str]]:
    """SUPPLYMAP etkResult_: what the operation hands on and how many pieces per batch. `single` — the name of the
    one-piece row (default: the result, the product after the last operation; «Ручная сборка» passes its block's)."""
    if not result:
        return []
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    wires = _wires(op, config)
    quantity = _part_quantity(config)
    single = _single_name(config, result, is_last) if single is None else single
    build_side = op.get("side") != "B"
    if kind == "prs" and _endpoint(op).get("double") and len(wires) >= 2:
        return [_row(name, _format(quantity)) for name in _double_crimp_rows(op, config, wires)]
    if (kind in {"ins", "sdr"} or (kind == "prs" and not build_side)) and wires:
        return [_row(single, _format(quantity))]
    if (operation_type in CUT_TYPES or (kind == "prs" and build_side)) and wires:
        terminal = _terminal(op) if kind or operation_type == "cutWireCrimp" else ""
        return [_row(_wire_result_name(wire, terminal, bool(op.get("tin"))), _per_wire(wire, quantity)) for wire in wires]
    norm = ""
    if operation_type == "coaxCutTut":
        norm = _format(max(Decimal(1), _decimal(op.get("units"))) * quantity)
    elif operation_type in PIECE_TYPES or operation_type.startswith("coax"):
        norm = _format(quantity)
    if operation_type in TUT_TYPES:
        norm = _format(quantity if operation_type == "insTut" and op.get("harness") else _decimal(op.get("tutCount")) * quantity)
    # The last operation hands over the product itself: never leave its quantity blank.
    return [_row(single, norm or (_format(quantity) if is_last else ""))]


def _semis_rows(
    op: Mapping[str, Any], config: Mapping[str, Any], previous: str, previous_norm: str,
    auxiliary: Mapping[str, Any] | None, tut_on: bool,
) -> list[dict[str, str]]:
    """SUPPLYMAP etkSemis_: the incoming semi-finished product with its quantity per batch."""
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    if (not previous and not kind) or operation_type in {"coaxCut", "coaxCutTut"}:
        return []
    wires = _wires(op, config)
    quantity = _part_quantity(config)
    build_side = op.get("side") != "B"
    branches = list(dict.fromkeys(str(item) for item in op.get("wireSources") or [] if item))
    if kind == "prs" and (len(branches) > 1 or (len(branches) == 1 and not previous)):
        return [_row(f"Заготовка ветви {branch}", _format(quantity)) for branch in branches]
    if kind == "ins" and _endpoint(op).get("double") and len(wires) >= 2:
        return [_row(name, _format(quantity)) for name in _double_crimp_rows(op, config, wires)]
    if build_side and kind == "prs" and wires:
        return [_row(_wire_name(wire), _per_wire(wire, quantity)) for wire in wires]
    if build_side and kind in {"ins", "sdr"} and wires:
        tail = " (ТУТ надет)" if tut_on else ""
        return [_row(_wire_result_name(wire, _terminal(op)) + tail, _per_wire(wire, quantity)) for wire in wires]
    norm = previous_norm
    if not norm and operation_type in {"insTut", "endCap", "foldCore", "stripCable"}:
        norm = _format(sum((_number(wire.get("qty"), Decimal(1)) for wire in wires), Decimal(0)) * quantity)
    rows = [_row(previous, norm)]
    if auxiliary and auxiliary.get("name"):
        rows.append(_row(str(auxiliary["name"]), auxiliary.get("norm")))
    return rows


def _output_norm(op: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """SUPPLYMAP computeOutputNorm_: pieces of the semi-finished product the next operation receives."""
    operation_type = str(op.get("type") or "")
    quantity = _format(_part_quantity(config))
    if operation_type.startswith("coax"):
        return quantity
    kind = _kind(op)
    if kind in {"ins", "sdr"}:
        return quantity if _wires(op, config) else ""
    if kind == "prs":
        return "" if op.get("side") != "B" else quantity
    if operation_type in PIECE_TYPES or (operation_type == "insTut" and op.get("harness")):
        return quantity
    return ""


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
            "name": _wire_result_name(wire, terminal, bool(op.get("tin"))),
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


def _cut_tolerance(op: Mapping[str, Any], wire_data: Mapping[str, Any]) -> str:
    """Допуск длины реза: введённый технологом в операции — как есть; иначе 0,7 % длины (SUPPLYMAP, вверх до мм), но не
    строже ±3 мм до 500 мм длины и ±5 мм длиннее (владелец 04.10: «(+/-)1мм» — перебор, «хотя бы 3–5»)."""
    manual = str(op.get("cutTolerance") or "").strip()
    if manual:
        return manual
    length = _decimal(str(wire_data.get("length") or "").split("\n", 1)[0])
    if not length:
        return ""
    by_length = max(1, int((length * Decimal(7) / 1000).to_integral_value(rounding="ROUND_CEILING")))
    return f"±{max(3 if length <= 500 else 5, by_length)} мм"


def _tin_strip_length(config: Mapping[str, Any], end: str) -> str:
    """Длина зачистки под лужение свободного конца `end` из конфигурации генератора: блок «Лужение», иначе общая
    зачистка этого конца (генератор кладёт туда длину под лужение, когда стороны В нет)."""
    tin = config.get("tin") if isinstance(config.get("tin"), Mapping) else {}
    strip = config.get("strip") if isinstance(config.get("strip"), Mapping) else {}
    return str(tin.get("stripLen") or (strip.get(f"len{end.upper()}") if strip.get(end) else "") or "").strip()


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


def _connection_end(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        pin = int(value.get("pin"))
    except (TypeError, ValueError):
        return None
    end = str(value.get("end") or "").strip()
    if pin <= 0 or not end:
        return None
    return {"end": end, "conn": str(value.get("conn") or ""), "label": str(value.get("label") or ""), "pin": pin}


def _connections(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Wire-by-wire links «end·pin → end·pin» the generator pairs from both ends' pin lists (list order = pairing)."""
    raw = config.get("connections")
    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        start, finish = _connection_end(item.get("from")), _connection_end(item.get("to"))
        if start and finish:
            result.append({"wire": str(item.get("wire") or ""), "from": start, "to": finish})
    return result


def _links(op: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per mounting operation: own pin → the other end of the same wire, for the sketch legend."""
    key = str(op.get("epKey") or "")
    if not key or not _kind(op):
        return []
    links: list[dict[str, Any]] = []
    for item in _connections(config):
        for own, peer, direction in (("from", "to", "to"), ("to", "from", "from")):
            if item[own]["end"] == key:
                links.append({"pin": item[own]["pin"], "wire": item["wire"], "dir": direction, "peer": item[peer]})
    return links


def _fields(op: Mapping[str, Any], config: Mapping[str, Any], wire_data: Mapping[str, Any]) -> dict[str, Any]:
    operation_type = str(op.get("type") or "")
    fields: dict[str, Any] = {}
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        fields["cutTolerance"] = _cut_tolerance(op, wire_data)
        first_length = str(wire_data.get("length") or "").split("\n", 1)[0]
        fields["lengthKd"] = f"{first_length}мм" if first_length else ""
    strip = config.get("strip") if operation_type == "cutWire" else op.get("strip") if operation_type in {"cutWireStrip", "cutWireCrimp"} else None
    if isinstance(strip, Mapping):
        fields["strip"] = {"a": bool(strip.get("a")), "b": bool(strip.get("b")), "lenA": strip.get("lenA") or "", "lenB": strip.get("lenB") or ""}
        if operation_type == "cutWireCrimp" and op.get("tin"):
            # Свободный конец станок зачищает и лудит: без своей длины у операции — длина зачистки под лужение.
            end = "a" if op.get("side") == "B" else "b"
            fields["strip"][end] = True
            fields["strip"][f"len{end.upper()}"] = fields["strip"][f"len{end.upper()}"] or _tin_strip_length(config, end)
    if operation_type == "twist":
        twist = config.get("twist") if isinstance(config.get("twist"), Mapping) else {}
        wires = config.get("wires") or []
        pairs = []
        for index, pair in enumerate(twist.get("pairs") or []):
            names = [str(wires[item].get("art") or wires[item].get("name") or "") for item in pair if 0 <= item < len(wires)]
            if names:
                pairs.append(f"{index + 1}) {' + '.join(names)}")
        fields["twist"] = {"pitch": twist.get("pitch") or "", "pairs": "\n".join(pairs), "count": len(pairs) or 1}
    if operation_type == "coaxStrip":
        # SUPPLYMAP TcBuild.gs etkFields_: размеры разделки стороны (L1–L3, D1–D3 из БД.КОАКС) для эскиза etkDrawCoax_.
        coax = config.get("coax") if isinstance(config.get("coax"), Mapping) else {}
        side = coax.get("sideB" if op.get("side") == "B" else "sideA")
        side = side if isinstance(side, Mapping) else {}
        fields["coax"] = {"cable": coax.get("cableArt") or "", "connector": side.get("article") or "", **{
            key: side.get(key) or "" for key in ("d1", "d2", "d3", "l1", "l2", "l3", "lPlus", "lMinus")}}
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
    links = _links(op, config)
    if links:
        fields["links"] = links
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


# Combined operation cards («резка + опрессовка», «резка с разделкой + маркировка», «резка коаксиала + разделка») list
# only the cutting defects, while the operation also crimps / marks / strips. Their ETK adds the defects of the card
# that performs that transition on its own, as labelled groups.
COMBINED_DEFECTS = (
    (re.compile(r"\+\d*crimp", re.IGNORECASE), "PRS_TERMINAL_auto", "Опрессовка"),
    (re.compile(r"\+tin\b", re.IGNORECASE), "SDR_tin", "Лужение"),
    (re.compile(r"\+\d*mark", re.IGNORECASE), "INS_MARK", "Маркировка"),
    (re.compile(r"^CUT_COAX\+\d*strip", re.IGNORECASE), "STRIP_COAX_auto", "Разделка коаксиала"),
)
_DEFECT_INTRO = re.compile(r"^(типовые дефекты|в соответствии|согласно)", re.IGNORECASE)
_DEFECT_GROUP = re.compile(r"^\d+[.)]\s*([^:;]{2,120}?)\s*:\s*$")


def _labelled_defects(text: str, label: str) -> str:
    """The component card's defects without its intro, every group titled «<label> — …»."""
    lines: list[str] = []
    for source in text.replace("\r", "").split("\n"):
        line = source.strip()
        if not line:
            continue
        if _DEFECT_INTRO.match(line):
            head, colon, rest = line.partition(":")
            # «ТИПОВЫЕ ДЕФЕКТЫ (NG): a; b» carries the list after the colon; «… считаются:» is only a lead-in.
            if colon and rest.strip() and "(" in head:
                lines.append(rest.strip())
            continue
        group = _DEFECT_GROUP.match(line)
        if group:
            title = group.group(1)
            lines.append(f"{label} — {title[:1].lower()}{title[1:]}:")
            continue
        lines.append(line)
    if not lines:
        return ""
    if not (lines[0].startswith(f"{label} — ") and lines[0].endswith(":")):
        lines.insert(0, f"{label}:")
    return "\n".join(lines)


def _combined_defects(code: str, defects: str, cards_by_code: Mapping[str, OperationCardTemplate]) -> str:
    parts = [defects.rstrip()] if defects.strip() else []
    for pattern, component, label in COMBINED_DEFECTS:
        card = cards_by_code.get(component)
        if not pattern.search(code) or card is None or not card.defects.strip():
            continue
        extra = _labelled_defects(card.defects, label)
        if extra:
            parts.append(extra)
    return "\n".join(parts)


# Мерительный инструмент, которым шаг карты велит мерить, а в перечне инструмента карты его нет (шаблоны резки
# SUPPLYMAP: «…длину зачистки штангенциркулем», у CUT_WIRE+1crimp ещё микрометр и разрывная машина) — дописывается.
STEP_TOOLS = (
    (re.compile(r"штангенциркул", re.IGNORECASE), "Штангенциркуль ШЦ-I (ГОСТ 166-89)", ""),
    (re.compile(r"микрометр", re.IGNORECASE), "EW-2550 | Прибор для измерения высоты опрессовки (микрометр)", "ОБ.034.2"),
    (re.compile(r"разрывн", re.IGNORECASE), "EW-2510 | Машина испытательная разрывная", "ОБ.033.2"),
)


def _machine_applicator(value: str) -> str:
    """Станку резки с обжимом нужен аппликатор автомата (A…) из списка БД.ТЕР «S9/M1/A19»: он и в поле операции, и в
    программе станка. Нет такого — список как есть."""
    automatic = [token.strip() for token in re.split(r"[/,;]", value) if re.fullmatch(r"[AА]\d+", token.strip(), re.IGNORECASE)]
    return "/".join(automatic) or value


def _resolve_card(
    card: OperationCardTemplate, replacements: Mapping[str, str], fields: Mapping[str, Any],
    cards_by_code: Mapping[str, OperationCardTemplate] | None = None,
) -> dict[str, Any]:
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
    step_text = " ".join(step["text"] for step in steps)
    for pattern, name, inventory in STEP_TOOLS:
        if pattern.search(step_text) and not any(pattern.search(tool["name"]) for tool in tooling):
            number = sum(1 for tool in tooling if tool["section"] == "Инструмент") + 1
            tooling.append({"section": "Инструмент", "number": str(number), "name": name, "inventory_number": inventory,
                            "program": "", "parameters": "", "norm": "", "unit": ""})
    defects = _combined_defects(card.code, card.defects, cards_by_code or {})
    return {"name": card.name, "safety": card.safety, "defects": defects, "steps": steps, "tooling": tooling}


def _wire_data(op: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The cut's wires as one placeholder set (several wires joined line by line); other operations have none."""
    return _combined_wire_data(_wires(op, config)) if op.get("type") in CUT_TYPES else {}


def _typed_fields(op: Mapping[str, Any], config: Mapping[str, Any], wire_data: Mapping[str, Any]) -> dict[str, Any]:
    fields = _fields(op, config, wire_data)
    if op.get("type") == "cutWireCrimp":
        fields["cutCrimp"] = True
        if fields.get("applicator"):
            fields["applicator"] = _machine_applicator(str(fields["applicator"]))
    return fields


def _card_warning(op: Mapping[str, Any], position: int, code: str) -> str:
    missing_card = code or op.get("templateId") or "шаблон не выбран"
    return f"Операция {position} ({op.get('type') or ''}): нет карточки {missing_card}"


def _terminal_warning(op: Mapping[str, Any], position: int, code: str) -> str | None:
    terminal_norm = op.get("terminalNorm")
    if not isinstance(terminal_norm, Mapping):
        return None
    missing_sections = [
        str(item.get("section") or "сечение не определено")
        for item in terminal_norm.get("bySection") or []
        if isinstance(item, Mapping) and not item.get("found")
    ]
    if not missing_sections:
        return None
    terminal = str(_endpoint(op).get("termArt") or _endpoint(op).get("termName") or "")
    return f"Операция {position} ({code}): нет нормы терминала {terminal} для {', '.join(missing_sections)}"


def _typed_connector(op: Mapping[str, Any], fallback: object) -> str:
    """The connector part of the ETK operation key: the sheet suffix, else the side, else `fallback`."""
    return _suffix(op) or str(op.get("side") or fallback)


def _typed_operation(
    op: Mapping[str, Any], config: Mapping[str, Any], *, position: int, code: str,
    card: OperationCardTemplate | None, cards_by_code: Mapping[str, OperationCardTemplate],
    previous: str, result: str, single: str, is_last: bool, semis: list[dict[str, str]], connector: str,
    warnings: list[str],
) -> dict[str, Any]:
    """One ETK operation from a generator operation (cut, crimp, mount, TUT, coax…): its card with the placeholders
    filled, kit lines, result rows, time and the fields of its sketch. `previous` / `result` — the product names of the
    generator chain (card placeholders), `single` — the name of the one piece it hands on, `semis` — its input."""
    wire_data = _wire_data(op, config)
    fields = _typed_fields(op, config, wire_data)
    terminal_warning = _terminal_warning(op, position, code)
    if terminal_warning:
        warnings.append(terminal_warning)
    replacements = _placeholder_map(op, config, previous, result, wire_data, fields)
    return {
        "position": position, "code": code, "type": str(op.get("type") or ""),
        "side": str(op.get("side") or ""), "connector": connector,
        "connector_article": str(_endpoint(op).get("connArt") or _endpoint(op).get("connName") or ""),
        "template_id": str(op.get("templateId") or ""), "title": card.title if card else "",
        "card": _resolve_card(card, replacements, fields, cards_by_code) if card else None,
        "components": _components(op, config, code), "semis": semis,
        "result": _result_rows(op, config, result, is_last, single),
        "result_label": "Изделие" if is_last else "Полуфабрикат",
        "time": _time(op, config, single),
        "times": _times(op),
        "fields": fields,
    }


def _times(op: Mapping[str, Any]) -> dict[str, str]:
    return {"operation_seconds": str(op.get("tOp") or ""), "setup_seconds": str(op.get("tPrep") or ""),
            "machine_seconds": str(op.get("tMachine") or "")}


def _topology(config: Mapping[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    splice = next((item for item in config.get("ops") or [] if item.get("type") == "splice"), None)
    # wireSources name the source connector by its BOM name (connName), nodes are keyed by article: map both.
    aliases: dict[str, str] = {}

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
        for alias in (endpoint.get("connName"), endpoint.get("connArt")):
            if alias:
                aliases.setdefault(str(alias), identifier)
        per_edge: dict[tuple[str, str], float] = {}
        sources = op.get("wireSources") if isinstance(op.get("wireSources"), list) else []
        for index, wire in enumerate(_wires(op, config)):
            source = str(sources[index] or "") if index < len(sources) else ""
            source = aliases.get(source, source)
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


def _advances_chain(op: Mapping[str, Any]) -> bool:
    """Whether the operation's result becomes the semi-finished product the next operation takes. TUT pieces and the
    side work on them (cut / put on wires / end caps / folding / stripping) do not move the product name on."""
    operation_type = op.get("type")
    return operation_type not in {"cutTut", "endCap", "foldCore", "stripCable"} and not (
        operation_type == "insTut" and not op.get("harness")
    )


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _chain_results(config: Mapping[str, Any]) -> list[str]:
    """The product name every generator operation hands on, replaying build_etk's previous_result chain."""
    previous = ""
    names: list[str] = []
    for op in _list(config.get("ops")):
        if not isinstance(op, Mapping):
            continue
        result = _result_name(op, config, previous)
        names.append(result)
        if _advances_chain(op):
            previous = result
    return names


def _row_minutes(operation: Mapping[str, Any]) -> Decimal | None:
    """Σ of the operation's ETK time rows, minutes per batch; None — it has no time rows."""
    rows = [row for row in _list(operation.get("time")) if isinstance(row, Mapping)]
    return sum((_decimal(row.get("norm")) for row in rows), Decimal(0)) if rows else None


def _operation_minutes(operation: Mapping[str, Any], quantity: Decimal) -> Decimal:
    """The ETK time norm of the operation, minutes per batch; an operation without time rows (moved from SUPPLYMAP)
    counts its seconds per unit × batch + setup."""
    minutes = _row_minutes(operation)
    if minutes is not None:
        return minutes
    times = operation.get("times") if isinstance(operation.get("times"), Mapping) else {}
    return (_decimal(times.get("operation_seconds")) * quantity + _decimal(times.get("setup_seconds"))) / 60


def _document_batch(document: Mapping[str, Any]) -> Decimal:
    """The batch the ETK's time rows are counted for: the config's partQty, else the kit's part_quantity, else 1."""
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    kit = document.get("kit") if isinstance(document.get("kit"), Mapping) else {}
    quantity = _decimal(config.get("partQty")) or _decimal(kit.get("part_quantity"))
    return quantity if quantity > 0 else Decimal(1)


def operation_norms(document: Mapping[str, Any] | None) -> tuple[Decimal, tuple[Decimal | None, ...]]:
    """The ETK's own time norms — what production plans by: the batch the ETK is counted for and, for every operation
    of the document in order, its norm in minutes per batch — Σ of its time rows, as the ETK shows them and the block
    scheme sums them. None — the operation has no time rows (moved from SUPPLYMAP, made by hand): its card's seconds
    per unit and setup are all there is."""
    if not isinstance(document, Mapping):
        return Decimal(1), ()
    operations = [item for item in _list(document.get("operations")) if isinstance(item, Mapping)]
    return _document_batch(document), tuple(_row_minutes(operation) for operation in operations)


def _route_ops(document: Mapping[str, Any], *, strict: bool) -> tuple[list[RouteOp], str, Decimal]:
    """The operations as the block scheme sees them (position = 1-based place in `operations`), the final product's
    name and the batch. Not strict (a stored document on read): a config the names cannot be replayed from gives
    the names of the result rows instead of failing the read."""
    operations = [item for item in _list(document.get("operations")) if isinstance(item, Mapping)]
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    kit = document.get("kit") if isinstance(document.get("kit"), Mapping) else {}
    config_ops = [item for item in _list(config.get("ops")) if isinstance(item, Mapping)]
    aligned = len(config_ops) == len(operations)
    try:
        results = _chain_results(config) if aligned else []
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        if strict:
            raise
        results = []
    quantity = _document_batch(document)
    ops: list[RouteOp] = []
    for position, operation in enumerate(operations, start=1):
        source = config_ops[position - 1] if aligned else operation
        rows = "; ".join(dict.fromkeys(
            str(row["name"]) for row in _list(operation.get("result")) if isinstance(row, Mapping) and row.get("name")
        ))
        name = (results[position - 1] if results else "") or rows or str(operation.get("title") or operation.get("code") or "")
        ops.append(RouteOp(
            position=position, type=str(source.get("type") or operation.get("type") or ""),
            code=str(operation.get("code") or ""), name=name or f"Операция {position}",
            minutes=_operation_minutes(operation, quantity),
        ))
    return ops, str(config.get("assemblyName") or kit.get("name") or ""), quantity


def _route(document: Mapping[str, Any], *, strict: bool) -> dict[str, Any] | None:
    """`document.route` of an assembled document. strict (the build): a hand-edited `build_config.route` must be
    valid — an error stops the build. Otherwise (the read path) the first scheme that fits wins, then the derived one."""
    ops, final_name, quantity = _route_ops(document, strict=strict)
    if not ops:
        return None
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    candidates = [(config.get("route"), True)] if strict else [
        (document.get("route"), isinstance(document.get("route"), Mapping) and document["route"].get("source") != "derived"),
        (config.get("route"), True),
    ]
    route = None
    for raw, manual in candidates:
        if not raw:
            continue
        try:
            route = parse_route(raw, ops, final_name, manual=manual)
            break
        except RouteError:
            if strict:
                raise
    route = route or derive_route(ops, final_name)
    return route_json(route, route_totals(route, {op.position: op.minutes for op in ops}, quantity))


# ── Ручная сборка (владелец 04.10): кастомные и нетиповые провода, под которые типовые генераторы не подходят.
# Технолог сам набирает операции — карточки из библиотеки (libraryOp) и свои (customOp: название, норма, ход операции,
# контроль с допуском, оснастка). Схема полуфабрикатов здесь всегда ручная: `build_config.route`, без неё — цепочка
# по списку; производство идёт по ней. Режимы типовых генераторов этот путь не трогает (их ЭТК прежние байт в байт).
MANUAL_MODE = "manual"
LIBRARY_OP = "libraryOp"
TOOL_SECTIONS = ("Инструмент", "Оборудование", "Расходники")
# Операции типового генератора (05.10: генератор и ручная сборка — одна модель, блоки с операциями). Такая операция
# в ручной сборке идёт со своими полями как есть (провода, длины, терминалы, нормы) и собирается тем же типовым кодом,
# что и в генераторе, — без потерь при переводе в libraryOp.
GENERATOR_TYPES = frozenset({
    "cutWire", "cutWireStrip", "cutWireCrimp", "cutTut", "insTut", "heatTut", "endCap", "foldCore", "stripCable",
    "twist", "tin", "insMark", "seal", "insWrap", "prsTerm", "insTerm", "solderConn", "splice", "insHousing",
    "coaxCut", "coaxCutTut", "coaxStrip", "coaxPin", "coaxShield", "coaxSolderShield", "coaxInsSleeve", "coaxInsTut",
    "coaxHeat", "coaxHousing", "coaxCapCrimp", "coaxCapScrew",
})


def _manual_error(message: str) -> ValueError:
    return ValueError(f"Ручная сборка: {message}")


def _manual_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _manual_amount(value: object, default: Decimal) -> Decimal | None:
    """A number the technologist typed: empty → default, else a finite decimal (comma allowed); None — not a number."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


@dataclass(frozen=True)
class _ManualOp:
    position: int
    source: Mapping[str, Any]
    kind: str
    label: str
    code: str
    card: OperationCardTemplate | None
    quantity: Decimal           # pieces of work per one item
    operation_seconds: Decimal  # per piece
    setup_seconds: Decimal      # per batch
    product: str                # what it hands on, unless it closes a block (then the block's name)
    generator: bool = False     # a typed generator operation (GENERATOR_TYPES)
    previous: str = ""          # generator chain: the product it takes (card placeholders, its result name)


def _manual_ops(
    config: Mapping[str, Any], cards_by_id: Mapping[str, OperationCardTemplate], warnings: list[str],
) -> list[_ManualOp]:
    raw = config.get("ops")
    if not isinstance(raw, list) or not raw:
        raise _manual_error("добавьте хотя бы одну операцию — из библиотеки или свою")
    result: list[_ManualOp] = []
    previous = ""  # the generator chain over the list: a generator operation names its product after the one before
    for position, op in enumerate(raw, start=1):
        if not isinstance(op, Mapping):
            raise _manual_error(f"операция {position} — непонятная запись")
        kind = _manual_text(op.get("type"))
        card = None
        generator = kind in GENERATOR_TYPES
        if generator:
            template = _manual_text(op.get("templateId"))
            card = cards_by_id.get(template)
            code = card.code if card else _manual_text(op.get("opNum"))
            label = _manual_text(op.get("label")) or (card.name if card else "") or code or kind
            if card is None:
                warnings.append(_card_warning(op, position, code))
        elif kind == LIBRARY_OP:
            template = _manual_text(op.get("templateId"))
            if not template:
                raise _manual_error(f"операция {position}: не выбрана карточка из библиотеки")
            card = cards_by_id.get(template)
            label = _manual_text(op.get("label")) or (card.name if card else "") or f"Операция {position}"
            code = card.code if card else _manual_text(op.get("opNum"))
            if card is None:
                warnings.append(f"Операция {position} ({label}): нет карточки {code or template} в библиотеке")
        elif kind == CUSTOM_OP:
            label, code = _manual_text(op.get("label")), ""
            if not label:
                raise _manual_error(f"операция {position}: укажите название своей операции")
        else:
            raise _manual_error(
                f"операция {position}: непонятный вид операции — выберите карточку из библиотеки или «Своя операция»"
            )
        named = f"операция {position} «{label}»"
        # A generator operation counts its own pieces (wires, terminals, TUT segments): no «на изделие» multiplier.
        quantity = Decimal(1) if generator else _manual_amount(op.get("qty"), Decimal(1))
        if quantity is None or quantity <= 0:
            raise _manual_error(f"{named}: количество на изделие должно быть числом больше нуля")
        setup = _manual_amount(op.get("tPrep"), Decimal(0))
        if setup is None or setup < 0:
            raise _manual_error(f"{named}: время подготовки должно быть числом не меньше нуля")
        seconds = _manual_amount(op.get("tOp"), Decimal(0))
        if seconds is None or seconds < 0:
            raise _manual_error(f"{named}: время на штуку должно быть числом не меньше нуля")
        if generator:
            chain_previous = previous
            product = _manual_text(op.get("result")) or _result_name(op, config, previous)
            if _advances_chain(op):
                previous = product
        else:
            chain_previous = ""
            product = previous = _manual_text(op.get("result")) or f"Полуфабрикат после «{label}»"
        result.append(_ManualOp(position, op, kind, label, code, card, quantity, seconds, setup, product,
                                generator, chain_previous))
    return result


def _manual_list(op: _ManualOp, key: str, what: str) -> list[Any]:
    value = op.source.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _manual_error(f"операция {op.position} «{op.label}»: {what} должны быть списком")
    return value


def _manual_steps(op: _ManualOp) -> list[dict[str, str]]:
    """Steps of an own operation, numbered; a «Контроль» step keeps its допуск (shown highlighted in the ETK)."""
    steps: list[dict[str, str]] = []
    for item in _manual_list(op, "steps", "шаги хода операции"):
        if isinstance(item, Mapping):
            text, kind, tolerance = (_manual_text(item.get(key)) for key in ("text", "kind", "tolerance"))
        elif isinstance(item, str):
            text, kind, tolerance = item.strip(), "", ""
        else:
            raise _manual_error(f"операция {op.position} «{op.label}»: непонятный шаг «{item}»")
        if text:
            control = "контрол" in kind.casefold()
            steps.append({
                "number": str(len(steps) + 1), "kind": "Контроль" if control else "Операция",
                "text": text, "tolerance": "" if tolerance in {"-", "—"} else tolerance,
            })
    return steps


def _manual_tooling(op: _ManualOp) -> list[dict[str, str]]:
    """Tooling of an own operation by section (Инструмент · Оборудование · Расходники), numbered within a section."""
    tooling: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for item in _manual_list(op, "tooling", "строки оснастки"):
        if isinstance(item, Mapping):
            section, name = _manual_text(item.get("section")), _manual_text(item.get("name"))
        elif isinstance(item, str):
            section, name = "", item.strip()
        else:
            raise _manual_error(f"операция {op.position} «{op.label}»: непонятная строка оснастки «{item}»")
        if not name:
            continue
        section = next((known for known in TOOL_SECTIONS if known.casefold() == section.casefold()), TOOL_SECTIONS[0])
        counts[section] = counts.get(section, 0) + 1
        tooling.append({
            "section": section, "number": str(counts[section]), "name": name, "inventory_number": "",
            "program": "", "parameters": "", "norm": "", "unit": "",
        })
    return tooling


def _manual_components(op: _ManualOp, quantity: Decimal) -> list[dict[str, str]]:
    """Kit lines the technologist put on the operation, per item → per batch (ETK «Вход»)."""
    rows: list[dict[str, str]] = []
    for item in _manual_list(op, "components", "комплектующие"):
        if not isinstance(item, Mapping):
            raise _manual_error(f"операция {op.position} «{op.label}»: комплектующие — строки состава КБ")
        art, name = _manual_text(item.get("art")), _manual_text(item.get("name"))
        if not art and not name:
            continue
        per_item = _manual_amount(item.get("qty"), Decimal(1))
        if per_item is None or per_item <= 0:
            raise _manual_error(
                f"операция {op.position} «{op.label}»: количество «{art or name}» должно быть числом больше нуля"
            )
        rows.append({"art": art or name, "name": name, "norm": _format(per_item * quantity),
                     "unit": _manual_text(item.get("unit")) or "Шт."})
    return rows


def _manual_generator_operation(
    item: _ManualOp, config: Mapping[str, Any], cards_by_code: Mapping[str, OperationCardTemplate], *,
    semis: list[dict[str, str]], single: str, is_last: bool, seq: str, warnings: list[str],
) -> dict[str, Any]:
    """A generator operation inside «Ручная сборка»: the typed build of the generator (card, kit lines, result rows,
    time, fields) with the input from the scheme and the product named by its block. Its own name, kit lines,
    parameters and note — if the technologist added them — go on top, as for a library operation."""
    operation = _typed_operation(
        item.source, config, position=item.position, code=item.code, card=item.card, cards_by_code=cards_by_code,
        previous=item.previous, result=item.product, single=single, is_last=is_last, semis=semis,
        connector=_typed_connector(item.source, seq), warnings=warnings,
    )
    if operation["card"] is not None and _manual_text(item.source.get("label")):
        operation["card"]["name"] = item.label
    operation["components"].extend(_manual_components(item, _part_quantity(config)))
    for key in ("params", "note"):
        if _manual_text(item.source.get(key)):
            operation["fields"][key] = _manual_text(item.source.get(key))
    return operation


def _build_manual(
    config: Mapping[str, Any], cards: Sequence[OperationCardTemplate], *,
    operation_library_commit: str, technology_source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    cards_by_id = {card.source_id: card for card in cards}
    cards_by_code: dict[str, OperationCardTemplate] = {}
    for item in cards:
        cards_by_code.setdefault(item.code, item)
    warnings: list[str] = []
    items = _manual_ops(config, cards_by_id, warnings)
    quantity = _part_quantity(config)
    final_name = str(config.get("assemblyName") or "")
    route_ops = [RouteOp(position=item.position, type=item.kind, code=item.code, name=item.product) for item in items]
    raw_route = config.get("route")
    route = parse_route(raw_route, route_ops, final_name, manual=True) if raw_route else chain_route(route_ops, final_name)

    # The ETK key of an operation (code::connector::side) carries its `seq` — the number the generator gave it when it
    # was added and keeps through reorders — so a hand-edited ETK operation stays with its own operation on rebuild.
    # A numeric connector is not shown in the ETK title. No usable seq — the position.
    seqs = [str(item.source.get("seq") or "").strip() for item in items]
    if not all(re.fullmatch(r"[1-9]\d{0,5}", seq) for seq in seqs) or len(set(seqs)) != len(seqs):
        seqs = [str(item.position) for item in items]
    owner: dict[int, Block] = {position: block for block in route.blocks for position in block.ops}
    by_key = {block.key: block for block in route.blocks}
    product = {item.position: owner[item.position].name if item.position == max(owner[item.position].ops) else item.product
               for item in items}
    built: list[dict[str, Any]] = []
    for item in items:
        block = owner[item.position]
        order = sorted(block.ops)
        place = order.index(item.position)
        if place:
            semis = [_row(product[order[place - 1]], _format(quantity))]
        else:
            semis = [_row(by_key[edge.source].name, _format(edge.per_unit * quantity))
                     for edge in route.edges if edge.target == block.key]
        result = product[item.position]
        if item.generator:
            built.append(_manual_generator_operation(
                item, config, cards_by_code, semis=semis, single=result,
                is_last=block.final and item.position == order[-1], seq=seqs[item.position - 1], warnings=warnings,
            ))
            continue
        norm = (item.operation_seconds * item.quantity * quantity + item.setup_seconds) / 60
        if not norm:
            warnings.append(f"Операция {item.position} ({item.label}): не задана норма времени")
        if item.card is not None:
            replacements = _placeholder_map(item.source, config, "; ".join(row["name"] for row in semis), result, {}, {})
            card = _resolve_card(item.card, replacements, {}, cards_by_code)
            card["name"] = item.label
        elif item.kind == CUSTOM_OP:
            card = {"name": item.label, "safety": "", "defects": "",
                    "steps": _manual_steps(item), "tooling": _manual_tooling(item)}
            if not card["steps"]:
                warnings.append(f"Операция {item.position} ({item.label}): не заполнен ход операции")
        else:
            card = None
        fields: dict[str, Any] = {}
        if item.quantity != 1:
            fields["perItem"] = _format(item.quantity)
        for key in ("params", "note"):
            if _manual_text(item.source.get(key)):
                fields[key] = _manual_text(item.source.get(key))
        built.append({
            "position": item.position, "code": item.code, "type": item.kind,
            "side": _manual_text(item.source.get("side")), "connector": seqs[item.position - 1], "connector_article": "",
            "template_id": _manual_text(item.source.get("templateId")),
            "title": item.card.title if item.card else item.label, "card": card,
            "components": _manual_components(item, quantity), "semis": semis, "result": [_row(result, _format(quantity))],
            "result_label": "Изделие" if block.final and item.position == order[-1] else "Полуфабрикат",
            "time": [{"name": result, "norm": _format(norm, 2)}],
            "times": {"operation_seconds": str(item.source.get("tOp") or ""), "setup_seconds": str(item.source.get("tPrep") or ""),
                      "machine_seconds": str(item.source.get("tMachine") or "")},
            "fields": fields,
        })

    connections = _connections(config)
    document = {
        "schema_version": ETK_VERSION,
        "kit": {
            "project": str(config.get("projectNo") or ""), "item": str(config.get("itemId") or ""),
            "index": str(config.get("assemblyIndex") or ""), "name": final_name,
            "part_quantity": _format(quantity), "mode": MANUAL_MODE,
        },
        "operations": built,
        "topology": _topology(config),
        **({"connections": connections} if connections else {}),
        "warnings": warnings,
        "build_config": deepcopy(dict(config)),
        "source": {
            "operation_library_commit": operation_library_commit,
            **({"technology_snapshot": deepcopy(dict(technology_source))} if technology_source else {}),
        },
    }
    # Totals from the ETK time rows, as the read path (`document_route`) counts them.
    minutes = {operation["position"]: _operation_minutes(operation, quantity) for operation in built}
    document["route"] = route_json(route, route_totals(route, minutes, quantity))
    return document


def build_etk(
    config: Mapping[str, Any], cards: Sequence[OperationCardTemplate], *,
    operation_library_commit: str,
    technology_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an immutable document snapshot suitable for ProjectTechcard.rebuild."""
    if config.get("mode") == MANUAL_MODE:
        return _build_manual(config, cards, operation_library_commit=operation_library_commit,
                             technology_source=technology_source)
    raw_operations = config.get("ops")
    if not isinstance(raw_operations, list):
        raise ValueError("Нет операций для сборки")
    cards_by_id = {card.source_id: card for card in cards}
    cards_by_code: dict[str, OperationCardTemplate] = {}
    for item in cards:
        cards_by_code.setdefault(item.code, item)
    operations = [deepcopy(item) for item in raw_operations if isinstance(item, Mapping)]
    warnings: list[str] = []
    built: list[dict[str, Any]] = []
    previous_result = ""
    previous_norm = ""
    tut_cuts: list[dict[str, Any]] = []
    tut_on_wires = False

    for index, op in enumerate(operations, start=1):
        card = cards_by_id.get(str(op.get("templateId") or ""))
        code = card.code if card else str(op.get("opNum") or "")
        if card is None:
            warnings.append(_card_warning(op, index, code))
        result = _result_name(op, config, previous_result)
        if op.get("type") == "cutTut":
            tut_cuts.append({"name": result, "norm": _decimal(op.get("tutCount")) * _part_quantity(config)})
        auxiliary = None
        if op.get("type") in {"insTut", "endCap"}:
            auxiliary = {"name": "; ".join(item["name"] for item in tut_cuts), "norm": _format(sum((item["norm"] for item in tut_cuts), Decimal(0)))}
            tut_cuts = []
        tut_on = False
        if op.get("type") == "insTut" and not op.get("harness"):
            tut_on_wires = True
        elif tut_on_wires and _kind(op):
            tut_on, tut_on_wires = True, False
        semis = [] if code.startswith("CUT_") else _semis_rows(op, config, previous_result, previous_norm, auxiliary, tut_on)
        is_last = index == len(operations)
        built.append(_typed_operation(
            op, config, position=index, code=code, card=card, cards_by_code=cards_by_code,
            previous=previous_result, result=result, single=_single_name(config, result, is_last), is_last=is_last,
            semis=semis, connector=_typed_connector(op, index), warnings=warnings,
        ))
        if _advances_chain(op):
            previous_result = result
            previous_norm = _output_norm(op, config)

    connections = _connections(config)
    document = {
        "schema_version": ETK_VERSION,
        "kit": {
            "project": str(config.get("projectNo") or ""), "item": str(config.get("itemId") or ""),
            "index": str(config.get("assemblyIndex") or ""), "name": str(config.get("assemblyName") or ""),
            "part_quantity": _format(_part_quantity(config)), "mode": str(config.get("mode") or "wire"),
        },
        "operations": built,
        "topology": _topology(config),
        **({"connections": connections} if connections else {}),
        "warnings": warnings,
        "build_config": deepcopy(dict(config)),
        "source": {
            "operation_library_commit": operation_library_commit,
            **({"technology_snapshot": deepcopy(dict(technology_source))} if technology_source else {}),
        },
    }
    document["route"] = _route(document, strict=True)
    return document


def document_route(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The block scheme of any stored ETK, for the read path: its own `route`, else the generator's hand-edited
    `build_config.route`, else derived from the operations (documents before version 3, SUPPLYMAP imports). A stored
    scheme that no longer fits the operations gives way to the next one; totals always come from the operations as
    they are now. None — the document has no operations."""
    return _route(document, strict=False) if isinstance(document, Mapping) else None


def attach_route(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A copy of the document with `route` from `document_route` — how the API returns a card."""
    if document is None:
        return None
    result = dict(document)
    route = document_route(document)
    if route is not None:
        result["route"] = route
    return result
