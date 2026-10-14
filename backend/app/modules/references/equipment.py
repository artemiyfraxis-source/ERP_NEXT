"""БД.ОБ rules: inventory numbers, the crimp-tool code and the links between tools and what they crimp.

Numbering, as found in the БД.ОБ data (216 sheet rows, 12.10.2026):
- `<prefix>.<NNN>` is a model (3 digits, «ОБ.044»); several units of one model are `<prefix>.<NNN>.<k>` («ОБ.070.1…8»);
  the legacy rows «ОБ.79.1», «ОБ.81» are written without padding and count as the same numbers (79, 81);
  «.X» is a resale marker («ОБ.070.X», «(R)») and takes no part in counting.
- Equipment, «Оборудование (ПР)» and «Инструмент» share one `ОБ.` sequence; «Матрицы» are mostly `ОСН.NNN`
  (26 of 33, the rest are the КВТ die sets `ОБ.069.k`). The prefix of a type is the one most of its rows use.

Crimp tools: БД.ТЕР «Аппликатор» holds tool codes («S10/M10/M19/M49/A8», S — hand tool, M — matrix, A — applicator);
a record in БД.ОБ owns a code in «Код в базе терминалов» and the terminals that name it are what it crimps.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping

from .domain import ReferenceValidationError, StaleRecordError

NAME_KEYS = ("Наменование", "Наименование")  # the sheet header really says «Наменование»
INV_KEY = "Инв. номер"
ROW_KEY = "№"
TYPE_KEY = "Тип"
ARTICLE_KEY = "Артикул"
BASE_KEY = "Для базы"
CODE_KEY = "Код в базе терминалов"
TOOL_KEY = "Для инструмента"  # inventory number of the tool a matrix belongs to
TARGETS_KEY = "Обжимает (серии разъёмов)"  # JSON list of {"kind", "key", "label"}
EXTRA_KEYS = (CODE_KEY, TOOL_KEY, TARGETS_KEY)
CRIMP_TOOL_TYPE = "Обжимной инструмент"
MATRIX_TYPE = "Матрицы"
DEFAULT_PREFIX = "ОБ."

_INV = re.compile(r"^\s*([A-Za-zА-Яа-яЁё]+)\.\s*(\d+)(?:\.(\d+|[A-Za-zА-Яа-яЁё]))?\s*$")
_CODE = re.compile(r"^[A-Z]\d{1,3}$")


@dataclass(frozen=True)
class Inventory:
    prefix: str  # «ОБ.»
    base: int  # 70
    base_text: str  # «070» as written
    suffix: str  # «» | «8» | «X»

    @property
    def instance(self) -> int:
        """1 for a bare number (the only unit), k for «.k», 0 for «.X»."""
        if not self.suffix:
            return 1
        return int(self.suffix) if self.suffix.isdigit() else 0


def parse_inventory(value: str) -> Inventory | None:
    match = _INV.match(str(value or ""))
    if not match:
        return None
    prefix, base, suffix = match.group(1).upper() + ".", match.group(2), (match.group(3) or "").upper()
    return Inventory(prefix, int(base), base, suffix)


def inv_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def field(fields: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def name_of(fields: Mapping[str, str]) -> str:
    return field(fields, *NAME_KEYS)


def type_of(fields: Mapping[str, str]) -> str:
    return field(fields, TYPE_KEY, "obType")


def prefix_for_type(equipment_type: str, existing: Iterable[Mapping[str, str]]) -> str:
    counts = Counter(
        parsed.prefix for fields in existing
        if type_of(fields) == equipment_type and (parsed := parse_inventory(field(fields, INV_KEY)))
    )
    return counts.most_common(1)[0][0] if counts else DEFAULT_PREFIX


def next_model_inventory(equipment_type: str, existing: Iterable[Mapping[str, str]]) -> str:
    """The next free model number of the type's prefix: one above the highest model already used under it."""
    rows = list(existing)
    prefix = prefix_for_type(equipment_type, rows)
    used = [parsed.base for fields in rows
            if (parsed := parse_inventory(field(fields, INV_KEY))) and parsed.prefix == prefix]
    return f"{prefix}{max(used, default=0) + 1:03d}"


def next_instance_inventory(source_inventory: str, existing: Iterable[Mapping[str, str]]) -> str:
    """Another unit of the model of `source_inventory`: «ОБ.070.8» → «ОБ.070.9»; a single «ОБ.044» → «ОБ.044.2».
    The existing records are never renumbered."""
    source = parse_inventory(source_inventory)
    if source is None:
        raise ReferenceValidationError("У записи нет инв. номера вида ОБ.070 — номер экземпляра не определить")
    taken = [parsed.instance for fields in existing
             if (parsed := parse_inventory(field(fields, INV_KEY))) and parsed.prefix == source.prefix
             and parsed.base == source.base]
    return f"{source.prefix}{source.base_text}.{max([1, *taken]) + 1}"


def next_row_number(existing: Iterable[Mapping[str, str]]) -> str:
    numbers = [int(value) for fields in existing if (value := field(fields, ROW_KEY)).isdigit()]
    return str(max(numbers, default=0) + 1)


def base_value(fields: Mapping[str, str]) -> str:
    """«Для базы» as the sheet's formula builds it: артикул/наименование."""
    return f"{field(fields, ARTICLE_KEY)}/{name_of(fields)}"


def normalize_code(value: str) -> str:
    code = re.sub(r"\s+", "", str(value or "")).upper()
    if code and not _CODE.match(code):
        raise ReferenceValidationError("Код в базе терминалов — буква и номер, например S10, M31 или A8")
    return code


