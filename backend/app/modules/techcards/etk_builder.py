"""Build a project ETK from a generator config and operation cards."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable, Mapping, Sequence

from dataclasses import replace as _replace

from .domain.pf_split import generator_split, same_split
from .domain.route import (
    Block, Route, RouteError, RouteOp, chain_route, derive_route, parse_route, route_json, route_levels, route_totals,
)
from .etk_marking import apply_marking, mark_length
from .operation_cards import OperationCardTemplate
from .project_cards import CUSTOM_OP


# 2: result/semi-finished rows with quantities (SUPPLYMAP etkResult_/etkSemis_), combined cards carry the defects of
#    every transition they perform.
# 3: `route` — the block scheme of semi-finished products (domain/route.py); nothing else changed. An older document
#    gets its route derived on read (`document_route`), so a version-2 ETK is not stale: `refresh` rebuilds only
#    documents below ETK_REBUILD_VERSION and the route alone makes no new revision.
# 4: time norms without the card's setup (tPrep): the operator starts a task only once everything is ready, so planning
#    counts no preparation (owner 18.10). tPrep stays on the operation as information. Older documents keep their
#    norms; production takes the setup out of them (`norms_include_setup`).
ETK_VERSION = 4
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


def _endpoint_b(op: Mapping[str, Any]) -> dict[str, Any]:
    """Side В of a cut that crimps both ends on the machine (`epB`, generator «Обе стороны»); {} for any other op."""
    value = op.get("epB") if op.get("type") == "cutWireCrimp" else None
    return dict(value) if isinstance(value, Mapping) else {}


def _side_b_op(op: Mapping[str, Any]) -> dict[str, Any]:
    """The side-В view of a both-sides cut: the same operation with `ep` = `epB`, for location and placeholders."""
    return {**op, "side": "B", "ep": _endpoint_b(op)}


def _side_label(op: Mapping[str, Any]) -> str:
    return "В" if op.get("side") == "B" else "А"


def _wires_text(count: int) -> str:
    """«1 провод», «2 провода», «5 проводов»: the full word, Russian plural."""
    tail = count % 100
    if 10 < tail < 20 or count % 10 >= 5 or count % 10 == 0:
        word = "проводов"
    elif count % 10 == 1:
        word = "провод"
    else:
        word = "провода"
    return f"{count} {word}"


def _location(op: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    endpoint = _endpoint(op)
    label = str(endpoint.get("label") or endpoint.get("connName") or "")
    return label if config.get("bigAssembly") and label else f"сторона {_side_label(op)}"


def _term_count(op: Mapping[str, Any], config: Mapping[str, Any]) -> int:
    count = sum(
        int(_number(wire.get("qty"), Decimal(1)) * _number(wire.get("cores"), Decimal(1)))
        for wire in _wires(op, config)
    )
    endpoint = _endpoint(op)
    if endpoint.get("double"):
        per_terminal = max(2, int(_number(endpoint.get("perTerm"), Decimal(2))))
        return (count + per_terminal - 1) // per_terminal
    # Двойной обжим в отдельных контактах (генератор, `doublePins`): два провода в контакте — один терминал на двоих.
    doubled = {pin for pin in endpoint.get("doublePins") or [] if isinstance(pin, int) and not isinstance(pin, bool) and pin > 0}
    return max(0, count - len(doubled))


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


# Tubes put on before the crimp (generator 15.10): the sleeve over a connector (cutShr<n> / heatShr<side><ord>) and
# the tube on the jacket cut (cutJs<n> / heatJs<side>[n]). Their own words in the ETK title instead of «на пайке», and
# the shrinks of two sides or two connectors no longer share one ETK key (it refused the build: «уникальные код и
# сторону»).
_SLEEVE_KEY = re.compile(r"^(cutShr|cutJs|heatShr|heatJs)([AB]?)(\d*)$")


def _sleeve_suffix(op: Mapping[str, Any]) -> str:
    match = _SLEEVE_KEY.match(str(op.get("opKey") or ""))
    if not match:
        return ""
    kind, side, number = match.groups()
    tube = str(op.get("tutArt") or "ТУТ")
    letter = {"A": "А", "B": "В"}.get(side, "")
    if kind == "cutShr":
        return f"{tube} поверх разъёма"
    if kind == "cutJs":
        return f"{tube} на место разделки"
    if kind == "heatShr":
        return f"поверх разъёма {letter}{number}".rstrip()
    return f"на месте разделки {letter}{f' ({number})' if number else ''}".rstrip()


# Free tails of a complex harness (generator «Сложная сборка», 16.10): their own stripping and tinning, next to the
# stripping under the splices and the tinning of the main ends — the ETK title says whose ends they are.
TAIL_KEYS = frozenset({"stripTail", "tinTail"})
TAIL_SUFFIX = "свободные концы"

# How the splices are joined (generator «Сложная сборка», owner 16.10: «основа это обжим»): crimp with a sleeve, solder
# or band crimp. A config without `method` (before 16.10) builds as it did — no words added, no parts.
SPLICE_DONE = {
    "solder": "провода спаяны",
    "sleeve": "провода опрессованы гильзой",
    "band": "провода соединены ленточным обжимом",
    "ultrasonic": "провода сварены ультразвуком",
}

# Marking tubes of a complex harness (generator, owner 16.10, 713К.4 КБ1 «Маркировка "5" 4/2мм»): cut with the other
# tubes (`tutParts` — pieces per kit article), put on before the connectors are mounted, shrunk with the other shrinks.
MARK_TUBE_SUFFIX = "маркировка"


def _mark_tube(op: Mapping[str, Any]) -> bool:
    return bool(op.get("markTube")) and op.get("type") in {"cutTut", "insTut", "heatTut"}


def _tut_parts(op: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pieces of a cut of several kit tubes (`tutParts`: art, name, count per item, length); [] — one tube (`tutArt`)."""
    parts = []
    for item in _list(op.get("tutParts")) if op.get("type") == "cutTut" else []:
        if not isinstance(item, Mapping):
            continue
        art = str(item.get("art") or item.get("name") or "").strip()
        count = _decimal(item.get("count"))
        if art and count > 0:
            parts.append({"art": art, "name": str(item.get("name") or art).strip(), "count": count, "len": str(item.get("len") or "").strip()})
    return parts


# «Сложная сборка» (generator, owner 16.10): one cut for every braid piece of the branches (CUT_NEYLON) — the braid is
# issued there, in metres; the branch «Монтаж оплётки» (`wrapCut`) only puts the cut piece on. One cut for every tube
# piece (`tutParts` without `markTube`) names the tubes it cut.
BRAID_CUT_SUFFIX = "оплётка"
TUBES_CUT_SUFFIX = "трубки"


def _braid_cut(op: Mapping[str, Any]) -> bool:
    return op.get("type") == "cutTut" and op.get("wrapKind") == "braid"


