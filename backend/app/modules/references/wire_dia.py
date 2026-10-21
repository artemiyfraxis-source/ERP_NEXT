"""«Ø изоляции по маркам» as the ETK generator reads it, built from the база монтажных проводов (owner 12.10.2026:
«вся инфа должна идти из монтажных кабелей»), not from the old СПР.КАБ AWG table.

Same shape as the СПР.КАБ table the generator parsed (`tcGenLogic.wireDiaFromRecords`): one row per size of
«Сечения AWG ↔ мм²» in its order, columns `AWG · ГОСТ · Ø жилы` and then one column per series of the база
монтажных (`cables.HOOKUP_SERIES`, 12 series of 12.10 + 10 moved from «Прочие кабели» 15.10) holding that series' Ø
наружный for the size, «—» when there is none. A ГОСТ-only size (0,07 мм²) has an empty AWG. Marks outside the series
(M22759, TXL…) have no column: the generator shows «Ø провода не найден…» for them instead of a number from nowhere.

Ø наружный of a series and size = the `outer_d_mm` of its монтажные провода (`hookup_wires`); a wire belongs to the
series its mark names (`cables.hookup_series`, the same rule that puts it in the монтажные list). Several wires of one
series and size (colours) with different Ø → the largest (a sleeve or a twist sized for it fits them all).

Until a source gives one, a size the base has no Ø for keeps the number the old СПР.КАБ table had for that series and
AWG (its «МС» column stood for every МС series), so the generator never silently loses a diameter it used to have.
Such a number is flagged: the row's first field `По старой таблице` names the series whose Ø in this row is the old
one («ПВАМ, МГТФ»), and the generator says «по старой таблице, не подтверждено». A Ø of the base always wins.

Pure but `read_wire_dia`, which takes the open unit of work."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from uuid import UUID

from .cable_structure import HookupWire, WireGauge, decimal_or_none, shown
from .cables import HOOKUP_SERIES, hookup_series
from .domain import TechOperationTab

SERIES = tuple(key for key, _pattern in HOOKUP_SERIES)
OLD_COLUMN = "По старой таблице"  # first: the generator reads the series columns after `Ø жилы` by their order
AWG_COLUMN, GOST_COLUMN, CORE_COLUMN = "AWG", "ГОСТ", "Ø жилы"
COLUMNS = (OLD_COLUMN, AWG_COLUMN, GOST_COLUMN, CORE_COLUMN, *SERIES)
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


def old_column(series: str, header: Iterable[str]) -> str | None:
    """The column of the old СПР.КАБ table that gave a series its Ø: its own, else «МС» for an МС series."""
    names = set(header)
    if series in names:
        return series
    return "МС" if series.startswith("МС ") and "МС" in names else None


def old_outer(old_table: Iterable[Mapping[str, str]]) -> dict[tuple[str, str], Decimal]:
    """(series, AWG) → the number the old СПР.КАБ «Ø изоляции» table had (its cells «1,35», «—»)."""
    result: dict[tuple[str, str], Decimal] = {}
    for row in old_table:
        awg = str(row.get(AWG_COLUMN) or "").strip()
        if not awg:
            continue
        for series in SERIES:
            column = old_column(series, row)
            value = decimal_or_none(row.get(column)) if column else None
            if value is not None and value > 0:
                result[(series, awg)] = value
    return result


def wire_dia_rows(gauges: Sequence[WireGauge], hookups: Iterable[HookupWire],
                  old_table: Iterable[Mapping[str, str]] = ()) -> list[dict[str, str]]:
    """The table rows (`fields` in column order, numbers with a decimal comma as the sheet showed them). `old_table` —
    the СПР.КАБ AWG records (tab kab_awg) for the flagged fallback; empty — no fallback."""
    outer, old = outer_by_series(hookups), old_outer(old_table)
    rows = []
    for gauge in gauges:
        row = {OLD_COLUMN: "", AWG_COLUMN: gauge.awg or "", GOST_COLUMN: shown(gauge.section_mm2),
               CORE_COLUMN: shown(gauge.conductor_d_mm) or NONE}
        from_old = []
        for series in SERIES:
            value = outer.get((series, gauge.id))
            if value is None and gauge.awg:
                value = old.get((series, gauge.awg))
                if value is not None:
                    from_old.append(series)
            row[series] = shown(value) if value is not None else NONE
        row[OLD_COLUMN] = ", ".join(from_old)
        rows.append(row)
    return rows


def wire_dia_records(rows: Sequence[Mapping[str, str]]) -> list[dict]:
    """The rows as records shaped like `/api/references/tech-operations` ones (without a tab: this is no БД tab)."""
    return [{"id": f"wire-dia-{index}", "display_text": row[AWG_COLUMN] or row[GOST_COLUMN],
             "source_sheet": SOURCE, "fields": dict(row)} for index, row in enumerate(rows)]


def read_wire_dia(uow) -> list[dict[str, str]]:
    """The rows over an open references unit of work: sizes, монтажные провода, the old СПР.КАБ AWG records."""
    old = [record.fields for record in uow.tech_operations.list(tab=TechOperationTab.KAB_AWG, query="", limit=1_000)]
    return wire_dia_rows(uow.cable_structure.gauges(), uow.cable_structure.hookups(), old)
