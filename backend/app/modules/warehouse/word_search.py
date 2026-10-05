"""Stock search by words (stage option warehouse.word_search): every word of the query must be found in at least one
field of the lot, in any order. Pure: no database, no options — the caller decides when to use it."""

from __future__ import annotations

import re
import unicodedata

# Storekeepers type either alphabet: Latin look-alikes fold to Cyrillic, ё = е.
_FOLD = str.maketrans({
    "ё": "е", "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т",
    "x": "х", "y": "у", "×": "х",
})
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
_ARTICLE_SEPARATORS = re.compile(r"[\s.\-/()_]")


def fold(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().translate(_FOLD)
    return _DECIMAL_COMMA.sub(".", text)


def article_key(value: object) -> str:
    """Article without separators; Molex 0430250600 = 430250600 (leading zeros up to 10 digits)."""
    key = _ARTICLE_SEPARATORS.sub("", fold(value))
    return key.lstrip("0") if re.fullmatch(r"0\d{9}", key) else key


def _fields(lot) -> tuple[str, ...]:
    package = str(getattr(lot, "package", "") or "")
    grn = str(lot.grn or "")
    grn_forms = [grn, package]
    if package:
        grn_forms += [f"{grn}-{package}", f"{grn} {package}"]
    return tuple(fold(text) for text in (
        *grn_forms, lot.name, lot.storage_location, lot.series, lot.purpose, lot.item_type,
    ))


def matches_words(lot, query: str) -> bool:
    words = fold(query).split()
    if not words:
        return True
    fields = _fields(lot)
    article = fold(lot.article)
    key = article_key(lot.article)
    for word in words:
        word_key = article_key(word)
        if word in article or (word_key and word_key in key):
            continue
        if not any(word in field for field in fields):
            return False
    return True