def _parts_text(parts: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(f"{part['art']} — {_format(part['count'])} шт" + (f" по {part['len']} мм" if part["len"] else "") for part in parts)


def _splice_method(op: Mapping[str, Any]) -> str:
    """The splice op's joining method, "" — not given or unknown (an old config: soldered, built as before)."""
    method = str(op.get("method") or "").strip() if op.get("type") == "splice" else ""
    return method if method in SPLICE_DONE else ""


def _splice_parts(op: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sleeves (or the band material) the splices use, folded by article: `count` — splices per item. Solder takes
    none: a sleeve left over from another method is not issued."""
    if _splice_method(op) not in {"sleeve", "band"}:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for item in op.get("sleeves") or []:
        if not isinstance(item, Mapping):
            continue
        art = str(item.get("art") or item.get("name") or "").strip()
        count = _decimal(item.get("count"))
        if not art or count <= 0:
            continue
        row = grouped.setdefault(art, {"art": art, "name": str(item.get("name") or art).strip(), "count": Decimal(0)})
        row["count"] += count
    return list(grouped.values())


def _suffix(op: Mapping[str, Any]) -> str:
    operation_type = op.get("type")
    if op.get("opKey") in TAIL_KEYS:
        return TAIL_SUFFIX
    if operation_type == "cutWireStrip":
        return "разделка"
    if operation_type == "cutWireCrimp":
        crimp = "обжим + лужение" if op.get("tin") else "обжим А и В" if _endpoint_b(op) else "обжим"
        # Партии обжима при резке (генератор, 16.10): у каждой свой терминал — иначе ключи ЭТК совпали бы.
        return f"{crimp} {_cut_terminal(op)}".strip() if op.get("crimpBatch") else crimp
    if _mark_tube(op):
        return MARK_TUBE_SUFFIX
    if _braid_cut(op):
        return BRAID_CUT_SUFFIX
    if operation_type == "cutTut" and _tut_parts(op) and not op.get("tutArt"):
        return TUBES_CUT_SUFFIX
    if operation_type in ("cutTut", "heatTut") and _sleeve_suffix(op):
        return _sleeve_suffix(op)
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


def _count_text(count: int, one: str, few: str, many: str) -> str:
    """«1 отрезок», «2 отрезка», «5 отрезков»: a number and the right Russian plural form."""
    tail = count % 100
    if 10 < tail < 20 or count % 10 >= 5 or count % 10 == 0:
        return f"{count} {many}"
    return f"{count} {one if count % 10 == 1 else few}"


def _cut_groups(wires: Sequence[Mapping[str, Any]]) -> str:
    """«НВ4 0,2 — 4 шт по 211 мм; НВ4 0,5 — 2 шт по 300 мм»: the cut wires folded by grade and length."""
    grouped: dict[tuple[str, str], Decimal] = {}
    for wire in wires:
        label = str(wire.get("art") or wire.get("name") or "").strip()
        length = _decimal(wire.get("length"))
        key = (label, _format(length) if length else "")
        grouped[key] = grouped.get(key, Decimal(0)) + _number(wire.get("qty"), Decimal(1))
    return "; ".join(
        (f"{label} — " if label else "") + f"{_format(count)} шт" + (f" по {length} мм" if length else "")
        for (label, length), count in grouped.items()
    )


def _side_word(op: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """«Сторона А» for the start of a sentence; a big assembly names the end by its label."""
    location = _location(op, config)
    return location[:1].upper() + location[1:]


def _result_name(
    op: Mapping[str, Any], config: Mapping[str, Any], previous: str,
    done: Sequence[Mapping[str, Any]] | None = None, seed: str = "",
) -> str:
    """The semi-finished product an operation hands on, as one short Russian sentence (owner 19.10, «результат
    по-человечески»): the part's name and its state now — never a log of the steps behind it. `done` — the operations
    of the chain up to and including `op` (state is folded from them); `seed` — a product name the chain starts from
    (a hand-written one). Without `done` the chain is `previous` plus this operation."""
    operation_type = str(op.get("type") or "")
    if operation_type.startswith("coax"):
        return _coax_result_name(op, config, done or [op])
    base: str | None = (previous if done is None else seed) or None
    facts: list[tuple[str, Any]] = []
    for step in done if done is not None else [op]:
        base, facts = _harness_step(step, config, base, facts)
    return _compose_result(base, facts)


def _with_fact(facts: list[tuple[str, Any]], key: str, payload: Any) -> list[tuple[str, Any]]:
    """The facts with `key` said anew: a crimped side is one more fact, any other kind replaces its earlier word
    («термоусадка надета» → «термоусадка усажена»)."""
    if key == "crimp":
        return [*facts, (key, payload)]
    if any(item[0] == key for item in facts):
        return [(key, payload) if item[0] == key else item for item in facts]
    return [*facts, (key, payload)]


def _fact_texts(facts: Sequence[tuple[str, Any]]) -> list[str]:
    """What is still to see on the part: stripped ends are not told once they are crimped, tinned or spliced; the
    same terminal crimped on both sides is one phrase."""
    keys = {key for key, _ in facts}
    crimps = [payload for key, payload in facts if key == "crimp"]
    texts: list[str] = []
    for key, payload in facts:
        if key == "strip" and keys & {"crimp", "splice", "tin"}:
            continue
        if key == "strip_tail" and "tail_tin" in keys:
            continue
        if key == "crimp":
            if payload is not crimps[0]:
                continue
            if len(crimps) == 2 and crimps[0][1] == crimps[1][1] and crimps[0][0] != crimps[1][0]:
                texts.append(f"терминалы {payload[1]} обжаты с двух сторон")
            else:
                listed = [f"{terminal} ({place})" for place, terminal in crimps]
                texts.append("обжаты терминалы " + (" и ".join(listed) if len(listed) == 2 else ", ".join(listed)))
            continue
        texts.append(str(payload))
    return texts


def _compose_result(base: str | None, facts: Sequence[tuple[str, Any]]) -> str:
    texts = _fact_texts(facts)
    if not texts:
        return base or ""
    joined = ", ".join(texts)
    return f"{base}; {joined}" if base else joined[:1].upper() + joined[1:]


def _harness_step(
    op: Mapping[str, Any], config: Mapping[str, Any], base: str | None, facts: list[tuple[str, Any]],
) -> tuple[str | None, list[tuple[str, Any]]]:
    """One operation of a wire chain applied to the part's state: `base` (what the part is — the cut wires, a side
    put together) and the `facts` that hold of it now. An operation that makes a new part starts the state anew."""
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    wires = _wires(op, config)
    endpoint = _endpoint(op)
    has_previous = bool(base) or bool(facts)
    if kind == "prs":
        terminal = str(endpoint.get("termArt") or endpoint.get("termName") or "")
        sources = list(dict.fromkeys(str(item) for item in op.get("wireSources") or [] if item))
        if op.get("side") == "B" and has_previous and len(sources) <= 1:
            return base, _with_fact(facts, "crimp", (_location(op, config), terminal))
        return f"{_side_word(op, config)}: на {_wires_text(len(wires))} обжаты терминалы {terminal}", []
    if kind == "ins":
        connector = str(endpoint.get("connArt") or endpoint.get("connName") or "")
        return f"{_side_word(op, config)} собрана: провода вставлены в разъём {connector}", []
    if kind == "sdr":
        connector = str(endpoint.get("connArt") or endpoint.get("connName") or "")
        return f"{_side_word(op, config)} собрана: провода припаяны к разъёму {connector}", []
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        cut = f"Провода нарезаны: {_cut_groups(wires)}" if wires else ""
        terminal = str(endpoint.get("termArt") or endpoint.get("termName") or "")
        new: list[tuple[str, Any]] = []
        if terminal:
            new.append(("crimp", (_location(op, config), terminal)))
            side_b = _side_b_op(op)
            if _terminal(side_b):
                new.append(("crimp", (_location(side_b, config), _terminal(side_b))))
            if op.get("tin"):
                new.append(("tin_end", TINNED_END))
        elif operation_type == "cutWireStrip" and op.get("tin") and cut:
            # Хвосты «сросток → облуженный конец» (CUT_WIRE+2strip+tin): оба конца зачищены, хвост облужен тем же станком.
            new += [("strip", "концы зачищены"), ("tail_tin", f"{TAIL_SUFFIX} облужены")]
        return cut or None, new
    if _mark_tube(op):
        if operation_type == "cutTut":
            parts = _tut_parts(op)
            listed = "; ".join(
                f"{part['art']} — {_format(part['count'])} шт" + (f" по {part['len']} мм" if part["len"] else "") for part in parts)
            return (f"Маркировка нарезана: {listed}" if listed else "Маркировка нарезана"), []
        return base, _with_fact(facts, "mark", "маркировка надета" if operation_type == "insTut" else "маркировка усажена")
    if _braid_cut(op):
        material = str(op.get("tutArt") or "")
        head = f"Оплётка {material or op.get('tutName') or ''}".strip()
        parts = _tut_parts(op)
        # Одна оплётка — отрезки без артикула: «Оплётка XP-3 нарезана: 2 шт по 120 мм; 2 шт по 110 мм».
        listed = ("; ".join(f"{_format(part['count'])} шт" + (f" по {part['len']} мм" if part["len"] else "") for part in parts)
                  if material else _parts_text(parts))
        return (f"{head} нарезана: {listed}" if listed else f"{head} нарезана"), []
    if operation_type == "cutTut" and _tut_parts(op) and not op.get("tutArt"):
        return f"Трубки нарезаны: {_parts_text(_tut_parts(op))}", []
    if operation_type == "cutTut":
        material = str(op.get("tutArt") or op.get("tutName") or "ТУТ")
        segments = op.get("tutSegs") if isinstance(op.get("tutSegs"), list) else []
        if any(item.get("len") for item in segments if isinstance(item, Mapping)):
            parts = [f"{item.get('count', 0)} шт по {item.get('len')} мм" for item in segments if item.get("len")]
            return f"Трубка {material} нарезана: {'; '.join(parts)}", []
        count = int(_decimal(op.get("tutCount")))
        return f"Трубка {material} нарезана" + (f": {_count_text(count, 'отрезок', 'отрезка', 'отрезков')}" if count else ""), []
    if operation_type == "insTut":
        return base, _with_fact(facts, "tube", "термоусадка надета" if op.get("harness") else "трубка надета на провода")
    if operation_type == "heatTut":
        return base, _with_fact(facts, "tube", "термоусадка усажена" if op.get("harness") else "трубка усажена")
    if operation_type == "stripCable":
        count = int(_decimal(op.get("tutCount")))
        tail = op.get("opKey") == "stripTail"
        if has_previous:
            return base, _with_fact(facts, "strip_tail" if tail else "strip", f"{TAIL_SUFFIX} разделаны" if tail else "концы разделаны")
        ends = f"{TAIL_SUFFIX} разделаны" if tail else "концы разделаны"
        return f"{ends[:1].upper()}{ends[1:]}: {count}", []
    if operation_type == "tin" and op.get("opKey") == "tinTail":
        return (base, _with_fact(facts, "tail_tin", f"{TAIL_SUFFIX} облужены")) if has_previous else (base, facts)
    method = _splice_method(op)
    if method:
        arts = [row["art"] for row in _splice_parts(op)]
        parts = (", ".join(arts[:-1]) + " и " if len(arts) > 1 else "") + arts[-1] if arts else ""  # no comma: the facts are joined by commas
        return base, _with_fact(facts, "splice", SPLICE_DONE[method] + (f" {parts}" if parts and method == "sleeve" else ""))
    labels = {
        "twist": ("twist", "провода скручены"), "tin": ("tin", "концы облужены"), "insMark": ("mark_print", "маркировка нанесена"),
        "seal": ("seal", "место загерметизировано"),
        "insWrap": ("wrap", "надета оплётка" if op.get("wrapKind") == "braid" else "намотана защитная лента"),
    }
    if has_previous and operation_type in labels:
        key, text = labels[operation_type]
        return base, _with_fact(facts, key, text)
    return base, facts


def _coax(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("coax")
    return value if isinstance(value, Mapping) else {}


def _coax_end(op: Mapping[str, Any], config: Mapping[str, Any]) -> Mapping[str, Any]:
    """The connector end of a coax operation (`config.coax.sideA` / `sideB` by the operation's side)."""
    side = _coax(config).get("sideB" if op.get("side") == "B" else "sideA")
    return side if isinstance(side, Mapping) else {}


def _coax_units(op: Mapping[str, Any]) -> Decimal:
    """Pieces per item a coax operation handles (`units`: two TUT pieces, two shield sleeves…); at least one."""
    return max(Decimal(1), _number(op.get("units"), Decimal(1)))


VSWR_SAMPLE_EVERY = 50


def vswr_control(raw: object) -> dict[str, Any] | None:
    """«Контроль КСВН» of the coax generator (owner 22.10: «1 из 50, всю партию или вообще не проверять»), from
    `build_config.coax.vswrControl`: {mode: "all" | "sample" | "none", every: N ≥ 2 for "sample", else None}. Not set
    or not readable — None: the assembly was built before the setting, its ETK reads as it did."""
    if not isinstance(raw, Mapping) or raw.get("mode") not in {"all", "sample", "none"}:
        return None
    if raw["mode"] != "sample":
        return {"mode": raw["mode"], "every": None}
    try:
        every = int(raw.get("every"))
    except (TypeError, ValueError):
        every = VSWR_SAMPLE_EVERY
    return {"mode": "sample", "every": every if every >= 2 else VSWR_SAMPLE_EVERY}


def vswr_in_sample(control: Mapping[str, Any] | None, position: int) -> bool:
    """Must unit `position` (1-based in the batch) be measured: sample 1 of N — the first and then every N-th
    ((position − 1) % N == 0); all or no setting — every unit; «none» — none."""
    mode = control.get("mode") if isinstance(control, Mapping) else None
    if mode == "none":
        return False
    if mode == "sample":
        return (position - 1) % int(control.get("every") or VSWR_SAMPLE_EVERY) == 0
    return True


def vswr_sample_count(control: Mapping[str, Any] | None, quantity: Decimal) -> Decimal:
    """Units of a batch of `quantity` the VSWR check measures: ceil(quantity / N) in sample mode, else the batch."""
    if isinstance(control, Mapping) and control.get("mode") == "sample":
        return (quantity / int(control.get("every") or VSWR_SAMPLE_EVERY)).to_integral_value(rounding="ROUND_CEILING")
    return quantity


def _coax_connector(config: Mapping[str, Any], side: str) -> str:
    end = _coax(config).get("sideB" if side == "B" else "sideA")
    end = end if isinstance(end, Mapping) else {}
    return str(end.get("article") or end.get("connName") or "").strip()


def _coax_end_name(config: Mapping[str, Any], side: str) -> str:
    """The connector of an end as the fitter reads it: «SMA-Male» by its type, else the article."""
    end = _coax(config).get("sideB" if side == "B" else "sideA")
    kind = coax_connector_kind(end)[0] if isinstance(end, Mapping) else ""
    return kind or _coax_connector(config, side)


def _coax_contact(config: Mapping[str, Any], side: str) -> str:
    end = _coax(config).get("sideB" if side == "B" else "sideA")
    pin_type = str(end.get("pinType") or "").lower() if isinstance(end, Mapping) else ""
    return "обжат" if "обжим" in pin_type else "припаян"


def _coax_result_name(op: Mapping[str, Any], config: Mapping[str, Any], done: Sequence[Mapping[str, Any]]) -> str:
    """What the fitter holds after a coax operation (generator tab «Коакс»; owner 19.10: «результат по-человечески»):
    the cable with its length and how its ends look now, then — after «;» — what is still to finish on it («ТУТ и
    втулки экрана надеты, не обжаты»). The state is folded from the operations done (`done`, `op` last), never a log
    of steps: stripped ends are not told once the contacts are on, the contacts once the housings are on.
    TUT pieces are a side product of their own."""
    operation_type = str(op.get("type") or "")
    coax = _coax(config)
    cable = str(coax.get("cableArt") or coax.get("cableName") or "").strip()
    tut = str(coax.get("tutArt") or coax.get("tutName") or "").strip()
    if operation_type == "coaxCutTut":
        count = int(_coax_units(op))
        length = _format(coax.get("tutLength"))
        return (f"Трубка {tut or 'ТУТ'} нарезана: {_count_text(count, 'отрезок', 'отрезка', 'отрезков')}"
                + (f" по {length} мм" if length != "0" else ""))
    sides: dict[str, set[str]] = {key: set() for key in ("strip", "pin", "housing", "shield_solder", "cap_crimp", "cap_screw")}
    clamp_put_on = 0
    flags: set[str] = set()
    many = True
    for step in done:
        kind = str(step.get("type") or "")
        end = "B" if step.get("side") == "B" else "A"
        if kind == "coaxCut":
            sides["strip"] |= {side for side in ("A", "B") if side in _list(step.get("stripSides"))}
        elif kind == "coaxStrip":
            sides["strip"].add(end)
        elif kind in ("coaxPin", "coaxPinCrimp"):
            sides["pin"].add(end)
        elif kind == "coaxHousing":
            sides["housing"].add(end)
        elif kind == "coaxSolderShield":
            sides["shield_solder"].add(end)
        elif kind == "coaxCapCrimp":
            sides["cap_crimp"].add(end)
        elif kind == "coaxCapScrew":
            sides["cap_screw"].add(end)
        elif kind == "coaxVswr":
            flags.add(kind)
        elif kind in {"coaxInsTut", "coaxInsSleeve", "coaxShield", "coaxHeat"}:
            clamp = len(_list(step.get("clampSides"))) if kind == "coaxInsSleeve" else 0
            clamp_put_on += clamp
            if kind != "coaxInsSleeve" or _coax_units(step) > clamp:  # на прижимной стороне втулки нет — гайка, шайба, уплотнитель
                flags.add(kind)
            many = _coax_units(step) > 1
    length = _format(coax.get("cableLength"))
    head = f"Кабель {cable or 'коаксиальный'}" + (f" {length} мм" if length != "0" else "")
    both = {"A", "B"}
    named = {side: _coax_end_name(config, side) for side in ("A", "B")}
    for side in sides["housing"]:
        if named[side] and coax_end_shield(_coax_end({"side": side}, config)) == SHIELD_CLAMP:
            named[side] += " (прижим)"  # «с корпусом N-Male (прижим) на стороне А»
    if sides["housing"]:
        if sides["housing"] == both and all(named.values()):
            head += f" с корпусами {named['A']} (А) и {named['B']} (В)"
        elif sides["housing"] == both:
            head += " с корпусами разъёмов на обоих концах"
        else:
            side = next(iter(sides["housing"]))
            head += " с корпусом" + (f" {named[side]}" if named[side] else "") + f" на стороне {_side_label({'side': side})}"
    elif sides["pin"]:
        contacts = {_coax_contact(config, side) for side in sides["pin"]}
        many_pins, one_pin = {"припаян": ("припаянными", "припаянным"), "обжат": ("обжатыми", "обжатым")}.get(
            next(iter(contacts)) if len(contacts) == 1 else "", ("", ""))
        if sides["pin"] == both:
            head += " с " + (f"{many_pins} " if many_pins else "") + "центральными контактами"
        else:
            head += " с " + (f"{one_pin} " if one_pin else "") + (
                f"центральным контактом на стороне {_side_label({'side': next(iter(sides['pin']))})}")
    elif sides["strip"]:
        if sides["strip"] == both:
            if all(named.values()):
                head += f", концы разделаны: А — под {named['A']}, В — под {named['B']}"
            else:
                head += ", концы А и В разделаны"
        else:
            side = next(iter(sides["strip"]))
            head += f", конец {_side_label({'side': side})} разделан" + (f" под {named[side]}" if named[side] else "")
    facts: list[str] = []
    if sides["shield_solder"]:
        facts.append("экран припаян к корпусам" if sides["shield_solder"] == both else "экран припаян к корпусу")
    if clamp_put_on and not sides["housing"]:
        facts.append("гайки, шайбы, уплотнители и зажимные кольца надеты" if clamp_put_on > 1 else "гайка, шайба, уплотнитель и зажимное кольцо надеты")
    for key, word in (("cap_crimp", "обжаты"), ("cap_screw", "завёрнуты")):
        if sides[key]:
            facts.append(f"крышки разъёмов {word}" if sides[key] == both else f"крышка разъёма {word[:-1]}а")
    tube_on, sleeve_on = "coaxInsTut" in flags, "coaxInsSleeve" in flags
    shrunk, crimped = "coaxHeat" in flags, "coaxShield" in flags
    if tube_on and sleeve_on and not shrunk and not crimped:
        facts.append("ТУТ и втулки экрана надеты, не обжаты" if many else "ТУТ и втулка экрана надеты, не обжата")
    else:
        if sleeve_on or crimped:
            facts.append(("втулки экрана обжаты" if many else "втулка экрана обжата") if crimped else
                         ("втулки экрана надеты, не обжаты" if many else "втулка экрана надета, не обжата"))
        if tube_on or shrunk:
            facts.append(("ТУТ усажены" if many else "ТУТ усажена") if shrunk else
                         ("ТУТ надеты, не усажены" if many else "ТУТ надета, не усажена"))
    if "coaxVswr" in flags:
        control = vswr_control(coax.get("vswrControl"))
        facts.append(f"КСВН проверен выборочно (1 из {control['every']})" if control and control["mode"] == "sample"
                     else "КСВН проверен")
    return f"{head}; {', '.join(facts)}" if facts else head


def _group_cut_components(wires: Sequence[Mapping[str, Any]], quantity: Decimal) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, Any]] = {}
    for wire in wires:
        key = str(wire.get("art") or wire.get("name") or "").strip()
        row = grouped.setdefault(key, {"art": key, "name": str(wire.get("name") or ""), "meters": Decimal(0)})
        row["meters"] += _number(wire.get("qty"), Decimal(1)) * _decimal(wire.get("length")) * quantity / 1000
    return [{"art": row["art"], "name": row["name"], "norm": _format(row["meters"]), "unit": "М."} for row in grouped.values()]


def _mark_items(op: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in op.get("marks") or [] if isinstance(item, Mapping)]


def _kd_marks(marks: Sequence[Mapping[str, Any]]) -> bool:
    """Метки по КД (генератор, «Маркировка по КД»): у строки есть надпись или что маркируем. Старая конфигурация —
    бирки из состава (art/name/qty) — собирается как раньше."""
    return any(str(item.get("text") or "").strip() or str(item.get("target") or "").strip() for item in marks)


