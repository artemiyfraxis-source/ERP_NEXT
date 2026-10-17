import io
import zipfile

import pytest

from app.modules.procurement.kp import (
    KpFileError,
    KpItem,
    extract_kp_items,
    kp_header_columns,
    match_kp_items,
    normalize_name,
    read_kp_grid,
)


def test_header_takes_vat_before_price_so_gross_price_is_not_the_price():
    columns = kp_header_columns(["№", " Наименование  товара ", "Кол-во", "Цена с НДС", "Цена", "НДС"])

    assert (columns.name, columns.qty, columns.vat, columns.price) == (1, 2, 3, 4)


@pytest.mark.parametrize("header", ["Кол-во", "КолВо", "Количество, шт"])
def test_quantity_header_spellings(header):
    assert kp_header_columns(["Наименование", header]).qty == 1


def test_items_start_after_header_found_in_first_eight_rows_and_skip_blank_names():
    grid = [["Коммерческое предложение"], [], ["Наименование", "Кол-во", "Цена", "НДС"],
            [" Разъём ", " 2 ", "10", "22"], ["", "5", "1", "22"], ["Кабель"]]

    assert extract_kp_items(grid) == [KpItem("Разъём", "2", "10", "22"), KpItem("Кабель", "", "", "")]


def test_header_below_row_eight_is_not_found():
    grid = [[""]] * 8 + [["Наименование", "Цена"]]

    with pytest.raises(KpFileError, match="Не нашёл шапку"):
        extract_kp_items(grid)


def test_name_normalization_matches_kp_form():
    assert normalize_name("  Разъём   SMA-F/50 Ом ") == "разъем sma f 50 ом"
    assert normalize_name(None) == ""


def test_matching_exact_then_substring_and_first_line_wins():
    items = [KpItem("Разъем SMA"), KpItem("Кабель РК-50 5м"), KpItem("Болт"), KpItem("SMA")]
    candidates = [("a", ["Разъём SMA", None]), ("b", ["кабель рк 50", "Разъем SMA"])]

    matched, not_found = match_kp_items(items, candidates)

    assert [(key, item.name) for key, item in matched] == [("a", "Разъем SMA"), ("b", "Кабель РК-50 5м"), ("a", "SMA")]
    assert not_found == ["Болт"]


def xlsx(sheet_rows: str, shared: list[str]) -> bytes:
    buffer = io.BytesIO()
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="КП" sheetId="1" r:id="rId7"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7" Target="worksheets/offer.xml"/></Relationships>',
        )
        items = "".join(f"<si><t>{text}</t></si>" for text in shared)
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{main}">{items}</sst>')
        archive.writestr("xl/worksheets/offer.xml", f'<worksheet xmlns="{main}"><sheetData>{sheet_rows}</sheetData></worksheet>')
    return buffer.getvalue()


def test_xlsx_first_sheet_with_shared_inline_numbers_and_gaps():
    rows = (
        '<row r="2"><c r="B2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="E2" t="s"><v>2</v></c></row>'
        '<row r="3"><c r="B3" t="inlineStr"><is><t>Разъём</t></is></c><c r="C3"><v>2</v></c>'
        '<c r="E3"><v>12.300000000000001</v></c></row>'
    )

    grid = read_kp_grid("offer.XLSX", xlsx(rows, ["Наименование", "Кол-во", "Цена"]))

    assert grid == [[], ["", "Наименование", "Кол-во", "", "Цена"], ["", "Разъём", "2", "", "12.3"]]
    assert extract_kp_items(grid) == [KpItem("Разъём", "2", "12.3", "")]


def test_csv_in_utf8_with_bom_and_comma_delimiter():
    content = "﻿Наименование,Кол-во,Цена\nРазъём,2,10\n".encode("utf-8")

    assert read_kp_grid("kp.csv", content)[1] == ["Разъём", "2", "10"]


@pytest.mark.parametrize(
    "name,content,message",
    [
        ("kp.xls", b"\xd0\xcf\x11\xe0", "Поддерживаются файлы КП .xlsx и .csv"),
        ("kp.xlsx", b"not a zip", "не читается как .xlsx"),
        ("kp.csv", b"x" * (5 * 1024 * 1024 + 1), "больше 5 МБ"),
    ],
    # A megabyte of "x" as a param id overflows the Windows env var limit (PYTEST_CURRENT_TEST, 32767).
    ids=["xls", "broken-xlsx", "too-large"],
)
def test_unreadable_files_are_refused(name, content, message):
    with pytest.raises(KpFileError, match=message):
        read_kp_grid(name, content)
