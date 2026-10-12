"""Pure SUPPLYMAP rules for technology-operation and terminal norms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import log
import re
from typing import Iterable, Mapping, Sequence

from .equipment import parse_inventory


WIRE_AWG_MM2 = {
    30: Decimal("0.05"),
    28: Decimal("0.08"),
    26: Decimal("0.13"),
    24: Decimal("0.2"),
    22: Decimal("0.33"),
    20: Decimal("0.52"),
    18: Decimal("0.82"),
    16: Decimal("1.31"),
    14: Decimal("2.08"),
    12: Decimal("3.31"),
    10: Decimal("5.26"),
}
GOST_AWG_CRIMP = {Decimal("1"): 18}
NO_DATA_MARK = "⚠ НЕТ ДАННЫХ"
TECH_SNAPSHOT_SCHEMA_VERSION = 14


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "").strip().replace(",", "."))
    except InvalidOperation:
        return Decimal(0)


def gost_awg_from_wire_table(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    """ГОСТ section → AWG from the СПР.КАБ AWG table, as SUPPLYMAP derives it (TcGenData.gs `readDiaTables_`):
    only whole-number AWG rows (1/0, 2/0 are not AWG numbers for the lookup)."""
    result: dict[str, int] = {}
    for row in rows:
        awg = str(row.get("AWG") or "").strip()
        gost = _decimal(row.get("ГОСТ"))
        if gost > 0 and re.fullmatch(r"\d+", awg):
            result[format(gost.normalize(), "f")] = int(awg)
    return result


def _nearest_awg(mm2: Decimal) -> int:
    if mm2 <= 0:
        return 0
    awg, ratio = min(
        ((candidate, abs(log(float(section / mm2)))) for candidate, section in WIRE_AWG_MM2.items()),
        key=lambda item: item[1],
    )
    return awg if ratio < 0.35 else 0


@dataclass(frozen=True)
class WireSection:
    awg: int = 0
    mm2: Decimal = Decimal(0)

    @property
    def label(self) -> str:
        if not self.awg and not self.mm2:
            return ""
        physical = WIRE_AWG_MM2.get(self.awg)
        mm2 = format(self.mm2.normalize(), "f").replace(".", ",")
        if self.awg and self.mm2 and physical is not None and abs(self.mm2 - physical) > Decimal("0.005"):
            return f"{mm2} мм² ({self.awg} AWG)"
        if self.awg:
            return f"{self.awg} AWG"
        return f"{mm2} мм²"


def wire_section(value: str, gost_awg: Mapping[str, int] | None = None) -> WireSection:
    text = str(value or "")
    awg_match = re.search(r"(\d{1,2})\s*AWG", text, re.IGNORECASE)
    if awg_match:
        awg = int(awg_match.group(1))
        return WireSection(awg, WIRE_AWG_MM2.get(awg, Decimal(0)))

    mm_match = re.search(r"(\d+(?:[.,]\d+)?)\s*мм(?:2|²)", text, re.IGNORECASE)
    mm2 = _decimal(mm_match.group(1)) if mm_match else Decimal(0)
    if not mm2:
        for token in text.split():
            if not re.fullmatch(r"\d+(?:[.,]\d+)?", token):
                continue
            candidate = _decimal(token)
            if Decimal("0.03") <= candidate <= Decimal(16):
                mm2 = candidate
                break
    if not mm2:
        return WireSection()

    if mm2 in GOST_AWG_CRIMP:
        return WireSection(GOST_AWG_CRIMP[mm2], mm2)
    normalized_gost = {_decimal(key): int(awg) for key, awg in (gost_awg or {}).items()}
    if mm2 in normalized_gost:
        return WireSection(normalized_gost[mm2], mm2)
    return WireSection(_nearest_awg(mm2), mm2)


def fold_wire_section(section: WireSection) -> WireSection:
    base = section.mm2 or WIRE_AWG_MM2.get(section.awg, Decimal(0))
    if not base:
        return section
    doubled = (base * 2).quantize(Decimal("0.001"))
    return WireSection(_nearest_awg(doubled), doubled)


@dataclass(frozen=True)
class CrimpProfile:
    awg_high: int
    awg_low: int
    height: str
    pull_force: str


@dataclass(frozen=True)
class CrimpEntry:
    height: str
    pull_force: str
    listed: bool
    range: bool


def parse_crimp_profiles(value: str) -> tuple[CrimpProfile, ...]:
    profiles = []
    for part in re.split(r"\n|;", str(value or "")):
        match = re.match(r"^\s*(\d+)(?:\s*[–—-]\s*(\d+))?\s*AWG\s*:\s*(.*)$", part, re.IGNORECASE)
        if not match:
            continue
        first = int(match.group(1))
        second = int(match.group(2) or first)
        body = match.group(3)
        pull = re.search(r"\(([^)]*)\)\s*$", body)
        height = body[:pull.start()].strip() if pull else body.strip()
        pull_force = re.sub(r"\s*Н\s*$", "", pull.group(1), flags=re.IGNORECASE).strip() if pull else ""
        profiles.append(CrimpProfile(max(first, second), min(first, second), height, pull_force))
    return tuple(profiles)


_PROFILE_START = re.compile(r"(?<=\s)(?=\d+(?:\s*[–—-]\s*\d+)?\s*AWG\s*:)", re.IGNORECASE)
_AWG_LABEL = re.compile(r"^(\d+)\s*[–—-]\s*(\d+)(\s*AWG)", re.IGNORECASE)


def normalize_crimp_text(value: object) -> str:
    """One canonical text for a crimp height / pull force cell: «28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62».
    Russian decimal comma, en dash in ranges, «; » between AWG profiles (also when the sheet glued them with a
    space or a line break), no stray spaces. The meaning is untouched: `parse_crimp_profiles` reads the result as the
    source with its ranges written the same way. Display-time only; stored rows are not rewritten here."""
    text = str(value or "").replace("\\n", "\n")
    text = re.sub(r"(\d)\.(\d)", r"\1,\2", text)
    text = _PROFILE_START.sub("\n", text)
    parts = []
    for part in re.split(r"\n|;", text):
        part = re.sub(r"\s+", " ", part).strip()
        if not part:
            continue
        part = _AWG_LABEL.sub(lambda m: f"{m.group(1)}–{m.group(2)}{m.group(3)}", part)
        part = re.sub(r"(\d)\s*AWG\s*:", r"\1 AWG:", part, flags=re.IGNORECASE)
        part = re.sub(r"(\d)\s+[-–—]\s+(?=\d)", r"\1–", part)
        part = re.sub(r"(\d,\d+)\s*-\s*(?=\d,\d)", r"\1–", part)
        parts.append(part)
    return "; ".join(parts)


_CRIMP_COLUMN = re.compile(r"^(высота обжима|усилие обрыва)")


def normalize_terminal_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """БД.ТЕР fields for display: the crimp height and pull force cells in the canonical text (see above)."""
    out = dict(fields)
    for key, value in fields.items():
        if key in ("terCrimpHeight", "terInsCrimpHeight", "terPullForceMin", "terPullForceMax") or _CRIMP_COLUMN.match(re.sub(r"\s+", " ", key).strip().lower()):
            out[key] = normalize_crimp_text(value)
    return out


def crimp_entry(value: str, awg: int) -> CrimpEntry:
    profiles = parse_crimp_profiles(value)
    if not profiles:
        return CrimpEntry(str(value or "").strip(), "", False, False)
    compatible = [item for item in profiles if item.awg_low <= awg <= item.awg_high]
    selected = next((item for item in compatible if item.awg_low == item.awg_high), None)
    selected = selected or (compatible[0] if compatible else None)
    if selected is None:
        return CrimpEntry("", "", True, False)
    return CrimpEntry(
        "" if selected.height == "—" else selected.height,
        selected.pull_force,
        True,
        selected.awg_low != selected.awg_high,
    )


@dataclass(frozen=True)
class TerminalRecord:
    article: str
    step: str = ""
    strip: str = ""
    l_plus: str = ""
    l_minus: str = ""
    applicator: str = ""
    crimp_height: str = ""
    pull_force_min: str = ""
    pull_force_max: str = ""
    awg_from: str = ""
    awg_to: str = ""
    mm2_from: str = ""
    mm2_to: str = ""
    # The contact card of protocol v5.2 (owner 12.10.2026): the same article in BAG packaging, Male / Female, the
    # insulation crimp, insulation OD, «Металл / Покрытие», max current, the maker's datasheet.
    article_bag: str = ""
    gender: str = ""
    ins_crimp_height: str = ""
    ins_od_from: str = ""
    ins_od_to: str = ""
    material: str = ""
    max_current: str = ""
    datasheet: str = ""

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> TerminalRecord:
        # A row stored before the card keys existed still has its sheet columns: they give the card meanwhile.
        card = {**terminal_card_fields(fields), **{key: fields[key] for key in TER_CARD_KEYS if fields.get(key)}}
        return cls(
            article_bag=card.get("terArticleBag", ""),
            gender=card.get("terGender", ""),
            ins_crimp_height=card.get("terInsCrimpHeight", ""),
            ins_od_from=card.get("terInsOdFrom", ""),
            ins_od_to=card.get("terInsOdTo", ""),
            material=card.get("terMaterial", ""),
            max_current=card.get("terMaxCurrent", ""),
            datasheet=card.get("terDatasheet", ""),
            article=fields.get("terArticle", ""),
            step=fields.get("terStep", ""),
            strip=fields.get("terStrip", ""),
            l_plus=fields.get("terLPlus", ""),
            l_minus=fields.get("terLMinus", ""),
            applicator=fields.get("terApplicator", ""),
            crimp_height=fields.get("terCrimpHeight", ""),
            pull_force_min=fields.get("terPullForceMin", ""),
            pull_force_max=fields.get("terPullForceMax", ""),
            awg_from=fields.get("awgFrom", ""),
            awg_to=fields.get("awgTo", ""),
            mm2_from=fields.get("mm2From", ""),
            mm2_to=fields.get("mm2To", ""),
        )


def terminal_record_for_section(
    records: Iterable[TerminalRecord], section: WireSection,
) -> TerminalRecord | None:
    compatible = []
    for record in records:
        awg_from, awg_to = _decimal(record.awg_from), _decimal(record.awg_to)
        mm2_from, mm2_to = _decimal(record.mm2_from), _decimal(record.mm2_to)
        fits = False
        if section.awg and awg_from and awg_to:
            fits = min(awg_from, awg_to) <= section.awg <= max(awg_from, awg_to)
        elif section.mm2 and mm2_from and mm2_to:
            fits = min(mm2_from, mm2_to) * Decimal("0.9") <= section.mm2 <= max(mm2_from, mm2_to) * Decimal("1.1")
        if fits:
            compatible.append(record)
    exact = next((
        record for record in compatible
        if (_decimal(record.awg_from) and _decimal(record.awg_from) == _decimal(record.awg_to))
        or (_decimal(record.mm2_from) and _decimal(record.mm2_from) == _decimal(record.mm2_to))
    ), None)
    return exact or (compatible[0] if compatible else None)


def _normalized_article(value: str) -> str:
    return re.sub(r"[\s./()_-]", "", str(value or "").casefold())


def matching_terminal_records(
    records: Iterable[TerminalRecord], article: str,
) -> tuple[TerminalRecord, ...]:
    """The rows of a terminal by its REEL or BAG article: exact, then without spaces . / ( ) _ - and case, then
    without a leading manufacturer word. The stored articles keep their spelling (owner 12.10.2026)."""
    records = tuple(records)

    def articles(item: TerminalRecord) -> tuple[str, ...]:
        return tuple(value for value in (item.article, item.article_bag) if value)

    exact = str(article or "").casefold().strip()
    matches = tuple(item for item in records if any(value.casefold().strip() == exact for value in articles(item)))
    if matches:
        return matches
    normalized = _normalized_article(article)
    if len(normalized) >= 3:
        matches = tuple(item for item in records if any(_normalized_article(value) == normalized
                                                         for value in articles(item)))
        if matches:
            return matches
    words = str(article or "").strip().split()
    without_manufacturer = _normalized_article(" ".join(words[1:])) if len(words) > 1 else ""
    if len(without_manufacturer) >= 3:
        return tuple(item for item in records if any(_normalized_article(value) == without_manufacturer
                                                     for value in articles(item)))
    return ()


@dataclass(frozen=True)
class TerminalSectionResolution:
    section: str
    crimp_height: str
    pull_force: str
    applicator: str
    range_only: bool
    found: bool


@dataclass(frozen=True)
class TerminalResolution:
    applicator: str
    crimp_height: str
    pull_force: str
    by_section: tuple[TerminalSectionResolution, ...]


def resolve_terminal(
    records: Iterable[TerminalRecord],
    article: str,
    sections: Iterable[WireSection],
) -> TerminalResolution:
    candidates = matching_terminal_records(records, article)
    unique_sections = tuple(dict.fromkeys(section for section in sections if section.label))
    resolved = []
    for section in unique_sections:
        record = terminal_record_for_section(candidates, section)
        height = crimp_entry(record.crimp_height, section.awg) if record else None
        pull = crimp_entry(record.pull_force_min, section.awg) if record else None
        awg_from = _decimal(record.awg_from) if record else Decimal(0)
        awg_to = _decimal(record.awg_to) if record else Decimal(0)
        pull_force = ""
        if height and height.pull_force:
            pull_force = height.pull_force
        elif pull and pull.listed:
            pull_force = pull.height
        elif record:
            pull_force = " - ".join(dict.fromkeys(
                value for value in (record.pull_force_min, record.pull_force_max) if value
            ))
        resolved.append(TerminalSectionResolution(
            section=section.label,
            crimp_height=height.height if height else "",
            pull_force=pull_force,
            applicator=record.applicator if record else "",
            range_only=bool(record and height and (height.range if height.listed else awg_from and awg_from != awg_to)),
            found=bool(record and height and (not height.listed or height.height)),
        ))

    found = [item for item in resolved if item.found]
    heights = list(dict.fromkeys(item.crimp_height for item in found))
    pulls = [item.pull_force for item in found if item.pull_force]
    # No row fits the section → the terminal's first row gives its plain height, applicator and pull force
    # (`buildTerData_`: `terRecordForSection_(…) || recs[0]`); bySection keeps found=False for the warning.
    record = (terminal_record_for_section(candidates, unique_sections[0]) if unique_sections else None) or (
        candidates[0] if candidates else None
    )
    plain_height = record.crimp_height if record and not parse_crimp_profiles(record.crimp_height) else ""
    plain_pull = (
        " - ".join(dict.fromkeys(value for value in (record.pull_force_min, record.pull_force_max) if value))
        if record and not parse_crimp_profiles(record.pull_force_min) else ""
    )
    return TerminalResolution(
        applicator=record.applicator if record and record.applicator else NO_DATA_MARK,
        crimp_height=(
            "; ".join(f"{item.section}: {item.crimp_height}" for item in found if item.crimp_height)
            if len(heights) > 1 else heights[0] if heights else plain_height
        ),
        pull_force=(
            plain_pull if not pulls else pulls[0] if all(value == pulls[0] for value in pulls)
            else "; ".join(f"{item.section}: {item.pull_force}" for item in found if item.pull_force)
        ),
        by_section=tuple(resolved),
    )


def operation_minutes(
    *, operation_seconds: object, setup_seconds: object,
    units: int | Decimal, batch_quantity: int | Decimal,
) -> Decimal:
    seconds = _decimal(operation_seconds) * _decimal(units) * _decimal(batch_quantity)
    seconds += _decimal(setup_seconds)
    return (seconds / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class WireWorkload:
    name: str
    quantity: int | Decimal = 1
    cores: int = 1


@dataclass(frozen=True)
class OperationWorkload:
    operation_type: str
    operation_seconds: object
    setup_seconds: object
    batch_quantity: int | Decimal
    terminal_kind: str = ""
    build_side: bool = False
    wires: tuple[WireWorkload, ...] = ()
    twist_count: int | Decimal = 1
    tinned_units: int | Decimal = 1
    tut_count: int | Decimal = 0
    mark_count: int | Decimal = 0
    seal_places: int | Decimal = 1
    units: int | Decimal = 1


@dataclass(frozen=True)
class OperationTimeLine:
    name: str
    minutes: Decimal


def calculate_operation_time(workload: OperationWorkload) -> tuple[OperationTimeLine, ...]:
    operation_type = workload.operation_type
    terminal_kind = workload.terminal_kind
    wires = workload.wires
    is_cut = operation_type in {"cutWire", "cutWireStrip"}
    batch = _decimal(workload.batch_quantity)
    operation_seconds = _decimal(workload.operation_seconds)
    setup_seconds = _decimal(workload.setup_seconds)

    if (is_cut or terminal_kind) and wires:
        def wire_units(wire: WireWorkload) -> Decimal:
            cores = max(1, int(wire.cores)) if terminal_kind else 1
            return _decimal(wire.quantity) * cores

        if terminal_kind in {"ins", "sdr"} or (terminal_kind == "prs" and not workload.build_side):
            count = sum((wire_units(wire) for wire in wires), Decimal(0))
            return (OperationTimeLine(
                operation_type,
                operation_minutes(
                    operation_seconds=operation_seconds,
                    setup_seconds=setup_seconds,
                    units=count,
                    batch_quantity=batch,
                ),
            ),)

        setup_per_wire = setup_seconds / len(wires)
        lines = []
        for wire in wires:
            units = wire_units(wire)
            if operation_seconds:
                minutes = operation_minutes(
                    operation_seconds=operation_seconds,
                    setup_seconds=setup_per_wire,
                    units=units,
                    batch_quantity=batch,
                )
            else:
                minutes = (units * batch + setup_per_wire / Decimal(60)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                )
            lines.append(OperationTimeLine(wire.name, minutes))
        return tuple(lines)

    count = None
    if operation_type == "twist":
        count = workload.twist_count
    elif operation_type == "tin":
        count = workload.tinned_units or 1
    elif operation_type in {
        "cutTut", "insTut", "endCap", "foldCore", "heatTut", "insWrap", "insMark",
    }:
        count = workload.mark_count if operation_type == "insMark" else workload.tut_count
        count = count or 1
    elif operation_type == "seal":
        count = workload.seal_places
    elif operation_type.startswith("coax") or operation_type in {"splice", "insHousing"}:
        count = workload.units or 1

    if count is not None:
        minutes = operation_minutes(
            operation_seconds=operation_seconds,
            setup_seconds=setup_seconds,
            units=max(Decimal(1), _decimal(count)),
            batch_quantity=batch,
        )
    else:
        minutes = (operation_seconds / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (OperationTimeLine(operation_type, minutes),)


@dataclass(frozen=True)
class ImportedTechnologyRecord:
    tab: str
    display_text: str
    search_text: str
    source_sheet: str
    sort_key: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ImportedTechnologySnapshot:
    schema_version: int
    source_updated_at: datetime | None
    records: tuple[ImportedTechnologyRecord, ...]
    declared_tabs: tuple[str, ...] = ()


SHEET_TABLES = {"prov": "БД.ПРОВ", "mat": "БД.МАТ"}
# СПР.КАБ (REF-SPRKAB): one sheet, two tables; view-only, kept in sheet order.
KAB_TABLES = {"kab_coax": "СПР.КАБ · коаксиал", "kab_awg": "СПР.КАБ · AWG"}
SPR_KAB_SHEET = "СПР.КАБ"


def _column_letter(index: int) -> str:
    name, number = "", index + 1
    while number > 0:
        number, rest = divmod(number - 1, 26)
        name = chr(65 + rest) + name
    return name


_MIRROR_ESCAPE = re.compile(r"\\(\\|t|n)")
_MIRROR_UNESCAPE = {"\\": "\\", "t": "\t", "n": "\n"}


def parse_mirror_rows(text: str) -> list[list[str]]:
    """Mirror TSV (SUPPLYMAP `db.mjs` `toTsv`): file line = sheet row, cells split by TAB; inside a cell the
    backslash, TAB and line break are escaped (`\\\\`, `\\t`, `\\n`); trailing empty cells are trimmed."""
    lines = str(text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [[_MIRROR_ESCAPE.sub(lambda match: _MIRROR_UNESCAPE[match.group(1)], cell) for cell in line.split("\t")]
            for line in lines]


def parse_mirror_tsv(text: str) -> dict[str, object]:
    """Mirror `_local/db/<sheet>.tsv` (header row 1) → the `tables` entry of the snapshot payload."""
    rows = parse_mirror_rows(text)
    return {"header": rows[0] if rows and rows[0] != [""] else [], "formulaCols": [], "rows": rows[1:]}


def parse_spr_kab_tsv(text: str) -> dict[str, dict[str, object]]:
    """Mirror `_local/db/СПР.КАБ.tsv` → `{"kab_coax": table, "kab_awg": table}` (the `tables` shape).

    The sheet holds two tables one under another with blank rows between them and no common header: the coax
    table (header `Кабель / D1 / D2 / D3`) on top, the AWG table below, its header starting with the cell
    `AWG`. Cells stay as displayed (decimal commas, «—»)."""
    rows = parse_mirror_rows(text)
    awg_at = next((i for i, row in enumerate(rows) if row and row[0].strip().casefold() == "awg"), None)
    if awg_at is None:
        raise ValueError(f"В {SPR_KAB_SHEET} нет шапки таблицы AWG (строка, начинающаяся с «AWG»)")

    def block(part: list[list[str]], name: str) -> dict[str, object]:
        filled = [row for row in part if any(cell.strip() for cell in row)]
        if not filled:
            raise ValueError(f"В {SPR_KAB_SHEET} нет таблицы «{name}»")
        return {"header": filled[0], "formulaCols": [], "rows": filled[1:]}

    return {"kab_coax": block(rows[:awg_at], "коаксиал"), "kab_awg": block(rows[awg_at:], "AWG")}


def import_sheet_table(tab: str, table: object) -> tuple[ImportedTechnologyRecord, ...]:
    """SUPPLYMAP PortalRef `refTable_` shape → records keyed by the sheet header (БД.ПРОВ / БД.МАТ).

    Formula columns are left out like in the other bases; empty cells stay as empty strings; rows with no
    non-formula cell are sheet filler, not records."""
    names = {**SHEET_TABLES, **KAB_TABLES}
    if tab not in names:
        raise ValueError(f"Таблица «{tab}» не поддерживается: только {', '.join(names)}")
    sheet = names[tab]
    if not isinstance(table, Mapping) or not isinstance(table.get("header"), list) or not isinstance(table.get("rows"), list):
        raise ValueError(f"Некорректная таблица {sheet}: нужны header и rows")
    header = [re.sub(r"\s+", " ", str(item or "")).strip() or f"Колонка {_column_letter(index)}"
              for index, item in enumerate(table["header"])]
    if len(set(header)) != len(header):
        raise ValueError(f"В шапке {sheet} повторяются колонки")
    formula_cols = {int(item) for item in table.get("formulaCols") or []}
    records = []
    for number, raw in enumerate(table["rows"], start=1):
        if isinstance(raw, Mapping):
            raw = raw.get("v")
        if not isinstance(raw, list):
            raise ValueError(f"Строка {number} таблицы {sheet} должна быть списком ячеек")
        cells = [str(value if value is not None else "").strip() for value in raw]
        if any(cells[len(header):]):
            raise ValueError(f"Строка {number} таблицы {sheet}: ячеек больше, чем колонок в шапке")
        cells = (cells + [""] * len(header))[:len(header)]
        if not any(value for index, value in enumerate(cells) if index not in formula_cols):
            continue
        fields = {name: cells[index] for index, name in enumerate(header) if index not in formula_cols}
        display = next(value for value in cells if value)
        records.append(ImportedTechnologyRecord(
            tab=tab, display_text=display, search_text="", source_sheet=sheet,
            sort_key=f"{number:05d}" if tab in KAB_TABLES else display, fields=fields,
        ))
    return tuple(records)


# The four bases the GAS exporter reads by header (SUPPLYMAP TcConfig.gs `TECHOPS_DB_APP.tabs`, in `tabOrder`):
# tab → (sheet, header row number).
TECH_SHEETS = {"ob": ("БД.ОБ", 2), "op": ("БД.ОП", 2), "ter": ("БД.ТЕР", 1), "coax": ("БД.КОАКС", 2)}


@dataclass(frozen=True)
class _SchemaField:
    key: str
    aliases: tuple[str, ...]
    store: bool = False
    pattern: str = ""  # fallback regex over the normalized headers


def _join_parts(*parts: str) -> str:
    return " | ".join(part for part in parts if part.strip())


# SUPPLYMAP TcOpsDb.gs `DB_SCHEMA` — the only place that says which column becomes which record field.
_TECH_SCHEMA = {
    "ob": ((
        _SchemaField("baseValue", ("для базы", "длябазы")),
        _SchemaField("obType", ("тип", "type", "категория", "category", "группа"), store=True),
    ), lambda v: v["baseValue"], lambda v: v["baseValue"],
        lambda v: f"{v['obType']} {v['baseValue']}" if v["obType"] else v["baseValue"]),
    "op": ((
        _SchemaField("opNumber", ("номер", "number"), store=True),
        _SchemaField("opName", ("название", "name"), store=True),
        _SchemaField("tOp", ("время операции", "время операции, сек", "время операции сек"), store=True),
        _SchemaField("tPrep", ("время подготовки, сек", "время подготовки сек", "время подготовки"), store=True),
        _SchemaField("tMachine", ("время машины, сек/оп; сек/м", "время машины сек/оп; сек/м", "время машины"), store=True),
    ), lambda v: _join_parts(v["opName"], v["opNumber"]), lambda v: v["opNumber"] + " " + v["opName"],
        lambda v: v["opName"] or v["opNumber"]),
    "ter": ((
        _SchemaField("terManufacturer", ("производитель", "бренд", "manufacturer"), store=True),
        _SchemaField("terSeries", ("series", "серия разъемов", "серия"), store=True),
        _SchemaField("terComponent", ("product name", "productname", "комплектующая"), store=True),
        _SchemaField("connType", ("тип разъёма", "тип разъема")),
        _SchemaField("terType", ("тип контакта", "тип конт.", "тип конт"), store=True),
        _SchemaField("artISL", ("артикул (контакта isl)", "артикул контакта isl")),
        _SchemaField("artSAG", ("артикул (контакт sag)", "артикул контакт sag")),
        _SchemaField("terArticle", ("артикул контакта (reel)", "артикул контакта", "артикул"), store=True),
        _SchemaField("terLPlus", ("l+", "l+ в мм", "l+(мм)", "l +"), store=True, pattern=r"^l\s*\+"),
        _SchemaField("terLMinus", ("l-", "l−", "l–", "l—", "l- в мм", "l-(мм)", "l −", "l -"), store=True,
                     pattern=r"^l\s*[-−–—]"),
        _SchemaField("terStep", ("шаг разъема", "шаг разъёма", "шаг", "pitch", "step", "шаг ленты", "шаг контакта"),
                     store=True, pattern=r"^шаг"),
        _SchemaField("terApplicator", ("аппликатор", "applicator", "applikator"), store=True),
        _SchemaField("terStrip", ("длина зачистки", "длина зачистки, мм", "зачистка", "длина снятия изоляции",
                                  "strip length", "strip"), store=True, pattern=r"зачист|strip"),
        _SchemaField("terCrimpHeight", ("высота обжима проводника , мм", "высота обжима проводника, мм",
                                        "высота обжима проводника", "crimp height conductor", "crimp height"), store=True),
        _SchemaField("terPullForceMin", ("усилие обрыва контакта, n", "усилие обрыва контакта n",
                                         "усилие обрыва контакта от, n", "усилие обрыва контакта от n",
                                         "усилие обрыва от, n", "усилие обрыва от", "pull force min", "pull test min",
                                         "pull-off force min"), store=True),
        _SchemaField("terPullForceMax", ("усилие обрыва контакта до, n", "усилие обрыва контакта до n",
                                         "усилие обрыва до, n", "усилие обрыва до", "pull force max", "pull test max",
                                         "pull-off force max"), store=True),
    ), lambda v: _join_parts(v["terManufacturer"], v["terSeries"], v["terComponent"]),
        lambda v: " ".join(v[key] for key in ("terManufacturer", "terSeries", "terComponent", "connType", "terType",
                                              "artISL", "artSAG")),
        None),  # no sortKey for ter, as in GAS
    "coax": ((
        _SchemaField("coaxArticle", ("артикул",), store=True),
        _SchemaField("coaxType", ("тип/серия", "тип / серия", "тип серия"), store=True),
        _SchemaField("coaxMfr", ("производитель", "бренд", "manufacturer"), store=True),
        _SchemaField("supplier", ("поставщик", "supplier")),
        _SchemaField("coaxWire", ("провод",), store=True),
        _SchemaField("program", ("программа",)),
    ), lambda v: _join_parts(v["coaxType"], v["coaxWire"], v["supplier"]),
        lambda v: " ".join(v[key] for key in ("coaxArticle", "coaxType", "coaxMfr", "supplier", "coaxWire", "program")),
        lambda v: f"{v['coaxWire']} {v['coaxType']} {v['coaxMfr']} {v['coaxArticle']}"),
}


def _normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def sheet_column_name(value: object) -> str:
    """A sheet header as a record field name: line breaks inside the header cell become spaces."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _resolve_schema(fields: Sequence[_SchemaField], header: Sequence[object]) -> dict[str, int]:
    """Schema field → header index, once by alias then by regex (TcOpsDb.gs `buildRecordFromSchema_`); -1 — none."""
    header_map: dict[str, int] = {}
    for index, name in enumerate(header):
        if _normalize_header(name):
            header_map[_normalize_header(name)] = index  # last duplicate wins, as in GAS
    resolved = {}
    for field in fields:
        index = next((header_map[_normalize_header(alias)] for alias in field.aliases
                      if _normalize_header(alias) in header_map), -1)
        if index < 0 and field.pattern:
            index = next((header_map[key] for key in header_map if re.search(field.pattern, key)), -1)
        resolved[field.key] = index
    return resolved


