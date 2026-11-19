"""Frozen cutting recipes. Unknown preparation settings never imply equivalence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping, Sequence
from uuid import UUID

from .domain import ProductionOperation, unit_factors
from .plan import normalize_project_number

# Version 1 — a PF with the cable cut (the mixed «Заготовки» of ETKs before 20.11 and a cut-only PF): its identity is
# unchanged, so stored group keys stay valid; its kind is «cut». Version 2 — a PF of only labels or only tubes, with an
# explicit `kind` in the key. Keys of different kinds can never be equal (owner 20.11: cut, labels, tubes — separate tasks).
RECIPE_VERSION = 1
KIND_RECIPE_VERSION = 2
SUPPORTED = frozenset({"coaxCut", "coaxCutTut", "cutWire", "cutWireStrip", "cutTut"})
CABLE_CUTS = frozenset({"coaxCut", "cutWire", "cutWireStrip"})
CUT, LABELS, TUBES = "cut", "labels", "tubes"
KIND_NAMES = {CUT: "Резка кабеля", LABELS: "Печать маркировки", TUBES: "Резка трубок"}


def recipe_kind(snapshot: Mapping | None) -> str:
    """The kind of a stored recipe: version 2 names it, version 1 always holds the cable cut."""
    return str((snapshot or {}).get("kind") or CUT)


def printed(raw: Mapping, code: object = "") -> bool:
    """A printing operation: tape labels (markMaterial «tape») or printed marking tubes (PRINT_MARK…, the generator's
    «Напечатанная маркировка» PF since 20.11)."""
    return raw.get("markMaterial") == "tape" or str(code or raw.get("opNumber") or "").upper().startswith("PRINT_MARK")


def pf_kind(types_and_printing: Sequence[tuple[str, bool]]) -> str:
    """Kind of one PF from its ETK operations (type, is printing). A PF with a cable cut is «cut» — the old mixed
    «Заготовки» PF (cut + labels + tubes) stays one recipe of that kind, as it was issued. Without a cut: only printing
    (tape labels, printed marking tubes) — «labels», only tube cuts — «tubes»; both together without a cut are not
    grouped."""
    if any(kind in CABLE_CUTS for kind, _printing in types_and_printing):
        return CUT
    printing = [bool(value) for _kind, value in types_and_printing]
    if printing and all(printing):
        return LABELS
    if printing and not any(printing):
        return TUBES
    raise ValueError("ПФ смешивает печать маркировки и резку трубок без резки кабеля")


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def number(value: object) -> str:
    try:
        result = Decimal(str(value).replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("Не задан числовой параметр резки") from error
    if not result.is_finite() or result <= 0:
        raise ValueError("Параметры резки должны быть положительными")
    return format(result.normalize(), "f")


def _required(value: object, label: str):
    if value is None or value == "" or value == [] or value == {} or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Не задано: {label}")
    if isinstance(value, str) and value.strip().casefold() in {"-", "не задано", "unknown"}:
        raise ValueError(f"Не задано: {label}")
    return deepcopy(value)


def _card_snapshot(card, marks, lengths, tube_cuts=()):
    """Only rendered step text may vary; never erase a number/text inside equipment or tooling."""
    result = deepcopy(card)
    for step in result.get("steps") or []:
        for field in ("text", "tolerance"):
            value = str(step.get(field) or "")
            for batch_text, per_item_text in tube_cuts:
                if value == batch_text:
                    value = per_item_text
            for mark in marks:
                if mark.get("text"):
                    value = value.replace(str(mark["text"]), "{label}")
            for length in lengths:
                # A bare number may also be a tool ID, layer length, or revision: require the unit.
                value = re.sub(r"(?<![\w.,])" + re.escape(length) + r"\s*мм(?!\w)", "{length}мм", value)
            step[field] = value
    return result


def _without_equipment_names(value):
    """Identity of equipment is its model/inventory number, never the display name (renames must not split a recipe)."""
    if isinstance(value, Mapping):
        return {k: v for k, v in value.items() if k != "name"}
    return value


def _equipment_identity_card(card):
    """Copy of the card whose equipment rows drop display names; other tooling (tools, consumables) keep theirs."""
    result = deepcopy(card)
    result["tooling"] = [
        _without_equipment_names(tool) if isinstance(tool, Mapping) and (
            tool.get("section") == "Оборудование" or tool.get("equipment_model_id")) else tool
        for tool in result.get("tooling") or []]
    return result


def _equipment_identity_extras(fields, parameters):
    if isinstance(fields.get("machine"), Mapping):
        fields["machine"] = _without_equipment_names(fields["machine"])
    if isinstance(parameters.get("machine"), Mapping):
        parameters["machine"] = _without_equipment_names(parameters["machine"])
    if isinstance(parameters.get("equipmentTools"), list):
        parameters["equipmentTools"] = [_without_equipment_names(tool) for tool in parameters["equipmentTools"]]


@dataclass(frozen=True)
class CuttingRecipe:
    operation_id: UUID
    key: str
    snapshot: dict
    row: dict
    tooling: tuple = ()  # display-only equipment/tooling with names, per operation; not part of the key
    kind: str = CUT


def cutting_project_label(project: str, document: Mapping | None = None) -> str:
    """Display the issued spelling, never reconstruct it from the normalized recipe key."""
    document = document or {}
    for token in ((document.get("kit") or {}).get("project"),
                  (document.get("build_config") or {}).get("projectNo"), project):
        if str(token or "").strip():
            return str(token).strip()
    return ""


def _own_ops(document: Mapping, pf: ProductionOperation) -> list[tuple[dict, dict]]:
    config = document.get("build_config") or {}
    raw_ops, issued = config.get("ops") or [], document.get("operations") or []
    if len(raw_ops) != len(issued):
        raise ValueError("Операции ЭТК не совпадают с параметрами")
    return [(issued[pos - 1], raw_ops[pos - 1]) for pos in pf.etk_positions]


def _preparation_pfs(document: Mapping, operations: Sequence[ProductionOperation]) -> list[ProductionOperation]:
    """Input-less PFs made only of cutting/preparation ETK operations, in route order."""
    result = []
    for pf in sorted(operations, key=lambda op: op.position):
        if pf.inputs or not pf.etk_ops or not pf.stage_key:
            continue
        try:
            own = _own_ops(document, pf)
        except IndexError:
            continue
        if own and all(op.get("type") in SUPPORTED for op, _raw in own):
            result.append(pf)
    return result


def cutting_recipe(project: str, site: str, document: Mapping | None,
                   operations: Sequence[ProductionOperation]) -> tuple[CuttingRecipe | None, str]:
    """Only the first issued PF, without incoming edges, consisting exclusively of preparation.

    Identity is deliberately conservative: only documented length and label-text fields vary.
    A frozen card body hash supplies a revision when the issued document has no revision stamp.
    The launch fingerprint and the launch response keep using this first PF; grouping uses `cutting_recipes`.
    """
    if not document or not operations:
        return None, "Нет выданного снимка ЭТК"
    return _pf_recipe(project, site, document, operations, min(operations, key=lambda op: op.position))


def cutting_recipes(project: str, site: str, document: Mapping | None, operations: Sequence[ProductionOperation],
                    ) -> tuple[list[CuttingRecipe], list[tuple[UUID, str]]]:
    """One recipe per input-less preparation PF (owner 20.11: cable cut, labels and tubes are separate grouped tasks).
    Returns the eligible recipes in route order and (PF id, reason) for preparation PFs that cannot be grouped."""
    if not document or not operations:
        return [], []
    recipes, refused = [], []
    for pf in _preparation_pfs(document, operations):
        recipe, reason = _pf_recipe(project, site, document, operations, pf)
        if recipe is None:
            refused.append((pf.id, reason))
        else:
            recipes.append(recipe)
    return recipes, refused


def recipe_for_operation(project: str, site: str, document: Mapping | None,
                         operations: Sequence[ProductionOperation], operation_id: UUID) -> tuple[CuttingRecipe | None, str]:
    """The recipe of one given PF (a group member), whatever its position in the route."""
    pf = next((op for op in operations if op.id == operation_id), None)
    if not document or pf is None:
        return None, "Нет выданного снимка ЭТК" if pf is not None else "ПФ нет в задании"
    return _pf_recipe(project, site, document, operations, pf)


def _pf_recipe(project: str, site: str, document: Mapping, operations: Sequence[ProductionOperation],
               pf: ProductionOperation) -> tuple[CuttingRecipe | None, str]:
    try:
        if pf.inputs or not pf.etk_ops or not pf.stage_key:
            raise ValueError("Нет начального ПФ резки без входов")
        config = document.get("build_config") or {}
        raw_ops, issued = config.get("ops") or [], document.get("operations") or []
        own = _own_ops(document, pf)
        if any(op.get("type") not in SUPPORTED or op.get("type") != raw.get("type") for op, raw in own):
            raise ValueError("ПФ содержит операции помимо резки и подготовки")
        recipe_kind_ = pf_kind([(op.get("type"), printed(raw, op.get("code"))) for op, raw in own])
        # Labels and tubes PFs take the cable multiplicity (and the cut length for the row) from the ETK's only cable cut.
        cuts = [(op, raw) for op, raw in (own if recipe_kind_ == CUT else zip(issued, raw_ops)) if op.get("type") in CABLE_CUTS]
        if len(cuts) != 1:
            raise ValueError("Требуется одна однозначная операция резки кабеля")
        cut, raw_cut = cuts[0]
        coax = config.get("coax") or {}
        if cut["type"] == "coaxCut":
            cable = _required(coax.get("cableArt"), "артикул кабеля")
            length = number(coax.get("cableLength"))
            multiplicity = number(raw_cut.get("units"))
            if "cables" in coax and number(coax["cables"]) != multiplicity:
                raise ValueError("Неоднозначное количество кабелей в сборке")
        else:
            wires = config.get("wires") or []
            if len(wires) != 1:
                raise ValueError("Состав проводов не поддерживает однозначную группировку")
            wire = wires[0]
            cable = _required(wire.get("art"), "артикул провода")
            length = number(wire.get("length"))
            multiplicity = number(wire.get("qty", 1))
        if Decimal(multiplicity) != Decimal(multiplicity).to_integral_value():
            raise ValueError("Количество кабелей в сборке должно быть целым")
        recipes, labels, display_tooling = [], [], []
        for op, raw in own:
            fields = deepcopy(op.get("fields") or {})
            card = deepcopy(_required(op.get("card"), "карточка операции"))
            tooling = _required(card.get("tooling"), "оборудование и оснастка")
            machines = [tool for tool in tooling if tool.get("section") == "Оборудование"]
            if not machines:
                raise ValueError("Не задано оборудование операции")
            for machine in machines:
                _required(machine.get("equipment_model_id") or machine.get("inventory_number"), "идентичность станка")
            kind = op["type"]
            parameters = {k: deepcopy(v) for k, v in raw.items() if k not in {
                "opKey", "key", "id", "label", "stage", "opNum", "opNumber", "tOp", "tPrep", "tMachine", "marks",
            }}
            if "units" in parameters:
                parameters["units"] = number(parameters["units"])
            if kind == "coaxCut":
                sides = raw.get("stripSides")
                if not isinstance(sides, list) or len(set(sides)) != len(sides) or set(sides) - {"A", "B"}:
                    raise ValueError("Не заданы стороны разделки")
                if "strip" in str(op.get("code", "")).lower() and not sides:
                    raise ValueError("Не заданы стороны разделки")
                for side in sides:
                    key = "coax" if side == "A" else "coaxB"
                    sizes = _required(fields.get(key), f"разделка {side}")
                    for dimension in ("l1", "l2", "l3", "d1", "d2", "d3"):
                        value = sizes.get(dimension)
                        sizes[dimension] = "" if value is None or value == "" else number(value)
                    program = sizes.get("stripProgram")
                    unconfirmed = not program or any(phrase in str(program.get("note", "")).casefold()
                                                     for phrase in ("не рассчитан", "подтвердить"))
                    if unconfirmed:
                        # No confirmed numeric program (HFM «подтвердить», ordinary coax without one): the members still cut
                        # by the same issued instruction, so identity = connector + side sizes + program as written + the card
                        # snapshot below. Grouping does not hide the open confirmation — every member carries it (owner 17.11).
                        sizes["stripProgram"] = deepcopy(program) if program else None
                        sizes["stripMode"] = sizes.get("stripMode") or (program or {}).get("mode") or (
                            "full" if coax.get("fakraCutStrip") is True else "")
                        sizes["programStatus"] = "unconfirmed" if program else "absent"
                        fields[key] = sizes
                        continue
                    boundaries = _required(program.get("boundaries"), f"границы разделки {side}")
                    previous = Decimal(0)
                    layers = set()
                    for boundary in boundaries:
                        boundary["value"] = number(boundary.get("value"))
                        layer = _required(boundary.get("layer"), "слой разделки")
                        if layer in layers or Decimal(boundary["value"]) <= previous:
                            raise ValueError(f"Неоднозначные границы разделки {side}")
                        layers.add(layer)
                        previous = Decimal(boundary["value"])
                        _required(boundary.get("tolerance"), f"допуск разделки {side}")
                    program["boundaries"] = boundaries
                    sizes["stripProgram"] = program
                    mode = sizes.get("stripMode") or program.get("mode")
                    if not mode and coax.get("fakraCutStrip") is True:
                        mode = "full"
                    if mode not in {"full", "partial"}:
                        raise ValueError(f"Не задан режим полной/частичной разделки {side}")
                    sizes["stripMode"] = mode
                    fields[key] = {k: v for k, v in sizes.items() if k not in {
                        "connector", "connectorType", "gender", "article", "name", "lPlus", "lMinus",
                    }}
                _required(fields.get("cutTolerance"), "допуск реза")
            if kind in {"cutWire", "cutWireStrip"}:
                strip = _required(fields.get("strip"), "режим разделки провода")
                for side in ("a", "b"):
                    if side not in strip:
                        raise ValueError("Не заданы стороны разделки")
                    if strip[side]:
                        number(strip.get("len" + side.upper()))
                        _required(strip.get("mode" + side.upper()), "полная/частичная разделка")
            marks = raw.get("marks") or []
            if marks:
                labels.extend(deepcopy(marks))
                parameters["marks"] = [{k: v for k, v in mark.items() if k != "text"} for mark in marks]
                fields.pop("coaxMarks", None)
                fields.pop("marks", None)
            if kind in {"coaxCutTut", "cutTut"}:
                number(raw.get("units", raw.get("tutCount")))
                if raw.get("markMaterial") == "tape":
                    # The generator keeps the tape as a consumable of the issued card, not as raw «parts» (708К., 17.11).
                    material = raw.get("parts") or [str(tool.get("name") or "").strip() for tool in tooling
                                                    if tool.get("section") == "Расходники" and str(tool.get("name") or "").strip()]
                    parameters["labelMaterial"] = _required(material, "материал этикетки")
                    _required(marks, "раскладка этикеток")
                    for mark in marks:
                        _required(mark.get("place"), "место этикетки")
                    copies = sum(Decimal(number(mark.get("copies", 1))) for mark in marks)
                    units = Decimal(number(raw.get("units")))
                    # Multi-cable assemblies (HFM, 4 coax) carry the layout once per cable (17.11 #875).
                    if copies != units and copies * Decimal(multiplicity) != units:
                        raise ValueError("Количество этикеток не совпадает с раскладкой")
                    # Printer program/parameters are part of the card snapshot: members must match them exactly, but a
                    # library placeholder («не задано») shared by all members does not block grouping.
                else:
                    tube = {"article": _required(coax.get("tutArt"), "материал ТУТ")}
                    if raw.get("protective") and isinstance(coax.get("tutLengths"), Mapping):
                        # Read the frozen issued lengths; never apply a new rule to an old production snapshot.
                        tube["lengths"] = {place: number(coax["tutLengths"].get(place)) for place in ("A", "B", "middle")}
                        layout = raw.get("marks") or coax.get("marks") or next(
                            (item.get("marks") for item in raw_ops if item.get("markMaterial") == "tape" and item.get("marks")), [])
                        tube["marks"] = [{k: v for k, v in mark.items() if k != "text"} for mark in layout]
                    else:
                        tube["length"] = number(coax.get("tutLength"))
                    parameters["tube"] = tube
            # Keep all other fields, including tolerances and A/B orientation. Only cable length varies.
            lengths = []
            if op is cut:
                lengths = [length, length.replace(".", ",")]
                if coax.get("finishedLength"):
                    lengths.append(number(coax["finishedLength"]))
                fields.pop("lengthKd", None)
                fields.pop("finishedLength", None)
                fields.pop("lPlusA", None)
                fields.pop("lPlusB", None)
            tube_cuts = []
            for group in fields.get("coaxProtectiveTubes") or []:
                units = number(group.get("units"))
                quantity = Decimal(number(config.get("partQty", (document.get("kit") or {}).get("part_quantity"))))
                total = number(Decimal(units) * quantity).replace(".", ",")
                place = "центральные" if group.get("place") == "middle" else "концевые"
                tail = f"отрезков по {number(group.get('length')).replace('.', ',')} мм ({place})."
                # Generic cards name the tube by its BOM name, short cards (etk_builder) by its article.
                for material in dict.fromkeys(str(value) for value in (
                        coax.get("tutName") or coax.get("tutArt") or "ТУТ", coax.get("tutArt")) if value):
                    tube_cuts.append((f"Нарезать {material}: {total} {tail}",
                                      f"Нарезать {material}: {units.replace('.', ',')} {tail}"))
            display_tooling.append(deepcopy(card.get("tooling") or []))
            card = _card_snapshot(_equipment_identity_card(card), marks, lengths, tube_cuts)
            _equipment_identity_extras(fields, parameters)
            recipes.append({"type": kind, "code": _required(op.get("code"), "код операции"),
                            "template_id": _required(op.get("template_id"), "шаблон операции"),
                            "card_revision": op.get("card_changed_at") or digest(card),
                            "card": card, "parameters": parameters, "fields": fields})
        if recipe_kind_ == CUT:
            snapshot = {"version": RECIPE_VERSION, "project": normalize_project_number(project), "site": site,
                        "cable": cable, "cables_per_assembly": multiplicity,
                        "pf_per_assembly": number(unit_factors(operations)[pf.id]), "operations": recipes}
        else:
            # Printing labels or cutting tubes does not depend on which cable they go on: no cable in the key.
            snapshot = {"version": KIND_RECIPE_VERSION, "kind": recipe_kind_, "project": normalize_project_number(project),
                        "site": site, "cables_per_assembly": multiplicity,
                        "pf_per_assembly": number(unit_factors(operations)[pf.id]), "operations": recipes}
        return CuttingRecipe(pf.id, digest(snapshot), snapshot,
                             {"cut_length_mm": length, "cables_per_assembly": multiplicity, "labels": labels},
                             tuple(display_tooling), recipe_kind_), ""
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        return None, str(error) or "Недостаточно параметров резки"
