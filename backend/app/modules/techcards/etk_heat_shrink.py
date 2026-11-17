"""Exact tube associations and frozen material requirements, independent of heater settings."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from .etk_marking import mark_kind

PLACEHOLDER = "‹температура усадки материала›"
MISSING = "не задано — проверить"
LEGACY_STEP = "Включить термофен, дождаться выхода на заданный температурный режим."
THERMAL_REVIEW = "Ручной температурный режим: согласовать с требованием материала до работы."
MODE_NOTE = " Программу термофена, температуру воздуха, поток и время выдержки согласовать до работы."


def _text(value: object) -> str:
    return str(value or "").strip()


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, (list, tuple)) else []


def _materials(items: object, *, marking: bool = False) -> list[dict[str, str]]:
    result = []
    for item in _items(items):
        material = {"article": _text(item.get("article") or item.get("art")), "name": _text(item.get("name"))}
        if not any(material.values()):
            continue
        if marking and mark_kind(" ".join(material.values())) != "heat":
            continue
        if material not in result:
            result.append(material)
    return result


def _own(op: Mapping[str, Any], *, marking: bool = False) -> list[dict[str, str]]:
    if "shrinkMaterials" in op:
        return _materials(op["shrinkMaterials"], marking=marking)
    if op.get("tutParts"):
        return _materials(op["tutParts"], marking=marking)
    return _materials([{"art": op.get("tutArt"), "name": op.get("tutName")}], marking=marking)


def is_shrink(op: Mapping[str, Any]) -> bool:
    if op.get("type") in {"heatTut", "coaxHeat", "endCap"}:
        return True
    if op.get("type") == "insMark" and op.get("markPhase") != "print":
        return bool(_materials(op.get("marks"), marking=True))
    return _text(op.get("code") or op.get("opNum") or op.get("tmplHint")).startswith(("HEAT_TYT_", "HEAT_TTK_", "HEAT_FAKRA_BH_TTK"))


def material_association(op: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict[str, str]]:
    """Recover old generator inputs only through an unambiguous association, never nearest-cut order.

    New generators put the association on each operation. Old grouped cuts without a destination cannot identify
    which of several materials a connector used; leave that operation unresolved for the technologist.
    """
    if op.get("wrapKind") or op.get("type") not in {"cutTut", "insTut", "heatTut", "coaxCutTut", "coaxInsTut", "coaxHeat", "insMark", "endCap"}:
        return []
    marking = bool(op.get("markTube") or op.get("wireMarks"))
    own = _own(op, marking=marking)
    if own or "shrinkMaterials" in op:
        return own
    if op.get("type") == "insMark":
        return _materials(op.get("marks"), marking=True)
    key = _text(op.get("opKey"))
    coax = config.get("coax")
    if str(op.get("type", "")).startswith("coax") and isinstance(coax, Mapping):
        if key.startswith("coaxHeatTtk"):
            # The bulkhead connector sleeve is NOT the separate marking/protection tube.
            end = coax.get("side" + _text(op.get("side"))) or {}
            materials = _materials([p for p in _items(end.get("parts")) if p.get("role") == "tube"])
            if materials:
                return materials
            preload = next((item for item in _items(config.get("ops"))
                            if item.get("opKey") == "coaxInsSleeve" + _text(op.get("side"))), {})
            return _materials(preload.get("parts"), marking=True)
        if op.get("markMaterial") != "tape":
            return _own(coax)
        return []
    ops = _items(config.get("ops"))
    if not key and op.get("type") in {"insTut", "heatTut", "endCap"}:
        found = _materials([m for item in ops if item.get("type") == "cutTut" and not item.get("wrapKind")
                            and bool(item.get("markTube")) == marking for m in _own(item, marking=marking)])
        return found if len(found) == 1 else []
    counterpart = {"heatTut": "cutTut", "insTut": "cutTut", "endCap": "cutCap", "heatWireMark": "cutWireMark",
                   "onWireMark": "cutWireMark", "heatMark": "cutMark", "onMark": "cutMark"}.get(key)
    if counterpart:
        candidates = [item for item in ops if item.get("opKey") == counterpart]
        return _materials([m for item in candidates for m in _own(item, marking=marking)])
    # Physical tube placements carry one shared id, independent of the operation order.
    if op.get("tubePlacement"):
        return _materials([m for item in ops if item.get("tubePlacement") == op["tubePlacement"]
                           for m in _own(item, marking=marking)])
    if key == "heatHs":
        return _materials([m for item in ops if re.fullmatch(r"cutHs\d+", _text(item.get("opKey")))
                           for m in _own(item)])
    # A dedicated matching preload already knows the branch's exact material.
    preload = re.sub(r"^heatShr", "onTubes", key)
    candidates = [item for item in ops if preload != key and item.get("opKey") == preload]
    found = _materials([m for item in candidates for m in _own(item)])
    if found:
        return found
    prefix = "cutShr" if key.startswith("heatShr") else "cutJs" if key.startswith("heatJs") else ""
    if prefix:
        found = _materials([m for item in ops if _text(item.get("opKey")).startswith(prefix) for m in _own(item)])
        return found if len(found) == 1 else []
    if re.fullmatch(r"(?:heatSpl|insSpl)\d*", key):
        # Legacy splice cuts were grouped under cutTubes. Only one unmarked material is safe to recover.
        found = _materials([m for item in ops if item.get("type") == "cutTut" and not item.get("markTube")
                            and item.get("opKey") in {"cutTut", "cutTubes"} for m in _own(item)])
        return found if len(found) == 1 else []
    return []


def missing_requirement(material: Mapping[str, str] | None = None) -> dict[str, Any]:
    return {"material": dict(material or {"article": "", "name": ""}), "status": "missing",
            "temperature": None, "reason": "Температура усадки материала не задана" if material
            else "Точный материал усадки не определён"}


def requirement_text(item: Mapping[str, Any]) -> str:
    material = item.get("material") or {}
    label = " · ".join(dict.fromkeys(value for value in (material.get("article"), material.get("name")) if value))
    prefix = f"{label}: " if label else ""
    temperature = item.get("temperature") or {}
    low, high = temperature.get("lower_c"), temperature.get("upper_c")
    if item.get("status") != "resolved" or (low is None and high is None):
        return prefix + MISSING
    def number(value: float) -> str:
        return f"{value:g}".replace(".", ",")
    value = (number(low) if low == high else f"{number(low)}…{number(high)}") if low is not None and high is not None \
        else f"не ниже {number(low)}" if low is not None else f"не выше {number(high)}"
    source = ", ".join(filter(None, (temperature.get("kind"), temperature.get("document"),
                                  f"стр. {temperature['page']}" if temperature.get("page") else "")))
    characteristic = temperature.get("characteristic_c")
    return prefix + f"{value} °C" + (f" ({source})" if source else "") + (
        f"; по характеристикам: {number(characteristic)} °C" if characteristic is not None else "")


def _instruction(requirements: list[Mapping[str, Any]]) -> str:
    return "Температура усадки материала: " + "; ".join(requirement_text(item) for item in requirements) + "." + MODE_NOTE


def apply_requirement_steps(operation: dict[str, Any]) -> None:
    """Replace only the material placeholder or the known legacy default, preserving approved programs."""
    requirements = (operation.get("fields") or {}).get("shrinkRequirements")
    if not requirements or not isinstance(operation.get("card"), Mapping):
        return
    text = _instruction(requirements)
    for step in operation["card"].get("steps") or []:
        old = str(step.get("text") or "")
        default_tolerance = bool(re.fullmatch(r"150\s*°\s*[CС]", str(step.get("tolerance") or "").strip()))
        legacy = default_tolerance and (old == LEGACY_STEP or "правило цеха: на усадке 150 °C" in old)
        if step.get("material_requirement") or PLACEHOLDER in old or legacy:
            if "правило цеха: на усадке 150 °C" in old:
                step["material_action"] = "Усадить равномерным прогревом термовоздушной станцией. "
            step.update(text=step.get("material_action", "") + text,
                        tolerance="", material_requirement=True)


def apply_requirements(operation: dict[str, Any], op: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    materials = material_association(op, config)
    if materials:
        operation["fields"]["shrinkMaterials"] = deepcopy(materials)
    if not is_shrink(op) and not is_shrink(operation):
        return
    # An explicitly identified PVC marker has no thermal requirement.
    if not materials and op.get("markTube") and (op.get("shrinkMaterials") or op.get("tutArt") or op.get("tutParts")):
        operation["fields"]["shrinkRequirements"] = []
        return
    requirements = op.get("shrinkRequirements")
    if not isinstance(requirements, list):
        requirements = [missing_requirement(material) for material in materials] or [missing_requirement()]
    operation["fields"]["shrinkRequirements"] = deepcopy(requirements)
    apply_requirement_steps(operation)


def refresh_override(operation: dict[str, Any], base: Mapping[str, Any]) -> bool:
    """Reference-owned requirements cannot be frozen by a full-operation hand edit. Return a review flag."""
    requirements = (base.get("fields") or {}).get("shrinkRequirements")
    fields = operation.setdefault("fields", {})
    previous = fields.get("shrinkRequirements")
    if isinstance(previous, list) and previous:
        # The operation editor serializes only text/kind/tolerance. Recover our marker only when its text is intact;
        # a human-written thermal instruction must remain visible and go to review instead.
        for step in (operation.get("card") or {}).get("steps") or []:
            for action in ("", "Усадить равномерным прогревом термовоздушной станцией. "):
                if step.get("text") == action + _instruction(previous) and not step.get("tolerance"):
                    step["material_requirement"] = True
                    if action:
                        step["material_action"] = action
    fields.pop("shrinkMaterials", None)
    if (base.get("fields") or {}).get("shrinkMaterials"):
        fields["shrinkMaterials"] = deepcopy(base["fields"]["shrinkMaterials"])
    fields.pop("shrinkRequirements", None)
    fields.pop("shrinkReview", None)
    if requirements is None:
        return False
    fields["shrinkRequirements"] = deepcopy(requirements)
    apply_requirement_steps(operation)
    # A custom thermal instruction is preserved, but visibly held for reconciliation, never silently accepted.
    review = any(not step.get("material_requirement") and re.search(r"\d\s*°\s*[CС]", str(step.get("text", ""))
                + " " + str(step.get("tolerance", ""))) for step in (operation.get("card") or {}).get("steps") or [])
    if review:
        fields["shrinkReview"] = THERMAL_REVIEW
    return review