# The contact card of protocol v5.2 (owner 12.10.2026, «карточка контакта»): БД.ТЕР columns the GAS exporter never
# read. Derived here from the sheet columns, like the AWG/мм² ranges, so the GAS snapshot and the mirror give equal
# records; terGender comes from terType («Тип контакта»).
_TER_CARD = (
    _SchemaField("terArticleBag", ("артикул контакта (bag)", "артикул (bag)", "артикул bag", "bag"), pattern=r"\(bag\)"),
    _SchemaField("terInsCrimpHeight", ("высота обжима изоляции, мм", "высота обжима изоляции",
                                       "insulation crimp height"), pattern=r"^высота обжима изоляц"),
    _SchemaField("terInsOdFrom", ("диаметр изоляции от, мм", "диаметр изоляции от", "insulation od from"),
                 pattern=r"^диаметр изоляции от"),
    _SchemaField("terInsOdTo", ("диаметр изоляции до, мм", "диаметр изоляции до", "insulation od to"),
                 pattern=r"^диаметр изоляции до"),
    _SchemaField("terMaterial", ("материал контакта", "материал / покрытие", "материал/покрытие", "material"),
                 pattern=r"^материал"),
    _SchemaField("terMaxCurrent", ("максимальная сила тока, а", "максисмальная сила тока, а", "максимальный ток, а",
                                   "max current"), pattern=r"^макс\S* (сила )?тока"),
    _SchemaField("terDatasheet", ("ссылка на datasheet на сайте производителя", "datasheet", "даташит"),
                 pattern=r"datasheet|даташит"),
)
TER_CARD_KEYS = ("terGender", *(field.key for field in _TER_CARD))

