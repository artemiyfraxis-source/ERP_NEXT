"""«Возможно есть»: which warehouse lots may be a КД position (owner, 12.10.2026; by name first — owner, 13.10.2026).

The warehouse articles are not normalized yet, so a КД position and the lot of the same part often differ in hyphens,
case, Cyrillic/Latin twins or wording. The position's «Наименование» is what the warehouse calls the part (owner,
13.10: «сверка нужно не по артикулу КД а по наименованию с артикулом на складе» — «BL2018P-04 / SMP-04V-BC» is the lot
SMP-04V-BC), so a lot is compared by the NAME first and by the КД article only when the name finds nothing.

By name (``Match.basis == "name"``), the name against the lot's article:

- ``exact`` ``name_article`` — the same text after trim, case and Cyrillic/Latin twins (А ↔ A, С ↔ C …);
- ``fuzzy`` ``name_separators`` (the same letters and digits without hyphens, spaces, dots), ``name_in`` (a code-like
  name stands inside the lot's article or name, or the lot's article inside the name), ``name_wire`` (the same series,
  size and colour: «НВ4 0,2 красный» ↔ «НВ-4 0,20 красн.»).

By the КД article (``basis == "article"``), the fallback: ``exact``; ``fuzzy`` with ``separators``, ``in_name`` (the
article inside the lot's article or name, or the other way round), ``wire``, ``name`` (the lot's name is the same name).

Pure rules, no I/O; the screen colours its rows by the kind, says by what the lot matched, and the binding log keeps
the label next to the lot.
"""

from __future__ import annotations

import re
from functools import lru_cache
from collections.abc import Iterable
from dataclasses import dataclass

EXACT = "exact"
FUZZY = "fuzzy"
MANUAL = "manual"

# Cyrillic letters drawn like Latin ones: both sides fold to Latin, so «НВ4» and «HB4» are one article.
_TWINS = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t",
    "у": "y", "х": "x",
})
_MIN_CODE = 4
_COLOR_PREFIX = 3


_NAME_RULES = ("name_article", "name_separators", "name_in", "name_wire")
_ARTICLE_RULES = ("", "separators", "in_name", "wire", "name")


@dataclass(frozen=True)
class Match:
    kind: str  # exact | fuzzy
    rule: str  # one of _NAME_RULES (by the name) or _ARTICLE_RULES ("" — the same КД article)

    @property
    def basis(self) -> str:
        """What the lot matched by: «name» — the position's name, «article» — the КД article."""
        return "name" if self.rule in _NAME_RULES else "article"

    @property
    def label(self) -> str:
        return self.kind if not self.rule else f"{self.kind}-{self.rule}"


@lru_cache(maxsize=50_000)
def _fold(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip()).casefold().replace(",", ".")
    return cleaned.translate(_TWINS)


def fold(value: object) -> str:
    """Case, spaces and Cyrillic twins out of the way; a decimal comma is a dot."""
    return _fold(str(value or ""))


@lru_cache(maxsize=50_000)
def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", _fold(text))


def compact(value: object) -> str:
    """Only letters and digits of the folded value: hyphens, spaces, dots and slashes are not part of the article."""
    return _compact(str(value or ""))


def _has_digit(value: str) -> bool:
    return any(char.isdigit() for char in value)


def _code_like(value: object) -> str:
    """The compact code of a name written as a code (no spaces, a digit, 4+ letters and digits), else ""."""
    text = str(value or "").strip()
    if not text or re.search(r"\s", text):
        return ""
    code = compact(text)
    return code if len(code) >= _MIN_CODE and _has_digit(code) else ""


# A size, optionally with the cores of a cable in front: «0,35», «0,5мм2», «4х0,35мм» (the Cyrillic «х» folds to «x»).
_SIZE = re.compile(r"^(?:(\d+)x)?(\d+(?:\.\d+)?)(awg|мм2|мм²|mm2|mm²|мм|mm)?$")
_Wire = tuple[str, float, str, str, str]  # series, size, size unit, colour stem, cores


def _wire_signature(*texts: object) -> _Wire | None:
    """(series, size, size unit, colour stem, cores) of «НВ4 0,2 красный»; None when the text is not a wire's wording."""
    return _wire_of(tuple(str(text or "") for text in texts))


@lru_cache(maxsize=50_000)
def _wire_of(texts: tuple[str, ...]) -> _Wire | None:
    for text in texts:
        tokens = [token for token in re.split(r"[\s/]+", fold(text).replace("×", "x")) if token]
        if len(tokens) < 2:
            continue
        series = compact(tokens[0])
        if not series or series.isdigit() or not any(char.isalpha() for char in series):
            continue
        size = None
        color = ""
        for token in tokens[1:]:
            found = _SIZE.match(token.rstrip("."))
            if found and size is None:
                size = (float(found.group(2)), found.group(3) or "", found.group(1) or "")
            elif not found and not color and token.rstrip(".").isalpha():
                color = token.rstrip(".")[:_COLOR_PREFIX + 1]
        if size is not None:
            return series, size[0], size[1], color, size[2]
    return None


