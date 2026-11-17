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

RECIPE_VERSION = 1
SUPPORTED = frozenset({"coaxCut", "coaxCutTut", "cutWire", "cutWireStrip", "cutTut"})


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


@dataclass(frozen=True)
class CuttingRecipe:
    operation_id: UUID
    key: str
    snapshot: dict
    row: dict


def cutting_recipe(project: str, site: str, document: Mapping | None,
                   operations: Sequence[ProductionOperation]) -> tuple[CuttingRecipe | None, str]:
    """Only the first issued PF, without incoming edges, consisting exclusively of preparation.

    Identity is deliberately conservative: only documented length and label-text fields vary.
    A frozen card body hash supplies a revision when the issued document has no revision stamp.
    """
    try:
        if not document or not operations:
            raise ValueError("Нет выданного снимка ЭТК")
        first = min(operations, key=lambda op: op.position)
        if first.inputs or not first.etk_ops or not first.stage_key:
            raise ValueError("Нет начального ПФ резки без входов")
        config = document.get("build_config") or {}
        raw_ops, issued = config.get("ops") or [], document.get("operations") or []
        if len(raw_ops) != len(issued):
            raise ValueError("Операции ЭТК не совпадают с параметрами")
        own = [(issued[pos - 1], raw_ops[pos - 1]) for pos in first.etk_positions]
        if any(op.get("type") not in SUPPORTED or op.get("type") != raw.get("type") for op, raw in own):
            raise ValueError("ПФ содержит операции помимо резки и подготовки")
        cuts = [(op, raw) for op, raw in own if op.get("type") in {"coaxCut", "cutWire", "cutWireStrip"}]
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
        recipes, labels = [], []
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
                material = coax.get("tutName") or coax.get("tutArt") or "ТУТ"
                place = "центральные" if group.get("place") == "middle" else "концевые"
                tail = f"отрезков по {number(group.get('length')).replace('.', ',')} мм ({place})."
                tube_cuts.append((f"Нарезать {material}: {total} {tail}",
                                  f"Нарезать {material}: {units.replace('.', ',')} {tail}"))
            card = _card_snapshot(card, marks, lengths, tube_cuts)
            recipes.append({"type": kind, "code": _required(op.get("code"), "код операции"),
                            "template_id": _required(op.get("template_id"), "шаблон операции"),
                            "card_revision": op.get("card_changed_at") or digest(card),
                            "card": card, "parameters": parameters, "fields": fields})
        snapshot = {"version": RECIPE_VERSION, "project": normalize_project_number(project), "site": site,
                    "cable": cable, "cables_per_assembly": multiplicity,
                    "pf_per_assembly": number(unit_factors(operations)[first.id]), "operations": recipes}
        return CuttingRecipe(first.id, digest(snapshot), snapshot,
                             {"cut_length_mm": length, "cables_per_assembly": multiplicity, "labels": labels}), ""
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        return None, str(error) or "Недостаточно параметров резки"
