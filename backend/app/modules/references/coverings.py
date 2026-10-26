"""«Оболочки» (owner 12.10.2026, 15.10.2026): the БД.МАТ rows that cover a harness — shrink tube, cambric, corrugated
tube, braid, tape — split into ТУТ / ТТК / Кембрик / Гофра / Оплётки / Ленты (a covering of no kind — «Прочее»), parsed
into per-group fields and sorted inside a group by size as a number, then by name (owner 15.10: «нормально отсортировать»).

Pure functions over a record's `fields` (the sheet header is the key). Nothing is guessed: a value comes from a sheet
column first, then from the name/series text, else it stays empty. The override «Раздел оболочек» (a column the record
gets on the first manual move) wins over the classifier."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

OVERRIDE_FIELD = "Раздел оболочек"

TUT, TTK, CAMBRIC, CORRUGATED, BRAID, TAPE, OTHER = "tut", "ttk", "cambric", "corrugated", "braid", "tape", "other"
GROUP_ORDER = (TUT, TTK, CAMBRIC, CORRUGATED, BRAID, TAPE, OTHER)
GROUP_TITLES = {
    TUT: "ТУТ — термоусадочная трубка", TTK: "ТТК — термоусадочная трубка с клеем", CAMBRIC: "Кембрик — трубка ПВХ",
    CORRUGATED: "Гофра", BRAID: "Оплётки", TAPE: "Ленты", OTHER: "Прочее",
}
# What a person types in the override cell → group id (also the short names the screen offers).
OVERRIDE_NAMES = {
    "тут": TUT, "ттк": TTK, "оплётки": BRAID, "оплётка": BRAID, "оплетки": BRAID, "оплетка": BRAID,
    "гофра": CORRUGATED, "кембрик": CAMBRIC, "ленты": TAPE, "лента": TAPE, "прочее": OTHER,
}
OVERRIDE_CHOICES = ("ТУТ", "ТТК", "Кембрик", "Гофра", "Оплётки", "Ленты", "Прочее")

# БД.МАТ «Группа» → group of the covering; None-less: groups missing here are not coverings (стяжка, клей, тройник…).
GROUP_MAP = {
    "тут": TUT, "ттк": TTK, "оплётка": BRAID, "оплетка": BRAID, "плетёнка": BRAID, "плетенка": BRAID,
    "гофра": CORRUGATED, "лента": TAPE, "кембрик": CAMBRIC,
}

_NUM = r"\d+(?:[.,]\d+)?"
_GLUE = re.compile(r"клеев|с\s+клеем|adhesive|dual[\s-]*wall|glue|\bттк\b", re.IGNORECASE)
_SHRINK = re.compile(r"термоусад|\bтут(?:нг)?\b|heat[\s-]*shrink|shrink", re.IGNORECASE)
_BRAID = re.compile(r"оплёт|оплет|плётк|плетён|плетенк|braid|sleeving|нейлон|(?<![A-Za-z])PET(?![A-Za-z])|ПЭТ", re.IGNORECASE)
_CORRUGATED = re.compile(r"гофр|corrug|(?<![A-Za-z])split", re.IGNORECASE)
_CAMBRIC = re.compile(r"кембрик", re.IGNORECASE)
_TAPE = re.compile(r"изолент|(?<![а-яё])лент[аы]?(?![а-яё])|\btape\b", re.IGNORECASE)
_WIDTH = re.compile(rf"(?<![\d.,/])({_NUM})\s*(?:мм)?\s*[хx×]", re.IGNORECASE)
_SPLIT_NO = re.compile(r"неразрез|не\s+разрез|solid|closed", re.IGNORECASE)
_SPLIT_YES = re.compile(r"разрез|split|slit", re.IGNORECASE)
_PAIR = re.compile(rf"(?<![\d.,])({_NUM})\s*/\s*({_NUM})(?![\d]|:)")
_RANGE = re.compile(rf"({_NUM})\s*[–—-]\s*({_NUM})\s*(?:мм|mm)", re.IGNORECASE)
_RATIO = re.compile(r"(?<!\d)([2-6])\s*:\s*1(?!\d)")
_TEMP = re.compile(rf"([+−-]?{_NUM})\s*(?:\.\.\.|…|÷|–|—|до)\s*\+?({_NUM})\s*°?\s*C", re.IGNORECASE)
_DENSITY = re.compile(rf"плотност\w*\s*[:=]?\s*({_NUM}\s*(?:г/м|нит\w*|%)?)", re.IGNORECASE)
_WALL = re.compile(rf"(?:стенк\w*|wall)\s*[:=]?\s*({_NUM})\s*(?:мм|mm)?", re.IGNORECASE)
_SIZE_MM = re.compile(rf"(?<![\d.,/])({_NUM})\s*мм", re.IGNORECASE)
_MODEL_NUM = re.compile(rf"[A-Za-zА-Яа-я]-({_NUM})(?![\d]|:)")
_NW = re.compile(rf"NW\s*({_NUM})", re.IGNORECASE)

COLORS = (("черн", "чёрный"), ("чёрн", "чёрный"), ("бел", "белый"), ("красн", "красный"), ("син", "синий"),
          ("жёлт", "жёлтый"), ("желт", "жёлтый"), ("зелён", "зелёный"), ("зелен", "зелёный"), ("прозрач", "прозрачный"),
          ("сер", "серый"))
MATERIAL_SHORT = (("полипропилен", "ПП"), ("полиамид", "ПА"), ("нейлон", "ПА"), ("пвх", "ПВХ"),
                  ("поливинилхлорид", "ПВХ"), ("полиэтилентерефталат", "ПЭТ"), ("пэт", "ПЭТ"), ("полиэстер", "ПЭТ"),
                  ("полиолефин", "полиолефин"), ("стекловолок", "стекловолокно"))


# Kinds inside ТУТ / ТТК (owner 25.10.2026: «рассортировка по ТУТ, ТУТнг…»). The order here is the order on the screen.
KIND_ORDER = ("plain", "ng", "ngls", "hf", "r3", "r4", "brand")
KIND_TITLES = {
    TUT: {"plain": "ТУТ — обычная (2:1)", "ng": "ТУТнг — не распространяющая горение",
          "ngls": "ТУТнг-LS — низкое дымо- и газовыделение", "hf": "ТУТ(HF) — безгалогенная",
          "r3": "ТУТ 3:1 и более", "r4": "ТУТ 4:1 и более", "brand": "Прочие серии"},
    TTK: {"plain": "ТТК — усадка не указана", "ng": "ТТКнг — не распространяющая горение",
          "ngls": "ТТКнг-LS — низкое дымо- и газовыделение", "hf": "ТТК(HF) — безгалогенная",
          "r3": "ТТК 3:1", "r4": "ТТК 4:1 и более", "brand": "Прочие серии"},
}
_HF = re.compile(r"(?<![a-z])hf(?![a-z])|безгалоген", re.IGNORECASE)
_LS = re.compile(r"нг[\s-]*ls|(?<![a-z])ls(?![a-z])|дымо", re.IGNORECASE)
_NG = re.compile(r"(?:(?<![а-яё])|(?<=тут)|(?<=ттк)|(?<=\)))нг(?![а-яё])", re.IGNORECASE)
_CATALOGUE_SERIES = re.compile(r"\d{2}-\d{4}")


def shrink_kind(name: str, series: str, material: str, ratio: str) -> str:
    """The kind of a shrink tube (one place for the rule; new imports land in a sub-group by themselves).
    1) the shrink ratio: 3:1 and more is chosen by ratio, whatever the flame class (so «ТУТ(3:1)нг-LS» is `r3`);
    2) the flame/halogen class from series + material, else from the name: HF, нг-LS, нг (the series wins over the
       name: КВТ's «ТУТнг 4/2» of series ТУТнг-LS is нг-LS);
    3) no marker: a ТУТ/ТТК series, a Rexant catalogue number or no series — «обычная»; any other brand series
       (ТСТ, HTS-14…) — «Прочие серии»."""
    match = re.match(r"\s*(\d+)", ratio or "")
    times = int(match.group(1)) if match else 0
    if times >= 4:
        return "r4"
    if times == 3:
        return "r3"
    for text in (f"{series} {material}", name):
        for kind, pattern in (("hf", _HF), ("ngls", _LS), ("ng", _NG)):
            if pattern.search(text):
                return kind
    own = re.sub(r"\s*\((?:\d{4,}|\d{2}-\d{4})\)\s*$", "", series).strip().casefold()
    if not own or _CATALOGUE_SERIES.fullmatch(own) or own.startswith(("тут", "ттк")):
        return "plain"
    return "brand"


def _text(fields: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def number(text: object) -> float | None:
    """«6,4» / «6.4 мм» / «Ø6,4» → 6.4; anything that is not one number → None."""
    match = re.fullmatch(rf"\s*[Øø⌀]?\s*([+−-]?{_NUM})\s*(?:мм|mm)?\s*", str(text or ""))
    return float(match.group(1).replace(",", ".").replace("−", "-")) if match else None


def _f(text: str) -> float:
    return float(text.replace(",", ".").replace("−", "-"))


def _blob(fields: Mapping[str, Any]) -> str:
    return " ".join(_text(fields, key) for key in ("Наименование", "Серия", "Материал"))


def classify(fields: Mapping[str, Any]) -> tuple[str | None, str]:
    """(group id, source): `None` group — not a covering at all (стяжка, клей, тройник…); source — «вручную» for the
    override cell, «по группе» for БД.МАТ «Группа», «по названию» for the text rules."""
    override = OVERRIDE_NAMES.get(_text(fields, OVERRIDE_FIELD).casefold())
    if override:
        return override, "вручную"
    name, series, glued = _text(fields, "Наименование"), _text(fields, "Серия"), _text(fields, "Клеевой слой").casefold()
    text = f"{name} {series}"
    sheet_group = _text(fields, "Группа").casefold()
    if sheet_group in GROUP_MAP:
        group = GROUP_MAP[sheet_group]
        if group == TUT and (glued in ("да", "yes", "true", "+") or _GLUE.search(text)):
            group = TTK
        return group, "по группе"
    if sheet_group:
        return None, ""
    for group, pattern in ((TTK, _GLUE), (TUT, _SHRINK), (CAMBRIC, _CAMBRIC), (BRAID, _BRAID), (CORRUGATED, _CORRUGATED),
                           (TAPE, _TAPE)):
        if pattern.search(text):
            if group == TUT and (glued in ("да", "yes", "true", "+")):
                group = TTK
            return group, "по названию"
    return None, ""


@dataclass
class Parsed:
    """Per-group fields; unused ones stay None. Ø in mm, temperature in °C."""
    before_mm: float | None = None      # ТУТ/ТТК: Ø до усадки
    after_mm: float | None = None       # ТУТ/ТТК: Ø после усадки
    ratio: str = ""                     # 2:1 / 3:1 / 4:1
    wall_mm: float | None = None
    adhesive: str = ""                  # «да» / «нет» / ""
    nominal_mm: float | None = None     # оплётки
    min_mm: float | None = None
    max_mm: float | None = None
    density: str = ""
    inner_mm: float | None = None       # гофра
    outer_mm: float | None = None
    split: bool | None = None
    width_mm: float | None = None       # ленты: ширина
    material: str = ""
    material_short: str = ""
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    color: str = ""
    maker: str = ""
    series: str = ""
    kind: str = ""                      # ТУТ/ТТК: see shrink_kind
    series_family: str = ""             # the series without a catalogue number: «ТУТнг (65994)», «ТУТ (20-3006)» → «ТУТнг», «ТУТ»
    notes: list[str] = field(default_factory=list)  # which values were taken from the text, not from a column


def _short_material(material: str) -> str:
    low = material.casefold()
    return next((short for stem, short in MATERIAL_SHORT if stem in low), "")


def _colour(fields: Mapping[str, Any]) -> str:
    column = _text(fields, "Цвет")
    if column:
        return column
    name = _text(fields, "Наименование").casefold()
    return next((full for stem, full in COLORS if re.search(rf"(?<![а-яё]){stem}", name)), "")


def _temperature(fields: Mapping[str, Any], parsed: Parsed) -> None:
    low, high = number(_text(fields, "Темп. от, °C")), number(_text(fields, "Темп. до, °C"))
    if low is None and high is None:
        match = _TEMP.search(_blob(fields))
        if match:
            low, high = _f(match.group(1)), _f(match.group(2))
            if low > 0 and high > 0 and low < high and "-" in match.group(1):
                low = -abs(low)
            parsed.notes.append("температура из названия")
    parsed.temp_min_c, parsed.temp_max_c = low, high


def _pair(fields: Mapping[str, Any]) -> tuple[float | None, float | None, bool]:
    """The two sizes of a row: the sheet columns «Размер до / после», else «A/B» in the name. Last: from the text."""
    before, after = number(_text(fields, "Размер до, мм")), number(_text(fields, "Размер после, мм"))
    if before is not None or after is not None:
        return before, after, False
    match = _PAIR.search(_text(fields, "Наименование"))
    if match:
        return _f(match.group(1)), _f(match.group(2)), True
    return None, None, False


def parse(fields: Mapping[str, Any], group: str) -> Parsed:
    name = _text(fields, "Наименование")
    material = _text(fields, "Материал")
    parsed = Parsed(material=material, material_short=_short_material(material), color=_colour(fields),
                    maker=_text(fields, "Производитель"), series=_text(fields, "Серия"))
    parsed.series_family = re.sub(r"\s*\((?:\d{4,}|\d{2}-\d{4})\)\s*$", "", parsed.series)
    if group == BRAID:  # the braid's model carries its size: GTRPA-10, XP-6 → GTRPA, XP
        parsed.series_family = re.sub(rf"-{_NUM}$", "", parsed.series_family)
    _temperature(fields, parsed)
    before, after, from_name = _pair(fields)
    if from_name:
        parsed.notes.append("Ø из названия")
    if group in (TUT, TTK):
        parsed.before_mm, parsed.after_mm = before, after
        ratio = _text(fields, "Коэф. усадки")
        if not ratio:
            match = _RATIO.search(f"{name} {parsed.series}")
            ratio = f"{match.group(1)}:1" if match else ""
            if ratio:
                parsed.notes.append("усадка из названия")
        parsed.ratio = ratio
        parsed.kind = shrink_kind(name, parsed.series, material, ratio)
        glued = _text(fields, "Клеевой слой").casefold()
        parsed.adhesive = "да" if glued in ("да", "yes", "true", "+") or group == TTK else "нет" if glued in ("нет", "no", "false", "-") else ""
        wall = _WALL.search(f"{name} {material}")
        parsed.wall_mm = _f(wall.group(1)) if wall else None
    elif group == BRAID:
        low, high = (before, after) if before is None or after is None else (min(before, after), max(before, after))
        if low is None and high is None:
            ranged = _RANGE.search(name)
            if ranged:
                low, high = _f(ranged.group(1)), _f(ranged.group(2))
                parsed.notes.append("диапазон из названия")
        if low is not None and high is not None and low != high:
            parsed.min_mm, parsed.max_mm = low, high
            model = _MODEL_NUM.search(f"{parsed.series} {name}")
            nominal = _f(model.group(1)) if model else None
            parsed.nominal_mm = nominal if nominal is not None and low <= nominal <= high else None
        else:
            single = low if low is not None else high
            if single is None:
                size = _SIZE_MM.search(name)
                single = _f(size.group(1)) if size else None
            parsed.nominal_mm = single
        density = _DENSITY.search(f"{name} {parsed.series} {material}")
        parsed.density = density.group(1).strip() if density else ""
    elif group == CORRUGATED:
        inner, outer = before, after
        if inner is None:
            nw = _NW.search(parsed.series)
            size = _SIZE_MM.search(name)
            inner = _f(size.group(1)) if size else _f(nw.group(1)) if nw else None
            if inner is not None:
                parsed.notes.append("Ø из названия")
        parsed.inner_mm, parsed.outer_mm = inner, outer
        text = f"{name} {parsed.series}"
        parsed.split = False if _SPLIT_NO.search(text) else True if _SPLIT_YES.search(text) else None
    elif group == TAPE:
        width = before
        if width is None:
            size = _SIZE_MM.search(name) or _WIDTH.search(name)
            width = _f(size.group(1)) if size else None
            if width is not None:
                parsed.notes.append("ширина из названия")
        parsed.width_mm = width
    else:
        parsed.before_mm, parsed.after_mm = before, after
    return parsed


def diameter(parsed: Parsed, group: str) -> float | None:
    """The size a group is ordered by."""
    if group in (TUT, TTK, CAMBRIC, OTHER):
        return parsed.before_mm
    if group == TAPE:
        return parsed.width_mm
    if group == BRAID:
        return parsed.nominal_mm if parsed.nominal_mm is not None else parsed.min_mm
    return parsed.inner_mm


def _natural(text: str) -> list[tuple[int, Any]]:
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", text.casefold()) if part]


def sort_key(group: str, parsed: Parsed, name: str):
    """Kind inside ТУТ/ТТК, then size as a number (Ø, a tape's width; unknown last), then the name with numbers as
    numbers (owner 15.10.2026: the series is a column, not the order)."""
    size = diameter(parsed, group)
    kind = KIND_ORDER.index(parsed.kind) if parsed.kind in KIND_ORDER else 0
    return (kind, size is None, size or 0.0, _natural(name))


def build(records: list[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """[(record id, fields)] → `{counts, rows}`; rows of the covering groups only, in group → size → name order."""
    rows: list[tuple[str, Parsed, dict[str, Any]]] = []
    for record_id, fields in records:
        group, source = classify(fields)
        if group is None:
            continue
        parsed = parse(fields, group)
        auto = classify({key: value for key, value in fields.items() if key != OVERRIDE_FIELD})[0]
        rows.append((group, parsed, {
            "id": record_id, "name": _text(fields, "Наименование"), "group": group, "group_source": source,
            "auto_group": auto, "datasheet": _text(fields, "Ссылка на datasheet"), "unit": _text(fields, "Ед. изм."),
            **{key: value for key, value in parsed.__dict__.items()},
        }))
    rows.sort(key=lambda item: (GROUP_ORDER.index(item[0]),) + sort_key(item[0], item[1], item[2]["name"]))
    counts = {group: sum(1 for item in rows if item[0] == group) for group in GROUP_ORDER}
    kinds = {group: [{"id": kind, "title": KIND_TITLES[group][kind]} for kind in KIND_ORDER
                     if any(item[0] == group and item[1].kind == kind for item in rows)] for group in KIND_TITLES}
    return {"counts": counts, "titles": GROUP_TITLES, "kinds": kinds, "rows": [item[2] for item in rows]}
