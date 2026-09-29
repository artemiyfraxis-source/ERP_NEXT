"""Reading a calculation into a kitset — SUPPLYMAP `tcReadCalcKitset_` (TcKitset.gs).

SUPPLYMAP opens the Google calculation by link. ERP does not reach Google: the same workbook comes in as an
.xlsx file (Google «Файл → Скачать → Microsoft Excel»), read here with the standard library only. The rules are
the portal's: ЗКЗ!B2 = номер проекта, B3 = заказчик, rows under the ЗКЗ header = сборки (индекс, наименование,
тираж, «Вкл. в расчет?»), СПЯ = состав по колонке «Сборка».
"""

from __future__ import annotations

from io import BytesIO
import re
from xml.etree import ElementTree
import zipfile

from .kitsets import (
    SCRAP_DEFAULT,
    CalcKitset,
    KitAssembly,
    KitComponent,
    KitsetValidationError,
    kit_number,
    project_number_valid,
)


PROJECT_SHEET = "ЗКЗ"
SPEC_SHEET = "СПЯ"
SPA_ARTICLE = ("Обозначение", "Артикул КД")
SPA_NAME = ("Наименование", "Наименование ЗКЗ")
_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MAX_CALC_BYTES = 20 * 1024 * 1024


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    index = 0
    for char in letters[0] if letters else "A":
        index = index * 26 + ord(char) - 64
    return index - 1


def _row_index(reference: str) -> int:
    digits = re.search(r"\d+", reference)
    return int(digits[0]) - 1 if digits else 0


def _display(value: str) -> str:
    """A number cell as the sheet shows it: «1.0» → «1», «1.5600000000000001» → «1.56»."""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return f"{number:.10f}".rstrip("0").rstrip(".")


def read_xlsx(content: bytes) -> dict[str, list[list[str]]]:
    """{sheet name: grid of display strings}. Only what the calculation rules read: values, not formulas."""
    if len(content) > MAX_CALC_BYTES:
        raise KitsetValidationError("Файл расчёта больше 20 МБ")
    try:
        archive = zipfile.ZipFile(BytesIO(content))
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relations = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise KitsetValidationError("Не похоже на файл расчёта .xlsx") from error
    targets = {
        item.get("Id"): item.get("Target", "") for item in relations.findall("rel:Relationship", _NS)
    }
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in strings.findall("m:si", _NS):
            shared.append("".join(text.text or "" for text in item.iter(f"{{{_NS['m']}}}t")))
    sheets: dict[str, list[list[str]]] = {}
    for sheet in workbook.findall("m:sheets/m:sheet", _NS):
        name = sheet.get("name", "")
        target = targets.get(sheet.get(f"{{{_NS['r']}}}id"), "")
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if path not in archive.namelist():
            continue
        grid: dict[int, dict[int, str]] = {}
        root = ElementTree.fromstring(archive.read(path))
        for cell in root.iter(f"{{{_NS['m']}}}c"):
            reference = cell.get("r", "")
            kind = cell.get("t", "")
            value_node = cell.find("m:v", _NS)
            raw = value_node.text if value_node is not None and value_node.text is not None else ""
            if kind == "s":
                value = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
            elif kind == "inlineStr":
                value = "".join(text.text or "" for text in cell.iter(f"{{{_NS['m']}}}t"))
            elif kind == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            elif kind in ("str", "e"):
                value = raw
            else:
                value = _display(raw) if raw else ""
            grid.setdefault(_row_index(reference), {})[_column_index(reference)] = value
        if not grid:
            sheets[name] = []
            continue
        width = max(max(row) for row in grid.values() if row) + 1
        sheets[name] = [
            [grid.get(row, {}).get(column, "") for column in range(width)]
            for row in range(max(grid) + 1)
        ]
    return sheets


def _find(header: list[str], label: str) -> int:
    wanted = label.lower().strip()
    return next((index for index, value in enumerate(header) if str(value or "").lower().strip() == wanted), -1)


def _find_any(header: list[str], labels: tuple[str, ...]) -> int:
    for label in labels:
        index = _find(header, label)
        if index >= 0:
            return index
    return -1


def _cell(grid: list[list[str]], row: int, column: int) -> str:
    if row < 0 or column < 0 or row >= len(grid) or column >= len(grid[row]):
        return ""
    return str(grid[row][column] or "")


