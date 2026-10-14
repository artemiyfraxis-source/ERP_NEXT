"""Price book rules (ported from Roller_Buyer price-book.ts): rows of «who offered what for how much» grouped into
items, and a source supplier name matched to the directory. The xlsx import is not here — the integrator's importer
fills `purchasing_price_refs`; these pure functions serve it and the tests.

Money: `unit_price4` = unit price × 10 000 in the source currency (a Chinese connector costs 0.0177 CNY)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .items import alias_key, guess_category, part_key

PRICE_CURRENCIES = ("RUB", "CNY", "USD")

_LATIN_TO_CYRILLIC = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х",
    "Y": "У",
}
_HAS_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_WORD = re.compile(r"\S+")
_BY_LENGTH_TYPES = frozenset({
    "Коаксиальный кабель", "Кабель", "Провод монтажный", "Термоусадочная трубка", "Оплётка",
})
_TYPE_CATEGORY = {
    "Коаксиальный соединитель": "connector", "Соединитель": "connector", "Терминал": "connector",
    "Коаксиальный кабель": "cable", "Кабель": "cable", "Провод монтажный": "cable", "Микросхема": "ekb",
}


def fold_mixed_script(text: str) -> str:
    """«Т/уcaд. тpубкa» typed with Latin look-alikes → Cyrillic where the word is Russian (RBF, mm stay Latin)."""
    def fix(match: re.Match) -> str:
        word = match.group(0)
        if not _HAS_CYRILLIC.search(word):
            return word
        return "".join(_LATIN_TO_CYRILLIC.get(ch, ch) for ch in word)

    return _WORD.sub(fix, text)


def category_for_type(item_type: str, name: str = "") -> str:
    return _TYPE_CATEGORY.get(item_type) or (guess_category(name) if name else "other")


def unit_for_type(item_type: str) -> str:
    return "м" if item_type in _BY_LENGTH_TYPES else "шт"


@dataclass(frozen=True)
class PriceBookRow:
    row: int
    article: str
    name: str = ""
    type: str = ""
    series: str = ""
    manufacturer: str = ""
    offered_part: str = ""
    supplier_name: str = ""
    qty: float | None = None
    currency: str | None = None  # None — the row has no price
    price: float | None = None
    vat_included: bool = False
    lead_time: str = ""
    category: str | None = None
    item_manufacturer: str | None = None
    unit: str | None = None


@dataclass
class PriceBookGroup:
    key: str
    name: str
    part_number: str
    manufacturer: str
    type: str
    category: str
    unit: str
    rows: list[PriceBookRow] = field(default_factory=list)


def is_part_like(text: str) -> bool:
    """A part number is one token; anything with spaces is a description."""
    return text != "" and not re.search(r"\s", text) and len(text) <= 100


def _mode(values: list[str]) -> str:
    counts = Counter(value for value in values if value)
    best, top = "", 0
    for value, count in counts.items():  # insertion order: the first of equals wins, as in the original
        if count > top:
            best, top = value, count
    return best


def build_name(article: str, item_type: str, series: str, manufacturer: str) -> str:
    if _HAS_CYRILLIC.search(article) and len(article) > 20:
        return article
    own = part_key(article) or ""

    def has(text: str) -> bool:
        other = part_key(text) or ""
        # Short words («GH» in «GHR-05V-S») are substrings by chance — only long ones count as repeated.
        return own == other or (len(other) >= 4 and other in own)

    extra = ", ".join(part for part in (
        manufacturer if manufacturer and not has(manufacturer) else "", series if series and not has(series) else "",
    ) if part)
    head = " ".join(part for part in (item_type, article) if part)
    return head + (f" ({extra})" if extra else "")


def group_price_book(rows: list[PriceBookRow]) -> list[PriceBookGroup]:
    """Rows → one group per article (normalized), or per name when the article is descriptive."""
    groups: dict[str, list[PriceBookRow]] = {}
    for source in rows:
        article = fold_mixed_script(source.article.strip())
        name = fold_mixed_script(source.name.strip())
        if is_part_like(article):
            key = f"p:{part_key(article)}" if part_key(article) else ""
        else:
            key = f"n:{alias_key(name or article)}" if alias_key(name or article) else ""
        if not key:
            continue
        groups.setdefault(key, []).append(PriceBookRow(**{**source.__dict__, "article": article, "name": name}))
    out = []
    for key, items in groups.items():
        article = _mode([row.article for row in items])
        item_type = _mode([row.type for row in items])
        # The item's manufacturer is known only from rows that offer the original part.
        original = [row for row in items if row.offered_part and part_key(row.offered_part) == part_key(article)]
        manufacturer = _mode([row.item_manufacturer or "" for row in items]) or _mode([row.manufacturer for row in original])
        series = _mode([row.series for row in items])
        name = (_mode([row.name for row in items]) or build_name(article, item_type, series, manufacturer))[:300]
        category = next((row.category for row in items if row.category), None) or category_for_type(item_type, name)
        unit = next((row.unit for row in items if row.unit), None) or unit_for_type(item_type)
        out.append(PriceBookGroup(
            key=key, name=name, part_number=article if is_part_like(article) else "", manufacturer=manufacturer,
            type=item_type, category=category, unit=unit, rows=items,
        ))
    return out


# ---- suppliers ----------------------------------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_LETTER = r"[^\W\d_]"
_LEGAL_FORMS = re.compile(
    r"общество с ограниченной ответственностью|акционерное общество|закрытое акционерное общество"
    rf"|индивидуальный предприниматель|(?<!{_LETTER})(?:ооо|ао|зао|пао|оао|ип|ltd|co|llc|jsc)(?!{_LETTER})",
    re.IGNORECASE,
)


def source_supplier_key(name: str) -> str:
    """«ООО "Группа МЕТТАТРОН"» and «METTATRON» → comparable Latin keys."""
    text = _LEGAL_FORMS.sub(" ", name.lower())
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    return re.sub(r"[^a-z0-9]", "", text)


def match_supplier(source_name: str, directory: list[tuple[object, str]]) -> tuple[object, str] | None:
    """The directory supplier (id, name) whose name contains the source name or the other way round; unique only."""
    key = source_supplier_key(source_name)
    if len(key) < 4:
        return next((entry for entry in directory if source_supplier_key(entry[1]) == key), None)
    hits = []
    for entry in directory:
        other = source_supplier_key(entry[1])
        if other == key or (len(other) >= 4 and (other in key or key in other)):
            hits.append(entry)
    return hits[0] if len(hits) == 1 else None