# Fields the import derives from the sheet besides the DB_SCHEMA ones (БД.ТЕР AWG/мм² ranges, the contact card).
_DERIVED_KEYS = {"ter": ("awgFrom", "awgTo", "mm2From", "mm2To", *TER_CARD_KEYS)}

_FEMALE = re.compile(r"^(female|socket|receptacle|гнезд|мама)|\(socket\)|\(гнездо\)")
_MALE = re.compile(r"^(male|pin|tab|plug|штыр|папа)|\(pin\)|\(tab\)|\(штырь\)")


def contact_gender(value: object) -> str:
    """«Тип контакта» → Male / Female («Male (Pin)», «Female (Socket)», «Гнездо»…); text it can't read stays as is."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    folded = text.casefold()
    if _FEMALE.search(folded):
        return "Female"
    if _MALE.search(folded):
        return "Male"
    return text


def normalize_material(value: object) -> str:
    """«Металл / Покрытие» (protocol v5.2): one space on both sides of a slash, no doubled spaces."""
    return re.sub(r"\s*/\s*", " / ", re.sub(r"\s+", " ", str(value or "")).strip())


def terminal_card_indexes(columns: Sequence[object]) -> dict[str, int]:
    """Card key → index of the БД.ТЕР column it is read from; -1 — the base has no such column."""
    return _resolve_schema(_TER_CARD, columns)


def terminal_card_fields(fields: Mapping[str, str]) -> dict[str, str]:
    """The contact card from a БД.ТЕР record's sheet columns (and terGender from terType); a key whose column the
    record lacks is left out, so a stored value is not wiped. Nothing is guessed: an empty cell stays empty."""
    keys = schema_keys("ter")
    columns = [name for name in fields if name not in keys]
    card = {}
    for key, index in terminal_card_indexes(columns).items():
        if index >= 0:
            value = str(fields[columns[index]] or "").strip()
            card[key] = normalize_material(value) if key == "terMaterial" else value
    if "terType" in fields:
        card["terGender"] = contact_gender(fields["terType"])
    return card


_FORCE_UNITS = re.compile(r"\s*(?<![^\W\d_])(kgf|kgs|кгс|кгф|lbf|lbs|lb|фунт\w*)\b\.?", re.IGNORECASE)
_FORCE_NUMBER = re.compile(r"(?<![\w,.])(\d+(?:[.,]\d+)?)(?![\d,.])(?!\s*(?:[-–—]\s*\d+\s*)?awg)", re.IGNORECASE)


def pull_force_newtons(value: object) -> str:
    """A pull force in N (protocol v5.2): a profile written in kgf is ×9.8, in lbf ×4.448, rounded to 0,1 N with a
    decimal comma; the unit is dropped. AWG labels are not numbers of force; a text without such units is untouched."""
    text = str(value or "")
    parts = re.split(r"(\n|;)", text)
    for number, part in enumerate(parts):
        unit = _FORCE_UNITS.search(part)
        if not unit:
            continue
        factor = Decimal("9.8") if unit.group(1).casefold().startswith(("kg", "кг")) else Decimal("4.448")

        def newtons(match: re.Match[str]) -> str:
            force = (Decimal(match.group(1).replace(",", ".")) * factor).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            return format(force.normalize(), "f").replace(".", ",")

        parts[number] = _FORCE_NUMBER.sub(newtons, _FORCE_UNITS.sub("", part))
    return "".join(parts).strip()


def schema_keys(tab: str) -> frozenset[str]:
    """Record fields that are schema values, not sheet columns (for ОБ/ОП/ТЕР/КОАКС); empty for other tabs."""
    if tab not in _TECH_SCHEMA:
        return frozenset()
    return frozenset(field.key for field in _TECH_SCHEMA[tab][0]) | frozenset(_DERIVED_KEYS.get(tab, ()))


def sync_schema_fields(tab: str, fields: Mapping[str, str]) -> dict[str, str]:
    """Sheet columns → the schema values the generator reads (opName, terArticle…), as the import derives them.
    A record without sheet columns (imported before they were kept) is returned unchanged."""
    result = dict(fields)
    if tab not in _TECH_SCHEMA:
        return result
    keys = schema_keys(tab)
    columns = [name for name in fields if name not in keys]
    if not columns:
        return result
    schema_fields = _TECH_SCHEMA[tab][0]
    resolved = _resolve_schema(schema_fields, columns)
    for field in schema_fields:
        index = resolved[field.key]
        if field.store and index >= 0:
            result[field.key] = str(fields[columns[index]] or "").strip()
    if tab == "ter":
        for key, index in _range_indexes(columns).items():
            if index >= 0:
                result[key] = str(fields[columns[index]] or "").strip()
        result.update(terminal_card_fields(result))
    return result


def schema_display_text(tab: str, fields: Mapping[str, str]) -> str:
    """The record's display text by DB_SCHEMA (ОБ «Для базы», ОП «Название | Номер»…), from its sheet columns."""
    if tab not in _TECH_SCHEMA:
        return ""
    schema_fields, display, _search, _sort = _TECH_SCHEMA[tab]
    keys = schema_keys(tab)
    columns = [name for name in fields if name not in keys]
    resolved = _resolve_schema(schema_fields, columns)
    values = {field.key: (str(fields[columns[resolved[field.key]]] or "").strip() if resolved[field.key] >= 0
                          else str(fields.get(field.key, "") or "").strip()) for field in schema_fields}
    return display(values)


