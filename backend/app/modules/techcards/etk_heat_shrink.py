"""Tube associations, catalog matching and frozen requirements, independent of heater settings."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
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


_SIZE = re.compile(r"(?<![\d.,/])(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)(?!\d|[.,/]\d)")
_CATALOG_NUMBER = re.compile(r"(?<![\w-])\d{5,6}(?![\w-])")
_KVT = re.compile(r"\b(?:квт|kvt)\b")
_SERIES = re.compile(
    r"(?<!\w)(?:тут\s*(?:\(\s*\d+\s*:\s*\d+\s*\))?\s*"
    r"(?:нг(?:\s*-?\s*ls)?|(?:-\s*|\(\s*)?hf\s*\)?)?"
    r"|[нс]?ттк2?\s*(?:\(\s*\d+\s*:\s*\d+\s*\)|[\s\"«]*flex[\"»]?)?"
    r"|тнт|тт\s*-\s*(?:150|175|200|auto|гсм|с\s*нг))(?![а-яa-z])"
)
_COLOUR = re.compile(
    r"\b(?:желто[-\s]+зелен|черн|бел|красн|син|голуб|желт|зелен|прозр(?:ачн)?"
    r"|сер|коричнев|оранжев|фиолетов|розов)(?:ая|ый|ий|яя|ое|ее|ые|ие|ой|ого|ей|ую|юю|ых|их)?\b"
)


def _key(value: object) -> str:
    return " ".join(_text(value).casefold().replace("ё", "е").replace("–", "-").replace("—", "-").split())


def _series_key(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9]", "", value)


def _sizes(text: str) -> set[tuple[Decimal, Decimal]]:
    return {tuple(Decimal(n.replace(",", ".")) for n in match) for match in _SIZE.findall(text)}


def _colours(text: str) -> set[str]:
    stems = ("черн", "бел", "красн", "син", "голуб", "желт", "зелен", "прозр", "сер", "коричнев", "оранжев", "фиолетов", "розов")
    return {"желто-зелен" if "желт" in colour and "зелен" in colour else next(stem for stem in stems if colour.startswith(stem))
            for colour in _COLOUR.findall(text)}


def _family(text: str) -> str:
    series = [_series_key(match.group()) for match in _SERIES.finditer(text)]
    adhesive = any(s.startswith(("ттк", "нттк", "сттк", "ттauto")) for s in series) or bool(
        re.search(r"\b(?:клеев[а-я]*|с\s+клеем)\b", text))
    plain = any(s.startswith(("тут", "тнт", "тт150", "тт175", "тт200", "ттгсм", "ттснг")) for s in series)
    return "conflict" if adhesive and plain else "ТТК" if adhesive else "ТУТ" if plain else ""


def _generic(text: str) -> bool:
    """Only a plain type/size/colour description authorizes the owner's default maker.

    Unrecognized brands, series and article fragments must not silently become KVT.
    """
    rest = _COLOUR.sub(" ", _SIZE.sub(" ", text))
    rest = re.sub(r"\b(?:тут|ттк|мм|трубка|термоусадочная|термоусаживаемая|клеевая|с\s+клеем)\b", " ", rest)
    return not re.search(r"\w", rest)


def match_shrink_material(
    material: Mapping[str, str], rows: tuple[Mapping[str, Any], ...],
) -> tuple[str, list[Mapping[str, Any]]]:
    """Exact identity first, then KVT identification, then the explicit shop default.

    Return every compatible row, in stable order. This function never reads temperature,
    so a missing value cannot make it substitute a different material.
    """
    def stable(found: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return sorted(found, key=lambda row: tuple(sorted((str(k), str(v)) for k, v in row.items())))

    # Preserve the old article precedence, including articles that contain a full catalog name.
    article, name = (_key(material.get(field)) for field in ("article", "name"))
    found = [row for row in rows if article and article == _key(row.get("Артикул"))]
    if not found:
        found = [row for row in rows if (article and article == _key(row.get("Наименование")))
                 or (name and name == _key(row.get("Наименование")) and (not article or not row.get("Артикул")))]
    if found:
        return "exact", stable(found)

    text = " ".join((article, name))
    sizes, colours = _sizes(text), _colours(text)
    series = {_series_key(match.group()) for match in _SERIES.finditer(text)} - {"тут", "ттк"}
    if len(sizes) > 1 or len(colours) > 1 or len(series) > 1:
        return "", []
    family = _family(text)
    if family == "conflict":
        return "", []
    kvt = [row for row in rows if _KVT.search(_key(row.get("Производитель")))]
    # An explicit other catalog maker forbids replacement, even if a KVT number also occurs.
    other_makers = {_key(row.get("Производитель")).split()[0] for row in rows
                    if _key(row.get("Производитель")) and not _KVT.search(_key(row.get("Производитель")))}
    if any(re.search(r"(?<!\w)" + re.escape(maker) + r"(?!\w)", text) for maker in other_makers):
        return "", []

    def compatible(row: Mapping[str, Any]) -> bool:
        row_size = _sizes(f"{row.get('Размер до, мм', '')}/{row.get('Размер после, мм', '')}")
        row_colour = _colours(_key(row.get("Цвет") or row.get("Наименование")))
        row_series = _series_key(_CATALOG_NUMBER.sub("", _key(row.get("Серия"))))
        return (not sizes or sizes == row_size) and (not colours or colours == row_colour) and (
            not series or row_series in series) and (not family or family == _text(row.get("Группа")))

    def colour_unambiguous(found: list[Mapping[str, Any]]) -> bool:
        available = {frozenset(_colours(_key(row.get("Цвет")))) for row in found}
        return bool(colours) or (len(available) == 1 and len(next(iter(available))) == 1)

    numbers = set(_CATALOG_NUMBER.findall(text))
    numbered = [row for row in kvt if numbers & set(_CATALOG_NUMBER.findall(" ".join(
        _text(row.get(field)) for field in ("Артикул", "Серия", "Наименование"))))]
    if numbered:
        found = [row for row in numbered if compatible(row)]
        return "catalogue", stable(found) if colour_unambiguous(found) else []
    if _KVT.search(text) and sizes and series:
        found = [row for row in kvt if compatible(row)]
        return "series", stable(found) if colour_unambiguous(found) else []
    if not family or not sizes or not _generic(text):
        return "", []
    before, after = next(iter(sizes))
    if before <= after or after <= 0 or (family == "ТУТ" and before != 2 * after):
        return "", []
    found = [row for row in kvt if compatible(row) and
             _key(row.get("Клеевой слой")) == ("да" if family == "ТТК" else "нет")]
    return "default", stable(found) if colour_unambiguous(found) else []


def _temperature_text(temperature: Mapping[str, Any]) -> str:
    low, high = temperature.get("lower_c"), temperature.get("upper_c")
    if temperature.get("needs_review") or not temperature.get("kind") or (low is None and high is None):
        return MISSING
    def number(value: float) -> str:
        return f"{value:g}".replace(".", ",")
    value = (number(low) if low == high else f"{number(low)}…{number(high)}") if low is not None and high is not None \
        else f"не ниже {number(low)}" if low is not None else f"не выше {number(high)}"
    source = ", ".join(filter(None, (temperature.get("kind"), temperature.get("document"),
                                  f"стр. {temperature['page']}" if temperature.get("page") else "")))
    characteristic = temperature.get("characteristic_c")
    return f"{value} °C" + (f" ({source})" if source else "") + (
        f"; по характеристикам: {number(characteristic)} °C" if characteristic is not None else "")


def requirement_summary(item: Mapping[str, Any]) -> str:
    """One frozen text for the operation step, screen and print, with all candidate evidence."""
    candidates = item.get("candidates") or []
    if candidates:
        values = "; ".join(f"{candidate['label']}: {_temperature_text(candidate['temperature'])}" for candidate in candidates)
        default = " — производитель по умолчанию" if item.get("resolution") == "default" else ""
        review = " — проверить" if item.get("status") == "review" else ""
        return values + default + review
    return _temperature_text(item.get("temperature") or {}) if item.get("status") == "resolved" else MISSING


def requirement_text(item: Mapping[str, Any]) -> str:
    material = item.get("material") or {}
    label = " · ".join(dict.fromkeys(value for value in (material.get("article"), material.get("name")) if value))
    return (f"{label}: " if label else "") + requirement_summary(item)


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
