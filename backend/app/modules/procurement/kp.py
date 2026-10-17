"""Pure rules of the «Внести КП» form: reading a supplier offer file and matching it to order lines.

Ports SUPPLYMAP `Kp.gs` (`kpExtractItems_`, `kpHeaderCols_`) and the client matching of `KpForm.html`
(`normName`, `applyImport`, `fillRow`). SUPPLYMAP converts any file through Google Drive; ERP reads
.xlsx and .csv itself — agent decision, see ARCHITECTURE.md.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from xml.etree import ElementTree

HEADER_SCAN_ROWS = 8
MAX_FILE_BYTES = 5 * 1024 * 1024
_MAX_XML_BYTES = 32 * 1024 * 1024
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}


class KpFileError(ValueError):
    """The offer file cannot be read or has no «Наименование | Кол-во | Цена | НДС» header."""


@dataclass(frozen=True)
class KpItem:
    name: str
    qty: str = ""
    price: str = ""
    vat: str = ""


@dataclass(frozen=True)
class KpColumns:
    name: int | None = None
    qty: int | None = None
    price: int | None = None
    vat: int | None = None


def _norm_header(value: object) -> str:
    # normHeader_: collapse whitespace, trim, lower case.
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def kp_header_columns(header_row: Sequence[object]) -> KpColumns:
    """`kpHeaderCols_`: НДС is checked before «цена», so «Цена с НДС» never becomes the price column."""
    name = qty = price = vat = None
    for index, cell in enumerate(header_row):
        header = _norm_header(cell)
        if name is None and "наименование" in header:
            name = index
        elif qty is None and ("кол-во" in header or "колво" in header or "количество" in header):
            qty = index
        elif vat is None and "ндс" in header:
            vat = index
        elif price is None and "цена" in header:
            price = index
    return KpColumns(name=name, qty=qty, price=price, vat=vat)


def _cell(row: Sequence[object], index: int | None) -> str:
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def extract_kp_items(grid: Sequence[Sequence[object]]) -> list[KpItem]:
    """`kpExtractItems_`: header row within the first 8 rows, then every row with a name."""
    for head in range(min(len(grid), HEADER_SCAN_ROWS)):
        columns = kp_header_columns(grid[head] or [])
        if columns.name is not None:
            break
    else:
        raise KpFileError("Не нашёл шапку «Наименование | Кол-во | Цена | НДС» в файле")
    items = []
    for row in grid[head + 1:]:
        row = row or []
        name = _cell(row, columns.name)
        if not name:
            continue
        items.append(
            KpItem(
                name=name,
                qty=_cell(row, columns.qty),
                price=_cell(row, columns.price),
                vat=_cell(row, columns.vat),
            )
        )
    return items


def normalize_name(value: object) -> str:
    """KpForm `normName`: lower case, ё→е, anything but a letter or digit → one space."""
    text = str(value or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zа-я]+", " ", text)).strip()


def match_kp_items(
    items: Sequence[KpItem],
    candidates: Sequence[tuple[str, Iterable[str | None]]],
) -> tuple[list[tuple[str, KpItem]], list[str]]:
    """KpForm `applyImport`: exact normalized name first, else the first substring match either way.

    `candidates` are (key, names) in form order; a name belongs to the first line that carries it.
    Several offer rows may land on one line — the later one wins, as in the form.
    """
    by_name: dict[str, str] = {}
    for key, names in candidates:
        for name in names:
            normalized = normalize_name(name)
            if normalized and normalized not in by_name:
                by_name[normalized] = key
    matched: list[tuple[str, KpItem]] = []
    not_found: list[str] = []
    for item in items:
        normalized = normalize_name(item.name)
        key = by_name.get(normalized)
        if key is None and normalized:
            key = next(
                (value for name, value in by_name.items() if normalized in name or name in normalized),
                None,
            )
        if key is None:
            not_found.append(item.name)
        else:
            matched.append((key, item))
    return matched, not_found


def read_kp_grid(file_name: str, content: bytes) -> list[list[str]]:
    """First sheet of an .xlsx or a .csv as text cells."""
    if len(content) > MAX_FILE_BYTES:
        raise KpFileError("Файл КП больше 5 МБ")
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if suffix == "csv":
        return _csv_grid(content)
    if suffix in {"xlsx", "xlsm"} or content[:2] == b"PK":
        return _xlsx_grid(content)
    raise KpFileError("Поддерживаются файлы КП .xlsx и .csv")


def _csv_grid(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1251")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_XML_BYTES:
        raise KpFileError("Файл КП слишком большой")
    return archive.read(info)


def _column_index(reference: str) -> int:
    index = 0
    for char in re.match(r"[A-Z]*", reference.upper()).group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def _text_of(node: ElementTree.Element) -> str:
    return "".join(part.text or "" for part in node.iter(f"{{{_NS['m']}}}t"))


def _display_number(raw: str) -> str:
    # Excel stores binary floats («12.300000000000001»); the sheet shows 15 significant digits.
    try:
        return format(float(raw), ".15g")
    except ValueError:
        return raw


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = _read_part(archive, "xl/workbook.xml")
    relations = _read_part(archive, "xl/_rels/workbook.xml.rels")
    if workbook is not None and relations is not None:
        sheet = ElementTree.fromstring(workbook).find("m:sheets/m:sheet", _NS)
        if sheet is not None:
            relation_id = sheet.get(f"{{{_NS['r']}}}id")
            for relation in ElementTree.fromstring(relations).findall("p:Relationship", _NS):
                if relation.get("Id") == relation_id:
                    target = relation.get("Target", "").lstrip("/")
                    return target if target.startswith("xl/") else f"xl/{target}"
    return "xl/worksheets/sheet1.xml"


def _xlsx_grid(content: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise KpFileError("Файл КП не читается как .xlsx") from error
    with archive:
        try:
            shared_part = _read_part(archive, "xl/sharedStrings.xml")
            shared = (
                [_text_of(item) for item in ElementTree.fromstring(shared_part).findall("m:si", _NS)]
                if shared_part is not None
                else []
            )
            sheet_part = _read_part(archive, _first_sheet_path(archive))
            if sheet_part is None:
                raise KpFileError("В файле КП нет листа")
            sheet = ElementTree.fromstring(sheet_part)
        except ElementTree.ParseError as error:
            raise KpFileError("Файл КП не читается как .xlsx") from error
    grid: list[list[str]] = []
    for row in sheet.iterfind("m:sheetData/m:row", _NS):
        row_number = int(row.get("r", len(grid) + 1))
        while len(grid) < row_number - 1:
            grid.append([])
        cells: list[str] = []
        for cell in row.findall("m:c", _NS):
            column = _column_index(cell.get("r", "")) if cell.get("r") else len(cells)
            kind = cell.get("t")
            if kind == "inlineStr":
                value = _text_of(cell)
            else:
                raw = cell.findtext("m:v", default="", namespaces=_NS)
                if kind == "s":
                    value = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
                elif kind in {None, "n"} and raw:
                    value = _display_number(raw)
                else:
                    value = raw
            while len(cells) < column:
                cells.append("")
            cells.append(value)
        grid.append(cells)
    return grid
