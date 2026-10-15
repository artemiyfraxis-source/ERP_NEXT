"""Поиск по базам — те же правила, что у таблиц на экране (frontend/src/searchText.ts; владелец 16.10.2026: «настроить
нормальный поиск во всех базах»). Регистр не важен; кириллица и латиница-двойники (А/A, В/B, Е/E, К/K, М/M, Н/H, О/O,
Р/P, С/C, Т/T, У/Y, Х/X) — одна буква; пробелы, дефисы, точки, запятые, косые и подчёркивания внутри марки не считаются
(RG58 = RG-58 = rg 58, 0,35 = 0.35); слова запроса — в любом порядке, каждое должно найтись хотя бы в одном значении."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")

_TWINS = str.maketrans({"а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
                        "с": "c", "т": "t", "у": "y", "х": "x"})
_SEPARATORS = re.compile(r"[\s\-‐‑‒–—−_.,/\\·]+")
# Десятичная запятая или точка между цифрами — точка, и она остаётся в ключе: «0,35» = «0.35», но «3,00 - 3,50» не
# склеивается в «300350» и не находится по «0,35».
_DECIMAL = re.compile(r"(\d)[.,](?=\d)")
_POINT = ""


def search_key(value: object) -> str:
    """«RG-58 C/U» → «rg58cu», «МГТФ 0,35» и «MГТФ 0.35» → один ключ «mгtф0.35»."""
    marked = _DECIMAL.sub(rf"\g<1>{_POINT}", str(value or "").lower())
    return _SEPARATORS.sub("", marked).replace(_POINT, ".").translate(_TWINS)


def search_words(query: object) -> list[str]:
    return [word for word in (search_key(part) for part in str(query or "").split()) if word]


def matches_search(values: Iterable[object], words: list[str]) -> bool:
    """Каждое слово — хотя бы в одном значении; пустой список слов подходит всему."""
    if not words:
        return True
    keys = [key for key in (search_key(value) for value in values) if key]
    return all(any(word in key for key in keys) for word in words)


def search_rows(rows: Iterable[T], values: Callable[[T], Iterable[object]], words: list[str], limit: int,
                extra: Callable[[T], Iterable[object]] = lambda _row: ()) -> list[T]:
    """Как `searchRows` на экране: запрос из нескольких слов — сперва как одна марка («rg 58» — RG58 / RG-58) целиком в
    одном значении; таких строк нет — слова в любом порядке. `extra` — значения только для поиска по словам (склейка
    полей, где марка сложилась бы из соседних полей)."""
    joined = "".join(words) if len(words) > 1 else ""
    whole: list[T] = []
    loose: list[T] = []
    for row in rows:
        own = [search_key(value) for value in values(row)]
        if joined and any(joined in key for key in own):
            whole.append(row)
            if len(whole) >= limit:
                break
        elif not whole and len(loose) < limit and matches_search([*own, *extra(row)], words):
            loose.append(row)
    return whole or loose