def _mark_materials(marks: Sequence[Mapping[str, Any]], quantity: Decimal) -> list[dict[str, str]]:
    """Комплектующие маркировки: материал меток, сложенный по артикулу, на партию; метка без материала — без строки."""
    grouped: dict[str, dict[str, Any]] = {}
    for item in marks:
        art = str(item.get("art") or item.get("name") or "").strip()
        if not art:
            continue
        row = grouped.setdefault(art, {"art": art, "name": str(item.get("name") or ""), "count": Decimal(0)})
        row["count"] += _number(item.get("qty"), Decimal(1))
    return [{"art": row["art"], "name": row["name"], "norm": _format(row["count"] * quantity), "unit": "Шт."} for row in grouped.values()]


def _mark_rows(op: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, str]]:
    """Метки по КД для ЭТК: что маркируем, надпись, материал, где, сколько на изделие и на партию."""
    quantity = _part_quantity(config)
    return [{
        "target": str(item.get("target") or "").strip(), "text": str(item.get("text") or "").strip(),
        "material": str(item.get("name") or item.get("art") or "").strip(), "place": str(item.get("place") or "").strip(),
        "qty": _format(_number(item.get("qty"), Decimal(1))), "batch": _format(_number(item.get("qty"), Decimal(1)) * quantity),
        **({"length": _format(length)} if (length := mark_length(item)) else {}),
    } for item in _mark_items(op) if str(item.get("text") or "").strip() or str(item.get("target") or "").strip()]


def _components(
    op: Mapping[str, Any], config: Mapping[str, Any], code: str, *, manual: bool = False,
) -> list[dict[str, str]]:
    """`manual` — «Ручная сборка» (25.10): cut tubes go to the kit in metres, tape names its material; the generator's
    own ETKs stay as they were."""
    quantity = _part_quantity(config)
    if _splice_method(op):
        # Sleeves of the splices — by the method, whatever the card (PRS_COPPER of the band crimp lists no parts).
        return [{"art": row["art"], "name": row["name"], "norm": _format(row["count"] * quantity), "unit": "Шт."}
                for row in _splice_parts(op)]
    operation_type = str(op.get("type") or "")
    # Опрессовка центрального контакта (владелец 19.10) идёт картой опрессовки коакса, пока своей нет: её вход —
    # разъём своей стороны, какой бы картой она ни шла.
    own_material = manual and operation_type == "insWrap" and not op.get("wrapCut") and bool(op.get("wrapArt") or op.get("wrapName"))
    if code in NO_COMPONENTS and operation_type != "coaxPinCrimp" and not own_material:
        return []
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        rows = _group_cut_components(_wires(op, config), quantity)
        endpoint = _endpoint(op)
        count = sum((_number(wire.get("qty"), Decimal(1)) for wire in _wires(op, config)), Decimal(0))
        # Обжим обеих сторон: терминал каждой стороны по разу на провод; один артикул на обеих — одной строкой.
        terminals: dict[str, dict[str, Any]] = {}
        for side in (endpoint, _endpoint_b(op)):
            art = str(side.get("termArt") or side.get("termName") or "")
            if art:
                row = terminals.setdefault(art, {"name": str(side.get("termName") or ""), "count": Decimal(0)})
                row["count"] += count
        rows.extend({"art": art, "name": row["name"], "norm": _format(row["count"] * quantity), "unit": "Шт."}
                    for art, row in terminals.items())
        return rows
    if operation_type.startswith("coax"):
        return _coax_components(op, config, manual=manual)
    if _braid_cut(op):
        # Резка оплётки ветвей: оплётка в метрах — сумма отрезков (по позиции состава) на партию.
        metres: dict[str, dict[str, Any]] = {}
        for part in _tut_parts(op):
            row = metres.setdefault(part["art"], {"name": part["name"], "m": Decimal(0)})
            row["m"] += part["count"] * _decimal(part["len"]) / 1000
        return [{"art": art, "name": row["name"], "norm": _format(row["m"] * quantity), "unit": "М."} for art, row in metres.items()]
    if manual and operation_type == "cutTut":
        metres = _cut_tube_metres(op, quantity)
        if metres:
            return metres
    if operation_type == "cutTut" and _tut_parts(op):
        # Резка трубок нескольких позиций состава (маркировка «5», «I3»…): каждая — своей строкой, отрезков на партию.
        return [{"art": part["art"], "name": part["name"], "norm": _format(part["count"] * quantity), "unit": "Шт."} for part in _tut_parts(op)]
    if operation_type == "cutTut":
        count = _decimal(op.get("tutCount")) * quantity
        return [{"art": str(op.get("tutArt") or ""), "name": str(op.get("tutName") or op.get("tutArt") or ""), "norm": _format(count), "unit": "Шт."}]
    if operation_type == "insWrap" and op.get("wrapCut"):
        return []
    if operation_type == "insWrap":
        meters = _decimal(op.get("wrapLen")) * quantity / 1000
        return [{"art": str(op.get("wrapArt") or ""), "name": str(op.get("wrapName") or op.get("wrapArt") or ""), "norm": _format(meters), "unit": "М."}]
    if operation_type == "insMark":
        marks = _mark_items(op)
        if _kd_marks(marks):
            return _mark_materials(marks, quantity)
        return [{"art": str(item.get("art") or item.get("name") or ""), "name": str(item.get("name") or ""), "norm": _format(_number(item.get("qty"), Decimal(1)) * quantity), "unit": "Шт."} for item in op.get("marks") or []]
    endpoint = _endpoint(op)
    kind = _kind(op)
    if kind == "prs":
        return [{"art": str(endpoint.get("termArt") or endpoint.get("termName") or ""), "name": str(endpoint.get("termName") or ""), "norm": _format(_term_count(op, config) * quantity), "unit": "Шт."}]
    if kind in {"ins", "sdr"}:
        return [{"art": str(endpoint.get("connArt") or endpoint.get("connName") or ""), "name": str(endpoint.get("connName") or ""), "norm": _format(quantity), "unit": "Шт."}]
    return []


def _cut_tube_metres(op: Mapping[str, Any], quantity: Decimal) -> list[dict[str, str]]:
    """A cut tube in the kit is in metres (spec and kit say so): segments × length per item × the batch. «Ручная сборка»
    only; [] — a length is missing somewhere, the cut stays in pieces as the generator counts it."""
    parts = _tut_parts(op)
    if parts:
        if not all(_decimal(part["len"]) > 0 for part in parts):
            return []
        return [{"art": part["art"], "name": part["name"], "norm": _format(part["count"] * _decimal(part["len"]) / 1000 * quantity),
                 "unit": "М."} for part in parts]
    segments = [item for item in _list(op.get("tutSegs")) if isinstance(item, Mapping)]
    if not segments or not all(_decimal(item.get("len")) > 0 and _decimal(item.get("count")) > 0 for item in segments):
        return []
    metres = sum((_decimal(item["count"]) * _decimal(item["len"]) / 1000 for item in segments), Decimal(0))
    return [{"art": str(op.get("tutArt") or ""), "name": str(op.get("tutName") or op.get("tutArt") or ""),
             "norm": _format(metres * quantity), "unit": "М."}]


def _coax_components(op: Mapping[str, Any], config: Mapping[str, Any], *, manual: bool = False) -> list[dict[str, str]]:
    """Вход коаксиальной операции (генератор, вкладка «Коакс»; владелец 15.10 «не появилось кол-во»): у вкладки нет
    `wires`, всё — в `config.coax`. Резка — кабель в метрах (длина реза уже с припуском L+ разъёмов), резка ТУТ — отрезки,
    пайка контакта — разъём своей стороны (один раз: корпус собирается из того же комплекта), втулка — по одной на
    сторону с обжимом. Нет артикула или длины (старая конфигурация) — без строки, как раньше."""
    operation_type = str(op.get("type") or "")
    coax = config.get("coax") if isinstance(config.get("coax"), Mapping) else {}
    quantity = _part_quantity(config)
    units = _number(op.get("units"), Decimal(1))

    def row(art_key: str, name_key: str, norm: Decimal, unit: str) -> list[dict[str, str]]:
        art = str(coax.get(art_key) or "").strip()
        if not art or norm <= 0:
            return []
        return [{"art": art, "name": str(coax.get(name_key) or ""), "norm": _format(norm), "unit": unit}]

    parts = [item for item in _list(op.get("parts")) if isinstance(item, Mapping) and str(item.get("art") or "").strip()]
    if parts:
        # FAKRA/HFM (генератор 25.10): детали комплекта конца, что берёт операция, — списком в самой операции.
        return [{"art": str(item["art"]).strip(), "name": str(item.get("name") or ""),
                 "norm": _format(_decimal(item.get("qty")) * quantity), "unit": str(item.get("unit") or "Шт.")} for item in parts]
    if operation_type == "coaxCut":
        cables = max(_decimal(coax.get("cables")), Decimal(1))  # HFM: четыре одинаковых кабеля на сборку
        return row("cableArt", "cableName", _decimal(coax.get("cableLength")) * quantity * cables / 1000, "М.")
    if operation_type == "coaxCutTut":
        if manual and _decimal(coax.get("tutLength")) > 0:
            return row("tutArt", "tutName", units * _decimal(coax.get("tutLength")) / 1000 * quantity, "М.")
        return row("tutArt", "tutName", units * quantity, "Шт.")
    if operation_type == "coaxInsSleeve":
        # Втулка — только на обжимных сторонах; гайка, шайба и уплотнитель прижимной стороны входят в комплект разъёма.
        crimped = units - len(op.get("clampSides") or [])
        return row("sleeveArt", "sleeveName", crimped * quantity, "Шт.") if crimped > 0 else []
    if operation_type in ("coaxPin", "coaxPinCrimp"):
        side = coax.get("sideB" if op.get("side") == "B" else "sideA")
        article = str(side.get("article") or "").strip() if isinstance(side, Mapping) else ""
        if not article:
            return []
        return [{"art": article, "name": str(side.get("connName") or ""), "norm": _format(quantity), "unit": "Шт."}]
    return []


CUT_TYPES = {"cutWire", "cutWireStrip", "cutWireCrimp"}
PIECE_TYPES = {"twist", "tin", "heatTut", "insWrap", "insMark", "seal"}
TUT_TYPES = {"cutTut", "insTut", "endCap", "foldCore", "stripCable"}


def _row(name: str, norm: object) -> dict[str, str]:
    return {"name": name, "norm": str(norm or ""), "unit": "Шт."}


def _terminal(op: Mapping[str, Any]) -> str:
    endpoint = _endpoint(op)
    return str(endpoint.get("termArt") or endpoint.get("termName") or "")


def _cut_terminal(op: Mapping[str, Any]) -> str:
    """What a cut-with-crimp leaves on the wire: the side's terminal; both sides — «X с двух сторон» / «X (А) / Y (В)»."""
    terminal, terminal_b = _terminal(op), _terminal(_side_b_op(op)) if _endpoint_b(op) else ""
    if not (terminal and terminal_b):
        return terminal
    return f"{terminal} с двух сторон" if terminal == terminal_b else f"{terminal} (А) / {terminal_b} (В)"


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
        f"{' + '.join(_wire_name(wire) for wire in wires[start:start + per])}, обжаты в терминал {_terminal(op)} "
        f"({label}, {_location(op, config)})"
        for start in range(0, len(wires), per)
    ]


def _per_wire(wire: Mapping[str, Any], quantity: Decimal) -> str:
    return _format(_number(wire.get("qty"), Decimal(1)) * quantity)


def _single_name(config: Mapping[str, Any], result: str, is_last: bool) -> str:
    """The name of the one piece an operation hands on: the product itself after the last operation."""
    return str(config.get("assemblyName") or result) if is_last else result


# «Ручная сборка»: these also hand on pieces of the product, and the tube operations never multiply the product
# by the tube count (the count is their own consumption). The generator's own ETKs keep their rows.
MANUAL_PIECE_TYPES = {"splice", "insHousing"}
MANUAL_PRODUCT_TUT_TYPES = {"insTut", "endCap", "foldCore", "stripCable"}


