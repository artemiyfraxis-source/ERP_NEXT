"""A small .xlsx writer and reader on the standard library (zipfile + ElementTree), for «Скачать для правки» /
«Загрузить исправленную» of «База данных» (roundtrip.py). No new dependency: the shop needs plain text tables, not
formulas or charts.

Writer: every cell is an inline string under the text format «@», so Excel keeps what the user types as text (an
article «39000038» stays an article, «01.10» stays text). Reader: the first visible sheet's cells as text, numbers as
`float`, booleans as `bool`; shared strings, inline strings and rich-text runs are all read, so a file saved again by
Excel, LibreOffice or Google Sheets comes back."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree
from xml.sax.saxutils import escape

MAX_FILE_BYTES = 6 * 1_048_576
MAX_UNPACKED_BYTES = 80 * 1_048_576  # a zip bomb stops here, long before memory does
MAX_ROWS = 20_000
MAX_COLUMNS = 300

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS = {"m": _MAIN}
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class XlsxError(ValueError):
    """The file is not an .xlsx table this reader understands; the message is for the user."""


@dataclass
class Sheet:
    name: str
    rows: list[list[str]]
    hidden: bool = False
    hidden_columns: set[int] = field(default_factory=set)  # 0-based
    grey_columns: set[int] = field(default_factory=set)  # 0-based: service columns (ID) in grey
    widths: list[float] | None = None


# ── Writer ────────────────────────────────────────────────────────────────────────────────────────────────────────


def column_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def sheet_name(text: str) -> str:
    name = _BAD_SHEET_CHARS.sub(" ", str(text or "")).strip().strip("'")[:31].strip()
    return name or "Лист1"


def _text(value: str) -> str:
    return escape(_CONTROL.sub("", str(value)))


def _widths(rows: list[list[str]]) -> list[float]:
    columns = max((len(row) for row in rows), default=0)
    widths = []
    for index in range(columns):
        longest = max((len(row[index]) for row in rows[:500] if index < len(row) and row[index]), default=0)
        widths.append(float(min(max(longest + 2, 9), 50)))
    return widths


def _sheet_xml(sheet: Sheet) -> str:
    widths = sheet.widths or _widths(sheet.rows)
    cols = "".join(
        f'<col min="{index + 1}" max="{index + 1}" width="{width:.1f}" customWidth="1"'
        + (' hidden="1"' if index in sheet.hidden_columns else "") + "/>"
        for index, width in enumerate(widths)
    )
    body = []
    for number, row in enumerate(sheet.rows, start=1):
        cells = []
        for index, value in enumerate(row):
            style = 1 if number == 1 else 2 if index in sheet.grey_columns else 0
            ref = f"{column_letter(index)}{number}"
            if value == "":
                cells.append(f'<c r="{ref}" s="{style}"/>')
                continue
            cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>')
        body.append(f'<row r="{number}">{"".join(cells)}</row>')
    last = column_letter(max(len(widths), 1) - 1)
    frozen = (
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
        'state="frozen"/></sheetView></sheetViews>'
    ) if not sheet.hidden else ""
    autofilter = f'<autoFilter ref="A1:{last}{max(len(sheet.rows), 1)}"/>' if not sheet.hidden and sheet.rows else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{_MAIN}" xmlns:r="{_REL}">'
        f'{frozen}<sheetFormatPr defaultRowHeight="15"/>{f"<cols>{cols}</cols>" if cols else ""}'
        f'<sheetData>{"".join(body)}</sheetData>{autofilter}</worksheet>'
    )


_STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{_MAIN}">'
    '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><sz val="11"/><color rgb="FF808080"/><name val="Calibri"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE8F6F3"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="49" fontId="2" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
)


def write_xlsx(sheets: list[Sheet]) -> bytes:
    """Sheets in order (the first one opens); a `hidden` sheet is kept out of sight but saved with the file."""
    if not sheets:
        raise ValueError("No sheets to write")
    buffer = io.BytesIO()
    names = [sheet_name(sheet.name) for sheet in sheets]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for index in range(1, len(sheets) + 1)
            ) + "</Types>"
        ))
        archive.writestr("_rels/.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG_REL}">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ))
        archive.writestr("xl/workbook.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
            '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
            + "".join(
                f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"'
                + (' state="hidden"' if sheet.hidden else "") + "/>"
                for index, (name, sheet) in enumerate(zip(names, sheets), start=1)
            ) + "</sheets></workbook>"
        ))
        archive.writestr("xl/_rels/workbook.xml.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{_PKG_REL}">'
            + "".join(
                f'<Relationship Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(sheets) + 1)
            )
            + f'<Relationship Id="rId{len(sheets) + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ))
        archive.writestr("xl/styles.xml", _STYLES)
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheet))
    return buffer.getvalue()


# ── Reader ────────────────────────────────────────────────────────────────────────────────────────────────────────

Cell = str | float | bool | None


@dataclass
class ReadSheet:
    name: str
    hidden: bool
    rows: list[list[Cell]]  # rows[0] is the sheet's row 1; a missing row is an empty list


_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _all_text(node: ElementTree.Element | None) -> str:
    """`<si>` / `<is>`: a plain `<t>` or rich-text runs `<r><t>`; phonetic `<rPh>` is skipped."""
    if node is None:
        return ""
    parts = []
    for child in node:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            parts.extend(item.text or "" for item in child.findall("m:t", _NS))
    return "".join(parts)


def _read_xml(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        data = archive.read(name)
    except KeyError as error:
        raise XlsxError("Файл не похож на таблицу Excel (.xlsx)") from error
    if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data.upper():
        raise XlsxError("Файл таблицы повреждён")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise XlsxError("Файл таблицы повреждён") from error


def _target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = [part for part in f"{base}/{target}".split("/") if part not in ("", ".")]
    resolved: list[str] = []
    for part in parts:
        if part == "..":
            if resolved:
                resolved.pop()
        else:
            resolved.append(part)
    return "/".join(resolved)


def _sheet_rows(root: ElementTree.Element, shared: list[str]) -> list[list[Cell]]:
    rows: list[list[Cell]] = []
    data = root.find("m:sheetData", _NS)
    if data is None:
        return rows
    next_row = 1
    for row in data.findall("m:row", _NS):
        number = int(row.get("r") or next_row)
        next_row = number + 1
        if number > MAX_ROWS + 1:
            raise XlsxError(f"Строк в файле больше {MAX_ROWS}")
        values: list[Cell] = []
        next_column = 0
        for cell in row.findall("m:c", _NS):
            match = _REF.match(cell.get("r") or "")
            column = _column_index(match.group(1)) if match else next_column
            next_column = column + 1
            if column >= MAX_COLUMNS:
                continue
            kind = cell.get("t") or "n"
            raw = cell.find("m:v", _NS)
            text = raw.text if raw is not None else None
            value: Cell
            if kind == "s":
                try:
                    value = shared[int(text or "")]
                except (ValueError, IndexError):
                    value = ""
            elif kind == "inlineStr":
                value = _all_text(cell.find("m:is", _NS))
            elif kind in ("str", "e"):
                value = text or ""
            elif kind == "b":
                value = (text or "").strip() == "1"
            else:
                try:
                    value = float(text) if text not in (None, "") else None
                except ValueError:
                    value = text
            while len(values) < column:
                values.append(None)
            values.append(value)
        while len(rows) < number - 1:
            rows.append([])
        rows.append(values)
    return rows


def read_xlsx(content: bytes) -> list[ReadSheet]:
    """Every sheet of the workbook in its order."""
    if len(content) > MAX_FILE_BYTES:
        raise XlsxError(f"Файл больше {MAX_FILE_BYTES // 1_048_576} МБ")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise XlsxError("Файл не похож на таблицу Excel (.xlsx). Сохраните его как «Книга Excel (.xlsx)»") from error
    with archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_UNPACKED_BYTES:
            raise XlsxError("Файл таблицы слишком большой")
        workbook = _read_xml(archive, "xl/workbook.xml")
        rels = _read_xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {item.get("Id"): _target("xl", item.get("Target") or "") for item in rels}
        shared: list[str] = []
        shared_path = next((path for path in targets.values() if path.endswith("sharedStrings.xml")), "")
        if shared_path and shared_path in archive.namelist():
            shared = [_all_text(item) for item in _read_xml(archive, shared_path).findall("m:si", _NS)]
        sheets = []
        node = workbook.find("m:sheets", _NS)
        for sheet in (node if node is not None else []):
            path = targets.get(sheet.get(f"{{{_REL}}}id"))
            if not path or path not in archive.namelist():
                continue
            sheets.append(ReadSheet(
                name=sheet.get("name") or "", hidden=(sheet.get("state") or "visible") != "visible",
                rows=_sheet_rows(_read_xml(archive, path), shared),
            ))
    if not sheets:
        raise XlsxError("В файле нет листов")
    return sheets
