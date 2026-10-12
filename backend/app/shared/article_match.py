"""«Возможно есть»: which warehouse lots may be a КД position (owner, 12.10.2026).

The warehouse articles are not normalized yet, so a КД article and the lot of the same part often differ in hyphens,
case, Cyrillic/Latin twins or wording. A lot is compared with a position in tiers, the first that holds names the kind:

- ``exact`` — the same article after trim, case and Cyrillic/Latin twins (А ↔ A, С ↔ C …);
- ``fuzzy`` with a rule: ``separators`` (the same article without hyphens, spaces, dots), ``in_name`` (the КД article
  stands inside the lot's article or name, or the other way round), ``kit_name`` (the code the estimator wrote over
  the article in the kit name — `kit_article` — is the lot's article), ``wire`` (the same series, size and colour:
  «НВ4 0,2 красный» ↔ «НВ-4 0,20 красн.»), ``name`` (the same name);
- ``None`` — not a candidate.

Pure rules, no I/O; the screen colours its rows by the kind and the binding log keeps it next to the lot.
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


@dataclass(frozen=True)
class Match:
    kind: str  # exact | fuzzy
    rule: str  # "" for exact, else separators | in_name | kit_name | wire | name

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


def _code_in_kit_name(article: object, name: object) -> str:
    """`use_article`: a code-like name (no spaces, a digit) that differs from the article is the article to apply."""
    text, art = str(name or "").strip(), str(article or "").strip()
    if text and text != art and not re.search(r"\s", text) and _has_digit(text):
        return text
    return ""


_SIZE = re.compile(r"^(\d+(?:\.\d+)?)(awg|мм2|мм²|mm2|mm²|мм)?$")


def _wire_signature(*texts: object) -> tuple[str, float, str, str] | None:
    """(series, size, size unit, colour stem) of «НВ4 0,2 красный»; None when the text is not a wire's wording."""
    return _wire_of(tuple(str(text or "") for text in texts))


@lru_cache(maxsize=50_000)
def _wire_of(texts: tuple[str, ...]) -> tuple[str, float, str, str] | None:
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
                size = (float(found.group(1)), found.group(2) or "")
            elif not found and not color and token.rstrip(".").isalpha():
                color = token.rstrip(".")[:_COLOR_PREFIX + 1]
        if size is not None:
            return series, size[0], size[1], color
    return None


def _same_wire(left: tuple[str, float, str, str], right: tuple[str, float, str, str]) -> bool:
    if left[0] != right[0] or abs(left[1] - right[1]) > 1e-9:
        return False
    if left[2] and right[2] and left[2] != right[2]:
        return False
    if left[3] and right[3]:
        return left[3][:_COLOR_PREFIX] == right[3][:_COLOR_PREFIX]
    return True


def match_lot(kd_article: object, kd_name: object, lot_article: object, lot_name: object) -> Match | None:
    """How the lot (article, name) may be the КД position (article, name); None — it is not."""
    kd_art, lot_art = fold(kd_article), fold(lot_article)
    if kd_art and lot_art and kd_art == lot_art:
        return Match(EXACT, "")
    kd_c, lot_c = compact(kd_article), compact(lot_article)
    if kd_c and lot_c and kd_c == lot_c:
        return Match(FUZZY, "separators")
    lot_name_c = compact(lot_name)
    used = compact(_code_in_kit_name(kd_article, kd_name))
    if used and lot_c and (used == lot_c or (used in lot_name_c and len(used) >= _MIN_CODE)):
        return Match(FUZZY, "kit_name")
    if len(kd_c) >= _MIN_CODE and _has_digit(kd_c) and (
        (lot_c and kd_c in lot_c and kd_c != lot_c) or (lot_name_c and kd_c in lot_name_c)
    ):
        return Match(FUZZY, "in_name")
    if len(lot_c) >= _MIN_CODE and _has_digit(lot_c) and (
        compact(kd_name).find(lot_c) >= 0 or (kd_c and lot_c in kd_c and kd_c != lot_c)
    ):
        return Match(FUZZY, "in_name")
    wanted = _wire_signature(kd_article, kd_name)
    offered = _wire_signature(lot_article, lot_name)
    if wanted and offered and _same_wire(wanted, offered):
        return Match(FUZZY, "wire")
    names = compact(kd_name)
    if len(names) >= _MIN_CODE and names == lot_name_c:
        return Match(FUZZY, "name")
    return None


def binding_kind(kd_article: object, kd_name: object, lot_article: object, lot_name: object) -> str:
    """The match kind kept in the binding log: «exact», «fuzzy-<rule>» or «manual» (a lot picked by search that the
    rules would not have offered)."""
    found = match_lot(kd_article, kd_name, lot_article, lot_name)
    return found.label if found else MANUAL


def rank(match: Match | None) -> int:
    """Sort key: exact first, then the fuzzy rules in the order of trust."""
    if match is None:
        return 99
    order = ("", "separators", "kit_name", "in_name", "wire", "name")
    return order.index(match.rule) if match.rule in order else len(order)


def candidates(kd_article: object, kd_name: object, lots: Iterable) -> list[tuple[Match, object]]:
    """The lots (anything with `.article` and `.name`) that may be the position, best first; stable within a rank."""
    found = [(match, lot) for lot in lots if (match := match_lot(kd_article, kd_name, lot.article, lot.name))]
    return sorted(found, key=lambda pair: rank(pair[0]))