def _result_rows(
    op: Mapping[str, Any], config: Mapping[str, Any], result: str, is_last: bool, single: str | None = None,
    *, manual: bool = False,
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
        terminal = _cut_terminal(op) if kind or operation_type == "cutWireCrimp" else ""
        return [_row(_wire_result_name(wire, terminal, bool(op.get("tin"))), _per_wire(wire, quantity)) for wire in wires]
    norm = ""
    if operation_type == "coaxCutTut":
        norm = _format(max(Decimal(1), _decimal(op.get("units"))) * quantity)
    elif operation_type in PIECE_TYPES or operation_type.startswith("coax") or (manual and operation_type in MANUAL_PIECE_TYPES):
        norm = _format(quantity)
    if operation_type in TUT_TYPES:
        whole = (operation_type == "insTut" and (op.get("harness") or _mark_tube(op))) or (
            manual and operation_type in MANUAL_PRODUCT_TUT_TYPES)
        norm = _format(quantity if whole else _decimal(op.get("tutCount")) * quantity)
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
        tail = "; трубка надета на провода" if tut_on else ""
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
    if operation_type in PIECE_TYPES or (operation_type == "insTut" and (op.get("harness") or _mark_tube(op))):
        return quantity
    return ""


def linear_seconds(timing: object, length_m: Decimal) -> Decimal | None:
    """Время одной штуки «Погонной» операции по циклограмме (расчётный лист цеха, владелец 15.10 и 18.10.2026): большее
    из машины — подача L / «Скорость проката» (м/с) + «Скорость работы инструмента» × «Кол-во операций инструмента» —
    и ручных работ (взять + работы + снять ПФ), как «Время операции» листа. None — узлов нет или подача не > 0: время,
    как раньше, из «Время операции»."""
    if not isinstance(timing, Mapping):
        return None
    roll = _decimal(timing.get("rollSpeed"))
    if roll <= 0:
        return None
    machine = length_m / roll + _decimal(timing.get("toolSeconds")) * _decimal(timing.get("toolOps"))
    hands = sum((_decimal(timing.get(key)) for key in ("takeSeconds", "manualSeconds", "putSeconds")), Decimal(0))
    return max(machine, hands)


def _time(op: Mapping[str, Any], config: Mapping[str, Any], result_name: str) -> list[dict[str, str]]:
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    wires = _wires(op, config)
    quantity = _part_quantity(config)
    operation_minutes = _decimal(op.get("tOp")) / 60
    if (operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"} or kind) and wires:
        units = lambda wire: _number(wire.get("qty"), Decimal(1)) * (_number(wire.get("cores"), Decimal(1)) if kind else 1)
        if kind in {"ins", "sdr"} or (kind == "prs" and op.get("side") == "B"):
            return [{"name": result_name, "norm": _format(operation_minutes * sum(map(units, wires)) * quantity, 2)}]
        terminal = _cut_terminal(op) if operation_type == "cutWireCrimp" or kind else ""

        def piece_minutes(wire: Mapping[str, Any]) -> Decimal:
            linear = None if kind else linear_seconds(op.get("linearTiming"), _decimal(wire.get("length")) / 1000)
            if linear is not None:
                return linear / 60
            return operation_minutes or Decimal(1)
        return [{
            "name": _wire_result_name(wire, terminal, bool(op.get("tin"))),
            "norm": _format(piece_minutes(wire) * units(wire) * quantity, 2),
        } for wire in wires]
    if operation_type.startswith("coax"):
        # Коакс (владелец 15.10, «0,06 мин» на партию): время на партию, как у провода — штука × units × партия + подготовка;
        # резка кабеля и ТУТ «Погонной» карты — по узлам станка от своей длины.
        coax = _coax(config)
        length = {"coaxCut": coax.get("cableLength"), "coaxCutTut": coax.get("tutLength")}.get(operation_type)
        linear = linear_seconds(op.get("linearTiming"), _decimal(length) / 1000) if length is not None else None
        piece = linear / 60 if linear is not None else operation_minutes
        if operation_type == "coaxVswr":
            # Выборочный контроль КСВН (1 из N) меряет ceil(партия / N) изделий, не всю партию.
            quantity = vswr_sample_count(vswr_control(coax.get("vswrControl")), quantity)
        return [{"name": result_name, "norm": _format(piece * _coax_units(op) * quantity, 2)}]
    twist =config.get("twist") if isinstance(config.get("twist"), Mapping) else {}
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
        # Лужение свободных концов (tinTail): `units` — сколько концов; без него — по проводам, как раньше.
        # Свивка ветви или группы сростков (генератор, 16.10): `units` — её пары; без него — все пары формы.
        "twist": _decimal(op.get("units")) if _decimal(op.get("units")) > 0 else twist_count,
        "tin": _decimal(op.get("units")) if _decimal(op.get("units")) > 0
        else sum((_number(item.get("qty"), Decimal(1)) for item in wires), Decimal(0)) or 1,
        "cutTut": _number(op.get("tutCount"), Decimal(1)), "insTut": _number(op.get("tutCount"), Decimal(1)),
        "endCap": _number(op.get("tutCount"), Decimal(1)), "foldCore": _number(op.get("tutCount"), Decimal(1)),
        "stripCable": _number(op.get("tutCount"), Decimal(1)),
        "heatTut": _number(op.get("tutCount"), Decimal(1)), "insWrap": _number(op.get("tutCount"), Decimal(1)),
        "insMark": sum((_number(item.get("qty"), Decimal(1)) for item in op.get("marks") or []), Decimal(0)) or 1,
        "seal": _number(op.get("sealPlaces"), Decimal(1)), "splice": _number(op.get("units"), Decimal(1)),
        "insHousing": _number(op.get("units"), Decimal(1)),
    }
    count = counters.get(operation_type)
    norm = operation_minutes * count * quantity if count is not None else operation_minutes
    return [{"name": result_name, "norm": _format(norm, 2)}]


def _length_tolerance(length: Decimal) -> str:
    """0,7 % длины (SUPPLYMAP, вверх до мм), но не строже ±3 мм до 500 мм длины и ±5 мм длиннее (владелец 04.10:
    «(+/-)1мм» — перебор, «хотя бы 3–5»)."""
    by_length = max(1, int((length * Decimal(7) / 1000).to_integral_value(rounding="ROUND_CEILING")))
    return f"±{max(3 if length <= 500 else 5, by_length)} мм"


def _cut_lengths(wires: Sequence[Mapping[str, Any]]) -> list[tuple[Decimal, Decimal]]:
    """Длины реза партии по возрастанию и сколько штук каждой (qty провода): [(180, 3), (200, 1)]; без длины — не в счёт."""
    counts: dict[Decimal, Decimal] = {}
    for wire in wires:
        length = _decimal(wire.get("length"))
        if length > 0:
            counts[length] = counts.get(length, Decimal(0)) + _number(wire.get("qty"), Decimal(1))
    return sorted(counts.items())


def _cut_tolerance(op: Mapping[str, Any], lengths: Sequence[Decimal]) -> str:
    """Допуск длины реза: введённый технологом в операции — как есть; иначе по длине (_length_tolerance). Партия проводов
    разной длины (владелец 17.10, 713К.4: «тут один чёрный 200 мм»): допуски длин совпали — один, иначе длины по допускам
    «150, 300 мм ±3 мм; 600 мм ±5 мм» (эскиз ЭТК подписывает так каждую размерную линию)."""
    manual = str(op.get("cutTolerance") or "").strip()
    if manual:
        return manual
    by_tolerance: dict[str, list[Decimal]] = {}
    for length in sorted(item for item in lengths if item > 0):
        by_tolerance.setdefault(_length_tolerance(length), []).append(length)
    if len(by_tolerance) <= 1:
        return next(iter(by_tolerance), "")
    return "; ".join(
        f"{', '.join(_format(length) for length in group)} мм {tolerance}" for tolerance, group in by_tolerance.items()
    )


def _length_kd(wires: Sequence[Mapping[str, Any]]) -> str:
    """Длина по КД резки: одна длина — «180мм»; партия разной длины — каждая со своим числом штук «180 мм ×3, 200 мм ×1»."""
    lengths = _cut_lengths(wires)
    if len(lengths) > 1:
        return ", ".join(f"{_format(length)} мм ×{_format(count)}" for length, count in lengths)
    first = str(wires[0].get("length") or "") if wires else ""
    return f"{first}мм" if first else ""


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


def _is_mm(value: object) -> bool:
    """A wire's diameter from the references (mm, a positive number); anything else is left out of the ETK."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _coax_strip_sizes(config: Mapping[str, Any], side_name: str) -> dict[str, Any]:
    """Strip sizes of one coax end for the sketch etkDrawCoax: L1–L3, L± of the БД.КОАКС connector, D1–D3 of the cable
    (СПР.КАБ, put on the end by `techcard_build.enrich_coax_diameters`)."""
    coax = config.get("coax") if isinstance(config.get("coax"), Mapping) else {}
    side = coax.get("sideB" if side_name == "B" else "sideA")
    side = side if isinstance(side, Mapping) else {}
    kind, gender = coax_connector_kind(side)
    depth = str(side.get("contactDepth") or "").strip()
    return {"cable": coax.get("cableArt") or "", "connector": side.get("article") or "",
            **({"connectorType": kind, "gender": gender} if kind else {}), **({"contactDepth": depth} if depth else {}), **{
        key: side.get(key) or "" for key in ("d1", "d2", "d3", "l1", "l2", "l3", "lPlus", "lMinus")}}


_FEMALE = re.compile(r"(?<![a-z])female(?![a-z])", re.IGNORECASE)
_MALE = re.compile(r"(?<![a-z])male(?![a-z])", re.IGNORECASE)


def coax_connector_kind(end: Mapping[str, Any]) -> tuple[str, str]:
    """The connector of a coax end the way the fitter names it: «SMA-Male» (the БД.КОАКС type, Male/Female capitalised) and
    the Russian half name by its contacts, «вилка» (male, pins) / «розетка» (female, sockets) — owner 17.10, the same
    words as `connector_housings.GENDER_WORD`. No type — ('', ''); a type without male/female (plug…) — no half name."""
    kind = " ".join(str(end.get("type") or "").split())
    if not kind:
        return "", ""
    gender = "розетка" if _FEMALE.search(kind) else "вилка" if _MALE.search(kind) else ""
    kind = _MALE.sub("Male", _FEMALE.sub("Female", kind))
    return kind, gender


def with_coax_connectors(op: Mapping[str, Any], config: Mapping[str, Any]) -> Mapping[str, Any]:
    """A view of a coax operation whose end fields (`coax`, `coaxB`, `coaxEnd`) name the connector type and half
    (`connectorType`, `gender`) — also an ETK issued before they were stored. Nothing to add — the same operation."""
    fields = op.get("fields")
    if not isinstance(fields, Mapping) or not isinstance(config.get("coax"), Mapping):
        return op
    patched = dict(fields)
    for key, side in (("coax", "A"), ("coaxB", "B"), ("coaxEnd", "B" if op.get("side") == "B" else "A")):
        end = fields.get(key)
        if not isinstance(end, Mapping):
            continue
        source = _coax(config).get("sideB" if side == "B" else "sideA") or {}
        kind, gender = coax_connector_kind(source)
        add: dict[str, Any] = {}
        if kind and not end.get("connectorType"):
            add.update(connectorType=kind, gender=gender)
        depth = str(source.get("contactDepth") or "").strip()
        if depth and not end.get("contactDepth"):
            add["contactDepth"] = depth  # заглубление контакта — на эскиз «Монтаж корпуса» и у выданного ЭТК
        if add:
            patched[key] = {**end, **add}
    parts = fields.get("coaxParts")
    if isinstance(parts, Mapping) and "ends" not in parts and _coax(config).get("sideA"):
        # ТУТ / втулки экрана надеваются на оба конца — эскиз рисует каждый разделанный конец со своим разъёмом.
        patched["coaxParts"] = {**parts, "ends": [
            {**_coax_strip_sizes(config, side), "side": "А" if side == "A" else "В", "pinType": str(_coax(config).get(f"side{side}", {}).get("pinType") or ""),
             "shield": coax_end_shield(_coax(config).get(f"side{side}") or {})}
            for side in ("A", "B") if _coax(config).get(f"side{side}")]}
    return {**op, "fields": patched} if patched != dict(fields) else op


COAX_END_TYPES = frozenset({"coaxPin", "coaxPinCrimp", "coaxHousing", "coaxSolderShield", "coaxCapCrimp", "coaxCapScrew"})


def coax_sketch_fields(op: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """What the sketch of a coax operation draws (etkDraw, владелец 15.10 «эскизов нет»): an end operation — the strip
    sizes and connector of its end (`coaxEnd`); TUT cut — the piece length (`lengthKd`, also the «[L КД]» of the card);
    putting on / shrinking TUT and sleeves — the part and how many per item (`coaxParts`). Cut and strip keep theirs."""
    operation_type = str(op.get("type") or "")
    coax = _coax(config)
    if operation_type in COAX_END_TYPES:
        end = _coax_end(op, config)
        return {"coaxEnd": {**_coax_strip_sizes(config, "B" if op.get("side") == "B" else "A"),
                            "side": _side_label(op), "pinType": str(end.get("pinType") or ""),
                            "shield": coax_end_shield(end)}}
    if operation_type == "coaxCutTut":
        length = _format(coax.get("tutLength"))
        return {"lengthKd": f"{length}мм"} if length != "0" else {}
    parts = {
        "coaxInsTut": ("tube", coax.get("tutArt") or coax.get("tutName"), coax.get("tutLength")),
        "coaxHeat": ("tube", coax.get("tutArt") or coax.get("tutName"), coax.get("tutLength")),
        "coaxInsSleeve": ("sleeve", coax.get("sleeveArt") or coax.get("sleeveName"), ""),
        "coaxShield": ("sleeve", coax.get("sleeveArt") or coax.get("sleeveName"), ""),
    }.get(operation_type)
    if not parts:
        return {}
    kind, name, length = parts
    length_text = _format(length) if length else "0"
    clamp = [_side_label({"side": side}) for side in _list(op.get("clampSides"))] if operation_type == "coaxInsSleeve" else []
    return {"coaxParts": {
        "kind": kind, "name": str(name or "").strip(), "count": int(_coax_units(op)),
        "cable": str(coax.get("cableArt") or coax.get("cableName") or ""), "length": "" if length_text == "0" else length_text,
        **({"clampSides": clamp} if clamp else {}),
    }}


_PUT_ON = re.compile(r"Надеть обжимную гильзу на внешнюю оболочку кабеля\.?")
_SLIDE = re.compile(r"Сдвинуть гильзу по кабелю вслед за ТУТ\.?")
_ORIENT = re.compile(r"Визуально проверить правильность ориентации гильзы[^.]*\.?")


def _clamp_put_on_text(text: str, clamp: int, total: int) -> str:
    """«Надеть» с прижимным экраном (владелец 19.10): карта INS_SLEEVE_coax говорит о гильзе; на прижимной стороне надеваются гайка
    (резьбой к разъёму), шайба и уплотнитель — на всех сторонах (карта переписана), на части (к шагу добавлена оговорка)."""
    if clamp >= total:
        text = _PUT_ON.sub("Надеть на внешнюю оболочку кабеля гайку (резьбой в сторону разъёма), затем шайбу, уплотнитель и зажимное кольцо (конус) — "
                           "в порядке по КД.", text)
        text = _SLIDE.sub("Сдвинуть гайку, шайбу, уплотнитель и зажимное кольцо по кабелю вслед за ТУТ, за зону разделки.", text)
        return _ORIENT.sub("Визуально проверить порядок и ориентацию деталей (гайка резьбой к разъёму, конус узкой частью к торцу), отсутствие вмятин, "
                           "повреждений резьбы и загрязнений.", text)
    return _PUT_ON.sub("Надеть обжимную гильзу на внешнюю оболочку кабеля — только на обжимную сторону; на прижимной стороне вместо неё надеть гайку "
                       "(резьбой в сторону разъёма), шайбу, уплотнитель и зажимное кольцо (конус) в порядке по КД.", text)


HEX_MISSING = "HEX не задан — по чертежу разъёма"
_COLLET_PIN = re.compile(r"припаянн\w*\s+центральн\w*\s+контакт\w*", re.IGNORECASE)
_PLAIN_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")


SHIELD_CLAMP = "прижим"


def coax_end_shield(end: Mapping[str, Any]) -> str:
    """Тип экрана конца коакс-сборки словами генератора: «прижим» (зажим, clamp), «пайка», «компрессия» (компрессионное
    кольцо — как обжим, но без HEX), «накрутка», «самообжим» (Push-On) и «корпус» (экран зажимает корпус) — без операции по экрану, иначе «обжим» (прежний тип
    по умолчанию). Как `coaxShieldKind_` в tcGenLogic.ts."""
    text = str(end.get("shield") or "").casefold()
    if re.search(r"прижим|зажим|clamp", text):
        return SHIELD_CLAMP
    if re.search(r"пайк|паян|solder", text):
        return "пайка"
    if re.search(r"компресс|compress", text):
        return "компрессия"
    if re.search(r"накру[тч]|резьб|twist", text):
        return "накрутка"
    if re.search(r"самообжим|push-?on", text):
        return "самообжим"
    if re.search(r"корпус", text):
        return "корпус"  # микро-коакс (MHF, U.FL): экран зажимает корпус разъёма — без гильзы и HEX (владелец 19.10)
    return "обжим"


def coax_crimp_hex(config: Mapping[str, Any]) -> list[dict[str, str]]:
    """HEX матрицы обжима втулки экрана по концам коакс-сборки (владелец 16.10): `crimpHex` конца — из БД.КОАКС пары
    «разъём × кабель» (workflows.techcard_build), пусто — «HEX не задан — по чертежу разъёма», никогда не догадка."""
    coax = _coax(config)
    result = []
    for key, label in (("sideA", "А"), ("sideB", "В")):
        end = coax.get(key)
        if not isinstance(end, Mapping) or not str(end.get("article") or "").strip():
            continue
        if coax_end_shield(end) != "обжим":
            # HEX есть только у обжимного экрана: у прижимного и паяного нет гильзы под матрицу — значение в базе не читается (владелец 19.10).
            continue
        value = str(end.get("crimpHex") or "").strip()
        if value and _PLAIN_NUMBER.match(value):
            value += " мм"
        result.append({"side": label, "connector": str(end.get("article") or "").strip(), "hex": value})
    return result


_DEPTH_NOMINAL = re.compile(r"^(.*?)\s*\(номинал\)$")


def _depth_phrase(value: Any) -> str:
    """«0,38…1,14» → «0,38…1,14 мм»; «не менее 2,50» → «не менее 2,50 мм»; «9,14 (номинал)» → «9,14 мм (номинал)»
    (владелец 20.10: односторонний предел и номинал пишутся как есть, единица — после числа)."""
    text = str(value or "").strip()
    nominal = _DEPTH_NOMINAL.match(text)
    return f"{nominal.group(1)} мм (номинал)" if nominal else f"{text} мм"


def _vswr_scope(control: Mapping[str, Any] | None) -> str:
    """How many units of the batch the check measures, as the step says it; no setting — nothing (text as before)."""
    mode = control.get("mode") if isinstance(control, Mapping) else None
    if mode == "all":
        return "контроль сплошной — каждое изделие партии"
    if mode == "sample":
        every = control.get("every")
        return f"контроль выборочный: первое изделие партии и далее каждое {every}-е (1 из {every})"
    return ""


def vswr_step(norm: Mapping[str, Any], control: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """(text, tolerance) of the «Контроль КСВН» step from the assembly's `coaxVswrNorm`: «Измерить КСВН (S11) в полосе
    1…6000 МГц, сторона Б нагружена согласованной нагрузкой 50 Ом. Норма ≤ 2,06 (по комплектующим: SMA-C174P ≤1,43;
    SMA-C316F ≤1,41).» Norm not defined (no КСВ of a connector) or not computed ({} — no references) — the reason, and
    the decision is ОТК's; never a default norm. `control` (generator «Контроль КСВН») adds which units of the batch
    are measured: «…; контроль выборочный: первое изделие партии и далее каждое 50-е (1 из 50)»."""
    scope = _vswr_scope(control)
    load = "сторона Б нагружена согласованной нагрузкой 50 Ом" + (f"; {scope}" if scope else "")
    if not norm:
        return f"Измерить КСВН (S11), {load}; норма не рассчитана — нет данных справочников, решение принимает ОТК.", "решение ОТК"
    band = _list(norm.get("bandMhz")) + [None, None]
    start = _format(band[0] if band[0] is not None else 1, 2)
    band_text = (f"в полосе {start}…{_format(band[1], 2)} МГц" if band[1]
                 else f"в полосе от {start} МГц (верхняя граница не определена — нет «Частота до» у разъёмов и кабеля)")
    head = f"Измерить КСВН (S11) {band_text}, {load}"
    if norm.get("source") == "customer" and norm.get("vswrMax") is not None:
        # Норма заказчика (технолог, владелец 23.10) — приёмка по ней в её полосе; по комплектующим — справочно.
        maximum = _format(norm["vswrMax"], 3)
        basis = str(norm.get("basis") or "").strip()
        components = norm.get("components") if isinstance(norm.get("components"), Mapping) else {}
        if components.get("status") == "ok" and components.get("vswrMax") is not None:
            reference = f"по комплектующим ≤ {_format(components['vswrMax'], 2)} — справочно"
        else:
            reference = ("по комплектующим норма не определена"
                         + (f" ({components['reason']})" if components.get("reason") else "") + " — справочно")
        text = (f"{head}. Норма заказчика ≤ {maximum} в полосе {start}…{_format(band[1], 2)} МГц"
                + (f" ({basis})" if basis else "") + f"; {reference}.")
        return text, f"≤ {maximum}"
    if norm.get("status") != "ok" or norm.get("vswrMax") is None:
        reason = str(norm.get("reason") or "нет данных разъёмов")
        return f"{head}; норма не определена — {reason}, решение принимает ОТК.", "решение ОТК"
    maximum = _format(norm["vswrMax"], 2)
    parts = "; ".join(f"{end.get('article')} ≤{_format(end.get('vswr'), 2)}"
                      for end in _list(norm.get("ends")) if isinstance(end, Mapping))
    text = f"{head}. Норма ≤ {maximum}" + (f" (по комплектующим: {parts})" if parts else "") + "."
    if norm.get("s21Db") is not None and band[1]:
        text += f" Справочно: затухание S21 на {_format(band[1], 2)} МГц ≈ {_format(norm['s21Db'], 2)} дБ."
    return text, f"≤ {maximum}"


def _hex_phrase(items: list[Mapping[str, str]]) -> str:
    """«HEX 5,41 мм» — один размер на все концы; иначе по концам: «А (S-111F) — HEX 5,41 мм; В (N-111F) — HEX не
    задан — по чертежу разъёма»."""
    values = {item["hex"] for item in items}
    if len(values) <= 1:
        value = next(iter(values), "")
        return f"HEX {value}" if value else HEX_MISSING
    return "; ".join(f"{item['side']} ({item['connector']}) — " + (f"HEX {item['hex']}" if item["hex"] else HEX_MISSING)
                     for item in items)


def _coax_machine(op: Mapping[str, Any]) -> dict[str, str]:
    """The rotary machine the technologist chose for cutting coax with stripping (generator, owner 14.10): model,
    inventory number and name from БД.ОБ; {} — no choice (the card's equipment stays as it is)."""
    machine = op.get("machine")
    if op.get("type") != "coaxCut" or not isinstance(machine, Mapping) or not str(machine.get("model") or "").strip():
        return {}
    return {key: str(machine.get(key) or "").strip() for key in ("model", "inventory", "name")}


# A rotary cut-and-strip machine in a card's equipment («BZW-886+Q2 | Станок резки провода с роторной зачисткой мал.»).
_ROTARY_MACHINE = re.compile(r"роторн", re.IGNORECASE)


def _machine_tooling(tooling: list[dict[str, Any]], machine: Mapping[str, str]) -> list[dict[str, Any]]:
    """The chosen machine wins over the card's list: of the rotary machines in «Оборудование» only the chosen one stays
    (the card CUT_COAX+…strip lists both, «мал.» and «больш.»); a card without it gets it in place of its rotary machine
    (or at the end of «Оборудование»). The equipment rows are renumbered."""
    model = machine["model"].casefold()
    own = lambda tool: tool["section"] == "Оборудование" and (
        model in tool["name"].casefold() or (machine["inventory"] and tool["inventory_number"] == machine["inventory"]))
    rotary = lambda tool: tool["section"] == "Оборудование" and bool(_ROTARY_MACHINE.search(tool["name"]))
    chosen = {
        "section": "Оборудование", "number": "", "name": " | ".join(part for part in (machine["model"], machine["name"]) if part),
        "inventory_number": machine["inventory"], "program": "", "parameters": "", "norm": "", "unit": "",
    }
    has_own = any(own(tool) for tool in tooling)
    result: list[dict[str, Any]] = []
    placed = has_own
    for tool in tooling:
        if own(tool) or not rotary(tool):
            result.append(tool)
        elif not placed:
            result.append({**chosen, "program": tool["program"], "parameters": tool["parameters"]})
            placed = True
    if not placed:
        last = max((index for index, tool in enumerate(result) if tool["section"] == "Оборудование"), default=len(result) - 1)
        result.insert(last + 1, chosen)
    number = 0
    for index, tool in enumerate(result):
        if tool["section"] == "Оборудование":
            number += 1
            result[index] = {**tool, "number": str(number)}
    return result


def _fields(op: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    operation_type = str(op.get("type") or "")
    fields: dict[str, Any] = {}
    if operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        cut_wires = _wires(op, config)
        fields["cutTolerance"] = _cut_tolerance(op, [length for length, _ in _cut_lengths(cut_wires)])
        fields["lengthKd"] = _length_kd(cut_wires)
    strip = config.get("strip") if operation_type == "cutWire" else op.get("strip") if operation_type in {"cutWireStrip", "cutWireCrimp", "stripCable"} else None
    if isinstance(strip, Mapping):
        fields["strip"] = {"a": bool(strip.get("a")), "b": bool(strip.get("b")), "lenA": strip.get("lenA") or "", "lenB": strip.get("lenB") or ""}
        # Кабель в оболочке (владелец 15.10): кроме оболочки (lenA/lenB) зачищаются жилы — под обжим или пайку.
        for key in ("coreA", "coreB"):
            if key in strip:
                fields["strip"][key] = str(strip.get(key) or "")
        if operation_type == "cutWireCrimp" and op.get("tin"):
            # Свободный конец станок зачищает и лудит: без своей длины у операции — длина зачистки под лужение.
            end = "a" if op.get("side") == "B" else "b"
            fields["strip"][end] = True
            fields["strip"][f"len{end.upper()}"] = fields["strip"][f"len{end.upper()}"] or _tin_strip_length(config, end)
    if operation_type == "twist":
        twist = config.get("twist") if isinstance(config.get("twist"), Mapping) else {}
        wires = config.get("wires") or []
        pairs = []
        # Свивка ветви или группы сростков (генератор, 17.10) — только свои пары (`twistPairs`), шаг — общий, формы.
        own = op.get("twistPairs")
        for index, pair in enumerate(own if isinstance(own, list) and own else twist.get("pairs") or []):
            names = [str(wires[item].get("art") or wires[item].get("name") or "") for item in pair if 0 <= item < len(wires)]
            if names:
                pairs.append(f"{index + 1}) {' + '.join(names)}")
        fields["twist"] = {"pitch": twist.get("pitch") or "", "pairs": "\n".join(pairs), "count": len(pairs) or 1}
    if operation_type == "coaxCut":
        # Длина реза кабеля (с припуском) — в «Общие» шапки, допуск — по ней же, как у провода.
        coax_data = config.get("coax") if isinstance(config.get("coax"), Mapping) else {}
        length = _format(coax_data.get("cableLength"))
        if length != "0":
            fields["lengthKd"] = f"{length}мм"
            fields["cutTolerance"] = _cut_tolerance(op, [_decimal(length)])
    if operation_type == "coaxStrip":
        # Размеры разделки стороны для эскиза etkDrawCoax_: L1–L3 — разъёма (БД.КОАКС), D1–D3 — кабеля (СПР.КАБ).
        fields["coax"] = _coax_strip_sizes(config, "B" if op.get("side") == "B" else "A")
    if operation_type == "coaxCut" and isinstance(op.get("stripSides"), list):
        # Резка с разделкой на роторном станке (владелец 14.10): размеры разделки обоих концов — в эскиз операции;
        # голый конец без разъёма размеров не имеет.
        fields["coax"] = _coax_strip_sizes(config, "A")
        if "B" in op["stripSides"]:
            side_b = _coax_strip_sizes(config, "B")
            if side_b["connector"]:
                fields["coaxB"] = side_b
    fields.update(coax_sketch_fields(op, config))
    if operation_type == "coaxShield":
        hexes = coax_crimp_hex(config)
        if not hexes and any(coax_end_shield(end) == "компрессия" for end in (_coax(config).get(k) for k in ("sideA", "sideB")) if isinstance(end, Mapping)):
            fields["compression"] = True  # только компрессионные разъёмы: матрицы HEX нет, ЭТК говорит про компрессионный инструмент
        else:
            fields["crimpHex"] = hexes
    if operation_type == "coaxHousing" and (depth := str(_coax_end(op, config).get("contactDepth") or "").strip()):
        fields["contactDepth"] = depth  # заглубление контакта от торца корпуса (БД.КОАКС) — в шаг «Контроль» карты
    if operation_type == "coaxVswr":
        # Норма КСВН по комплектующим (владелец 22.10, справочники дописывают `coax.vswrNorm`); {} — не рассчитана.
        norm = _coax(config).get("vswrNorm")
        fields["coaxVswrNorm"] = deepcopy(dict(norm)) if isinstance(norm, Mapping) else {}
        control = vswr_control(_coax(config).get("vswrControl"))
        if control:
            fields["coaxVswrControl"] = control  # сплошной или выборочный контроль (генератор, владелец 22.10)
    if operation_type == "coaxHousing" and "цанг" in str(_coax_end(op, config).get("pinType") or "").casefold():
        # Цанговый центральный контакт (владелец 16.10): пайки пина не было — карта корпуса не говорит «припаянный».
        fields["collet"] = True
    machine = _coax_machine(op)
    if machine:
        fields["machine"] = machine
    if operation_type.startswith("coax") and op.get("noTooling"):
        fields["noTooling"] = True
    if operation_type == "insMark" and _kd_marks(_mark_items(op)):
        fields["marks"] = _mark_rows(op, config)
    wires = _wires(op, config)
    if _kind(op) or operation_type in {"cutWire", "cutWireStrip", "cutWireCrimp"}:
        pin_values = op.get("pins") if isinstance(op.get("pins"), list) else []
        fields["wires"] = [{
            "name": str(wire.get("name") or ""), "art": str(wire.get("art") or ""),
            "qty": float(_number(wire.get("qty"), Decimal(1))), "length": wire.get("length") or "",
            "pins": _pins(pin_values[index]) if _kind(op) and index < len(pin_values) else [],
            **{key: float(wire[key]) for key in ("outerDiameter", "conductorDiameter") if _is_mm(wire.get(key))},
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
    # Обе стороны с разными терминалами: станку нужны оба аппликатора, нормы — по сторонам. Один терминал — нормы те же.
    terminal_norm_b = op.get("terminalNormB")
    if isinstance(terminal_norm_b, Mapping) and _endpoint_b(op) and _terminal(_side_b_op(op)) != _terminal(op):
        for key in ("applicator", "crimpHeight", "pullForce"):
            value_a, value_b = str(fields.get(key) or ""), str(terminal_norm_b.get(key) or "")
            if key == "applicator" and value_b and value_b != value_a:
                fields[key] = f"{value_a}/{value_b}"
            elif key != "applicator" and (value_a or value_b) and value_b != value_a:
                fields[key] = f"{value_a or NOT_SET} (А) / {value_b or NOT_SET} (В)"
    crimp_ends = _crimp_ends(op, fields, wires)
    if crimp_ends:
        fields["crimpEnds"] = crimp_ends
    return fields


NOT_SET = "не задано"
# references.technology NO_DATA_MARK: the terminal is not in БД.ТЕР — on a contact's spec line that is «не задано».
_NO_DATA = "⚠ НЕТ ДАННЫХ"


def _wire_group(labels: Sequence[str]) -> str:
    """One name for the wires a contact crimps: the wire itself, or the words the colours of one grade share
    («НВ4 0,2 белый», «НВ4 0,2 зелёный» → «НВ4 0,2»); nothing in common — «N проводов»."""
    names = list(dict.fromkeys(label.strip() for label in labels if label.strip()))
    if len(names) <= 1:
        return names[0] if names else ""
    common: list[str] = []
    for column in zip(*(name.split() for name in names)):
        if any(word != column[0] for word in column):
            break
        common.append(column[0])
    return " ".join(common) or _wires_text(len(names))


def _crimp_ends(
    op: Mapping[str, Any], fields: Mapping[str, Any], wire_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Crimp spec of every contact the operation crimps, per end and wire section — for the contact's line in
    «Комплектующие» and the per-end header chips: ends A/B (one entry for both when the contact and its norms match),
    wires and section, applicator, crimp height, pull force, strip. An empty value is not in БД.ТЕР («не задано»)."""
    operation_type = str(op.get("type") or "")
    if operation_type not in {"prsTerm", "cutWireCrimp"}:
        return []
    sides = [("B" if op.get("side") == "B" else "A", op, op.get("terminalNorm"))]
    if _endpoint_b(op):
        sides.append(("B", _side_b_op(op), op.get("terminalNormB")))
    if not any(isinstance(norm, Mapping) for _, _, norm in sides):
        return []
    wires = [str(wire.get("art") or wire.get("name") or "") for wire in wire_rows]
    strip = fields.get("strip") if isinstance(fields.get("strip"), Mapping) else {}
    entries: list[dict[str, Any]] = []
    for end, side_op, norm in sides:
        art = _terminal(side_op)
        if not art:
            continue
        norm = norm if isinstance(norm, Mapping) else {}
        rows = [item for item in norm.get("bySection") or [] if isinstance(item, Mapping)]
        machine_strip = str(strip.get(f"len{end}") or "").strip() if strip.get(end.lower()) else ""
        common_applicator = str(norm.get("applicator") or "")
        common_applicator = "" if common_applicator == _NO_DATA else common_applicator
        for item in rows or [None]:
            row = item or {}
            applicator = str(row.get("applicator") or common_applicator)
            if operation_type == "cutWireCrimp" and applicator:
                applicator = _machine_applicator(applicator)
            listed = row.get("wires")
            section_wires = listed if isinstance(listed, list) else wires if len(rows) <= 1 else []
            entries.append({
                "ends": [end], "art": art, "name": str(_endpoint(side_op).get("termName") or ""),
                "wire": _wire_group([str(value) for value in section_wires]), "section": str(row.get("section") or ""),
                "applicator": applicator,
                "crimpHeight": str((row if item else norm).get("crimpHeight") or ""),
                "pullForce": str((row if item else norm).get("pullForce") or ""),
                "strip": machine_strip or str(row.get("strip") or ""),
            })
    merged: list[dict[str, Any]] = []
    for entry in entries:
        twin = next((item for item in merged if {**item, "ends": [], "name": ""} == {**entry, "ends": [], "name": ""}), None)
        if twin:
            twin["ends"].extend(end for end in entry["ends"] if end not in twin["ends"])
        else:
            merged.append(entry)
    return merged


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
    endpoint_b = _endpoint_b(op)
    if endpoint_b:
        side_b = {**endpoint_side, **{key: endpoint_b.get(key) or "" for key in ("termName", "termArt", "connName", "connArt")}}
    values = {
        "index": config.get("assemblyIndex") or "", "name": config.get("assemblyName") or "",
        "wire_name": wire_data.get("name") or "", "wire_art": wire_data.get("art") or "",
        "wire_qty": wire_data.get("qty") or "", "length": wire_data.get("length") or "",
        "semifinished": previous, "result": result, "op_num": op.get("opNum") or "",
        "t_prep": op.get("tPrep") or "", "t_op": op.get("tOp") or "", "t_machine": op.get("tMachine") or "",
        # «[L КД] мм» в шаблоне: число без своей единицы, иначе «20мм мм».
        "tolerance": fields.get("cutTolerance") or "", "length_kd": re.sub(r"\s*мм$", "", str(fields.get("lengthKd") or "")),
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


_DEFECT_CRITERIA = re.compile(r"^критерии, метод контроля", re.IGNORECASE)


def _combined_defects(code: str, defects: str, cards_by_code: Mapping[str, OperationCardTemplate]) -> str:
    parts = [defects.rstrip()] if defects.strip() else []
    for pattern, component, label in COMBINED_DEFECTS:
        card = cards_by_code.get(component)
        if not pattern.search(code) or card is None or not card.defects.strip():
            continue
        extra = _labelled_defects(card.defects, label)
        if extra:
            parts.append(extra)
    if len(parts) < 2:
        return "\n".join(parts)
    # Every СТО card ends with «Критерии, метод контроля и действие — по СТО…»: a combined card says it once, at the end
    # (owner 24.10: no repeats in the ETK).
    lines, criteria = [], []
    for line in "\n".join(parts).split("\n"):
        if _DEFECT_CRITERIA.match(line.strip()):
            if line.strip() not in criteria:
                criteria.append(line.strip())
            continue
        lines.append(line)
    return "\n".join(lines + criteria)


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
    depth_checked = vswr_checked = False
    parts_fields = fields.get("coaxParts") if isinstance(fields.get("coaxParts"), Mapping) else {}
    for step in card.steps:
        text = substitute(step.text)
        if parts_fields.get("clampSides"):
            text = _clamp_put_on_text(text, len(parts_fields["clampSides"]), int(parts_fields.get("count") or 0))
        tolerance = substitute(step.tolerance)
        if step.kind == "Контроль" and tolerance in {"", "-", "—"}:
            lowered = text.casefold()
            if fields.get("crimpHeight") is not None and re.search(r"datasheet|высота обжима|геометр", lowered):
                tolerance = f"{fields.get('crimpHeight')} мм".strip()
            elif fields.get("pullForce") is not None and re.search(r"pull[\s-]?test|разрыв|усилие обрыва", lowered):
                tolerance = f"{fields.get('pullForce')} Н".strip()
        if re.search(r"тестовый\s*рез", text.casefold()) and tolerance.strip() in {"", "-", "—"}:
            tolerance = str(fields.get("cutTolerance") or "")
        if tolerance.strip() == "мм":
            # «[L КД] мм» без длины в генераторе — единица без числа не допуск.
            tolerance = "по КД"
        if fields.get("collet"):
            text = _COLLET_PIN.sub("центральный контакт (цанговый, без пайки)", text)
        if fields.get("compression") and step.kind == "Контроль" and "hex" in text.casefold():
            text = f"{text.rstrip()} Компрессионный разъём: компрессионный инструмент, матрица HEX не применяется."
        if isinstance(fields.get("crimpHex"), list) and step.kind == "Контроль" and "hex" in text.casefold():
            # Обжим втулки экрана: размер HEX по разъёму и кабелю из БД.КОАКС, нет — явно «не задан».
            text = f"{text.rstrip()} Размер: {_hex_phrase(fields['crimpHex'])}."
        if fields.get("contactDepth") and step.kind == "Контроль" and not depth_checked:
            # Монтаж корпуса: заглубление контакта от торца корпуса из БД.КОАКС (владелец 19.10) — в первый шаг контроля.
            text = f"{text.rstrip()} Проверить заглубление контакта от торца корпуса: {_depth_phrase(fields['contactDepth'])}."
            depth_checked = True
        if isinstance(fields.get("coaxVswrNorm"), Mapping) and step.kind == "Контроль" and not vswr_checked \
                and "ксвн" in text.casefold():
            # Контроль КСВН: полоса и норма по комплектующим, не общая «≤1,3» (владелец 22.10).
            text, tolerance = vswr_step(fields["coaxVswrNorm"], fields.get("coaxVswrControl"))
            vswr_checked = True
        steps.append({"number": step.number, "kind": step.kind, "text": text, "tolerance": "" if tolerance in {"-", "—"} else tolerance})
    tooling = []
    applicator_set = False
    for tool in card.tooling:
        program = substitute(tool.program)
        free_program = not program or (fields.get("cutCrimp") and program in {"-", "—"})
        if not applicator_set and fields.get("applicator") and tool.section == "Оборудование" and free_program:
            program = str(fields["applicator"])
            applicator_set = True
        name = substitute(tool.name)
        if isinstance(fields.get("crimpHex"), list) and tool.section == "Матрицы":
            name = f"{name.rstrip()} — {_hex_phrase(fields['crimpHex'])}"
        tooling.append({
            "section": tool.section, "number": tool.number, "name": name,
            "inventory_number": tool.inventory_number, "program": program,
            "parameters": substitute(tool.parameters), "norm": tool.norm, "unit": tool.unit,
        })
    if isinstance(fields.get("machine"), Mapping):
        tooling = _machine_tooling(tooling, fields["machine"])
    if fields.get("noTooling"):
        # Оснастка операции не определена (владелец 25.10, FAKRA 708К): строк оснастки нет — ни карты, ни подсказок шагов.
        return {"name": card.name, "safety": card.safety, "defects": _combined_defects(card.code, card.defects, cards_by_code or {}),
                "steps": steps, "tooling": []}
    step_text =" ".join(step["text"] for step in steps)
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


def _typed_fields(op: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    fields = _fields(op, config)
    if op.get("type") == "cutWireCrimp":
        fields["cutCrimp"] = True
        if fields.get("applicator"):
            fields["applicator"] = _machine_applicator(str(fields["applicator"]))
    return fields


def _card_warning(op: Mapping[str, Any], position: int, code: str) -> str:
    missing_card = code or op.get("templateId") or "шаблон не выбран"
    return f"Операция {position} ({op.get('type') or ''}): нет карточки {missing_card}"


def _terminal_warning(op: Mapping[str, Any], position: int, code: str) -> str | None:
    messages = []
    for norm_key, side_op in (("terminalNorm", op), ("terminalNormB", _side_b_op(op))):
        terminal_norm = op.get(norm_key)
        if not isinstance(terminal_norm, Mapping) or (norm_key == "terminalNormB" and not _endpoint_b(op)):
            continue
        missing_sections = [
            str(item.get("section") or "сечение не определено")
            for item in terminal_norm.get("bySection") or []
            if isinstance(item, Mapping) and not item.get("found")
        ]
        terminal = _terminal(side_op)
        if missing_sections and not any(terminal in message for message in messages):
            messages.append(f"Операция {position} ({code}): нет нормы терминала {terminal} для {', '.join(missing_sections)}")
    return "; ".join(messages) or None


def _typed_connector(op: Mapping[str, Any], fallback: object) -> str:
    """The connector part of the ETK operation key: the sheet suffix, else the side, else `fallback`. An operation of a
    «Сложная сборка» ПФ (generator, 16.10: put the tubes on a branch, twist it, splice a group) names it (`branch`:
    «А1 «5»», «С1, С2») — four branch put-ons are told apart by it, not by their position."""
    branch = str(op.get("branch") or "").strip()
    suffix = " ".join(part for part in (_suffix(op), branch) if part)
    return suffix or str(op.get("side") or fallback)


def _typed_operation(
    op: Mapping[str, Any], config: Mapping[str, Any], *, position: int, code: str,
    card: OperationCardTemplate | None, cards_by_code: Mapping[str, OperationCardTemplate],
    previous: str, result: str, single: str, is_last: bool, semis: list[dict[str, str]], connector: str,
    warnings: list[str], result_rows: list[dict[str, str]] | None = None, manual: bool = False,
) -> dict[str, Any]:
    """One ETK operation from a generator operation (cut, crimp, mount, TUT, coax…): its card with the placeholders
    filled, kit lines, result rows, time and the fields of its sketch. `previous` / `result` — the product names of the
    generator chain (card placeholders), `single` — the name of the one piece it hands on, `semis` — its input,
    `result_rows` — its result rows when the chain set them (a ПФ of the generator's scheme)."""
    wire_data = _wire_data(op, config)
    fields = _typed_fields(op, config)
    terminal_warning = _terminal_warning(op, position, code)
    if terminal_warning:
        warnings.append(terminal_warning)
    replacements = _placeholder_map(op, config, previous, result, wire_data, fields)
    operation = {
        "position": position, "code": code, "type": str(op.get("type") or ""),
        "side": str(op.get("side") or ""), "connector": connector,
        "connector_article": str(_endpoint(op).get("connArt") or _endpoint(op).get("connName") or ""),
        "template_id": str(op.get("templateId") or ""), "title": card.title if card else "",
        "card": _resolve_card(card, replacements, fields, cards_by_code) if card else None,
        "components": _components(op, config, code, manual=manual), "semis": semis,
        "result": _result_rows(op, config, result, is_last, single, manual=manual) if result_rows is None else result_rows,
        "result_label": "Изделие" if is_last else "Полуфабрикат",
        "time": _time(op, config, single),
        "times": _times(op),
        "fields": fields,
    }
    if op.get("type") == "insMark" and _kd_marks(_mark_items(op)):
        # Маркировка по КД: ход операции, дефекты, оборудование и время — по видам меток (etk_marking).
        apply_marking(operation, op, _mark_items(op), _part_quantity(config), warnings)
    return operation


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
    return operation_type not in {"cutTut", "coaxCutTut", "endCap", "foldCore", "stripCable"} and not (
        operation_type == "insTut" and not op.get("harness") and not _mark_tube(op)
    )


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _chain_results(config: Mapping[str, Any]) -> list[str]:
    """The product name every generator operation hands on, replaying build_etk's previous_result chain (along the
    generator's own ПФ scheme when it drew one)."""
    scheme = _scheme_chain([op for op in _list(config.get("ops")) if isinstance(op, Mapping)], config)
    if scheme is not None:
        return [link.result for link in scheme]
    chain: list[Mapping[str, Any]] = []
    names: list[str] = []
    for op in _list(config.get("ops")):
        if not isinstance(op, Mapping):
            continue
        names.append(_result_name(op, config, "", [*chain, op]))
        if _advances_chain(op):
            chain.append(op)
    return names


def _row_minutes(operation: Mapping[str, Any]) -> Decimal | None:
    """Σ of the operation's ETK time rows, minutes per batch; None — it has no time rows."""
    rows = [row for row in _list(operation.get("time")) if isinstance(row, Mapping)]
    return sum((_decimal(row.get("norm")) for row in rows), Decimal(0)) if rows else None


def _operation_minutes(operation: Mapping[str, Any], quantity: Decimal) -> Decimal:
    """The ETK time norm of the operation, minutes per batch; an operation without time rows (moved from SUPPLYMAP)
    counts its seconds per unit × batch (no setup, ETK_VERSION 4)."""
    minutes = _row_minutes(operation)
    if minutes is not None:
        return minutes
    times = operation.get("times") if isinstance(operation.get("times"), Mapping) else {}
    return _decimal(times.get("operation_seconds")) * quantity / 60


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


def norms_include_setup(document: Mapping[str, Any] | None) -> bool:
    """An ETK built before ETK_VERSION 4 has the card's setup (tPrep) inside its time norms."""
    return isinstance(document, Mapping) and int(_decimal(document.get("schema_version"))) < 4


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
            side=str(source.get("side") or operation.get("side") or ""), key=str(source.get("opKey") or ""),
        ))
    return ops, str(config.get("assemblyName") or kit.get("name") or ""), quantity


# Вкладки генератора (13.10): типовые — «Межплатная» и «КОАКС» — режут ЭТК на ПФ сами (domain/pf_split.py);
# «Ручная сборка» — свои блоки. «Сложная сборка» с 16.10 тоже (владелец, 713К.4 КБ1: «нам нужно понимать иерархию» —
# резки одним ПФ, а не ПФ на операцию); правка — в «Править ПФ».
TYPICAL_TABS = frozenset({"inter", "coax"})
COMPLEX_TAB = "complex"
SPLIT_TABS = TYPICAL_TABS | {COMPLEX_TAB}
# Операции, которые генератор ставит только на «Сложной сборке» (сростки, герметизация, складывание жилы).
_COMPLEX_TYPES = frozenset({"splice", "seal", "foldCore"})


def generator_tab(config: Mapping[str, Any] | None) -> str:
    """The generator tab a build config came from: «manual», «coax», «inter» or «complex»; "" — no config (an ETK
    moved from SUPPLYMAP). The generator says it (`tab`, since 13.10); an older config is told by what only «Сложная
    сборка» writes: a big-assembly flag, splices, sealing, a folded core, a connector label."""
    if not isinstance(config, Mapping) or not config:
        return ""
    if config.get("mode") == MANUAL_MODE:
        return MANUAL_MODE
    tab = str(config.get("tab") or "").strip()
    if tab in TYPICAL_TABS or tab == COMPLEX_TAB:
        return tab
    if config.get("mode") == "coax":
        return "coax"
    ops = [item for item in _list(config.get("ops")) if isinstance(item, Mapping)]
    endpoints = [item.get(name) for item in ops for name in ("ep", "epB") if isinstance(item.get(name), Mapping)]
    if (config.get("bigAssembly") or any(item.get("type") in _COMPLEX_TYPES for item in ops)
            or any(str(end.get("label") or "").strip() or end.get("fold") for end in endpoints)):
        return COMPLEX_TAB
    return "inter"


def _generated_split(ops: Sequence[RouteOp], final_name: str, current: Route | None = None) -> Route | None:
    try:
        return generator_split(ops, final_name, current)
    except (RouteError, KeyError, ValueError):
        return None


def _route(
    document: Mapping[str, Any], *, strict: bool, warnings: list[str] | None = None,
) -> dict[str, Any] | None:
    """`document.route` of an assembled document. strict (the build): a hand-edited `build_config.route` must be
    valid — an error stops the build. Otherwise (the read path) the first scheme that fits wins, then the derived one.

    A config scheme the generator drew itself (`build_config.route.source` «generated», «Сложная сборка» 16.10) is
    checked the same way but stored as generated, so a later hand edit («Сохранить ПФ») wins over it on a rebuild; one
    that does not fit the operations does not stop the build — the generator's ПФ rule goes instead, with a warning."""
    ops, final_name, quantity = _route_ops(document, strict=strict)
    if not ops:
        return None
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    stored = document.get("route") if isinstance(document.get("route"), Mapping) else {}
    drawn = config.get("route")
    drawn_generated = isinstance(drawn, Mapping) and drawn.get("source") == "generated"
    candidates = [(drawn, True)] if strict else [
        (stored, bool(stored) and stored.get("source") != "derived"),
        (drawn, True),
    ]
    route = None
    failed: RouteError | None = None
    for raw, manual in candidates:
        if not raw:
            continue
        try:
            route = parse_route(raw, ops, final_name, manual=manual)
            if (raw is stored and stored.get("source") == "generated") or (raw is drawn and drawn_generated):
                route = _replace(route, generated=True)
            break
        except RouteError as error:
            if strict and not drawn_generated:
                raise
            failed = error
    # A generator tab cuts its ETK into ПФ by itself (owner 13.10, «Сложная сборка» since 16.10) — only a config that
    # says its tab, so a rebuild of an old config keeps its scheme until the generator builds it anew.
    if route is None and strict and (drawn_generated or str(config.get("tab") or "").strip() in SPLIT_TABS):
        route = _generated_split(ops, final_name)
        if failed is not None and warnings is not None:
            warnings.append(f"{failed} — схема генератора не подошла к операциям ЭТК, "
                            + ("ПФ разбиты по правилу генератора" if route else "ПФ — по операции"))
    route = route or derive_route(ops, final_name)
    return route_json(route, route_totals(route, {op.position: op.minutes for op in ops}, quantity))


def propose_route(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """«Предложить разбивку на ПФ»: the ПФ split the ETK should have — «Ручная сборка» its own blocks
    (`build_config.route`, else the chain of its list), any other ETK the generator's rule over its operations and
    current scheme. {route, same, rule}: `same` — the ETK is already cut so; nothing is saved. None — no operations."""
    if not isinstance(document, Mapping):
        return None
    ops, final_name, quantity = _route_ops(document, strict=False)
    if not ops:
        return None
    current_json = document_route(document)
    current = parse_route(current_json, ops, final_name, manual=True) if current_json else None
    config = document.get("build_config") if isinstance(document.get("build_config"), Mapping) else {}
    route: Route | None = None
    rule = "generator"
    if generator_tab(config) == MANUAL_MODE:
        rule = "blocks"
        try:
            route = parse_route(config["route"], ops, final_name, manual=True) if config.get("route") else chain_route(ops, final_name)
        except RouteError:
            route = None
    if route is None:
        rule = "generator"
        route = _generated_split(ops, final_name, current) or derive_route(ops, final_name)
    # A scheme still «one ПФ per operation» (`derived`) is not cut into ПФ even when the split comes out the same
    # (cut, crimp A, side A, crimp B, side B — a ПФ per operation): the proposal names the ПФ and launches by them.
    return {
        "route": route_json(route, route_totals(route, {op.position: op.minutes for op in ops}, quantity)),
        "same": same_split(route, current) and (current_json or {}).get("source") != "derived",
        "rule": rule,
    }


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
    "coaxCut", "coaxCutTut", "coaxStrip", "coaxPin", "coaxPinCrimp", "coaxShield", "coaxSolderShield", "coaxInsSleeve", "coaxInsTut",
    "coaxHeat", "coaxHousing", "coaxCapCrimp", "coaxCapScrew", "coaxVswr",
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
    chain: list[Mapping[str, Any]] = []  # the generator operations behind `previous` (the state it is folded from)
    seed = ""  # the hand-written product the chain started from
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
            written = _manual_text(op.get("result"))
            product = written or _result_name(op, config, previous, [*chain, op], seed)
            if _advances_chain(op):
                previous = product
                chain, seed = ([], written) if written else ([*chain, op], seed)
        else:
            chain_previous = ""
            product = previous = _manual_text(op.get("result")) or f"Полуфабрикат после «{label}»"
            chain, seed = [], product
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
        connector=_typed_connector(item.source, seq), warnings=warnings, manual=True,
    )
    if operation["card"] is not None and _manual_text(item.source.get("label")):
        operation["card"]["name"] = item.label
    operation["components"].extend(_manual_components(item, _part_quantity(config)))
    for key in ("params", "note"):
        if _manual_text(item.source.get(key)):
            operation["fields"][key] = _manual_text(item.source.get(key))
    return operation


def _etk_key(operation: Mapping[str, Any]) -> tuple[str, str, str]:
    """The ETK operation key (project_cards.operation_key): code, connector, side."""
    code, connector, side = (str(operation.get(field) or "").strip() for field in ("code", "connector", "side"))
    return code, connector, side


def _distinct_typed_keys(built: list[dict[str, Any]]) -> None:
    """Generator operations of one card, connector words and side (two cuts of one tube at different lengths, the same
    shrink on two connectors of a side) would share the ETK key and the build would be refused: the second and later
    ones get their ETK number on the connector. The first keeps its key, so a unique operation never changes."""
    taken: set[tuple[str, str, str]] = set()
    for operation in built:
        key = _etk_key(operation)
        if key in taken:
            code, connector, side = key
            connector = f"{connector} · {operation['position']}" if connector else str(operation["position"])
            while (code, connector, side) in taken:
                connector = f"{connector} · {operation['position']}"
            operation["connector"] = connector
            key = (code, connector, side)
        taken.add(key)


def _distinct_generator_keys(items: Sequence[_ManualOp], built: list[dict[str, Any]], seqs: Sequence[str]) -> None:
    """Generator operations of one card, connector and side in a manual scheme — a scheme from the harness drawing has
    them per route block (X1 crimped in two blocks, a strip cut per block) — would share the ETK key. Only those get a
    tail on the connector: the operation's own connector when that tells them apart, else its `seq`. An operation whose
    key is unique keeps it as it was, so saved techcards and issued ETKs keep their keys."""
    counts = Counter(_etk_key(operation) for operation in built)
    groups: dict[tuple[str, str, str], list[tuple[_ManualOp, dict[str, Any]]]] = defaultdict(list)
    for item, operation in zip(items, built):
        if item.generator and counts[_etk_key(operation)] > 1:
            groups[_etk_key(operation)].append((item, operation))
    taken = set(counts)
    for (code, base, side), members in groups.items():
        own = [str(_endpoint(item.source).get("connName") or _endpoint(item.source).get("connArt") or "").strip()
               for item, _operation in members]
        by_connector = all(name and name != base for name in own) and len(set(own)) == len(own)
        for (item, operation), name in zip(members, own):
            tail = name if by_connector else seqs[item.position - 1]
            connector = f"{base} · {tail}" if base else tail
            while (code, connector, side) in taken:
                connector = f"{connector} · {item.position}"
            taken.add((code, connector, side))
            operation["connector"] = connector


def _block_products(
    items: Sequence[_ManualOp], route: Route, raw_route: Mapping[str, Any], config: Mapping[str, Any], final_name: str,
) -> list[_ManualOp]:
    """The generator operations' product names (and the product they take) along the block scheme: a block starts from
    the name of the block feeding it — the one that moved a product on, a tube cut only feeds its tubes — and folds its
    own operations onto it. An explicit `result` still wins; the other operations name as in `_manual_ops`."""
    by_position = {item.position: item for item in items}
    by_key = {block.key: block for block in route.blocks}
    named = {str(block.get("key") or "").strip() for block in raw_route.get("blocks") or []
             if isinstance(block, Mapping) and str(block.get("name") or "").strip()}
    feeders: dict[str, list[str]] = defaultdict(list)
    for edge in route.edges:
        feeders[edge.target].append(edge.source)
    done: dict[int, _ManualOp] = {}
    ends: dict[str, tuple[str, bool]] = {}  # block key → (what it hands on: its name, did it move a product on)

    def end_of(key: str) -> tuple[str, bool]:
        if key not in ends:
            ends[key] = walk(by_key[key])
        return ends[key]

    def walk(block: Block) -> tuple[str, bool]:
        fed = [end_of(source) for source in feeders[block.key]]
        previous = next((name for name, moved in fed if moved), fed[0][0] if fed else "")
        chain: list[Mapping[str, Any]] = []
        seed, moved = previous, False
        for position in sorted(block.ops):
            item = by_position[position]
            op = item.source
            written = _manual_text(op.get("result"))
            if item.generator:
                before = previous
                product = written or _result_name(op, config, previous, [*chain, op], seed)
                if _advances_chain(op):
                    previous, moved = product, True
                    chain, seed = ([], written) if written else ([*chain, op], seed)
                done[position] = _replace(item, product=product, previous=before)
            else:
                product = previous = written or f"Полуфабрикат после «{item.label}»"
                chain, seed, moved = [], product, True
                done[position] = _replace(item, product=product, previous="")
        if block.key in named:
            return block.name, moved
        return (final_name if block.final else done[sorted(block.ops)[-1]].product), moved

    for block in route.blocks:
        end_of(block.key)
    return [done[item.position] for item in items]


def _build_manual(
    config: Mapping[str, Any], cards: Sequence[OperationCardTemplate], *,
    operation_library_commit: str = "", technology_source: Mapping[str, Any] | None = None,
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
    if raw_route:
        # A scheme of blocks: the default product name of an operation comes from its own block and the block that
        # feeds it, not from the operation before it in the list (that one can sit in another branch).
        items = _block_products(items, route, raw_route, config, final_name)
        route_ops = [RouteOp(position=item.position, type=item.kind, code=item.code, name=item.product) for item in items]
        route = parse_route(raw_route, route_ops, final_name, manual=True)

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
        norm = item.operation_seconds * item.quantity * quantity / 60
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

    _distinct_generator_keys(items, built, seqs)
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
            **({"operation_library_commit": operation_library_commit} if operation_library_commit else {}),
            **({"technology_snapshot": deepcopy(dict(technology_source))} if technology_source else {}),
        },
    }
    # Totals from the ETK time rows, as the read path (`document_route`) counts them.
    minutes = {operation["position"]: _operation_minutes(operation, quantity) for operation in built}
    document["route"] = route_json(route, route_totals(route, minutes, quantity))
    return document


@dataclass(frozen=True)
class _ChainLink:
    """One generator operation's place in the semi-finished chain: the product it takes (`previous`), the one it
    hands on (`result`, `single` — the one-piece row name, the product itself after the last operation) and its input
    rows (`semis`)."""

    previous: str
    result: str
    single: str
    is_last: bool
    semis: list[dict[str, str]]
    rows: list[dict[str, str]] | None = None  # the result rows, when the chain sets them itself (a ПФ of the scheme)


TUT_CUT_TYPES = frozenset({"cutTut", "coaxCutTut"})
# Which cut tubes an operation that puts tubes on takes as its second input, by opKey: the splice tube, the sleeves over
# connectors / on the jacket cut / on the harness, the marking, the end caps, the solder TUT. «Сложная сборка» cuts all
# tubes first (owner 16.10) — without this the splice would take the connector sleeves and the marking too. An
# operation not listed (older configs, coax) takes every tube cut so far, as before.
TUT_TAKES: dict[str, tuple[str, ...]] = {
    "insSpl": ("cutSpl",), "insSleeves": ("cutShr", "cutJs", "cutHs"), "onMark": ("cutMark",), "endCap": ("cutCap",),
    "insTut": ("cutTut",),
}


def _takes_tut(consumer: str, cut: str) -> bool:
    family = TUT_TAKES.get(consumer)
    if family is None:
        return True
    return any(cut == prefix if prefix in {"cutTut", "cutSpl", "cutMark", "cutCap"} else cut.startswith(prefix) for prefix in family)


def _tut_pieces(op: Mapping[str, Any], config: Mapping[str, Any]) -> Decimal:
    """TUT pieces a TUT cut makes for the batch: «Резка ТУТ» — `tutCount` per item, coax — `units` per item."""
    per_item = _coax_units(op) if op.get("type") == "coaxCutTut" else _decimal(op.get("tutCount"))
    return per_item * _part_quantity(config)


# «Сложная сборка» (owner 16.10, final): when the generator drew the ПФ scheme itself (`build_config.route`, source
# «generated»), the semi-finished chain follows the scheme, not the list. The first operation of a ПФ takes one row per
# incoming arrow — a kit's arrow by what it carries («НВ4 0,12 белый, синий — 2 шт», «ТТК 8/2 — 1 шт»), any other ПФ by
# its name — × the batch, with the ПФ it comes from («ПФ 1.2»); an inner operation takes what the previous operation of
# its ПФ handed on. Inside a ПФ the results are short («Оплётка надета», «ТТК усажена»); the last operation hands on
# the ПФ itself («Ветвь X2 «I3», сторона А»). Kits (cut batches, tubes, braid, marking) keep their own results, one row
# per piece. Any other config — the linear chain, as before.
SCHEME_SOURCE = "generated"
_EDGE_PIECES = re.compile(r"\s+—\s+(\d+(?:[.,]\d+)?)\s*шт\.?$")
_EDGE_TIMES = re.compile(r"^(.*?)\s*×\s*(\d+(?:[.,]\d+)?)$")
SCHEME_DONE = {
    "tin": "Концы облужены", "insMark": "Маркировка нанесена", "seal": "Место загерметизировано",
    "stripCable": "Концы разделаны", "endCap": "Концы изолированы", "foldCore": "Жила сложена",
    "insHousing": "Корпус собран",
}


def _edge_pieces(item: str) -> tuple[str, Decimal]:
    """What one item of an arrow says and how many pieces per product: «НВ4 0,12 белый, синий — 2 шт» → 2; a scheme
    drawn before («ТТК 8/2 ×1») reads as «ТТК 8/2 — 1 шт»; nothing said — one piece."""
    text = item.strip()
    match = _EDGE_PIECES.search(text)
    if match:
        return text, _decimal(match.group(1))
    match = _EDGE_TIMES.match(text)
    if match:
        count = _decimal(match.group(2))
        return f"{match.group(1)} — {_format(count)} шт", count
    return text, Decimal(1)


def _tube_word(op: Mapping[str, Any]) -> str:
    art = str(op.get("tutArt") or "").upper()
    return "ТТК" if "ТТК" in art else "ТУТ" if "ТУТ" in art else ""


def _scheme_step(op: Mapping[str, Any]) -> str:
    """What an inner operation of a ПФ hands on, short: «Контакты установлены в разъём», «Оплётка надета»."""
    operation_type = str(op.get("type") or "")
    kind = _kind(op)
    if kind == "ins":
        return "Контакты установлены в разъём"
    if kind == "sdr":
        return "Провода припаяны к разъёму"
    if kind == "prs":
        return "Терминалы обжаты"
    if _mark_tube(op):
        return "Маркировка надета" if operation_type == "insTut" else "Маркировка усажена"
    if operation_type == "insWrap":
        return "Оплётка надета" if op.get("wrapKind") == "braid" else "Лента намотана"
    if operation_type in {"insTut", "heatTut"}:
        on = operation_type == "insTut"
        tube = _tube_word(op)
        if str(op.get("opKey") or "").startswith(("insSpl", "heatSpl")):
            return "Трубка надета на сростки" if on else "Трубка на сростках усажена"
        if tube:
            return f"{tube} надета" if on else f"{tube} усажена"
        if op.get("harness"):
            return "Трубки надеты" if on else "Термоусадка усажена"
        return "Трубка надета на провода" if on else "Трубка усажена"
    if operation_type == "twist":
        return "Пара свита" if _decimal(op.get("units")) == 1 else "Пары свиты"
    method = _splice_method(op) or ("solder" if operation_type == "splice" else "")
    if method:
        done = SPLICE_DONE[method]
        return done[:1].upper() + done[1:]
    return SCHEME_DONE.get(operation_type) or str(op.get("label") or "").strip() or "Операция выполнена"


def _kit_rows(op: Mapping[str, Any], config: Mapping[str, Any], result: str) -> list[dict[str, str]]:
    """A kit's result, one row per piece: wires of a cut batch as before, tube / braid / marking pieces by article and
    length («ТТК 8/2мм, черная — 15 мм», 3 × batch) instead of one long sentence."""
    quantity = _part_quantity(config)
    parts = _tut_parts(op)
    if parts:
        return [_row(f"{part['art']} — {part['len']} мм" if part["len"] else part["art"], _format(part["count"] * quantity))
                for part in parts]
    return _result_rows(op, config, result, False, result)


def _scheme_chain(
    operations: Sequence[Mapping[str, Any]], config: Mapping[str, Any], codes: Sequence[str] | None = None,
) -> list[_ChainLink] | None:
    """The chain along the ПФ scheme the generator drew (see SCHEME_SOURCE); None — no such scheme, or it does not fit
    the operations (the linear chain then, and the build falls back with its warning)."""
    raw = config.get("route")
    if not isinstance(raw, Mapping) or raw.get("source") != SCHEME_SOURCE or not operations:
        return None
    final_name = str(config.get("assemblyName") or "").strip()
    try:
        route = parse_route(raw, [RouteOp(position=index) for index in range(1, len(operations) + 1)], final_name)
    except RouteError:
        return None
    labels = route_levels(route)
    quantity = _part_quantity(config)
    count = _format(quantity)
    names: dict[str, str] = {}
    for block in route.blocks:
        last = operations[block.ops[-1] - 1]
        names[block.key] = block.name or (final_name if block.final else "") or (
            _result_name(last, config, "") if block.kit else _scheme_step(last))
    by_key = {block.key: block for block in route.blocks}

    def number(key: str) -> tuple[int, ...]:
        return tuple(int(part) for part in labels[key].split("."))

    links: dict[int, _ChainLink] = {}
    for block in route.blocks:
        incoming = sorted((edge for edge in route.edges if edge.target == block.key), key=lambda edge: number(edge.source))
        previous = ""
        for index, position in enumerate(block.ops):
            op = operations[position - 1]
            last = index == len(block.ops) - 1
            is_last = block.final and last
            if block.kit:
                result = _result_name(op, config, previous, [operations[item - 1] for item in block.ops[:index + 1]])
            else:
                result = names[block.key] if last else _scheme_step(op)
            if index:
                semis = [{**_row(previous, count), "source": f"операция {_op_number(block.ops[index - 1])}"}]
            elif codes is not None and codes[position - 1].startswith("CUT_"):
                semis = []
            else:
                semis = []
                for edge in incoming:
                    source = by_key[edge.source]
                    where = f"ПФ {labels[source.key]}"
                    items = [item for item in edge.item.split("; ") if item.strip()] if source.kit else []
                    if not items:
                        semis.append({**_row(names[source.key], _format(edge.per_unit * quantity)), "source": where})
                    for item in items:
                        text, pieces = _edge_pieces(item)
                        semis.append({**_row(text, _format(pieces * edge.per_unit * quantity)), "source": where})
            single = _single_name(config, result, is_last)
            rows =_kit_rows(op, config, result) if block.kit else [_row(single, count)]
            taken = previous if index else "; ".join(row["name"] for row in semis)
            links[position] = _ChainLink(taken, result, single, is_last, semis, rows)
            previous = result
    return [links[position] for position in range(1, len(operations) + 1)]


def _op_number(position: int) -> str:
    """«09»: an ETK operation number as the card shows it."""
    return str(position).zfill(2)


def _generator_chain(
    operations: Sequence[Mapping[str, Any]], config: Mapping[str, Any], codes: Sequence[str],
) -> list[_ChainLink]:
    """SUPPLYMAP etkSemis_/etkResult_ chain over the generator operations in order: every operation takes what the
    last chain-advancing one handed on; TUT pieces cut aside come in as a second input of the operation that puts them
    on. `codes` — the operation cards' codes (a cut — `CUT_…` — takes no semi-finished product). A config whose ПФ
    scheme the generator drew itself goes along the scheme (`_scheme_chain`)."""
    scheme = _scheme_chain(operations, config, codes)
    if scheme is not None:
        return scheme
    links: list[_ChainLink] = []
    previous_result = ""
    previous_norm = ""
    chain: list[Mapping[str, Any]] = []
    tut_cuts: list[dict[str, Any]] = []
    tut_on_wires = False
    for index, (op, code) in enumerate(zip(operations, codes), start=1):
        result = _result_name(op, config, previous_result, [*chain, op])
        if op.get("type") in TUT_CUT_TYPES:
            tut_cuts.append({"name": result, "norm": _tut_pieces(op, config), "key": str(op.get("opKey") or "")})
        auxiliary = None
        if op.get("type") in {"insTut", "endCap", "coaxInsTut"}:
            consumer = str(op.get("opKey") or "")
            taken = [item for item in tut_cuts if _takes_tut(consumer, item["key"])]
            tut_cuts = [item for item in tut_cuts if item not in taken]
            auxiliary = {"name": "; ".join(item["name"] for item in taken), "norm": _format(sum((item["norm"] for item in taken), Decimal(0)))}
        tut_on = False
        if op.get("type") == "insTut" and not op.get("harness") and not _mark_tube(op):
            tut_on_wires = True
        elif tut_on_wires and _kind(op):
            tut_on, tut_on_wires = True, False
        semis = [] if code.startswith("CUT_") else _semis_rows(op, config, previous_result, previous_norm, auxiliary, tut_on)
        is_last = index == len(operations)
        links.append(_ChainLink(previous_result, result, _single_name(config, result, is_last), is_last, semis))
        if _advances_chain(op):
            previous_result = result
            previous_norm = _output_norm(op, config)
            chain.append(op)
    return links


def fill_generator_chain(document: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """A stored generator ETK whose operations lack the semi-finished chain — coax ETKs built before 15.10 had no
    «Результат», no input from the previous operation and no sketch data (владелец: «нет результата в ТК КБ1») — gets
    them replayed from its own `build_config` on read, so the issued card needs no new revision. Only what is missing
    is filled: an operation with its result, input or sketch data (hand-edited or newer) keeps them; times stay as
    issued (they are what production planned by — «Пересобрать» recounts them). A config that no longer lines up with
    the operations (count or types differ) or cannot be replayed leaves the document as it is."""
    if not isinstance(document, Mapping):
        return document
    config = document.get("build_config")
    operations = _list(document.get("operations"))
    if not isinstance(config, Mapping) or config.get("mode") == MANUAL_MODE:
        return document
    raw = [item for item in _list(config.get("ops")) if isinstance(item, Mapping)]
    if not raw or len(raw) != len(operations) or any(
        not isinstance(stored, Mapping) or str(stored.get("type") or "") != str(op.get("type") or "")
        for stored, op in zip(operations, raw)
    ):
        return document
    coax = [str(op.get("type") or "").startswith("coax") for op in raw]
    if not any(coax):
        return document
    try:
        chain = _generator_chain(raw, config, [str(item.get("code") or "") for item in operations])
        filled: list[Any] = []
        changed = False
        for stored, op, link, is_coax in zip(operations, raw, chain, coax):
            item = dict(stored)
            if is_coax and not _list(item.get("result")):
                item["result"] = _result_rows(op, config, link.result, link.is_last, link.single)
                item["time"] = [{**row, "name": row.get("name") or link.single} if isinstance(row, Mapping) else row
                                for row in _list(item.get("time"))]
            if is_coax and not _list(item.get("semis")) and link.semis:
                item["semis"] = link.semis
            fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
            extra = {key: value for key, value in coax_sketch_fields(op, config).items() if key not in fields} if is_coax else {}
            if extra:
                item["fields"] = {**fields, **extra}
            changed = changed or item != stored
            filled.append(item)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, InvalidOperation):
        return document
    return {**document, "operations": filled} if changed else document


def build_etk(
    config: Mapping[str, Any], cards: Sequence[OperationCardTemplate], *,
    operation_library_commit: str = "",
    technology_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an immutable document snapshot suitable for ProjectTechcard.rebuild. `operation_library_commit` — only a
    provenance label in `source` (documents built from the SUPPLYMAP library carry its commit); empty — none."""
    if config.get("mode") == MANUAL_MODE:
        return _build_manual(config, cards, operation_library_commit=operation_library_commit,
                             technology_source=technology_source)
    raw_operations = config.get("ops")
    if not isinstance(raw_operations, list):
        raise ValueError("Нет операций для сборки")
    if (vswr_control(_coax(config).get("vswrControl")) or {}).get("mode") == "none":
        # «Контроль КСВН: не проверять» (генератор, владелец 22.10) — операции контроля в ЭТК нет.
        raw_operations = [item for item in raw_operations if not (isinstance(item, Mapping) and item.get("type") == "coaxVswr")]
        config = {**config, "ops": raw_operations}
    cards_by_id = {card.source_id: card for card in cards}
    cards_by_code: dict[str, OperationCardTemplate] = {}
    for item in cards:
        cards_by_code.setdefault(item.code, item)
    operations = [deepcopy(item) for item in raw_operations if isinstance(item, Mapping)]
    warnings: list[str] = []
    built: list[dict[str, Any]] = []
    found = [cards_by_id.get(str(op.get("templateId") or "")) for op in operations]
    codes = [card.code if card else str(op.get("opNum") or "") for op, card in zip(operations, found)]
    for index, (op, card, code, link) in enumerate(zip(operations, found, codes, _generator_chain(operations, config, codes)), start=1):
        if card is None:
            warnings.append(_card_warning(op, index, code))
        built.append(_typed_operation(
            op, config, position=index, code=code, card=card, cards_by_code=cards_by_code,
            previous=link.previous, result=link.result, single=link.single, is_last=link.is_last,
            semis=link.semis, connector=_typed_connector(op, index), warnings=warnings, result_rows=link.rows,
        ))
    _distinct_typed_keys(built)

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
            **({"operation_library_commit": operation_library_commit} if operation_library_commit else {}),
            **({"technology_snapshot": deepcopy(dict(technology_source))} if technology_source else {}),
        },
    }
    vswr_norm = _coax(config).get("vswrNorm")
    if isinstance(vswr_norm, Mapping):
        document["coaxVswrNorm"] = deepcopy(dict(vswr_norm))  # норма КСВН сборки по комплектующим (владелец 22.10)
    document["route"] = _route(document, strict=True, warnings=warnings)
    return document


def document_route(document: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The block scheme of any stored ETK, for the read path: its own `route`, else the generator's hand-edited
    `build_config.route`, else derived from the operations (documents before version 3). A stored
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
