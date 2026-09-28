"""Reader for the SUPPLYMAP warehouse export (WH-IMPORT): ЦАР.csv, ЖУК.csv, СПИСАНИЕ.csv + _manifest.json.

Pure parsing, no database. Values are sheet display values: comma decimals, dd.mm.yyyy dates. Columns are
mapped by header name only, because the live sheets keep different column orders (ЖУК has junk columns).
A row the importer cannot take is returned as a rejection with its reason, never dropped silently.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

STOCK_SHEETS = ("ЦАР", "ЖУК")
WRITEOFF_SHEET = "СПИСАНИЕ"
MANIFEST = "_manifest.json"

# Header → field. Required headers abort the import when missing: a sheet without them is not a stock sheet.
_STOCK_REQUIRED = {
    "ГРН": "grn", "НАЗНАЧЕНИЕ": "purpose", "ЕД.ИЗМ": "unit",
    "ПРИХОД": "inbound", "СПИСАНО": "written_off", "БАЛАНС": "balance",
}
_STOCK_OPTIONAL = {
    "ТИП": "item_type", "АРТИКУЛ": "article", "НАИМЕНОВАНИЕ": "name", "СЕРИЯ": "series",
    "ПАКЕТ": "package", "МЕСТО ХРАНЕНИЯ": "storage_location", "РЕЗЕРВ": "reserved",
}
_WRITEOFF_REQUIRED = {"ДАТА": "date", "СКЛАД": "warehouse", "ГРН": "grn", "КОЛ-ВО": "quantity", "ОСНОВАНИЕ": "reason"}
_WRITEOFF_OPTIONAL = {
    "ПАКЕТ": "package", "НАИМЕНОВАНИЕ": "name", "ЕД.ИЗМ": "unit", "НАЗНАЧЕНИЕ": "purpose",
    "КТО СПИСАЛ": "author", "ПРОВЕДЕНО": "posted", "ОСТАТОК ПОСЛЕ": "remaining_after",
}
# Column widths of warehouse_lots: longer text would fail on PostgreSQL, so it is a rejection, not a crash.
_MAX_LENGTH = {"grn": 32, "package": 32, "purpose": 128, "unit": 32, "item_type": 255, "series": 255,
               "article": 1024, "name": 1024, "storage_location": 255}
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


class StockExportError(ValueError):
    """The export as a whole cannot be read (missing file, header or truncated sheet)."""


def normalize_header(value: str) -> str:
    # SUPPLYMAP normHeader_: collapse whitespace, trim, ignore case.
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def parse_quantity(text: str) -> Decimal:
    """Display value → Decimal: «1,81», «1 234,5», empty = 0. Negative or garbage raises ValueError."""
    cleaned = re.sub(r"[\s  ]", "", str(text or "")).replace(",", ".")
    if not cleaned:
        return Decimal("0")
    if not _NUMBER.fullmatch(cleaned):
        raise ValueError(f"не число: «{text}»")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as error:
        raise ValueError(f"не число: «{text}»") from error
    if value.as_tuple().exponent < -6:
        raise ValueError(f"больше 6 знаков после запятой: «{text}»")
    return value


@dataclass(frozen=True)
class LegacyStockRow:
    sheet: str
    row_number: int  # 1-based sheet row, header is row 1
    warehouse: str
    grn: str
    package: str
    article: str
    name: str
    series: str
    item_type: str
    purpose: str
    unit: str
    storage_location: str
    inbound: Decimal
    written_off: Decimal
    balance: Decimal
    reserved: Decimal

    @property
    def key(self) -> tuple[str, str, str]:
        return self.warehouse, self.grn, self.package


@dataclass(frozen=True)
class LegacyWriteOffRow:
    row_number: int
    date: str
    warehouse: str
    grn: str
    package: str
    quantity: Decimal
    unit: str
    reason: str
    purpose: str
    author: str
    posted: str
    remaining_after: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.warehouse, self.grn, self.package

    @property
    def is_posted(self) -> bool:
        return self.posted.strip().casefold() == "да"

    @property
    def is_return(self) -> bool:
        # SUPPLYMAP writeoffIsReturn_: a return is a journal row whose basis starts with «Возврат».
        return self.reason.strip().casefold().startswith("возврат")


@dataclass(frozen=True)
class RejectedRow:
    sheet: str
    row_number: int
    grn: str
    reason: str
    balance: Decimal | None = None
    unit: str = ""
    package: str = ""


@dataclass
class SheetSummary:
    sheet: str
    rows_total: int = 0
    blank_rows: int = 0
    skipped_columns: dict[str, int] = field(default_factory=dict)  # header → how many rows had a value


@dataclass
class StockExport:
    exported_at: str
    lots: list[LegacyStockRow]
    writeoffs: list[LegacyWriteOffRow]
    rejected: list[RejectedRow]
    sheets: dict[str, SheetSummary]


def _read_csv(path: Path) -> list[list[str]]:
    if not path.is_file():
        raise StockExportError(f"Нет файла {path.name}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def _header_map(sheet: str, header: list[str], required: dict[str, str], optional: dict[str, str]):
    names = [normalize_header(cell) for cell in header]
    duplicates = [name for name, count in Counter(n for n in names if n).items() if count > 1]
    if duplicates:
        raise StockExportError(f"{sheet}: повторяются колонки {', '.join(sorted(duplicates))}")
    missing = [name for name in required if name not in names]
    if missing:
        raise StockExportError(f"{sheet}: нет колонок {', '.join(missing)}")
    known = {**required, **optional}
    mapped = {known[name]: index for index, name in enumerate(names) if name in known}
    skipped = {header[index].strip(): index for index, name in enumerate(names) if name and name not in known}
    return mapped, skipped


def _cells(row: list[str], mapped: dict[str, int]) -> dict[str, str]:
    return {key: (row[index] if index < len(row) else "").strip() for key, index in mapped.items()}


def _check_manifest(directory: Path) -> tuple[str, dict]:
    path = directory / MANIFEST
    if not path.is_file():
        raise StockExportError(f"Нет {MANIFEST}: без него нельзя проверить, что выгрузка полная")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    sheets = manifest.get("sheets") or {}
    for name in (*STOCK_SHEETS, WRITEOFF_SHEET):
        if name not in sheets:
            raise StockExportError(f"{MANIFEST}: нет листа {name}")
    return str(manifest.get("exportedAt") or ""), sheets


def _rows_of(directory: Path, name: str, meta: dict) -> list[list[str]]:
    rows = _read_csv(directory / str(meta.get("file") or f"{name}.csv"))
    if not rows:
        raise StockExportError(f"{name}: пустой файл без заголовка")
    expected = meta.get("rows")
    if expected is not None and int(expected) != len(rows):
        raise StockExportError(f"{name}: в файле {len(rows)} строк, в манифесте {expected} — выгрузка неполная")
    return rows


def _parse_stock_sheet(name: str, rows: list[list[str]], export: StockExport) -> None:
    mapped, skipped = _header_map(name, rows[0], _STOCK_REQUIRED, _STOCK_OPTIONAL)
    summary = SheetSummary(sheet=name, skipped_columns={header: 0 for header in skipped})
    export.sheets[name] = summary
    for offset, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            summary.blank_rows += 1
            continue
        summary.rows_total += 1
        for header, index in skipped.items():
            if index < len(row) and row[index].strip():
                summary.skipped_columns[header] += 1
        cells = _cells(row, mapped)
        grn = cells["grn"]

        def reject(reason: str, balance: Decimal | None = None) -> None:
            export.rejected.append(RejectedRow(name, offset, grn, reason, balance, cells.get("unit", ""),
                                               cells.get("package", "")))

        if not grn:
            reject("нет ГРН")
            continue
        try:
            numbers = {key: parse_quantity(cells.get(key, "")) for key in ("inbound", "written_off", "balance", "reserved")}
        except ValueError as error:
            reject(str(error))
            continue
        if numbers["inbound"] - numbers["written_off"] != numbers["balance"]:
            reject(
                f"БАЛАНС {numbers['balance']} ≠ ПРИХОД {numbers['inbound']} − СПИСАНО {numbers['written_off']}",
                numbers["balance"],
            )
            continue
        too_long = [key for key, limit in _MAX_LENGTH.items() if len(cells.get(key, "")) > limit]
        if too_long:
            reject(f"слишком длинное значение: {', '.join(too_long)}", numbers["balance"])
            continue
        export.lots.append(LegacyStockRow(
            sheet=name, row_number=offset, warehouse=name, grn=grn,
            package=cells.get("package", ""), article=cells.get("article", ""), name=cells.get("name", ""),
            series=cells.get("series", ""), item_type=cells.get("item_type", ""), purpose=cells["purpose"],
            unit=cells["unit"], storage_location=cells.get("storage_location", ""), **numbers,
        ))


def _drop_duplicate_keys(export: StockExport) -> None:
    # SUPPLYMAP: one GRN (+ package) is one row across both warehouses. Which copy is right is not ours to
    # guess, so every copy is rejected and the owner decides.
    counts = Counter((lot.grn, lot.package) for lot in export.lots)
    kept = []
    for lot in export.lots:
        if counts[(lot.grn, lot.package)] > 1:
            label = lot.grn + (f" / {lot.package}" if lot.package else "")
            export.rejected.append(RejectedRow(lot.sheet, lot.row_number, lot.grn,
                                               f"повтор партии {label} в выгрузке", lot.balance, lot.unit,
                                               lot.package))
        else:
            kept.append(lot)
    export.lots = kept


def _parse_writeoffs(rows: list[list[str]], export: StockExport) -> None:
    mapped, skipped = _header_map(WRITEOFF_SHEET, rows[0], _WRITEOFF_REQUIRED, _WRITEOFF_OPTIONAL)
    summary = SheetSummary(sheet=WRITEOFF_SHEET, skipped_columns={header: 0 for header in skipped})
    export.sheets[WRITEOFF_SHEET] = summary
    for offset, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            summary.blank_rows += 1
            continue
        summary.rows_total += 1
        for header, index in skipped.items():
            if index < len(row) and row[index].strip():
                summary.skipped_columns[header] += 1
        cells = _cells(row, mapped)
        try:
            quantity = parse_quantity(cells["quantity"])
        except ValueError as error:
            export.rejected.append(RejectedRow(WRITEOFF_SHEET, offset, cells["grn"], str(error)))
            continue
        export.writeoffs.append(LegacyWriteOffRow(
            row_number=offset, date=cells["date"], warehouse=cells["warehouse"], grn=cells["grn"],
            package=cells.get("package", ""), quantity=quantity, unit=cells.get("unit", ""),
            reason=cells["reason"], purpose=cells.get("purpose", ""), author=cells.get("author", ""),
            posted=cells.get("posted", ""), remaining_after=cells.get("remaining_after", ""),
        ))


def read_stock_export(directory: Path | str) -> StockExport:
    directory = Path(directory)
    exported_at, sheets = _check_manifest(directory)
    export = StockExport(exported_at=exported_at, lots=[], writeoffs=[], rejected=[], sheets={})
    for name in STOCK_SHEETS:
        _parse_stock_sheet(name, _rows_of(directory, name, sheets[name]), export)
    _drop_duplicate_keys(export)
    _parse_writeoffs(_rows_of(directory, WRITEOFF_SHEET, sheets[WRITEOFF_SHEET]), export)
    return export
