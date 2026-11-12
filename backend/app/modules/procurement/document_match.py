"""Which row of the supplier's document prices which line of the order (owner, 05.10: «Вставить КП / счёт»).

A part number decides first: SHR-02V-S-B, 510471000 match after spaces, dashes, dots and case are
dropped (and Cyrillic look-alikes read as Latin: «С» typed for «C»). Confirmed numeric Molex articles also compare
without leading zeros. Stored keys retain the historical spelling rules. A part number counts only when one line of the
order carries it. Then the name words, then the quantity as the tie-breaker. Each line takes at most one row and each
row prices at most one line. Confidence: «article» (part number), «name» (most words agree), «weak» (some words) —
the buyer checks the weak ones before applying.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.shared.article_key import article_key, is_molex_article
from app.shared.article_maker import normalize_maker

from .document_items import DocumentItem, is_code
from .kp import normalize_name

_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
_SPLIT = re.compile(r"[\s,;()\[\]«»\"']+")


@dataclass(frozen=True)
class OrderLineCandidate:
    key: str
    article: str | None
    names: tuple[str | None, ...]
    quantity: Decimal | None = None


@dataclass(frozen=True)
class DocumentMatch:
    key: str
    item_index: int
    confidence: str  # «article» / «name» / «weak»


def code_key(value: str | None) -> str:
    """«SHR 02V-s-b» → «SHR02VSB»: what is left of a part number once spaces, dashes and case are dropped."""
    return re.sub(r"[\s\-‐‑‒–—_./\\]", "", str(value or "")).upper().translate(_HOMOGLYPHS)


def comparison_code_key(value: str | None, *, manufacturer: str = "", catalog_molex: bool = False) -> str:
    """Comparison only; writers must keep using code_key for historical storage keys.

    Apply the identity policy before the legacy punctuation fold: a slash, dot or suffix
    must not turn an unknown article into evidence of a numeric Molex family.
    """
    return code_key(article_key(value, manufacturer=manufacturer, catalog_molex=catalog_molex))


def _molex_code_key(value: str | None, *, manufacturer: str) -> str:
    """Only numeric Molex evidence may establish a new, non-legacy match."""
    candidate = article_key(value, manufacturer=manufacturer)
    if candidate.isascii() and candidate.isdigit() and is_molex_article(value, manufacturer=manufacturer):
        return candidate
    return ""


def _words(text: str | None) -> list[str]:
    return [word for word in _SPLIT.split(str(text or "")) if word]


def _manufacturer(*texts: str | None) -> str:
    """Use a standalone maker label in the row, never a substring of a part number."""
    makers = {
        "TE" if maker == "AMP" else maker
        for text in texts for word in _words(text)
        if (maker := normalize_maker(word)) in {
            "MOLEX", "JST", "HIROSE", "JAE", "TE", "CONNFLY", "AMASS", "AMP",
            "RUICHI", "SCONDAR", "WEIPU", "CNLINKO", "SAMTEC", "ROSENBERGER",
        }
    }
    return next(iter(makers)) if len(makers) == 1 else ("CONFLICT" if makers else "")


def _codes(text: str | None, manufacturer: str = "", *, molex_only: bool = False) -> set[str]:
    key = _molex_code_key if molex_only else comparison_code_key
    return {key(word, manufacturer=manufacturer) for word in _words(text) if is_code(word)} - {""}


def _item_keys(item: DocumentItem, manufacturer: str = "", *, molex_only: bool = False) -> set[str]:
    """Part numbers of the row, with neighbouring words joined («SHR 02V-S-B» is one number split by a space)."""
    words = _words(item.name)
    key = _molex_code_key if molex_only else comparison_code_key
    keys = _codes(item.name, manufacturer, molex_only=molex_only) | _codes(item.article, manufacturer, molex_only=molex_only)
    if item.article:
        keys.add(key(item.article, manufacturer=manufacturer))
    for size in (2, 3):
        for start in range(len(words) - size + 1):
            joined = key(" ".join(words[start:start + size]), manufacturer=manufacturer)
            if len(joined) >= 5 and any(char.isdigit() for char in joined):
                keys.add(joined)
    return {key for key in keys if key}


def _line_keys(line: OrderLineCandidate, manufacturer: str, *, molex_only: bool = False) -> set[str]:
    codes = set()
    key = _molex_code_key if molex_only else comparison_code_key
    article = key(line.article, manufacturer=manufacturer)
    if len(article) >= 3:
        codes.add(article)
    codes |= _codes(line.article, manufacturer, molex_only=molex_only)
    for name in line.names:
        codes |= _codes(name, manufacturer, molex_only=molex_only)
    return codes


def _name_words(text: str | None) -> set[str]:
    return {word for word in normalize_name(text).split() if len(word) >= 2 or word.isdigit()}


def _name_score(item: DocumentItem, line: OrderLineCandidate) -> float:
    item_words = _name_words(item.name)
    best = 0.0
    for name in line.names:
        words = _name_words(name)
        if not words or not item_words:
            continue
        common = len(words & item_words)
        dice = 2 * common / (len(words) + len(item_words))
        if words <= item_words:
            dice = max(dice, 0.6)  # every word of the line is in the row: the row only says more
        best = max(best, dice)
    return best


def match_document_items(
    items: Sequence[DocumentItem], lines: Sequence[OrderLineCandidate],
) -> tuple[list[DocumentMatch], list[int], list[str]]:
    """Matches (line key, item index, confidence), the item indexes left over, the line keys left over."""
    line_makers = {line.key: _manufacturer(line.article, *line.names) for line in lines}
    line_codes = {}
    molex_codes = {}
    distinct = {}
    # Use the same policy on both sides of a pair and on its uniqueness counts.
    # OTHER preserves legacy matching; "" infers known families; MOLEX uses a maker label.
    for manufacturer in ("OTHER", "", "MOLEX"):
        by_line = {line.key: _line_keys(line, manufacturer) for line in lines}
        counts = Counter(code for codes in by_line.values() for code in codes)
        line_codes[manufacturer] = by_line
        molex_codes[manufacturer] = {
            line.key: _line_keys(line, manufacturer, molex_only=True) for line in lines
        }
        distinct[manufacturer] = {
            key: {code for code in codes if counts[code] == 1} for key, codes in by_line.items()
        }
    confirmed_codes = {
        code for line in lines if line_makers[line.key] == "MOLEX"
        for code in line_codes["MOLEX"][line.key]
    }

    pairs: list[tuple[float, int, int, str]] = []
    for item_index, item in enumerate(items):
        maker = _manufacturer(item.article, item.name)
        item_keys = {policy: _item_keys(item, policy) for policy in line_codes}
        item_molex_codes = {policy: _item_keys(item, policy, molex_only=True) for policy in line_codes}
        own_codes = {policy: _codes(item.name, policy) | _codes(item.article, policy) for policy in line_codes}
        # An explicitly Molex order row also establishes identity for its document
        # counterpart. Count every compatible spelling before selecting any row.
        confirmed = maker == "MOLEX" or bool(item_keys["MOLEX"] & confirmed_codes)
        for line_index, line in enumerate(lines):
            line_maker = line_makers[line.key]
            if any(label and label != "MOLEX" for label in (maker, line_maker)):
                policy = "OTHER"
            else:
                policy = "MOLEX" if confirmed or line_maker == "MOLEX" else ""
            # Legacy punctuation matches remain valid. A newly equal key must have
            # numeric Molex evidence on BOTH sides; a slash/dot cannot supply it.
            legacy_shared = item_keys["OTHER"] & line_codes["OTHER"][line.key]
            molex_shared = item_molex_codes[policy] & molex_codes[policy][line.key]
            if not molex_shared:
                policy = "OTHER"
            keys = item_keys[policy]
            codes = line_codes[policy][line.key]
            name = _name_score(item, line)
            if distinct[policy][line.key] & keys & (legacy_shared | molex_shared):
                tier, confidence = 3, "article"
            elif name >= 0.6:
                tier, confidence = 2, "name"
            elif name >= 0.3:
                tier, confidence = 1, "weak"
            else:
                continue
            if tier < 3 and own_codes[policy] and codes and not (legacy_shared | molex_shared):
                # Both carry part numbers and none agree: «Разъём XH-2P» is not «Разъём XH-3P», whatever the words.
                continue
            same_quantity = (
                item.quantity is not None and line.quantity is not None and item.quantity == line.quantity
            )
            score = tier * 100 + name * 50 + (10 if same_quantity else 0)
            pairs.append((score, item_index, line_index, confidence))
    pairs.sort(key=lambda pair: (-pair[0], abs(pair[1] - pair[2]), pair[1]))
    matched: list[DocumentMatch] = []
    used_items: set[int] = set()
    used_lines: set[int] = set()
    for score, item_index, line_index, confidence in pairs:
        if item_index in used_items or line_index in used_lines:
            continue
        used_items.add(item_index)
        used_lines.add(line_index)
        matched.append(DocumentMatch(lines[line_index].key, item_index, confidence))
    matched.sort(key=lambda match: next(i for i, line in enumerate(lines) if line.key == match.key))
    return (
        matched,
        [index for index in range(len(items)) if index not in used_items],
        [line.key for index, line in enumerate(lines) if index not in used_lines],
    )