def _same_wire(left: _Wire, right: _Wire) -> bool:
    if left[0] != right[0] or abs(left[1] - right[1]) > 1e-9 or left[4] != right[4]:
        return False
    if left[2] and right[2] and left[2] != right[2]:
        return False
    if left[3] and right[3]:
        return left[3][:_COLOR_PREFIX] == right[3][:_COLOR_PREFIX]
    return True


def _by_name(kd_name: object, lot_article: object, lot_name: object) -> Match | None:
    """How the lot may be the position judged by the position's name against the lot's article."""
    name, lot_art = fold(kd_name), fold(lot_article)
    if not name:
        return None
    if lot_art and name == lot_art:
        return Match(EXACT, "name_article")
    name_c, lot_c = compact(kd_name), compact(lot_article)
    if name_c and lot_c and name_c == lot_c:
        return Match(FUZZY, "name_separators")
    code = _code_like(kd_name)
    if code and ((lot_c and code in lot_c) or code in compact(lot_name)):
        return Match(FUZZY, "name_in")
    if len(lot_c) >= _MIN_CODE and _has_digit(lot_c) and lot_c in name_c:
        return Match(FUZZY, "name_in")
    wanted, offered = _wire_signature(kd_name), _wire_signature(lot_article, lot_name)
    if wanted and offered and _same_wire(wanted, offered):
        return Match(FUZZY, "name_wire")
    return None


def _by_article(kd_article: object, kd_name: object, lot_article: object, lot_name: object) -> Match | None:
    """The fallback: how the lot may be the position judged by the КД article (and, last, the same lot name)."""
    kd_art, lot_art = fold(kd_article), fold(lot_article)
    if kd_art and lot_art and kd_art == lot_art:
        return Match(EXACT, "")
    kd_c, lot_c = compact(kd_article), compact(lot_article)
    if kd_c and lot_c and kd_c == lot_c:
        return Match(FUZZY, "separators")
    lot_name_c = compact(lot_name)
    if len(kd_c) >= _MIN_CODE and _has_digit(kd_c) and (
        (lot_c and kd_c in lot_c and kd_c != lot_c) or (lot_name_c and kd_c in lot_name_c)
    ):
        return Match(FUZZY, "in_name")
    if len(lot_c) >= _MIN_CODE and _has_digit(lot_c) and kd_c and lot_c in kd_c and kd_c != lot_c:
        return Match(FUZZY, "in_name")
    wanted, offered = _wire_signature(kd_article), _wire_signature(lot_article, lot_name)
    if wanted and offered and _same_wire(wanted, offered):
        return Match(FUZZY, "wire")
    names = compact(kd_name)
    if len(names) >= _MIN_CODE and names == lot_name_c:
        return Match(FUZZY, "name")
    return None


def match_lot(kd_article: object, kd_name: object, lot_article: object, lot_name: object) -> Match | None:
    """How the lot (article, name) may be the КД position (article, name): by the name first, then by the КД article;
    None — it is not."""
    return _by_name(kd_name, lot_article, lot_name) or _by_article(kd_article, kd_name, lot_article, lot_name)


def binding_kind(kd_article: object, kd_name: object, lot_article: object, lot_name: object) -> str:
    """The match kind kept in the binding log: «exact», «fuzzy-<rule>» or «manual» (a lot picked by search that the
    rules would not have offered)."""
    found = match_lot(kd_article, kd_name, lot_article, lot_name)
    return found.label if found else MANUAL


def rank(match: Match | None) -> int:
    """Sort key: by the name before by the article, exact first, then the fuzzy rules in the order of trust."""
    if match is None:
        return 99
    order = _NAME_RULES + _ARTICLE_RULES
    return order.index(match.rule) if match.rule in order else len(order)


def candidates(kd_article: object, kd_name: object, lots: Iterable) -> list[tuple[Match, object]]:
    """The lots (anything with `.article` and `.name`) that may be the position, best first; stable within a rank.
    When the name finds any lot, only those are offered — the КД article is the fallback for a name that finds none."""
    found = [(match, lot) for lot in lots if (match := match_lot(kd_article, kd_name, lot.article, lot.name))]
    if any(match.basis == "name" for match, _lot in found):
        found = [pair for pair in found if pair[0].basis == "name"]
    return sorted(found, key=lambda pair: rank(pair[0]))
