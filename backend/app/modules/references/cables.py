"""«Кабели и провода» (owner 04.10.2026, 10.10.2026): БД.ПРОВ and the coax D1·D2·D3 table (СПР.КАБ · коаксиал) as four
lists — коаксиальные кабели, монтажные провода, многожильные кабели («разобрать позже»), прочие кабели.

Storage stays as SUPPLYMAP keeps it: БД.ПРОВ rows (`prov`) and the D-table rows (`kab_coax`) the ETK generator reads
by cable name. This module only classifies and joins them; nothing here changes what the generator looks up."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from .cable_structure import CoaxCard, card_for
from .domain import ReferenceValidationError, TechOperationRecord, cable_key


class CableKind(str, Enum):
    COAX = "coax"
    WIRE = "wire"
    MULTI = "multi"
    OTHER = "other"


CABLE_KIND_LABELS = {
    CableKind.COAX: "Коаксиальные кабели", CableKind.WIRE: "Монтажные провода", CableKind.MULTI: "Многожильные кабели",
    CableKind.OTHER: "Прочие кабели",
}
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


# The база монтажных проводов (owner 12.10.2026: «UL1061 UL1571 UL1007 UL3239 НВ-4 600 ПВАМ МГТФ МГШВ МС 16-13
# МС 15-11 МК 26-13 ПУГВ … остальные марки туда не заноси»); 15.10.2026: «Прочие кабели» closed — every single-core
# series it held moves here (UL1015 … Кабель высоковольтный). Series key → the start of a mark that names it. Spaces
# and hyphens inside a series name are free («МС16-13» = «МС 16-13»), the next character must not continue the name
# (МГТФЭ, ПуГВнг(А)-LS for ПуГВ, UL10610 are other wires), a mark is matched from its start (МНВ-4 is not НВ-4).
# The order is the column order of «Ø изоляции по маркам» (`wire_dia`): new series go to the end.
HOOKUP_SERIES = (
    ("UL1061", r"UL[\s-]*1061"), ("UL1571", r"UL[\s-]*1571"), ("UL1007", r"UL[\s-]*1007"), ("UL3239", r"UL[\s-]*3239"),
    ("НВ-4", r"НВ[\s-]*4"), ("ПВАМ", r"ПВАМ"), ("МГТФ", r"МГТФ"), ("МГШВ", r"МГШВ"),
    ("МС 16-13", r"МС[\s-]*16[\s-]*13"), ("МС 15-11", r"МС[\s-]*15[\s-]*11"), ("МК 26-13", r"МК[\s-]*26[\s-]*13"),
    ("ПуГВ", r"ПУГВ"),
    ("UL1015", r"UL[\s-]*1015"), ("UL3135", r"UL[\s-]*3135"), ("UL758", r"UL[\s-]*758"), ("НВМ", r"НВМ"),
    ("МНВ-4", r"МНВ[\s-]*4"), ("ПГВА", r"ПГВА"), ("ПуГВнг(А)-LS", r"ПУГВНГ\s*\(\s*А\s*\)[\s-]*LS"),
    ("Силиконовый провод", r"СИЛИКОНОВЫЙ[\s-]+ПРОВОД"), ("AGR2", r"AGR[\s-]*2"),
    ("Кабель высоковольтный", r"КАБЕЛЬ[\s-]+ВЫСОКОВОЛЬТНЫЙ"),
)
_HOOKUP_PATTERNS = tuple(
    (key, re.compile(rf"^(?:ПРОВОД\s+)?{pattern}(?![0-9A-ZА-ЯЁ(])")) for key, pattern in HOOKUP_SERIES
)
# Latin letters typed for their Cyrillic twins («MГТФ», «HB-4»); the UL series are matched before this is applied.
_LATIN_TWINS = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
NOT_HOOKUP_REASON = "марка не из базы монтажных"


def hookup_series(mark: str) -> str | None:
    """The series key of a mark from the база монтажных проводов, or None: «МС16-13 0,08» → «МС 16-13», «НВ-4 600
    0,20» → «НВ-4», «ПуГВ 1,0» → «ПуГВ», «ПуГВнг(А)-LS 1х1,0» → «ПуГВнг(А)-LS», «МНВ-4 0,12» → «МНВ-4»; «МГТФЭ 0,2»,
    «UL10610 28AWG», «M22759/16 22AWG» → None."""
    text = " ".join(str(mark or "").split()).upper().replace("Ё", "Е")
    for candidate in (text, text.translate(_LATIN_TWINS)):
        for key, pattern in _HOOKUP_PATTERNS:
            if pattern.match(candidate):
                return key
    return None


def shape_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> tuple[CableKind, str]:
    """The class a БД.ПРОВ row has by its shape alone, first rule that fits:
    1. «Тип провода» starts with «коакс» → коаксиальный;
    2. the mark has a row in the D1·D2·D3 table → коаксиальный;
    3. no type, the mark carries the generator's coax markers (RG-…, coax, коакс) → коаксиальный;
    4. more than one core, or the type says «многожильный» → многожильный кабель (owner 10.10.2026: «все многожильные
       в отдельную вкладку»; UL2725 is listed with one core but typed as a multi-core cable);
    5. one core («Число жил» = 1) → монтажный провод;
    6. cores not given, type starts with «монтаж» or «силикон» → монтажный провод;
    7. everything else → прочие кабели."""
    kind_type = str(fields.get(TYPE_FIELD) or "").strip()
    lowered = kind_type.casefold()
    if lowered.startswith("коакс"):
        return CableKind.COAX, f"тип «{kind_type}»"
    if cable_key(mark) in dia_keys:
        return CableKind.COAX, "есть в таблице D1 · D2 · D3"
    if not kind_type and _COAX_MARK.search(mark):
        return CableKind.COAX, "марка коаксиального кабеля"
    cores = _cores(fields)
    if cores is not None and cores > 1:
        return CableKind.MULTI, f"жил: {cores}"
    if "многожил" in lowered:
        return CableKind.MULTI, f"тип «{kind_type}»"
    if cores == 1:
        return CableKind.WIRE, "одна жила"
    if cores is None and lowered.startswith(("монтаж", "силикон")):
        return CableKind.WIRE, f"тип «{kind_type}»"
    if cores is not None:
        return CableKind.OTHER, f"жил: {cores}"
    return CableKind.OTHER, f"тип «{kind_type}»" if kind_type else "тип не указан"


def auto_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> tuple[CableKind, str]:
    """Deterministic class of a БД.ПРОВ row and the reason shown to the user. Коаксиальные and многожильные — by
    shape (`shape_kind`); монтажные провода — only the series of the база монтажных (`hookup_series`), whatever the
    row's type or core count says (ПВАМ typed «силовой» is one); every other mark → прочие кабели. A manual move
    (`cable_kind` map) still beats this rule."""
    kind, reason = shape_kind(fields, mark, dia_keys)
    if kind in (CableKind.COAX, CableKind.MULTI):
        return kind, reason
    series = hookup_series(mark)
    if series is None:
        return CableKind.OTHER, NOT_HOOKUP_REASON
    return (kind, reason) if kind == CableKind.WIRE else (CableKind.WIRE, f"серия {series} из базы монтажных")


def left_hookups(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> bool:
    """A row the shape rule calls монтажный that the база монтажных sends to прочие (a single-core mark of no series)."""
    return shape_kind(fields, mark, dia_keys)[0] == CableKind.WIRE and auto_kind(fields, mark, dia_keys)[0] == CableKind.OTHER


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
    card: CoaxCard | None = None  # coax card by the exact name, else the family card («RG-58» for «RG-58 C/U»)
    card_exact: bool = False

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
    cards: Mapping[str, CoaxCard] | None = None,
) -> list[CableRow]:
    """БД.ПРОВ rows, each with its class, plus the D-table cables that have no БД.ПРОВ coax row of the same name, plus
    the coax cards no row is named as. `overrides` — cable key → the class the user moved the row to; it beats the
    automatic rule. Every coax row carries its card (`card_for`)."""
    cards = cards or {}
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
        card, exact = card_for(key, cards) if kind == CableKind.COAX else (None, False)
        rows.append(CableRow(
            kind=kind, auto=auto, reason="перенесён вручную" if manual and manual != auto else reason,
            manual=bool(manual and manual != auto), cable=mark, record=record, dia=dia,
            dia_hint=family_dia(key, index) if kind == CableKind.COAX and dia is None else None,
            card=card, card_exact=exact,
        ))
    named = {row.key for row in rows if row.kind == CableKind.COAX}
    for key, dia in index.items():
        if key not in joined:
            card, exact = card_for(key, cards)
            named.add(key)
            rows.append(CableRow(
                kind=CableKind.COAX, auto=CableKind.COAX, reason="есть в таблице D1 · D2 · D3", manual=False,
                cable=dia_cable(dia), record=None, dia=dia, dia_hint=None, card=card, card_exact=exact,
            ))
    for key, card in cards.items():
        if key not in named:
            rows.append(CableRow(
                kind=CableKind.COAX, auto=CableKind.COAX, reason="есть карточка коаксиального кабеля", manual=False,
                cable=card.name, record=None, dia=None, dia_hint=family_dia(key, index), card=card, card_exact=True,
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
