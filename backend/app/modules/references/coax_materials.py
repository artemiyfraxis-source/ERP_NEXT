"""«Коаксиальные кабели»: материалы жилы, диэлектрика, экрана и оболочки (владелец 15.10.2026).

Четыре своих столбца БД.ПРОВ. Значение берётся из точной карточки кабеля (слои `construction` в
`backend/data/cables/coax-cables.json` — проверенный источник), иначе разбирается из текста строки:
«Жила: проволок × диаметр, мм» («1×0,94 (сталь омеднённая посеребрённая, сплошная)»), «Изоляция» («ПЭ (оболочка ПВХ)»,
«ПТФЭ / оболочка ФЭП») и «Экран» («оплётка медь лужёная 95 %»). Не узнан материал — пусто, не догадка.

Словарь: жила — медь, медь посеребрённая, медь лужёная, сталь омеднённая [посеребрённая], алюминий [омеднённый];
диэлектрик — ПЭ, ПЭ вспененный, ПЭ сшитый, ПТФЭ, ПТФЭ низкой плотности, ФЭП; оболочка — ПВХ, ПЭ, ФЭП, ПТФЭ, ТПЭ, LSZH;
экран — «материал, вид» по слоям через « + » («алюминий, фольга + медь лужёная, оплётка 95 %»).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

CORE, DIELECTRIC, SHIELD, JACKET = "Материал жилы", "Материал диэлектрика", "Материал экрана", "Материал оболочки"
MATERIAL_COLUMNS = (CORE, DIELECTRIC, SHIELD, JACKET)
LAYER_COLUMNS = {"core": CORE, "dielectric": DIELECTRIC, "shield": SHIELD, "jacket": JACKET}
CORE_FIELD, INSULATION_FIELD, SHIELD_FIELD = "Жила: проволок × диаметр, мм", "Изоляция", "Экран"
CARD_SOURCE = "карточка"


def _low(text: object) -> str:
    return " ".join(str(text or "").split()).lower().replace("ё", "е")


def _metal(low: str) -> str:
    """The metal of a conductor or a screen layer, in the vocabulary; '' — none named."""
    silver = "посеребр" in low or "spc" in low or "spcw" in low
    if "сталь" in low or "ccs" in low:
        return "сталь омеднённая посеребрённая" if silver else "сталь омеднённая"
    if "алюмин" in low and "магн" in low:
        return "алюминий-магний"
    if "алюмин" in low or "cca" in low:
        if "медн" in low or "cca" in low:
            return "алюминий омеднённый лужёный" if "лужен" in low else "алюминий омеднённый"
        return "алюминий"
    if "мед" in low or "copper" in low:
        if silver:
            return "медь посеребрённая"
        return "медь лужёная" if "лужен" in low else "медь"
    return ""


def core_material(text: object) -> str:
    """«1×0,94 (сталь омеднённая посеребрённая, сплошная)» → «сталь омеднённая посеребрённая»; «7 проволок, ⌀1,42 (медь BC)»
    → «медь»; «медь отожжённая сплошная» (карточка) → «медь»; «1×2,7» → ''."""
    return _metal(_low(text))


def _dielectric(text: str) -> str:
    low = _low(text)
    if "птфэ" in low or "ptfe" in low or "фторопласт-4" in low:
        if "низкой плотности" in low:
            return "ПТФЭ низкой плотности"
        return "ПТФЭ вспененный" if "вспен" in low else "ПТФЭ"
    if "фэп" in low or "fep" in low or "ф-4мб" in low:
        return "ФЭП"
    if re.search(r"(?<![а-яa-z])(пэ|pe|fpe)(?![а-яa-z])", low) or "полиэтилен" in low:
        if "вспен" in low or "fpe" in low:
            return "ПЭ вспененный"
        return "ПЭ сшитый" if "сшит" in low else "ПЭ"
    return ""


def _jacket(text: str) -> str:
    low = _low(text)
    if "lszh" in low or "безгалоген" in low:
        return "LSZH"
    if "пвх" in low or "pvc" in low:
        return "ПВХ"
    if "птфэ" in low or "ptfe" in low:
        return "ПТФЭ"
    if "фэп" in low or "fep" in low:
        return "ФЭП"
    if "тпэ" in low or "tpe" in low:
        return "ТПЭ"
    if re.search(r"(?<![а-яa-z])(пэ|pe)(?![а-яa-z])", low) or "полиэтилен" in low:
        return "ПЭ"
    if "полиуретан" in low or re.search(r"(?<![а-яa-z])(пур|pur)(?![а-яa-z])", low):
        return "ПУР"
    return ""


_ROLE = re.compile(r"\((диэлектрик|оболочка)\)\s*$")
_SHEATHED = re.compile(r"^(.*?)\s*\(\s*оболочка\s+(.*)\)\s*$", re.IGNORECASE)


def insulation_materials(text: object) -> tuple[str, str]:
    """«Изоляция» → (диэлектрик, оболочка): «ПЭ (оболочка ПВХ)» → (ПЭ, ПВХ); «ПТФЭ / оболочка ФЭП» → (ПТФЭ, ФЭП);
    «ПТФЭ ⌀1,52 (диэлектрик) / ФЭП ⌀2,90 (оболочка)» → (ПТФЭ, ФЭП); «ПЭ вспененный» → (ПЭ вспененный, '')."""
    value = " ".join(str(text or "").split())
    if not value:
        return "", ""
    parts = [part.strip() for part in value.split(" / ")]
    if len(parts) > 1:
        dielectric, jacket = "", ""
        for part in parts:
            role = _ROLE.search(part)
            if (role and role.group(1) == "оболочка") or _low(part).startswith("оболочка"):
                jacket = jacket or _jacket(part)
            else:
                dielectric = dielectric or _dielectric(part)
        return dielectric, jacket
    sheathed = _SHEATHED.match(value)
    if sheathed:
        return _dielectric(sheathed.group(1)), _jacket(sheathed.group(2))
    return _dielectric(value), ""


_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%(?:\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*%)?")
_FORMS = (("оплет", "оплётка"), ("фольг", "фольга"), ("лент", "лента"), ("трубк", "трубка"))


def _coverage(low: str) -> str:
    found = _PERCENT.findall(low)
    if len(found) != 1:  # none, or one per layer of a double braid («⌀2,0 (93%) и ⌀2,4 (95%)») — not one number
        return ""
    low_end, high_end = found[0]
    return f"{low_end}–{high_end} %" if high_end else f"{low_end} %"


def _shield_part(low: str, double: bool) -> tuple[str, str]:
    """One screen layer → («медь лужёная, оплётка пропаянная», its form)."""
    form = next((name for stem, name in _FORMS if stem in low), "трубка" if "гофр" in low else "")
    extra = []
    if form == "трубка" and "гофр" in low:
        extra.append("гофрированная")
    if form == "оплётка" and double:
        extra.append("двойная")
    if "пропаян" in low:
        extra.append("пропаянная")
    kind = " ".join([form, *extra]).strip()
    metal = _metal(low)
    return (f"{metal}, {kind}" if metal and kind else metal or kind), form


def shield_material(text: object) -> str:
    """«Экран» → материал по слоям: «оплётка медь лужёная 95 %» → «медь лужёная, оплётка 95 %»; «фольга алюминиевая +
    оплётка медь лужёная» → «алюминий, фольга + медь лужёная, оплётка»; «двойной (алюминиевая фольга и луженая медная
    оплетка), оплётка 87%» → «алюминий, фольга + медь лужёная, оплётка 87 %»; «гофрированная медная трубка» → «медь,
    трубка гофрированная». Ни одного металла («оплётка 85%», «DF, оплётка 95%») — ''."""
    low = _low(text)
    if not low:
        return ""
    # «двойной (A и B)» — two layers; «двойная оплётка … (SPC): ⌀2,0 (93%) и ⌀2,4 (95%)» — one braid, doubled.
    grouped = re.search(r"\(([^()]*\s+и\s+[^()]*)\)", low) if low.startswith("двойн") else None
    pieces = re.split(r"\s+и\s+", grouped.group(1)) if grouped else re.split(r"\s*\+\s*", low)
    double = not grouped and "двойн" in low
    parts = [_shield_part(piece, double) for piece in pieces if piece.strip()]
    if not any(_metal(piece) for piece in pieces):
        return ""
    coverage = _coverage(low)
    texts = [text for text, _form in parts]
    braid = max((index for index, (_text, form) in enumerate(parts) if form == "оплётка"), default=-1)
    if coverage and braid >= 0:
        texts[braid] = f"{texts[braid]} {coverage}"
    return " + ".join(item for item in texts if item)


def card_materials(layers: Mapping[str, str]) -> dict[str, str]:
    """Слои карточки (layer → material в словаре `coax-vocabulary.json`) → столбцы материалов; пустой слой не даёт ничего."""
    parse = {"core": core_material, "dielectric": _dielectric, "shield": shield_material, "jacket": _jacket}
    values = {LAYER_COLUMNS[layer]: parse[layer](material) for layer, material in layers.items() if layer in parse}
    return {column: value for column, value in values.items() if value}


def coax_materials(fields: Mapping[str, str], card_layers: Mapping[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """Столбец → (значение, источник) для строки БД.ПРОВ: слой точной карточки, иначе разбор текста строки; столбец без
    материала не попадает в ответ. Источник — «карточка» или имя столбца, из которого разобрано."""
    card = card_materials(card_layers or {})
    dielectric, jacket = insulation_materials(fields.get(INSULATION_FIELD))
    parsed = {
        CORE: (core_material(fields.get(CORE_FIELD)), CORE_FIELD),
        DIELECTRIC: (dielectric, INSULATION_FIELD),
        SHIELD: (shield_material(fields.get(SHIELD_FIELD)), SHIELD_FIELD),
        JACKET: (jacket, INSULATION_FIELD),
    }
    result: dict[str, tuple[str, str]] = {}
    for column in MATERIAL_COLUMNS:
        if card.get(column):
            result[column] = (card[column], CARD_SOURCE)
        elif parsed[column][0]:
            result[column] = parsed[column]
    return result
