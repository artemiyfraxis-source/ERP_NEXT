"""Цены из КП / счёта поставщика (owner, 05.10): таблица товаров из текста PDF, .xlsx, .csv, .docx; НДС; итоги."""

import io
import zipfile
from decimal import Decimal

from app.modules.procurement.document_items import (
    NO_TABLE,
    UNREADABLE,
    XLS,
    SupplierDocument,
    document_currency,
    parse_document_grid,
    parse_document_text,
    read_supplier_document,
    seller_mismatch,
    unit_key,
)
from tests.pdf_samples import fpdf_pages_pdf, identity_pdf

ELITAN_INVOICE = """ООО "Элитан Трейд"
ИНН 7700000000 КПП 770001001
Счет на оплату № ЭТ-10234 от 2 октября 2026 г.
Покупатель: ООО "Фраксис"
№ Артикул Товары (работы, услуги) Кол-во Ед. Цена Сумма
1 SHR-02V-S-B Корпус разъёма SHR-02V-S-B (JST) 200 шт 3,66 732,00
2 510471000 Разъём Molex 51047-1000 Picoblade 10 pin 50 шт 12,20 610,00
3 SSHL-002T-P0.2 Контакт обжимной SSHL-002T-P0.2 1 000 шт 1,22 1 220,00
Итого: 2 562,00
В том числе НДС (22%): 462,00
Всего к оплате: 2 562,00
Всего наименований 3, на сумму 2 562,00 руб.
Две тысячи пятьсот шестьдесят два рубля 00 копеек"""

KP_NET = """Коммерческое предложение № 145 от 01.10.2026
ООО "Кабель-Профи"
Наименование Кол-во Ед. изм. Цена без НДС, руб. Сумма без НДС, руб.
1 Провод ПуГВ 1х0,75 красный 300 м 18,50 5 550,00
2 Провод ПуГВ 1х0,75 черный 300 м 18,50 5 550,00
3 Трубка термоусадочная ТУТ 6/3 черная 50 м 24,00 1 200,00
Итого без НДС: 12 300,00
НДС 22% сверху: 2 706,00
Итого с НДС: 15 006,00
Срок поставки 5 рабочих дней"""

MULTILINE = """Счет № 77 от 03.10.2026
№ Наименование товара Ед. изм. Кол-во Цена с
НДС Сумма с НДС
1 Кабель монтажный МКЭШ 2х0,5 в бухте
по 200 м, ГОСТ 10348-80
м 400 45,10 18 040,00
2 Наконечник штыревой втулочный изолированный
НШВИ 0,5-8 (100 шт/уп) упак 4 210,00 840,00
3 Хомут нейлоновый 2,5х100 мм белый (упаковка 100 шт) упак 2 95,00 190,00
Итого: 19 070,00
в т.ч. НДС 22%: 3 438,85"""


def rows(document):
    return [
        (item.article, item.name, item.quantity, item.unit, item.price, item.price_with_vat, item.sum, item.vat_rate)
        for item in document.items
    ]


def D(value):
    return Decimal(value)


def test_invoice_with_part_numbers_prices_include_vat_by_the_totals():
    document = parse_document_text(ELITAN_INVOICE, "invoice")

    assert (document.number, document.date, document.currency) == ("ЭТ-10234", "02.10.2026", "RUB")
    assert (document.vat_mode, document.vat_rate, document.hint) == ("included", D("22"), "")
    assert rows(document) == [
        ("SHR-02V-S-B", "Корпус разъёма SHR-02V-S-B (JST)", D("200"), "шт", D("3"), D("3.66"), D("732.00"), D("22")),
        ("510471000", "Разъём Molex 51047-1000 Picoblade 10 pin", D("50"), "шт", D("10"), D("12.2"), D("610.00"), D("22")),
        ("SSHL-002T-P0.2", "Контакт обжимной SSHL-002T-P0.2", D("1000"), "шт", D("1"), D("1.22"), D("1220.00"), D("22")),
    ]
    assert (document.totals.sum, document.totals.vat, document.totals.sum_with_vat) == (
        D("2100.00"), D("462.00"), D("2562.00"),
    )
    assert document.items[0].source.startswith("SHR-02V-S-B Корпус")


