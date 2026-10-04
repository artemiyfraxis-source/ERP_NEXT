"""«Кабели и провода» (owner 04.10.2026): БД.ПРОВ and the coax D1·D2·D3 table (СПР.КАБ · коаксиал) as three lists —
коаксиальные кабели, монтажные провода, прочие кабели.

Storage stays as SUPPLYMAP keeps it: БД.ПРОВ rows (`prov`) and the D-table rows (`kab_coax`) the ETK generator reads
by cable name. This module only classifies and joins them; nothing here changes what the generator looks up."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .domain import ReferenceValidationError, TechOperationRecord, cable_key


class CableKind(str, Enum):
    COAX = "coax"
    WIRE = "wire"
    OTHER = "other"


CABLE_KIND_LABELS = {CableKind.COAX: "Коаксиальные кабели", CableKind.WIRE: "Монтажные провода", CableKind.OTHER: "Прочие кабели"}
DIAMETERS = ("D1", "D2", "D3")
MARK_FIELD = "Марка"
TYPE_FIELD = "Тип провода"
CORES_FIELD = "Число жил"
_DIA_CABLE_FIELD = "Кабель"
# The generator's coax markers (tcGenLogic `isCoaxAssembly_`, SUPPLYMAP TcGenInline): used only for a row without a type.
_COAX_MARK = re.compile(r"коакс|coax|\bRG[\s-]?\d", re.IGNORECASE)
_LEADING_INT = re.compile(r"^\s*(\d+)")
_DIAMETER_VALUE = re.compile(r"^\d+([.,]\d+)?$")


def cable_mark(record: TechOperationRecord) -> str:
    return str(record.fields.get(MARK_FIELD) or record.display_text or "").strip()


def dia_cable(record: TechOperationRecord) -> str:
    """D-table row → its cable name: the first column («Кабель»), as `coaxDiaFromRecords` takes `Object.values(r)[0]`."""
    first = next(iter(record.fields.values()), "") if record.fields else ""
    return str(record.fields.get(_DIA_CABLE_FIELD, first) or record.display_text or "").strip()


def diameter_index(records: Iterable[TechOperationRecord]) -> dict[str, TechOperationRecord]:
    """Cable key → the D-table row the generator uses: rows in list order, the last one with a key wins
    (`byCable[key] = …` in `coaxDiaFromRecords`)."""
    index: dict[str, TechOperationRecord] = {}
    for record in records:
        key = cable_key(dia_cable(record))
        if key:
            index[key] = record
    return index


def _cores(fields: Mapping[str, str]) -> int | None:
    match = _LEADING_INT.match(str(fields.get(CORES_FIELD) or ""))
    return int(match.group(1)) if match else None


def auto_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> tuple[CableKind, str]:
    """Deterministic class of a БД.ПРОВ row and the reason shown to the user, first rule that fits:
    1. «Тип провода» starts with «коакс» → коаксиальный;
    2. the mark has a row in the D1·D2·D3 table → коаксиальный;
    3. no type, the mark carries the generator's coax markers (RG-…, coax, коакс) → коаксиальный;
    4. one core («Число жил» = 1) → монтажный провод;
    5. cores not given, type starts with «монтаж» or «силикон» → монтажный провод;
    6. everything else → прочие кабели."""
    kind_type = str(fields.get(TYPE_FIELD) or "").strip()
    lowered = kind_type.casefold()
    if lowered.startswith("коакс"):
        return CableKind.COAX, f"тип «{kind_type}»"
    if cable_key(mark) in dia_keys:
        return CableKind.COAX, "есть в таблице D1 · D2 · D3"
    if not kind_type and _COAX_MARK.search(mark):
        return CableKind.COAX, "марка коаксиального кабеля"
    cores = _cores(fields)
    if cores == 1:
        return CableKind.WIRE, "одна жила"
    if cores is None and lowered.startswith(("монтаж", "силикон")):
        return CableKind.WIRE, f"тип «{kind_type}»"
    if cores is not None:
        return CableKind.OTHER, f"жил: {cores}"
    return CableKind.OTHER, f"тип «{kind_type}»" if kind_type else "тип не указан"


@dataclass(frozen=True)
class CableRow:
    kind: CableKind
    auto: CableKind
    reason: str
    manual: bool
    cable: str
    record: TechOperationRecord | None  # БД.ПРОВ row; None — the cable is only in the D-table
    dia: TechOperationRecord | None  # the D-table row the generator uses for this name
    dia_hint: TechOperationRecord | None  # no own D row: the D row of the same family (RG58 for «RG-58 A/U»)

    @property
    def key(self) -> str:
        return cable_key(self.cable)


def family_dia(key: str, index: Mapping[str, TechOperationRecord]) -> TechOperationRecord | None:
    """The longest D-table cable whose key starts the row key and is followed by a non-digit («rg58» for
    «rg58a/u», not «rg14» for «rg142»). A suggestion only — the generator matches the exact name."""
    best = ""
    for candidate in index:
        if len(candidate) >= 3 and len(candidate) > len(best) and key.startswith(candidate) and not key[len(candidate):][:1].isdigit():
            best = candidate
    return index.get(best) if best and best != key else None


def cable_rows(
    wires: Sequence[TechOperationRecord], diameters: Sequence[TechOperationRecord], overrides: Mapping[str, CableKind],
) -> list[CableRow]:
    """БД.ПРОВ rows, each with its class, plus the D-table cables that have no БД.ПРОВ coax row of the same name.
    `overrides` — cable key → the class the user moved the row to; it beats the automatic rule."""
    index = diameter_index(diameters)
    dia_keys = frozenset(index)
    rows: list[CableRow] = []
    joined: set[str] = set()
    for record in wires:
        mark = cable_mark(record)
        key = cable_key(mark)
        auto, reason = auto_kind(record.fields, mark, dia_keys)
        manual = overrides.get(key)
        kind = manual or auto
        dia = index.get(key) if kind == CableKind.COAX else None
        if dia is not None:
            joined.add(key)
        rows.append(CableRow(
            kind=kind, auto=auto, reason="перенесён вручную" if manual and manual != auto else reason,
            manual=bool(manual and manual != auto), cable=mark, record=record, dia=dia,
            dia_hint=family_dia(key, index) if kind == CableKind.COAX and dia is None else None,
        ))
    for key, dia in index.items():
        if key not in joined:
            rows.append(CableRow(
                kind=CableKind.COAX, auto=CableKind.COAX, reason="есть в таблице D1 · D2 · D3", manual=False,
                cable=dia_cable(dia), record=None, dia=dia, dia_hint=None,
            ))
    return rows


def cable_counts(rows: Iterable[CableRow]) -> dict[str, int]:
    counts = {kind.value: 0 for kind in CableKind}
    for row in rows:
        counts[row.kind.value] += 1
    return counts


def clean_diameter(name: str, value: str) -> str:
    """A D value is empty or a number in mm with a comma or a dot («0,5»), as the sheet keeps it."""
    text = str(value or "").strip()
    if text and not _DIAMETER_VALUE.match(text):
        raise ReferenceValidationError(f"{name}: укажите число в мм, например 0,5")
    return text
