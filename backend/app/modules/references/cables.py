"""«Кабели и провода» (owner 04.10.2026, 10.10.2026): БД.ПРОВ and the coax D1·D2·D3 table (СПР.КАБ · коаксиал) as three
lists — коаксиальные кабели, монтажные провода, многожильные кабели («Прочие кабели» closed 20.10.2026).

Storage stays as SUPPLYMAP keeps it: БД.ПРОВ rows (`prov`) and the D-table rows (`kab_coax`) the ETK generator reads
by cable name. Each БД.ПРОВ row stores its list (`TechOperationRecord.cable_kind`, migration 0129); the rule here
(`auto_kind`) gives the list of a row being created and the «auto» shown beside a moved one. This module only
classifies and joins; nothing here changes what the generator looks up."""

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


CABLE_KIND_LABELS = {
    CableKind.COAX: "Коаксиальные кабели", CableKind.WIRE: "Монтажные провода", CableKind.MULTI: "Многожильные кабели",
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
    found = hookup_series_rest(mark)
    return found[0] if found else None


def hookup_series_rest(mark: str) -> tuple[str, str] | None:
    """The series key and what follows the series name in the mark («Провод МС 16-13 0,08 красный» → («МС 16-13»,
    « 0,08 КРАСНЫЙ»), upper case) — where the size of the wire is written; None — not a series of the база."""
    text = " ".join(str(mark or "").split()).upper().replace("Ё", "Е")
    for candidate in (text, text.translate(_LATIN_TWINS)):
        for key, pattern in _HOOKUP_PATTERNS:
            match = pattern.match(candidate)
            if match:
                return key, candidate[match.end():]
    return None


def shape_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> tuple[CableKind | None, str]:
    """The class a БД.ПРОВ row has by its shape alone, first rule that fits:
    1. «Тип провода» starts with «коакс» → коаксиальный;
    2. the mark has a row in the D1·D2·D3 table → коаксиальный;
    3. no type, the mark carries the generator's coax markers (RG-…, coax, коакс) → коаксиальный;
    4. more than one core, or the type says «многожильный» → многожильный кабель (owner 10.10.2026: «все многожильные
       в отдельную вкладку»; UL2725 is listed with one core but typed as a multi-core cable);
    5. one core («Число жил» = 1) → монтажный провод;
    6. cores not given, type starts with «монтаж» or «силикон» → монтажный провод;
    7. everything else → None: the shape does not tell (`auto_kind` decides)."""
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
    return None, f"жил: {cores}" if cores is not None else f"тип «{kind_type}»" if kind_type else "тип не указан"


def auto_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> tuple[CableKind, str]:
    """Deterministic class of a БД.ПРОВ row and the reason shown to the user. Коаксиальные and многожильные — by
    shape (`shape_kind`); every other row is a монтажный провод: of a series of the база монтажных (`hookup_series`),
    whatever its type or core count says (ПВАМ typed «силовой» is one), or a mark of no series (NOT_HOOKUP_REASON — no
    structure is made for it). «Прочие кабели» is closed (owner 20.10.2026): a row that used to land there has at most
    one core, so it is монтажный. A row stores its list; this rule gives the list of a row being created."""
    kind, reason = shape_kind(fields, mark, dia_keys)
    if kind in (CableKind.COAX, CableKind.MULTI):
        return kind, reason
    series = hookup_series(mark)
    if series is None:
        return CableKind.WIRE, NOT_HOOKUP_REASON
    return (kind, reason) if kind == CableKind.WIRE else (CableKind.WIRE, f"серия {series} из базы монтажных")


def resolve_kind(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str],
                 overrides: Mapping[str, CableKind]) -> CableKind:
    """The list of a БД.ПРОВ row being created: where a row of its mark was moved by hand, else the rule."""
    return overrides.get(cable_key(mark)) or auto_kind(fields, mark, dia_keys)[0]