def test_offer_with_prices_without_vat_and_vat_on_top():
    document = parse_document_text(KP_NET, "kp")

    assert (document.number, document.date, document.vat_mode, document.vat_rate) == (
        "145", "01.10.2026", "on_top", D("22"),
    )
    assert rows(document) == [
        ("", "Провод ПуГВ 1х0,75 красный", D("300"), "м", D("18.5"), D("22.57"), D("6771.00"), D("22")),
        ("", "Провод ПуГВ 1х0,75 черный", D("300"), "м", D("18.5"), D("22.57"), D("6771.00"), D("22")),
        ("", "Трубка термоусадочная ТУТ 6/3 черная", D("50"), "м", D("24"), D("29.28"), D("1464.00"), D("22")),
    ]
    assert (document.totals.sum, document.totals.vat, document.totals.sum_with_vat) == (
        D("12300.00"), D("2706.00"), D("15006.00"),
    )


def test_names_wrapped_over_several_lines_and_unit_before_quantity():
    document = parse_document_text(MULTILINE, "invoice")

    assert document.vat_mode == "included"
    assert [(name, quantity, unit, gross, total) for _, name, quantity, unit, _, gross, total, _ in rows(document)] == [
        ("Кабель монтажный МКЭШ 2х0,5 в бухте по 200 м, ГОСТ 10348-80", D("400"), "м", D("45.1"), D("18040.00")),
        ("Наконечник штыревой втулочный изолированный НШВИ 0,5-8 (100 шт/уп)", D("4"), "упак", D("210"), D("840.00")),
        ("Хомут нейлоновый 2,5х100 мм белый (упаковка 100 шт)", D("2"), "упак", D("95"), D("190.00")),
    ]
    assert document.items[0].price == D("36.97")
    assert (document.totals.vat, document.totals.sum_with_vat) == (D("3438.85"), D("19070.00"))


def test_net_and_gross_columns_side_by_side_are_read_by_arithmetic():
    text = """Счет № 12 от 01.10.2026
№ Товар Кол-во Ед. Цена без НДС Цена с НДС Сумма без НДС Сумма с НДС
1 Реле Finder 40.52.9.024.0000 10 шт 500,00 610,00 5 000,00 6 100,00
Итого: 6 100,00"""
    (item,) = parse_document_text(text).items

    assert (item.quantity, item.price, item.price_with_vat, item.sum, item.vat_rate) == (
        D("10"), D("500"), D("610"), D("6100.00"), D("22"),
    )


def test_upd_like_row_with_rate_and_vat_amount_columns():
    text = """Счет на оплату № 9 от 01.10.2026
№ Наименование Кол-во Ед. Цена Стоимость без налога Ставка Сумма налога Стоимость с налогом
1 Клемма WAGO 221-413 100 шт 25,00 2 500,00 20% 500,00 3 000,00
Всего к оплате 3 000,00"""
    document = parse_document_text(text)
    (item,) = document.items

    assert (item.price, item.price_with_vat, item.sum, item.vat_rate) == (D("25"), D("30"), D("3000.00"), D("20"))
    assert document.vat_rate == D("20")


def test_no_vat_supplier_gross_equals_net():
    text = """Счет № 5 от 01.10.2026
№ Наименование Кол-во Ед. Цена Сумма
1 Втулка латунная М4 20 шт 15,50 310,00
Итого: 310,00
Без налога (НДС)
Всего к оплате: 310,00"""
    document = parse_document_text(text)
    (item,) = document.items

    assert document.vat_mode == "none"
    assert (item.price, item.price_with_vat, item.vat_rate) == (D("15.5"), D("15.5"), D("0"))
    assert (document.totals.sum, document.totals.vat, document.totals.sum_with_vat) == (
        D("310.00"), D("0.00"), D("310.00"),
    )


