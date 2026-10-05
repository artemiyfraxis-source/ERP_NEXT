"""Which row of the supplier's document prices which line of the order (owner, 05.10: «Вставить КП / счёт»).

A part number decides first: SHR-02V-S-B, 510471000 match only exactly, after spaces, dashes, dots and case are
dropped (and Cyrillic look-alikes read as Latin: «С» typed for «C»). A part number counts only when one line of the
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


def _words(text: str | None) -> list[str]:
    return [word for word in _SPLIT.split(str(text or "")) if word]


def _codes(text: str | None) -> set[str]:
    return {code_key(word) for word in _words(text) if is_code(word)}


def _item_keys(item: DocumentItem) -> set[str]:
    """Part numbers of the row, with neighbouring words joined («SHR 02V-S-B» is one number split by a space)."""
    words = _words(item.name)
    keys = _codes(item.name) | _codes(item.article)
    if item.article:
        keys.add(code_key(item.article))
    for size in (2, 3):
        for start in range(len(words) - size + 1):
            joined = code_key("".join(words[start:start + size]))
            if len(joined) >= 5 and any(char.isdigit() for char in joined):
                keys.add(joined)
    return {key for key in keys if key}


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
    line_codes = {}
    for line in lines:
        codes = set()
        article = code_key(line.article)
        if len(article) >= 3:
            codes.add(article)
        codes |= _codes(line.article)
        for name in line.names:
            codes |= _codes(name)
        line_codes[line.key] = codes
    counts = Counter(code for codes in line_codes.values() for code in codes)
    distinct = {key: {code for code in codes if counts[code] == 1} for key, codes in line_codes.items()}

    pairs: list[tuple[float, int, int, str]] = []
    for item_index, item in enumerate(items):
        keys = _item_keys(item)
        own_codes = _codes(item.name) | _codes(item.article)
        for line_index, line in enumerate(lines):
            name = _name_score(item, line)
            if distinct[line.key] & keys:
                tier, confidence = 3, "article"
            elif name >= 0.6:
                tier, confidence = 2, "name"
            elif name >= 0.3:
                tier, confidence = 1, "weak"
            else:
                continue
            if tier < 3 and own_codes and line_codes[line.key] and not (line_codes[line.key] & keys):
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
