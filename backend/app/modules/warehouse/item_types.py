"""The item type dictionary of the stock (SK-14; SUPPLYMAP Stock05.gs:18-35, 51-68, 1085-1110). Pure rules.

A type groups stock for search, and people write one group five ways («ТУТ», «тут», «термоусадка»), so the
type is picked from a dictionary: the canonical list (or the one the storekeeper saved) plus, as a tail,
every type that already occurs on the stock, so old rows stay selectable."""

from __future__ import annotations

import re
from collections.abc import Iterable


# STOCK05_TYPES_ (owner, 2026-08-27): the list until someone saves their own.
ITEM_TYPES_CANON = (
    "Коаксиальный соединитель",
    "Коаксиальный кабель",
    "Соединитель",
    "Кабель",
    "Терминал",
    # НШВ, НШВИ, НКИ, РПИ, ПМ — not a connector's crimp contact (owner 26.10.2026: «тип наконечник, а не терминал»)
    "Наконечник",
    "Провод Монтажный",
    "Микросхема",
    "Термоусадочная трубка",
    "Лента",
)
# STOCK05_TYPE_ALIAS_ (owner, 2026-08-31): an old spelling → the canonical type. Keys are type_key()s.
ITEM_TYPE_ALIASES = {
    "мультипровод": "Кабель",
    "коаксиальный провод": "Коаксиальный кабель",
    "тут": "Термоусадочная трубка",
    "ттк": "Термоусадочная трубка",
    "термоусадка": "Термоусадочная трубка",
    "термоусадочная трубка (тут)": "Термоусадочная трубка",
    "наконечник кольцевой": "Наконечник",
    "наконечник втулочный": "Наконечник",
}
ITEM_TYPES_MAX = 200  # STOCK05_TYPES_MAX_
ITEM_TYPE_MAX_LENGTH = 80  # a longer line of the saved list is dropped (stock05TypesNorm_)

_SPACES = re.compile(r"\s+")


def type_key(value: object) -> str:
    """normHeader_: spaces collapsed and trimmed, lower case — «ТуТ», « тут » and «ТУТ» are one type."""
    return _SPACES.sub(" ", str(value if value is not None else "")).strip().lower()


def canonical_item_type(value: object) -> str:
    """stock05CanonType_: an old spelling → the canonical type; anything else is kept as typed (trimmed).
    An unknown type is not refused: the dictionary grows from what really lies on the stock."""
    text = str(value if value is not None else "").strip()
    return ITEM_TYPE_ALIASES.get(type_key(text), text)


def merge_item_types(canon: Iterable[str], extra: Iterable[str]) -> list[str]:
    """stock05Merge_: the list, then the values not in it yet (without case and extra spaces), in order."""
    merged = list(canon)
    seen = {type_key(item) for item in merged}
    for item in extra:
        key = type_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def normalize_item_types(values: Iterable[object] | str) -> list[str]:
    """stock05TypesNorm_: one type per line, spaces collapsed, empty and longer than 80 characters dropped,
    duplicates (without case) dropped, the order as typed — it is the order of the dropdown. At most 200."""
    lines = re.split(r"\r?\n", values) if isinstance(values, str) else list(values)
    clean = [_SPACES.sub(" ", str(item if item is not None else "")).strip() for item in lines]
    return merge_item_types([], [item for item in clean if item and len(item) <= ITEM_TYPE_MAX_LENGTH])[:ITEM_TYPES_MAX]
