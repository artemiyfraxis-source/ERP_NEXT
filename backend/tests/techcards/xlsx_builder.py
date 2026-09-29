"""Tiny .xlsx writer for tests: inline strings and numbers, enough for the calculation reader."""

from io import BytesIO
import zipfile
from xml.sax.saxutils import escape


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet(rows: list[list[object]]) -> str:
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            if value is None or value == "":
                continue
            ref = f"{_column(c)}{r}"
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def build_xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        names = list(sheets)
        archive.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))
            + "</sheets></workbook>"
        ))
        archive.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(names) + 1)
            )
            + "</Relationships>"
        ))
        for i, name in enumerate(names, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(sheets[name]))
    return buffer.getvalue()


def calc_workbook(project_cell: str = "708К.4", customer: str = "ООО Заказчик") -> bytes:
    return build_xlsx({
        "ЗКЗ": [
            ["Название проекта", ""],
            ["Номер", project_cell],
            ["Заказчик", customer],
            [],
            ["№", "Индекс сборки", "Наименование сборки по чертежу", "Кол-во 1", "Вкл. в расчет?"],
            [1, "кб1", "Жгут питания", 10, 1],
            [2, "КБ2", "Жгут сигнальный", 4, 1],
            [3, "КБ3", "Не в расчёте", 2, 0],
        ],
        "СПЯ": [
            ["Сборка", "Артикул КД", "Тип", "Наименование", "Кол-во на комплект", "Ссылка"],
            ["КБ1", "UL1007 22AWG", "Провод", "", "1,5", "https://snab.example/ul1007"],
            ["КБ1", "43025-0400", "Разъём", "Корпус Molex", 2, "Склад"],
            ["КБ2", "43030-0007", "Контакт", "", 8, ""],
            ["КБ3", "LOST", "Разъём", "", 1, ""],
            ["", "", "", "", "", ""],
        ],
    })
