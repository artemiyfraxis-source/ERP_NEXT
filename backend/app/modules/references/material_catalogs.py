"""Write-time ownership of materials; unknown records retain their current catalog."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Mapping

from .coverings import GROUP_MAP, OVERRIDE_NAMES, OVERRIDE_FIELD, classify

MATERIAL_TABLES = {"heat_shrink": "heat_shrink_records", "cov": "covering_records"}
DERIVED_COLUMN_TITLES = frozenset({"Стенка, мм", "Разрезная", "Ø номинал, мм", "Диапазон Ø, мм", "Ширина, мм",
                                    "Плотность", "Температура, °C", "Рабочая температура, °C", "Температура усадки, °C"})
_SHRINK = re.compile(r"термоусад|\bтут(?:нг)?|heat[\s-]*shrink|(?<!\w)(?:с?ттк|нттк)\d*", re.I)
_OTHER = (
    ("cambric", re.compile(r"кембрик", re.I)),
    ("corrugated", re.compile(r"гофр|corrugated", re.I)),
    ("braid", re.compile(r"опл[её]т|плет[её]н|braid", re.I)),
    ("tape", re.compile(r"изолент|(?<![а-яё])лент[аы]?(?![а-яё])|\btape\b", re.I)),
)


def route(fields: Mapping[str, str], current_tab: str = "mat") -> tuple[str, str, str]:
    """Return (storage tab, group, status), without changing the original payload.

    An explicit override and a recognized sheet group retain their established priority.
    Clear product names also work with an unrecognized sheet group. Adhesive alone is
    not evidence that a product shrinks, and an unknown record never leaves its tab.
    """
    override = str(fields.get(OVERRIDE_FIELD) or "").strip().casefold()
    sheet_group = str(fields.get("Группа") or "").strip().casefold()
    if override in OVERRIDE_NAMES or sheet_group in GROUP_MAP:
        group = classify(fields)[0]
    elif override:
        group = None  # An unrecognized manual choice needs review, not an automatic move.
    else:
        text = " ".join(str(fields.get(key) or "") for key in ("Наименование", "Серия"))
        if _SHRINK.search(text):
            group = classify({**fields, "Группа": ""})[0]
            group = group if group in ("tut", "ttk") else "tut"
            if str(fields.get("Клеевой слой") or "").strip().casefold() in ("да", "yes", "true", "+"):
                group = "ttk"
        else:
            group = next((name for name, pattern in _OTHER if pattern.search(text)), None)
    if group is None:
        return current_tab, "other", "unknown"
    return ("heat_shrink" if group in ("tut", "ttk") else "cov"), group, "classified"


def prepare(record, *, current_tab: str | None = None):
    from .domain import MATERIAL_TABS, ReferenceValidationError, TechOperationTab
    from .shrink_temperature import read
    if record.tab not in MATERIAL_TABS:
        return record
    try:
        read(record.fields)
    except ValueError as error:
        raise ReferenceValidationError(str(error)) from error
    tab, group, status = route(record.fields, current_tab or record.tab.value)
    return replace(record, tab=TechOperationTab(tab), material_group=group, classification_status=status)
