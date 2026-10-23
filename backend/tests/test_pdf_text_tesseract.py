"""PDF text of a Tesseract-made searchable PDF (its ToUnicode map is the identity range <0000> <FFFF> <0000>, which
crosses a byte boundary — Cyrillic used to read as garbage, «!GQB» for «Счёт»)."""

from datetime import date
from pathlib import Path

from app.modules.purchasing import quote_import as qi
from app.modules.warehouse.receipt_scan import parse_upd_text
from app.shared.pdf_text import _cmap, _Budget, pdf_text

FIXTURES = Path(__file__).parent / "fixtures" / "ocr"


def test_identity_range_map_counts_the_whole_code_up():
    stream = (b"begincmap\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
              b"1 beginbfrange\n<0000> <FFFF> <0000>\nendbfrange\nendcmap")
    width, table = _cmap(stream, _Budget())
    assert width == 2 and table[0x0421] == "С" and table[0x0451] == "ё" and table[0x0031] == "1"


def test_upd_pdf_from_tesseract_reads_through_the_upd_parser():
    upd = parse_upd_text(pdf_text((FIXTURES / "upd.pdf").read_bytes()))
    assert (upd.upd_number, upd.upd_date) == ("4521", date(2026, 10, 12))
    assert upd.inn == "7701234567" and upd.invoice_number == "45" and upd.invoice_date == date(2026, 10, 1)
    assert "Ромашка" in upd.supplier


def test_quote_pdf_from_tesseract_reads_through_the_quote_importer():
    text = qi.text_of_pdf((FIXTURES / "quote.pdf").read_bytes())
    lines, _ = qi.read_text_lines(text)
    first = next(line for line in lines if "SHR-02V-S-B" in line.source_name)
    assert first.qty == 2 and first.lead_days == 5
    header = qi.read_header(text, date(2026, 10, 23))
    assert header["number"] == "77/26" and header["inn"] == "7812345678" and header["valid_until"] == "2026-11-30"