def prepare_equipment(
    fields: dict[str, str], others: list[tuple[object, Mapping[str, str]]], *, self_id: object = None,
    creating: bool, previous: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Checks and fills an equipment record before it is saved. `others` — every БД.ОБ record as (id, fields).
    Raises ReferenceValidationError (422) or DuplicateInventoryError (409)."""
    result = dict(fields)
    siblings = [item for item in others if item[0] != self_id]
    old = previous or {}
    # An old sheet row may lack a name; it is only refused when it is saved nameless after having had one (or new).
    if not name_of(result) and (creating or name_of(old)):
        raise ReferenceValidationError("Наименование обязательно — без него запись не сохранить")
    inv = field(result, INV_KEY)
    if not inv and creating:
        inv = next_model_inventory(type_of(result), [item[1] for item in siblings])
    if inv:
        result[INV_KEY] = inv
        if (creating or inv_key(inv) != inv_key(field(old, INV_KEY))) and any(inv_key(field(item[1], INV_KEY)) == inv_key(inv) for item in siblings):
            raise DuplicateInventoryError(f"Инв. номер {inv} уже есть в справочнике — возьмите другой")
    if creating and not field(result, ROW_KEY):
        result[ROW_KEY] = next_row_number([item[1] for item in siblings])
    if creating and not field(result, BASE_KEY):
        result[BASE_KEY] = base_value(result)
    code = normalize_code(result.get(CODE_KEY, ""))
    if code:
        result[CODE_KEY] = code
        owner = next((field(item[1], INV_KEY) or name_of(item[1]) for item in siblings
                      if field(item[1], CODE_KEY).upper() == code), None)
        if owner is not None:
            raise DuplicateInventoryError(f"Код {code} уже у записи {owner} — у каждого инструмента свой код")
    elif CODE_KEY in result:
        result[CODE_KEY] = ""
    tool = field(result, TOOL_KEY)
    if tool and not any(inv_key(field(item[1], INV_KEY)) == inv_key(tool) for item in siblings):
        raise ReferenceValidationError(f"Инструмент {tool} не найден среди оборудования")
    return result


class DuplicateInventoryError(StaleRecordError):
    """A number or code that is taken: the API answers 409."""


# --- Tokens of БД.ТЕР «Аппликатор» ---

_SPLIT = re.compile(r"([/,;\s]+)")


@dataclass(frozen=True)
class Token:
    code: str
    doubtful: bool  # stands after a word like «возможно» / «совмест.»


def _parts(value: str) -> list[tuple[str, bool]]:
    """(text, is_separator) pieces of the cell, separators kept so a rewrite can leave the rest as it was."""
    return [(piece, bool(index % 2)) for index, piece in enumerate(_SPLIT.split(str(value or "").strip())) if piece]


def tokens(value: str) -> list[Token]:
    result, noise = [], False
    for piece, is_separator in _parts(value):
        if is_separator:
            continue
        code = piece.upper()
        if _CODE.match(code):
            result.append(Token(code, noise))
        else:
            noise = True
    return result


def has_code(value: str, code: str, *, sure_only: bool = True) -> bool:
    return any(token.code == code and not (sure_only and token.doubtful) for token in tokens(value))


def add_code(value: str, code: str) -> str:
    """Appends the code as a sure token. A code that stands there «под вопросом» is not turned sure silently."""
    current = tokens(value)
    if any(token.code == code and not token.doubtful for token in current):
        return str(value or "").strip()
    if any(token.code == code for token in current):
        raise ReferenceValidationError(
            f"Код {code} в «Аппликатор» стоит под вопросом — подтвердите его в справочнике терминалов")
    text = str(value or "").strip()
    if not text:
        return code
    if any(token.doubtful for token in current) or any(
            not is_separator and not _CODE.match(piece.upper()) for piece, is_separator in _parts(text)):
        return f"{code}/{text}"  # before the remark, so the new code is not read as doubtful
    return f"{text}/{code}"


def remove_code(value: str, code: str) -> str:
    """Drops only that sure token (S1 never touches S10); doubtful ones are left as they are."""
    parts = _parts(value)
    noise, drop = False, None
    for index, (piece, is_separator) in enumerate(parts):
        if is_separator:
            continue
        if _CODE.match(piece.upper()):
            if piece.upper() == code and not noise:
                drop = index
                break
        else:
            noise = True
    if drop is None:
        if has_code(value, code, sure_only=False):
            raise ReferenceValidationError(f"Код {code} в «Аппликатор» стоит под вопросом — уберите его в справочнике терминалов")
        return str(value or "").strip()
    del parts[drop]
    if drop < len(parts) and parts[drop][1]:
        del parts[drop]
    elif drop > 0 and parts[drop - 1][1]:
        del parts[drop - 1]
    return "".join(piece for piece, _ in parts).strip()


def applicator_keys(fields: Mapping[str, str]) -> list[str]:
    """The БД.ТЕР fields that hold the cell: the schema value and the sheet column «Аппликатор»."""
    return [key for key in fields if key == "terApplicator" or re.sub(r"\s+", " ", key).strip().casefold() == "аппликатор"]


def applicator_value(fields: Mapping[str, str]) -> str:
    return field(fields, "terApplicator", *applicator_keys(fields))


def parse_targets(value: str) -> list[dict[str, str]]:
    try:
        items = json.loads(value) if str(value or "").strip() else []
    except ValueError:
        return []
    return [{"kind": str(item.get("kind", "")), "key": str(item.get("key", "")), "label": str(item.get("label", ""))}
            for item in items if isinstance(item, dict) and item.get("key")]


def dump_targets(items: list[dict[str, str]]) -> str:
    return json.dumps(items, ensure_ascii=False) if items else ""
