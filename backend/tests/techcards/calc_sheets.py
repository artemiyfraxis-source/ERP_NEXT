"""A calculation as Google Sheets returns it (display strings) — ЗКЗ and СПЯ of `tcReadCalcKitset_`."""


def grid(rows: list[list[object]]) -> list[list[str]]:
    width = max((len(row) for row in rows), default=0)
    return [[str(cell) for cell in row] + [""] * (width - len(row)) for row in rows]


def calc_sheets(project_cell: str = "708К.4", customer: str = "ООО Заказчик") -> dict[str, list[list[str]]]:
    return {
        "ЗКЗ": grid([
            ["Название проекта", ""],
            ["Номер", project_cell],
            ["Заказчик", customer],
            [],
            ["№", "Индекс сборки", "Наименование сборки по чертежу", "Кол-во 1", "Вкл. в расчет?"],
            [1, "кб1", "Жгут питания", 10, 1],
            [2, "КБ2", "Жгут сигнальный", 4, 1],
            [3, "КБ3", "Не в расчёте", 2, 0],
        ]),
        "СПЯ": grid([
            ["Сборка", "Артикул КД", "Тип", "Наименование", "Кол-во на комплект", "Ссылка"],
            ["КБ1", "UL1007 22AWG", "Провод", "", "1,5", "https://snab.example/ul1007"],
            ["КБ1", "43025-0400", "Разъём", "Корпус Molex", 2, "Склад"],
            ["КБ2", "43030-0007", "Контакт", "", 8, ""],
            ["КБ3", "LOST", "Разъём", "", 1, ""],
            ["", "", "", "", "", ""],
        ]),
    }


class FakeSheets:
    """`CalcSheetsReader` fake: returns the sheets asked for, records the calls."""

    def __init__(self, sheets=None, title: str = "708К.4 Расчет", error: Exception | None = None):
        self.sheets = calc_sheets() if sheets is None else sheets
        self.title, self.error, self.calls = title, error, []

    def read_sheets(self, spreadsheet_id, names):
        self.calls.append((spreadsheet_id, names))
        if self.error:
            raise self.error
        return self.title, {name: rows for name, rows in self.sheets.items() if name in names}
