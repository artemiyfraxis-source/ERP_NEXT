"""PDF text layer under a budget (audit F05): a small file that unpacks huge is refused fast, normal files read as before."""

import time
from functools import lru_cache

import pytest

from app.modules.procurement.document_items import TOO_HEAVY, UNREADABLE, read_supplier_document
from app.modules.procurement.invoice_file import TOO_HEAVY_HINT, recognise_invoice
from app.shared import pdf_text as reader
from app.shared.pdf_text import PdfLimitExceeded, pdf_text
from tests.pdf_samples import _pdf, _stream, flate_bomb_pdf, identity_pdf

INVOICE = identity_pdf(["ООО Поставка", "Счет на оплату № 58 от 14 сентября 2026 г."])


@lru_cache(maxsize=1)
def bomb() -> bytes:
    return flate_bomb_pdf()


def test_flate_bomb_is_refused_quickly_without_unpacking_it():
    content = bomb()
    assert len(content) < 200_000  # small upload…

    started = time.perf_counter()
    with pytest.raises(PdfLimitExceeded):
        pdf_text(content)  # …that would unpack to 64 MB
    assert time.perf_counter() - started < 5


def test_document_budget_counts_every_stream_together(monkeypatch):
    monkeypatch.setattr(reader, "DOCUMENT_LIMIT", 100)
    page = b"BT /F1 10 Tf (" + b"x" * 80 + b") Tj ET"
    two_pages = _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        _stream(page),
    ])

    with pytest.raises(PdfLimitExceeded):
        pdf_text(two_pages)


def test_object_count_and_font_code_tables_are_capped():
    many = _pdf([b"null"] * (reader.OBJECT_LIMIT + 1))
    cmap = b"begincmap\n4 beginbfrange\n" + b"<0000> <FFFF> <0041>\n" * 4 + b"endbfrange\nendcmap"
    wide_font = _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        _stream(b"BT /F1 10 Tf <0041> Tj ET"),
        b"<< /Type /Font /Subtype /Type0 /ToUnicode 6 0 R >>",
        _stream(cmap),
    ])

    with pytest.raises(PdfLimitExceeded):
        pdf_text(many)
    with pytest.raises(PdfLimitExceeded):
        pdf_text(wide_font)


def test_ordinary_pdf_and_files_without_text_keep_their_behaviour():
    assert "Счет на оплату № 58" in pdf_text(INVOICE)
    assert pdf_text(b"%PDF-1.4\n%%EOF") == ""
    assert pdf_text(b"\xff\xd8\xff\xe0 scan") == ""


def test_recognition_tells_the_limit_apart_from_a_scan():
    info, hint = recognise_invoice(bomb())
    _, scan_hint = recognise_invoice(b"%PDF-1.4\n%%EOF")

    assert (info.number, info.date, hint) == ("", "", TOO_HEAVY_HINT)
    assert "превышен лимит обработки" in hint and "скан" in scan_hint
    assert read_supplier_document("счёт.pdf", bomb()).hint == TOO_HEAVY
    assert read_supplier_document("скан.pdf", b"%PDF-1.4\n%%EOF").hint == UNREADABLE