# Owner 12.10.2026: «Оборудование (ПР)» is a Google-era leftover — those rows are plain «Оборудование»; the «.X» rows
# with «(R)» in the article stood in for a model in the calculation and are replaced by the model's own text.
RETIRED_EQUIPMENT_TYPES = {"Оборудование (ПР)": "Оборудование"}
_CALC_UNIT_SUFFIX = re.compile(r"\(R\)(?=\s*/)")


def repoint_calc_unit(text: str) -> str:
    """«T21(R)/Станция паяльная» → «T21/Станция паяльная» (the «Для базы» text of the model's numbered units)."""
    return _CALC_UNIT_SUFFIX.sub("", text)


def retired_unit(inventory: str, article: str) -> bool:
    parsed = parse_inventory(inventory)
    return bool(parsed and parsed.suffix == "X") or article.strip().endswith("(R)")


def sheet_snapshot_records(tab: str, rows: Sequence[Sequence[object]]) -> tuple[list[str], list[dict[str, object]]]:
    """One base sheet (display values, sheet rows from 1) → `(columnHeaders, records)` exactly as the GAS exporter
    builds them (SUPPLYMAP TcOpsDb.gs `fetchTechOperationsSnapshotFromSource_` + `buildRecordFromSchema_`): header
    at the tab's fixed row, each field resolved once by alias then regex, rows without display text skipped, and
    `exportValues` = every named column of the row. The result goes through `import_supplymap_snapshot` unchanged."""
    sheet, header_row = TECH_SHEETS[tab]
    fields, display, search, sort_key = _TECH_SCHEMA[tab]
    if len(rows) < 2 or len(rows) < header_row:
        return [], []
    header = list(rows[header_row - 1])
    named = [(str(name or "").strip(), index) for index, name in enumerate(header) if str(name or "").strip()]
    resolved = _resolve_schema(fields, header)

    def cell(row: Sequence[object], index: int) -> str:
        text = str(row[index] if 0 <= index < len(row) and row[index] is not None else "").strip()
        if tab == "ob" and index in (type_index, resolved.get("obType", -2)):
            return RETIRED_EQUIPMENT_TYPES.get(text, text)
        if tab == "op":
            return repoint_calc_unit(text)
        return text

    inv_index = next((index for index, name in enumerate(header) if _normalize_header(name) == "инв. номер"), -1)
    article_index = next((index for index, name in enumerate(header) if _normalize_header(name) == "артикул"), -1)
    type_index = next((index for index, name in enumerate(header) if _normalize_header(name) == "тип"), -1)
    identity = [index for index, name in enumerate(header) if tab == "ob"
                and _normalize_header(name) in ("инв. номер", "наменование", "наименование")]
    records = []
    for row in rows[header_row:]:
        values = {field.key: cell(row, resolved[field.key]) for field in fields}
        text = display(values)
        if not text:
            continue
        # БД.ОБ: a row dragged down with only «№» and the «Для базы» formula («/») is no equipment — it has neither
        # an inventory number, nor a name, nor a type.
        if tab == "ob" and not values["obType"] and not any(cell(row, index) for index in identity):
            continue
        if tab == "ob" and retired_unit(cell(row, inv_index), cell(row, article_index)):
            continue  # «.X» / «(R)»: a calculation stand-in of the model, not a machine (owner 12.10.2026)
        record: dict[str, object] = {
            "tabKey": tab, "displayText": text,
            "normalizedSearch": re.sub(r"\s+", "", search(values).lower()),
            "exportValues": [cell(row, index) for _name, index in named],
            "sourceSheet": sheet,
        }
        if sort_key is not None:
            record["sortKey"] = sort_key(values)
        record.update({field.key: values[field.key] for field in fields if field.store})
        records.append(record)
    return [name for name, _index in named], records


