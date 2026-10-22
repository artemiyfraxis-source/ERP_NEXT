"""Stock search by words: every word of the query must be found in at least one field of the lot, in any order.
A word without digits matches from the start of a word («male» does not find «female», owner); a word with digits
(article, size, ГРН) is a substring, and against the article also without separators. Pure: no database."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from functools import lru_cache

# Storekeepers type either alphabet: Latin look-alikes fold to Cyrillic, ё = е.
_FOLD = str.maketrans({
    "ё": "е", "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о", "p": "р", "t": "т",
    "x": "х", "y": "у", "×": "х",
})
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
_ARTICLE_SEPARATORS = re.compile(r"[\s.\-/()_]")


def fold(value: object) -> str:
    return _fold_text(str(value or ""))


@lru_cache(maxsize=1 << 16)
def _fold_text(text: str) -> str:
    # Pure, so memoized: every page and keystroke of the stock list folds the same lot texts again.
    return _DECIMAL_COMMA.sub(".", unicodedata.normalize("NFKC", text).casefold().translate(_FOLD))


def article_key(value: object) -> str:
    """Article without separators; Molex 0430250600 = 430250600 (leading zeros up to 10 digits)."""
    return _article_key(fold(value))


@lru_cache(maxsize=1 << 16)
def _article_key(folded: str) -> str:
    key = _ARTICLE_SEPARATORS.sub("", folded)
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


def _word(word: str) -> Callable[[str], bool]:
    """Where a query word is found in a folded text: digits — anywhere, letters only — from the start of a word."""
    if any(char.isdigit() for char in word):
        return lambda text: word in text
    starts_a_word = re.compile(rf"(?<![^\W_]){re.escape(word)}").search
    return lambda text: word in text and starts_a_word(text) is not None


def words_matcher(query: str) -> Callable[[object], bool]:
    """`matches_words` with the query folded once: the predicate the stock list runs over every lot."""
    words = [
        (_word(word), article_key(word) if any(char.isdigit() for char in word) else "")
        for word in fold(query).split()
    ]
    if not words:
        return lambda lot: True

    def matches(lot) -> bool:
        fields = None
        article = fold(lot.article)
        key = article_key(lot.article)
        for found, word_key in words:
            if found(article) or (word_key and word_key in key):
                continue
            if fields is None:
                fields = _fields(lot)
            if not any(found(field) for field in fields):
                return False
        return True

    return matches


def matches_words(lot, query: str) -> bool:
    return words_matcher(query)(lot)