def calc_project_number(cell: object, title: object) -> str:
    """`tcCalcCellProjectNo_`: the ЗКЗ cell often holds a short «720К.», the file a full «720К.1.2 Расчет».
    The file name wins only when it REFINES the cell: valid, starts with it and is longer."""
    value = re.sub(r"\s+", "", str(cell or "").strip())
    head = (str(title or "").strip().split() or [""])[0]
    base = value.rstrip(".") if value.endswith(".") else value
    if base and project_number_valid(head) and head.startswith(base) and len(head.rstrip(".")) > len(base):
        return head
    return value


def parse_calc(content: bytes, file_name: str) -> CalcKitset:
    """Расчёт файлом .xlsx (запасной путь без Google)."""
    title = re.sub(r"\.xlsx$", "", str(file_name or "").strip(), flags=re.I)
    return parse_calc_sheets(read_xlsx(content), title, str(file_name or "").strip())


def parse_calc_sheets(sheets: dict[str, list[list[str]]], title: str, source: str) -> CalcKitset:
    """Весь расчёт одним проходом: проект, заказчик, сборки со своим составом. `source` — ссылка или имя файла."""
    zkz = sheets.get(PROJECT_SHEET)
    if zkz is None:
        raise KitsetValidationError(f"В расчёте нет листа «{PROJECT_SHEET}»")
    project = calc_project_number(_cell(zkz, 1, 1), title)
    if not project:
        raise KitsetValidationError(f"В расчёте пуст номер проекта ({PROJECT_SHEET}!B2)")
    customer = _cell(zkz, 2, 1).strip()

    header = next(
        (row for row, values in enumerate(zkz)
         if _find(values, "Индекс сборки") >= 0 and _find(values, "Наименование сборки по чертежу") >= 0),
        -1,
    )
    if header < 0:
        raise KitsetValidationError(f"В листе «{PROJECT_SHEET}» не найдена шапка со «Индекс сборки»")
    index_col = _find(zkz[header], "Индекс сборки")
    name_col = _find(zkz[header], "Наименование сборки по чертежу")
    run_col = _find(zkz[header], "Кол-во 1")
    on_col = _find(zkz[header], "Вкл. в расчет?")

    assemblies: list[dict] = []
    by_index: dict[str, dict] = {}
    for row in range(header + 1, len(zkz)):
        index = _cell(zkz, row, index_col).strip().upper()
        if not index:
            continue
        # «Вкл. в расчет?» = 0 → сборка в проект не попала, состав по ней не нужен.
        if on_col >= 0 and kit_number(_cell(zkz, row, on_col)) == 0:
            continue
        assembly = {
            "index": index, "name": _cell(zkz, row, name_col).strip(),
            "run": kit_number(_cell(zkz, row, run_col)) if run_col >= 0 else kit_number(0),
            "components": [],
        }
        assemblies.append(assembly)
        by_index[index] = assembly
    if not assemblies:
        raise KitsetValidationError("В расчёте нет сборок с «Вкл. в расчет?» = 1")

    spa = sheets.get(SPEC_SHEET) or []
    spa_header = next(
        (row for row, values in enumerate(spa)
         if _find_any(values, SPA_ARTICLE) >= 0 and _find(values, "Кол-во на комплект") >= 0),
        -1,
    )
    if spa_header >= 0:
        head = spa[spa_header]
        assembly_col = _find(head, "Сборка")
        article_col = _find_any(head, SPA_ARTICLE)
        quantity_col = _find(head, "Кол-во на комплект")
        type_col = _find(head, "Тип")
        name_col = _find_any(head, SPA_NAME)
        link_col = _find(head, "Ссылка")
        for row in range(spa_header + 1, len(spa)):
            owner = by_index.get(_cell(spa, row, assembly_col).strip().upper() if assembly_col >= 0 else "")
            if owner is None:
                continue
            article = _cell(spa, row, article_col).strip()
            if not article:
                continue
            owner["components"].append(KitComponent(
                article=article,
                type=_cell(spa, row, type_col).strip(),
                name=_cell(spa, row, name_col).strip(),
                link=_cell(spa, row, link_col).strip(),
                per_unit=kit_number(_cell(spa, row, quantity_col)) or kit_number(1),
                scrap_pct=SCRAP_DEFAULT,
            ))
    return CalcKitset(
        project=project,
        customer=customer,
        calc_source=source,
        assemblies=tuple(
            KitAssembly(index=item["index"], name=item["name"], run=item["run"], components=tuple(item["components"]))
            for item in assemblies
        ),
    )
