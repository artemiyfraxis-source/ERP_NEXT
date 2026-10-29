"""«УПД № 1788 от 20.10.2026.pdf» — the name a primary document is shown and downloaded under."""

from datetime import date, datetime
from urllib.parse import unquote

import pytest
from app.shared.document_names import document_file_name, document_title
from app.shared.stored_files import file_response


@pytest.mark.parametrize(("kind", "number", "expected"), [
    ("УПД", "1788", "УПД № 1788 от 20.10.2026.pdf"),
    ("УПД до 2019 года", "1788", "УПД № 1788 от 20.10.2026.pdf"),
    ("Счёт", "3789518", "Счёт № 3789518 от 20.10.2026.pdf"),
    ("КП", "17", "КП № 17 от 20.10.2026.pdf"),
    ("ТОРГ-12", "5", "ТОРГ-12 № 5 от 20.10.2026.pdf"),
    ("Акт", "9", "Акт № 9 от 20.10.2026.pdf"),
    ("М-15", "3", "М-15 № 3 от 20.10.2026.pdf"),
    ("Счёт-фактура", "7", "Счёт-фактура № 7 от 20.10.2026.pdf"),
    ("ТН + СФ", "8", "ТН + СФ № 8 от 20.10.2026.pdf"),
])
def test_kinds(kind, number, expected):
    assert document_file_name(kind, number, date(2026, 10, 20), "scan.pdf") == expected


def test_no_number_keeps_the_date_and_no_date_keeps_the_number():
    assert document_file_name("УПД", "", date(2026, 10, 20), "scan.pdf") == "УПД от 20.10.2026.pdf"
    assert document_file_name("УПД", None, "2026-10-20", "scan.pdf") == "УПД от 20.10.2026.pdf"
    assert document_file_name("УПД", "1788", None, "scan.pdf") == "УПД № 1788.pdf"
    assert document_title("УПД", "1788", datetime(2026, 10, 20, 9, 30)) == "УПД № 1788 от 20.10.2026"


def test_nothing_recognised_or_unknown_kind_keeps_the_original_name():
    assert document_file_name("УПД", "", None, "scan.pdf") == "scan.pdf"
    assert document_file_name("", "5", date(2026, 10, 20), "scan.pdf") == "scan.pdf"
    assert document_file_name("Прочее", "5", date(2026, 10, 20), "Скан 1.JPG") == "Скан 1.JPG"
    assert document_file_name("УПД", "5", "не дата", "scan.pdf") == "УПД № 5.pdf"


def test_a_number_that_starts_with_the_kind_does_not_repeat_it():
    assert document_file_name("КП", "КП-17", date(2026, 10, 20), "q.pdf") == "КП № 17 от 20.10.2026.pdf"
    assert document_file_name("КП", "КП-2609-001", date(2026, 10, 20), "q.pdf") == "КП № 2609-001 от 20.10.2026.pdf"
    assert document_file_name("УПД", "УПД", date(2026, 10, 20), "q.pdf") == "УПД № УПД от 20.10.2026.pdf"


def test_the_real_extension_is_kept():
    for name, ext in [("a.XML", ".xml"), ("a.zip", ".zip"), ("фото.JPG", ".jpg"), ("a.b.pdf", ".pdf"), ("noext", "")]:
        assert document_file_name("УПД", "1", date(2026, 10, 20), name) == f"УПД № 1 от 20.10.2026{ext}"


def test_unsafe_characters_are_cleaned_and_a_slash_becomes_a_dash():
    assert document_file_name("УПД", "АБ/12", date(2026, 10, 20), "x.pdf") == "УПД № АБ-12 от 20.10.2026.pdf"
    name = document_file_name("Счёт", '№ 5\\6:*?"<>|\n7', date(2026, 10, 20), "x.pdf")
    assert name == "Счёт № 5-6 7 от 20.10.2026.pdf"
    assert not set('/\\:*?"<>|') & set(name)
    assert len(document_file_name("УПД", "9" * 500, date(2026, 10, 20), "x.pdf")) < 120


def test_download_header_carries_the_utf8_name():
    name = document_file_name("УПД", "1788", date(2026, 10, 20), "scan.pdf")
    response = file_response(b"%PDF-1.4 x", name)
    header = response.headers["content-disposition"]
    assert header.startswith("inline; filename*=UTF-8''") and "%D0%A3%D0%9F%D0%94" in header
    assert unquote(header) == "inline; filename*=UTF-8''УПД № 1788 от 20.10.2026.pdf"
