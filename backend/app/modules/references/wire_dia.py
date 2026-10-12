"""«Ø изоляции по маркам» as the ETK generator reads it, built from the база монтажных проводов (owner 12.10.2026:
«вся инфа должна идти из монтажных кабелей»), not from the old СПР.КАБ AWG table.

Same shape as the СПР.КАБ table the generator parsed (`tcGenLogic.wireDiaFromRecords`): one row per size of
«Сечения AWG ↔ мм²» in its order, columns `AWG · ГОСТ · Ø жилы` and then one column per series of the база
монтажных (`cables.HOOKUP_SERIES`, the 12 series) holding that series' Ø наружный for the size, «—» when the base has
none. A ГОСТ-only size (0,07 мм²) has an empty AWG. Marks outside the 12 series (UL1015, M22759, силиконовый…) have
no column: the generator shows «Ø провода не найден…» for them instead of a number from nowhere.

Ø наружный of a series and size = the `outer_d_mm` of its монтажные провода (`hookup_wires`); a wire belongs to the
series its mark names (`cables.hookup_series`, the same rule that puts it in the монтажные list). Several wires of one
series and size (colours) with different Ø → the largest (a sleeve or a twist sized for it fits them all).

Pure: no I/O, the caller passes what the repository read."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from uuid import UUID

from .cable_structure import HookupWire, WireGauge, shown
from .cables import HOOKUP_SERIES, hookup_series

SERIES = tuple(key for key, _pattern in HOOKUP_SERIES)
AWG_COLUMN, GOST_COLUMN, CORE_COLUMN = "AWG", "ГОСТ", "Ø жилы"
COLUMNS = (AWG_COLUMN, GOST_COLUMN, CORE_COLUMN, *SERIES)
NONE = "—"
SOURCE = "База монтажных проводов"


def outer_by_series(hookups: Iterable[HookupWire]) -> dict[tuple[str, UUID], Decimal]:
    """(series, gauge id) → Ø наружный of the база монтажных; the largest when the wires of one size differ."""
    result: dict[tuple[str, UUID], Decimal] = {}
    for wire in hookups:
        series = hookup_series(wire.mark) or hookup_series(wire.series)
        if series is None or wire.outer_d_mm is None:
            continue
        key = (series, wire.gauge_id)
        if key not in result or wire.outer_d_mm > result[key]:
            result[key] = wire.outer_d_mm
    return result


def wire_dia_rows(gauges: Sequence[WireGauge], hookups: Iterable[HookupWire]) -> list[dict[str, str]]:
    """The table rows (`fields` in column order, numbers with a decimal comma as the sheet showed them)."""
    outer = outer_by_series(hookups)
    rows = []
    for gauge in gauges:
        row = {AWG_COLUMN: gauge.awg or "", GOST_COLUMN: shown(gauge.section_mm2),
               CORE_COLUMN: shown(gauge.conductor_d_mm) or NONE}
        for series in SERIES:
            value = outer.get((series, gauge.id))
            row[series] = shown(value) if value is not None else NONE
        rows.append(row)
    return rows


def wire_dia_records(rows: Sequence[Mapping[str, str]]) -> list[dict]:
    """The rows as records shaped like `/api/references/tech-operations` ones (without a tab: this is no БД tab)."""
    return [{"id": f"wire-dia-{index}", "display_text": row[AWG_COLUMN] or row[GOST_COLUMN],
             "source_sheet": SOURCE, "fields": dict(row)} for index, row in enumerate(rows)]
