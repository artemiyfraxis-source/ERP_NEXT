"""Invoice № and date from the file (Invoices.gs parseInvoiceText_) and the PDF text layer ERP reads."""

from datetime import date

import pytest

from app.modules.procurement.invoice_file import (
    InvoiceInfo, invoice_date, invoice_extension, invoice_file_name, parse_invoice_text, recognise_invoice,
)
from app.shared.pdf_text import pdf_text
from tests.pdf_samples import cp1251_pdf, identity_pdf


@pytest.mark.parametrize("text,number,day", [
    ("Счет на оплату № 1234 от 11 июня 2026 г.", "1234", "11.06.2026"),
    ("СЧЕТ N 997/7425323 от 03.02.26", "997/7425323", "03.02.2026"),
    ("Счёт No Сч00019804, от 5.3.2026", "Сч00019804", "05.03.2026"),
    ("Счет-оферта N° 2606-415191-52596.", "2606-415191-52596", ""),
    # «1 Код 2026» из тела не перебивает дату: берётся первое «число месяц год».
    ("Счет № 7\n1 Код 2026\nДата 2 мая 2026", "7", "02.05.2026"),
    ("Итого 1 234,56\nДоговор 14/09/2026", "", "14.09.2026"),
    ("Счет № 15 от 1 сентября 2026", "15", "01.09.2026"),
    ("", "", ""),
])
def test_parse_invoice_text_as_supplymap(text, number, day):
    assert parse_invoice_text(text) == InvoiceInfo(number=number, date=day)


def test_number_label_must_be_near_the_word_invoice():
    far = "Счет " + "x" * 45 + " № 99"
    assert parse_invoice_text(far).number == ""


def test_date_helpers_and_file_name():
    assert invoice_date("11.06.2026") == date(2026, 6, 11)
    assert invoice_date("31.02.2026") is None and invoice_date("") is None
    assert invoice_extension("счёт.PDF") == ".PDF" and invoice_extension("счёт") == ""
    assert invoice_file_name(InvoiceInfo("17", "11.06.2026"), "ООО Поставка", "RND.001", "PO-001", "a.pdf") == (
        "Счёт № 17 от 11.06.2026 ООО Поставка RND.001 PO-001.pdf"
    )
    assert invoice_file_name(InvoiceInfo(), "", " ", "", "scan.jpg") == "Счёт.jpg"


def test_pdf_text_reads_identity_fonts_through_to_unicode_and_keeps_lines():
    content = identity_pdf(["ООО Поставка", "Счет на оплату № 58 от 14 сентября 2026 г."])

    text = pdf_text(content)

    assert text.split("\n") == ["ООО Поставка", "Счет на оплату № 58 от 14 сентября 2026 г."]
    assert recognise_invoice(content) == (InvoiceInfo("58", "14.09.2026"), "")


def test_pdf_text_reads_single_byte_fonts_as_cp1251():
    content = cp1251_pdf(["Счет (оферта) № A-12", "от 01.10.2026"])

    assert pdf_text(content).split("\n") == ["Счет (оферта) № A-12", "от 01.10.2026"]
    assert recognise_invoice(content)[0] == InvoiceInfo("A-12", "01.10.2026")


@pytest.mark.parametrize("content", [b"\xff\xd8\xff\xe0 jpeg", b"%PDF-1.4 broken", b"", b"%PDF-1.7 /Encrypt"])
def test_files_without_text_layer_ask_for_manual_entry(content):
    info, hint = recognise_invoice(content)
    assert info == InvoiceInfo() and "впишите вручную" in hint


def test_text_without_number_and_date_asks_for_manual_entry():
    info, hint = recognise_invoice(identity_pdf(["Коммерческое предложение"]))
    assert info == InvoiceInfo() and hint == "Не распознаны № и дата — впишите вручную."