def _range_indexes(headers: Iterable[object]) -> dict[str, int]:
    normalized = [re.sub(r"[\s.]", "", str(header or "").casefold()) for header in headers]

    def find(edge: str, units: tuple[str, ...]) -> int:
        return next((
            index for index, header in enumerate(normalized)
            if header.startswith(edge) and any(unit in header for unit in units)
        ), -1)

    return {
        "awgFrom": find("от", ("awg",)),
        "awgTo": find("до", ("awg",)),
        "mm2From": find("от", ("мм2", "мм²")),
        "mm2To": find("до", ("мм2", "мм²")),
    }


def import_supplymap_snapshot(payload: Mapping[str, object]) -> ImportedTechnologySnapshot:
    meta = payload.get("meta")
    raw_records = payload.get("records")
    if not isinstance(meta, Mapping) or not isinstance(raw_records, list):
        raise ValueError("Некорректный снимок БД.Технология")
    schema_version = int(meta.get("schemaVersion") or 0)
    if schema_version != TECH_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"Ожидается схема БД.Технология v{TECH_SNAPSHOT_SCHEMA_VERSION}")

    raw_updated = str(meta.get("updatedAt") or "").strip()
    try:
        source_updated_at = datetime.fromisoformat(raw_updated.replace("Z", "+00:00")) if raw_updated else None
    except ValueError as error:
        raise ValueError("В снимке БД.Технология неверная дата обновления") from error

    headers_by_tab = meta.get("columnHeadersByTab")
    headers_by_tab = headers_by_tab if isinstance(headers_by_tab, Mapping) else {}
    terminal_indexes = _range_indexes(headers_by_tab.get("ter", []))
    core_keys = {
        "tabKey", "displayText", "normalizedSearch", "sourceSheet",
        "sortKey", "exportValues",
    }
    records = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ValueError("Запись снимка БД.Технология должна быть объектом")
        fields = {
            str(key): str(value or "").strip()
            for key, value in raw.items()
            if key not in core_keys and isinstance(value, (str, int, float))
        }
        tab = str(raw.get("tabKey") or "").strip()
        export_values = raw.get("exportValues")
        # Every named sheet column goes into the record, as the portal shows the base (owner 29.09.2026:
        # «не всё перенеслось»); the schema values above stay for the generator.
        tab_headers = headers_by_tab.get(tab)
        if isinstance(export_values, list) and isinstance(tab_headers, list):
            for name, value in zip(tab_headers, export_values):
                column = sheet_column_name(name)
                if column and column not in fields:
                    fields[column] = str(value or "").strip()
        if tab == "ter" and isinstance(export_values, list):
            for key, index in terminal_indexes.items():
                fields[key] = str(export_values[index] or "").strip() if 0 <= index < len(export_values) else ""
        if tab == "ter":
            fields.update(terminal_card_fields(fields))
        records.append(ImportedTechnologyRecord(
            tab=tab,
            display_text=str(raw.get("displayText") or "").strip(),
            search_text=str(raw.get("normalizedSearch") or "").strip(),
            source_sheet=str(raw.get("sourceSheet") or "").strip(),
            sort_key=str(raw.get("sortKey") or "").strip(),
            fields=fields,
        ))
    tables = payload.get("tables")
    if tables is not None and not isinstance(tables, Mapping):
        raise ValueError("tables снимка БД.Технология должен быть объектом")
    for tab, table in (tables or {}).items():
        records.extend(import_sheet_table(str(tab), table))
    return ImportedTechnologySnapshot(
        schema_version, source_updated_at, tuple(records), tuple(str(tab) for tab in (tables or {})),
    )
