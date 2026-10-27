"""Слова PDF с координатами (как pdf.js в Roller_Buyer) и полный разбор счёта: шапка, деньги, позиции, замечания."""

import pytest

from app.shared.invoice_reading import MAX_BYTES, NO_LINES, NO_SELLER, NO_TOTAL, read_invoice
from app.shared.pdf_layout import pdf_layout
from app.shared.pdf_text import PdfLimitExceeded
from tests.pdf_samples import _pdf, _stream, flate_bomb_pdf, identity_pdf

_CODES = [code for code in range(32, 256) if code != 0x98]
_TO_UNICODE = (
    b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n1 begincodespacerange\n<00> <FF>\n"
    b"endcodespacerange\n%d beginbfchar\n" % len(_CODES)
    + b"".join(b"<%02X> <%s>\n" % (code, bytes([code]).decode("cp1251").encode("utf-16-be").hex().upper().encode())
               for code in _CODES)
    + b"endbfchar\nendcmap\nend\nend\n"
)


def cells_pdf(cells: list[tuple[float, float, str]], size: float = 10, wrap: bool = True) -> bytes:
    """A single-byte cp1251 font with a ToUnicode map, every glyph 500/1000 wide (a space 250): a glyph is 5 units at
    size 10. Each cell is its own `q BT … Tj ET Q`, the way 1С writes table cells (`wrap`), or a bare BT … ET."""
    content = b""
    for x, y, text in cells:
        escaped = text.encode("cp1251").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        cell = b"BT /F1 %g Tf %g %g Td (%s) Tj ET\n" % (size, x, y, escaped)
        content += b"q " + cell + b"Q\n" if wrap else cell
    widths = b" ".join(b"250" if code == 32 else b"500" for code in range(32, 256))
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> "
        b"/Contents 4 0 R >>",
        _stream(content),
        b"<< /Type /Font /Subtype /TrueType /BaseFont /Arial /FirstChar 32 /LastChar 255 /Widths [" + widths
        + b"] /Encoding /WinAnsiEncoding /ToUnicode 6 0 R >>",
        _stream(_TO_UNICODE),
    ])


def words(content: bytes) -> list[tuple[str, float, float, float]]:
    return [(w.s, w.x, w.y, w.w) for w in pdf_layout(content).pages[0]]


def test_glyphs_glue_into_items_as_pdf_js_does():
    content = cells_pdf([(40, 700, "Итого"), (68, 700, "100"), (100, 700, "шт"), (40, 680, "Счёт № 5")], wrap=False)
    # «Итого» ends at 65: a gap of 3 (≤ 0.6 of the size) joins with a space; «шт» 17 further is an item of its own;
    # another baseline is another item. Widths come from /Widths by the character code (a Cyrillic code ≥ 128 too).
    assert words(content) == [
        ("Итого 100", 40, 700, 43), ("шт", 100, 700, 10), ("Счёт № 5", 40, 680, 35),
    ]
    assert pdf_layout(content).text == "Итого 100 шт Счёт № 5\n"


def test_a_restore_that_drops_the_font_ends_the_item():
    # 1С wraps every cell in q … Q without a font outside: pdf.js starts a new item at each Q, however close the cells.
    assert [w[0] for w in words(cells_pdf([(40, 700, "500"), (57, 700, "м")]))] == ["500", "м"]
    assert [w[0] for w in words(cells_pdf([(40, 700, "500"), (57, 700, "м")], wrap=False))] == ["500 м"]


def test_budget_scans_and_broken_files():
    with pytest.raises(PdfLimitExceeded):
        read_invoice(flate_bomb_pdf())
    with pytest.raises(PdfLimitExceeded):
        read_invoice(b"%PDF-1.4\n" + b" " * MAX_BYTES)
    for content in (b"\xff\xd8\xff\xe0 jpeg", b"%PDF-1.4 broken", b"", b"%PDF-1.7 /Encrypt"):
        layout = pdf_layout(content)
        assert (layout.text, layout.pages) == ("", [])
        assert read_invoice(content).scan
    # a font pypdf can't read (Type0 without descendant fonts): the small reader's text for the header, no table
    reading = read_invoice(identity_pdf(["ООО Поставка ИНН 7701234567", "Счет на оплату № 58 от 14 сентября 2026 г."]))
    assert (reading.header.number, reading.header.seller_inn, reading.lines) == ("58", "7701234567", ())


INVOICE_CELLS = [
    (30, 800, 'ООО "Ромашка"'), (30, 790, "Получатель"),
    (30, 760, "Счет на оплату № 12 от 20 октября 2026 г."),
    (30, 740, 'Поставщик (Исполнитель): ООО "Ромашка", ИНН 7701234567, КПП 770101001, Москва'),
    (30, 728, 'Покупатель (Заказчик): ООО "ЗЭП "ФРАКСИС", ИНН 5040191181'),
    (30, 700, "№"), (60, 700, "Товары (работы, услуги)"), (300, 700, "Кол-во"), (340, 700, "Ед."),
    (380, 700, "Цена"), (450, 700, "Сумма"),
    (32, 685, "1"), (60, 685, "Разъём SMA-J"), (300, 685, "10"), (340, 685, "шт"), (380, 685, "50,00"),
    (450, 685, "500,00"),
    (32, 672, "2"), (60, 672, "Кабель RG-58"), (300, 672, "12"), (340, 672, "м"), (380, 672, "60,00"),
    (450, 672, "720,00"),
    (380, 650, "Итого:"), (450, 650, "1 220,00"),
    (300, 638, "В том числе НДС 22%:"), (450, 638, "220,00"),
    (30, 620, "Одна тысяча двести двадцать рублей 00 копеек"),
]


def test_a_whole_invoice_header_money_and_positions():
    reading = read_invoice(cells_pdf(INVOICE_CELLS))

    assert reading.as_json() == {
        "number": "12", "date": "2026-10-20", "seller_name": 'ООО "Ромашка"', "seller_inn": "7701234567",
        "total": 122000, "vat": 22000, "vat_free": False, "scan": False, "issues": [],
        "lines": [
            {"seq": 1, "name": "Разъём SMA-J", "article": "", "qty": 10, "unit": "шт", "price": 5000, "sum": 50000},
            {"seq": 2, "name": "Кабель RG-58", "article": "", "qty": 12, "unit": "м", "price": 6000, "sum": 72000},
        ],
    }
    assert "Счет на оплату № 12" in reading.text


def test_remarks_for_a_scan_and_for_a_text_without_table():
    assert read_invoice(b"\xff\xd8\xff\xe0 jpeg").issues == (NO_SELLER, NO_TOTAL)
    text_only = read_invoice(cells_pdf(INVOICE_CELLS[:5] + INVOICE_CELLS[-1:]))
    assert (text_only.header.seller_inn, text_only.lines, text_only.issues) == ("7701234567", (), (NO_LINES,))
