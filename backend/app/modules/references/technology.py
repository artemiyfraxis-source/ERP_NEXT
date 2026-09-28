"""Pure SUPPLYMAP rules for technology-operation and terminal norms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import log
import re
from typing import Iterable, Mapping


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

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> TerminalRecord:
        return cls(
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
    records = tuple(records)
    exact = str(article or "").casefold().strip()
    matches = tuple(item for item in records if item.article.casefold().strip() == exact)
    if matches:
        return matches
    normalized = _normalized_article(article)
    if len(normalized) >= 3:
        matches = tuple(item for item in records if _normalized_article(item.article) == normalized)
        if matches:
            return matches
    words = str(article or "").strip().split()
    without_manufacturer = _normalized_article(" ".join(words[1:])) if len(words) > 1 else ""
    if len(without_manufacturer) >= 3:
        return tuple(item for item in records if _normalized_article(item.article) == without_manufacturer)
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
        if tab == "ter" and isinstance(export_values, list):
            for key, index in terminal_indexes.items():
                fields[key] = str(export_values[index] or "").strip() if 0 <= index < len(export_values) else ""
        records.append(ImportedTechnologyRecord(
            tab=tab,
            display_text=str(raw.get("displayText") or "").strip(),
            search_text=str(raw.get("normalizedSearch") or "").strip(),
            source_sheet=str(raw.get("sourceSheet") or "").strip(),
            sort_key=str(raw.get("sortKey") or "").strip(),
            fields=fields,
        ))
    return ImportedTechnologySnapshot(schema_version, source_updated_at, tuple(records))