def test_offer_without_unit_column_and_vat_on_top_by_totals():
    text = """КП № 3 от 30.09.2026
Наименование Количество Цена Сумма
1 Корпус Gainta G1020 5 410,00 2 050,00
Итого: 2 050,00
НДС 22%: 451,00
Всего к оплате: 2 501,00"""
    document = parse_document_text(text, "kp")
    (item,) = document.items

    assert (document.number, document.vat_mode) == ("3", "on_top")
    assert (item.name, item.quantity, item.price, item.price_with_vat, item.sum) == (
        "Корпус Gainta G1020", D("5"), D("410"), D("500.2"), D("2501.00"),
    )


def test_fine_unit_price_keeps_four_places():
    text = """Счет № 1 от 01.10.2026
№ Наименование Кол-во Ед. Цена без НДС Сумма без НДС
1 Резистор 0805 10 кОм 5000 шт 0,4583 2 291,50
НДС 22% сверху"""
    (item,) = parse_document_text(text).items

    assert (item.price, item.price_with_vat) == (D("0.4583"), D("0.5591"))


def test_text_without_items_table_gives_a_hint():
    document = parse_document_text("Акт сверки\nСальдо на 01.10.2026: 0,00")

    assert (document.items, document.hint) == ((), NO_TABLE)


def test_foreign_currency_is_recognised_from_the_header_and_totals():
    text = """Proforma Invoice № 88 от 01.10.2026
№ Наименование Кол-во Ед. Цена, USD Сумма, USD
1 Connector TE 1-1564337-1 100 шт 1,20 120,00
Итого, USD: 120,00"""

    assert parse_document_text(text).currency == "USD"
    assert document_currency("Цена, руб. Итого: 100", "вилка евро 1 шт") == "RUB"
    assert document_currency("Итого: 10", "Цена $12.50") == "USD"


def test_units_written_differently_share_one_key():
    assert unit_key("шт.") == unit_key("штук") == unit_key("pcs") == "шт"
    assert unit_key("упак") == unit_key("уп.") == "уп"
    assert unit_key("м") != unit_key("шт")


# --- files ----------------------------------------------------------------------------------------------------------


def test_pdf_text_layer_is_read():
    document = read_supplier_document("счёт.pdf", identity_pdf(ELITAN_INVOICE.split("\n")))

    assert [item.price_with_vat for item in document.items] == [D("3.66"), D("12.2"), D("1.22")]
    assert document.number == "ЭТ-10234"


def test_scan_old_xls_and_unknown_files_give_hints():
    assert read_supplier_document("scan.pdf", b"%PDF-1.4\n%%EOF").hint == UNREADABLE
    assert read_supplier_document("счёт.xls", b"\xd0\xcf\x11\xe0rest").hint == XLS
    assert "Формат файла не читается" in read_supplier_document("photo.jpg", b"\xff\xd8\xff").hint


def _xlsx(grid):
    def cell(ref, value):
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'

    rows_xml = "".join(
        f'<row r="{r}">' + "".join(cell(f"{chr(65 + c)}{r}", v) for c, v in enumerate(row) if v != "") + "</row>"
        for r, row in enumerate(grid, start=1)
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rows_xml}</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


