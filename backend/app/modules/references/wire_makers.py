"""БД.ПРОВ: «Стандарт / производитель» разделено на два поля (owner 12.10.2026): «Стандарт» — один, «Производители» — список.

Ячейки записи: `Стандарт` (одно значение) и `Производители` (имена через «, »). Старая ячейка `Стандарт / производитель`
разбирается детерминированно: часть, где назван стандарт (ГОСТ, ТУ, ОСТ, UL, IEC, DIN, MIL, JIS…), — в «Стандарт»,
всё остальное — в «Производители», по «,», «;», «/» (скобки не режутся).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

LEGACY_FIELD = "Стандарт / производитель"
STANDARD_FIELD = "Стандарт"
MAKERS_FIELD = "Производители"
MAKERS_SEPARATOR = ", "

_STANDARD = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9])(ГОСТ|ТУ|ОСТ|OCT|UL|IEC|DIN|MIL|JIS|ISO|EN|TIA|EIA|ASTM|AWM)(?![A-Za-zА-Яа-яЁё])"
)


def _split(text: str, separators: str) -> list[str]:
    """Parts split on the separators outside brackets, trimmed, empty ones dropped."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([«":
            depth += 1
        elif char in ")]»":
            depth = max(0, depth - 1)
        if char in separators and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def looks_like_standard(part: str) -> bool:
    return bool(_STANDARD.search(part))


def unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)
    return result


def split_legacy(value: str) -> tuple[str, list[str]]:
    """«Паритет (Подольск), ГОСТ 7399-97» → («ГОСТ 7399-97», ["Паритет (Подольск)"])."""
    standards: list[str] = []
    makers: list[str] = []
    for part in _split(str(value or ""), ",;"):
        if looks_like_standard(part):
            standards.append(part)
        else:
            makers.extend(_split(part, "/"))
    return "; ".join(unique(standards)), unique(makers)


def parse_makers(text: str) -> list[str]:
    return unique(_split(str(text or ""), ",;"))


def join_makers(names: list[str]) -> str:
    return MAKERS_SEPARATOR.join(unique([name.strip() for name in names if name.strip()]))


def split_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """The record's cells with the legacy one replaced by the two new ones (own non-empty values win, makers merge)."""
    if LEGACY_FIELD not in fields:
        return dict(fields)
    standard, makers = split_legacy(fields[LEGACY_FIELD])
    own_makers = parse_makers(fields.get(MAKERS_FIELD) or "")
    result: dict[str, str] = {}
    for name, value in fields.items():
        if name == LEGACY_FIELD:
            result[STANDARD_FIELD] = str(fields.get(STANDARD_FIELD) or "").strip() or standard
            result[MAKERS_FIELD] = join_makers(own_makers + makers)
        elif name not in (STANDARD_FIELD, MAKERS_FIELD):
            result[name] = value
    return result