def left_hookups(fields: Mapping[str, str], mark: str, dia_keys: frozenset[str] | set[str]) -> bool:
    """A row the shape rule calls монтажный whose mark is of no series of the база монтажных: it stays in the
    монтажные, without a structure (it used to go to «прочие» before 20.10.2026)."""
    return shape_kind(fields, mark, dia_keys)[0] == CableKind.WIRE and hookup_series(mark) is None


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
    wires: Sequence[TechOperationRecord], diameters: Sequence[TechOperationRecord],
    cards: Mapping[str, CoaxCard] | None = None,
) -> list[CableRow]:
    """БД.ПРОВ rows, each in the list it stores (`cable_kind`; «перенесён вручную» when the rule says another), plus the
    D-table cables that have no БД.ПРОВ coax row of the same name, plus the coax cards no row is named as. Every coax
    row carries its card (`card_for`)."""
    cards = cards or {}
    index = diameter_index(diameters)
    dia_keys = frozenset(index)
    rows: list[CableRow] = []
    joined: set[str] = set()
    for record in wires:
        mark = cable_mark(record)
        key = cable_key(mark)
        auto, reason = auto_kind(record.fields, mark, dia_keys)
        kind = CableKind(record.cable_kind)
        dia = index.get(key) if kind == CableKind.COAX else None
        if dia is not None:
            joined.add(key)
        card, exact = card_for(key, cards) if kind == CableKind.COAX else (None, False)
        rows.append(CableRow(
            kind=kind, auto=auto, reason="перенесён вручную" if kind != auto else reason,
            manual=kind != auto, cable=mark, record=record, dia=dia,
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


# «Совместимые разъёмы» of a coax cable (owner 19.10.2026: «совместимые разъёмы … должны подтягиваться — из базы
# коаксиальных разъёмов»): the БД.КОАКС rows whose «Провод» (`coaxWire`) is this cable. Only the cells the card shows.
CONNECTOR_FIELDS = ("coaxArticle", "Артикул", "coaxType", "Тип/Серия", "coaxWire", "Провод", "Серия", "Пол", "Исполнение",
                    "Монтаж", "Поставщик", "Ссылка", "Источник данных", "Цена, ₽", "Дата цены")


def connector_cable(record: TechOperationRecord) -> str:
    """The cable a БД.КОАКС row is for: «coaxWire», else the sheet's «Провод»."""
    return str(record.fields.get("coaxWire") or record.fields.get("Провод") or "").strip()


def connectors_by_cable(records: Iterable[TechOperationRecord]) -> dict[str, list[TechOperationRecord]]:
    """Cable key → the БД.КОАКС rows made for it, in list order."""
    index: dict[str, list[TechOperationRecord]] = {}
    for record in records:
        key = cable_key(connector_cable(record))
        if key:
            index.setdefault(key, []).append(record)
    return index


def compatible_connectors(row: CableRow, index: Mapping[str, Sequence[TechOperationRecord]]) -> tuple[list[TechOperationRecord], str]:
    """БД.КОАКС rows for a coax row: by its mark, and — when the card is this very cable — by the card's name and its
    aliases (another spelling of the same cable: «RG58» finds the rows written «RG-58»). A row with none of its own and
    a family card («5D-FB» for «5D-FB CCA PE AL PE» — the same dimensions, other materials) takes the family's
    (owner 19.10.2026: «они сами должны подтягиваться»); the second item is then the family card's name. Each row once."""
    if row.kind != CableKind.COAX:
        return [], ""

    def found_by(keys: Iterable[str]) -> list[TechOperationRecord]:
        found: dict[object, TechOperationRecord] = {}
        for key in dict.fromkeys(keys):
            for record in index.get(key, ()):
                found.setdefault(record.id, record)
        return list(found.values())

    card_keys = [row.card.name_key, *(alias.alias_key for alias in row.card.aliases)] if row.card is not None else []
    own = found_by([row.key, *(card_keys if row.card_exact else [])])
    if own or row.card is None or row.card_exact:
        return own, ""
    family = found_by(card_keys)
    return family, row.card.name if family else ""


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