INVOICE_GRID = [
    ["ООО «Элитан Трейд»"],
    ["Счет на оплату № 501 от 04.10.2026"],
    [""],
    ["№", "Артикул", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"],
    ["", "", "", "", "", "с НДС", "с НДС"],
    [1, "SHR-02V-S-B", "Корпус разъёма", 200, "шт", 3.66, 732],
    [2, "510471000", "Разъём Molex Picoblade", 50, "шт", 12.2, 610],
    ["Итого", "", "", "", "", "", 1342],
    ["В том числе НДС 22%", "", "", "", "", "", 242],
]


def test_xlsx_invoice_with_a_two_row_header():
    document = read_supplier_document("счёт.xlsx", _xlsx(INVOICE_GRID))

    assert (document.number, document.vat_mode) == ("501", "included")
    assert rows(document) == [
        ("SHR-02V-S-B", "Корпус разъёма", D("200"), "шт", D("3"), D("3.66"), D("732.00"), D("22")),
        ("510471000", "Разъём Molex Picoblade", D("50"), "шт", D("10"), D("12.2"), D("610.00"), D("22")),
    ]
    assert (document.totals.vat, document.totals.sum_with_vat) == (D("242"), D("1342"))


def test_csv_offer_with_net_prices_and_a_rate_column():
    content = (
        "Коммерческое предложение № 7\n"
        "Наименование;Кол-во;Ед.;Цена без НДС;Ставка НДС;Сумма без НДС\n"
        "Провод ПуГВ 1х0,75 красный;300;м;18,50;22%;5 550,00\n"
    ).encode("cp1251")
    document = read_supplier_document("кп.csv", content, "kp")
    (item,) = document.items

    assert (document.number, document.vat_mode) == ("7", "on_top")
    assert (item.price, item.price_with_vat, item.sum, item.vat_rate) == (D("18.5"), D("22.57"), D("6771.00"), D("22"))


def test_docx_table_is_read():
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    def row(*cells):
        return "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in cells) + "</w:tr>"

    body = (
        "<w:p><w:r><w:t>Коммерческое предложение № 19 от 02.10.2026</w:t></w:r></w:p>"
        "<w:tbl>" + row("№", "Наименование", "Кол-во", "Ед.", "Цена с НДС", "Сумма с НДС")
        + row("1", "Корпус Gainta G1020", "5", "шт", "500,20", "2 501,00") + "</w:tbl>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    document = read_supplier_document("кп.docx", buffer.getvalue(), "kp")

    assert document.number == "19"
    assert [(item.name, item.quantity, item.price_with_vat, item.price) for item in document.items] == [
        ("Корпус Gainta G1020", D("5"), D("500.2"), D("410")),
    ]


def test_grid_without_header_gives_a_hint():
    document = parse_document_grid([["Акт сверки"], ["Сальдо", "0"]])

    assert (document.items, document.hint) == ((), NO_TABLE)


# --- a real PDF read by word positions (Roller_Buyer's reader, 26.10) ---------------------------------------------

_COLUMNS = (12, 20, 120, 140, 155, 180)  # № | Товары | Кол-во | Ед. | Цена | Сумма (mm)


def _table_row(y: float, *cells: str) -> list[tuple[float, float, str]]:
    return [(x, y, text) for x, text in zip(_COLUMNS, cells) if text]


ELITAN_LAYOUT_PDF = fpdf_pages_pdf([[
    (12, 20, 'ООО "Элитан Трейд"'), (12, 26, "ИНН 1831096455 КПП 183101001"),
    (12, 40, "Счет на оплату № 684803Z от 02 октября 2026 г."),
    (12, 50, 'Поставщик (Исполнитель): ООО "Элитан Трейд", ИНН 1831096455, КПП 183101001, 426028, Ижевск'),
    (12, 56, 'Покупатель (Заказчик): ООО "ЗЭП "ФРАКСИС", ИНН 5040191181'),
    *_table_row(70, "№", "Товары (работы, услуги)", "Кол-во", "Ед.", "Цена", "Сумма"),
    *_table_row(78, "1", "Корпус разъёма SHR-02V-S-B (JST)", "200", "шт", "3,66", "732,00"),
    *_table_row(86, "2", "Разъём Molex 51047-1000 Picoblade 10 pin", "50", "шт", "12,20", "610,00"),
    *_table_row(94, "3", "Контакт обжимной SSHL-002T-P0.2", "1 000", "шт", "1,22", "1 220,00"),
    (150, 104, "Итого:"), (180, 104, "2 562,00"),
    (130, 110, "В том числе НДС (22%):"), (180, 110, "462,00"),
    (12, 120, "Всего наименований 3, на сумму 2 562,00 руб."),
    (12, 126, "Две тысячи пятьсот шестьдесят два рубля 00 копеек"),
]])


def test_pdf_table_is_read_by_word_positions_with_the_seller_and_the_total_in_words():
    document = read_supplier_document("счёт.pdf", ELITAN_LAYOUT_PDF)

    assert document.reader == "pdf_table"
    assert (document.number, document.date, document.seller_name, document.seller_inn) == (
        "684803Z", "02.10.2026", 'ООО "Элитан Трейд"', "1831096455",
    )
    assert [(item.name, item.quantity, item.unit, item.price_with_vat, item.price, item.sum) for item in document.items] == [
        ("Корпус разъёма SHR-02V-S-B (JST)", Decimal(200), "шт", Decimal("3.66"), Decimal(3), Decimal("732.00")),
        ("Разъём Molex 51047-1000 Picoblade 10 pin", Decimal(50), "шт", Decimal("12.2"), Decimal(10), Decimal("610.00")),
        # «1 000» and «1 220,00» are one cell each: the thousands are not split into two numbers.
        ("Контакт обжимной SSHL-002T-P0.2", Decimal(1000), "шт", Decimal("1.22"), Decimal(1), Decimal("1220.00")),
    ]
    assert (document.vat_mode, document.vat_rate) == ("included", Decimal(22))
    assert document.totals.sum_with_vat == Decimal("2562.00") and document.totals.vat == Decimal("462.00")


def test_net_prices_with_vat_columns_right_of_the_sum_and_the_table_running_on_the_next_page():
    columns = (12, 20, 110, 125, 140, 160, 175, 190)  # № | Наименование | Кол-во | Ед. | Цена | Сумма | НДС | Всего

    def row(y, *cells):
        return [(x, y, text) for x, text in zip(columns, cells) if text]

    header = row(60, "№", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма", "НДС", "Всего")
    pdf = fpdf_pages_pdf([
        [
            (12, 30, "СЧЕТ - ДОГОВОР № 57148683 от 29.01.2026"),
            (12, 40, 'Продавец : ЗАО " МПО Электромонтаж ", ИНН 7719079370, КПП 773301001'),
            *header,
            *row(70, "1", "Трубка термоусаживаемая 20/10 белая", "75", "м", "10,20", "765,00", "153,00", "918,00"),
        ],
        [
            *header,
            *row(70, "2", "Доставка товара", "1", "усл", "415,83", "415,83", "83,17", "499,00"),
            (100, 80, "Итого:"), (160, 80, "1 180,83"), (175, 80, "236,17"), (190, 80, "1 417,00"),
            (12, 90, "Всего: Одна тысяча четыреста семнадцать рублей 00 копеек"),
        ],
    ])

    document = read_supplier_document("счёт.pdf", pdf)

    assert (document.reader, document.number, document.seller_name) == ("pdf_table", "57148683", 'ЗАО "МПО Электромонтаж"')
    assert [(item.name, item.price, item.price_with_vat, item.sum, item.vat_rate) for item in document.items] == [
        ("Трубка термоусаживаемая 20/10 белая", Decimal("10.2"), Decimal("12.24"), Decimal("918.00"), Decimal(20)),
        ("Доставка товара", Decimal("415.83"), Decimal("499"), Decimal("499.00"), Decimal(20)),
    ]
    assert document.totals.sum_with_vat == Decimal("1417.00")


def test_seller_against_the_order_supplier():
    elitan = SupplierDocument(seller_name='ООО "Элитан Трейд"', seller_inn="1831096455")
    assert seller_mismatch("Элитан-Трейд", elitan) == ""
    assert seller_mismatch("Общество с ограниченной ответственностью «Элитан Трейд»", elitan) == ""
    assert seller_mismatch("chipdip.ru", elitan) == ""  # a site is not a legal name: nothing to compare
    assert seller_mismatch("https://www.elitan.ru/", elitan) == ""
    assert seller_mismatch("", elitan) == ""
    assert seller_mismatch("ООО Платан", SupplierDocument()) == ""
    assert seller_mismatch("ООО Платан", elitan).startswith('Продавец в счёте — ООО "Элитан Трейд" (ИНН 1831096455)')


def test_pdf_without_a_positions_table_keeps_erp_text_reading():
    pdf = fpdf_pages_pdf([[(12, 20 + 6 * index, line) for index, line in enumerate(KP_NET.split("\n"))]])

    document = read_supplier_document("кп.pdf", pdf, "kp")

    # Each line is one piece of text: no columns to read by position — ERP's reading of the rows by arithmetic.
    assert document.reader == "text"
    assert [item.name for item in document.items] == [
        "Провод ПуГВ 1х0,75 красный", "Провод ПуГВ 1х0,75 черный", "Трубка термоусадочная ТУТ 6/3 черная",
    ]
    assert (document.number, document.seller_name) == ("145", 'ООО "Кабель-Профи"')
