"""«Поставка» from an ЭДО file: the Diadoc XML / ZIP through the same «Файл…» as a scan — no OCR, kept as it came."""

import io
import zipfile
from datetime import date
from pathlib import Path

from app.modules.warehouse import receipt_llm
from app.modules.warehouse.infrastructure import WarehouseReceiptFileRow
from app.workflows.edo_receipt import payment_docs
from tests.warehouse.test_receipt_llm import FakeApi, _reply
from tests.warehouse.test_receipts import stand  # noqa: F401 — fixture

FIXTURES = Path(__file__).parents[1] / "purchasing" / "fixtures" / "edo"
UPD = (FIXTURES / "upd-503.xml").read_bytes()
CORRECTION = (FIXTURES / "ukd-501.xml").read_bytes()
SCAN = "/api/stock/receipts/scan"


def _post(client, name, content, kind="application/xml"):
    return client.post(SCAN, files={"file": (name, content, kind)})


def _zip(*entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


def test_an_edo_upd_gives_header_and_lines_without_ocr(stand):  # noqa: F811
    client, _ = stand
    response = _post(client, "upd.xml", UPD)
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["note"] == "Электронный УПД (ЭДО) — данные из файла"
    assert body["recognised"] == {
        "supplier": "ООО \"Ромашка Электро\"", "inn": "7701000019", "upd_number": "РЭ-0915",
        "upd_date": "2026-09-15", "invoice_number": "РЭ-777", "invoice_date": "2026-09-08",
        "prd_number": "901, 915", "prd_date": "2026-09-10",
    }
    assert [(line["name"], line["quantity"], line["unit"]) for line in body["lines"]] == [
        ("Разъём тестовый РТ-2, 2 контакта", "100", "шт"), ("Провод монтажный тестовый 0,5 мм2", "250.5", "м"),
    ]
    assert body["lines"][0]["supplier_code"] == "RT-2@TEST"


def test_the_xml_is_kept_as_it_came_under_its_own_type(stand):  # noqa: F811
    client, sessions = stand
    body = _post(client, "upd.xml", UPD).json()["result"]
    served = client.get(body["scan_file"])
    assert served.status_code == 200 and served.content == UPD
    assert served.headers["content-disposition"].startswith("attachment")
    with sessions() as session:
        [row] = session.query(WarehouseReceiptFileRow).all()
    assert row.content_type == "application/xml" and row.file_name == "upd.xml"


def test_a_zip_takes_the_first_upd_and_says_how_many_there_were(stand):  # noqa: F811
    client, _ = stand
    archive = _zip(("ON_NSCHFDOPPR_a.xml", UPD), ("ON_NSCHFDOPPR_b.xml", UPD), ("card.pdf", b"%PDF-1"))
    body = _post(client, "diadoc.zip", archive, "application/zip").json()["result"]
    assert body["note"].startswith("В архиве 2 документов — взят № РЭ-0915")
    assert body["recognised"]["upd_number"] == "РЭ-0915"
    assert client.get(body["scan_file"]).content == archive


def test_a_correction_invoice_is_refused_and_nothing_is_stored(stand):  # noqa: F811
    client, sessions = stand
    response = _post(client, "ukd.xml", CORRECTION)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("Это не УПД в формате ФНС — ")
    with sessions() as session:
        assert session.query(WarehouseReceiptFileRow).count() == 0


def test_other_xml_and_a_zip_without_documents_are_refused(stand):  # noqa: F811
    client, _ = stand
    other = _post(client, "x.xml", '<?xml version="1.0" encoding="utf-8"?><a/>'.encode())
    assert other.status_code == 422 and other.json()["detail"].startswith("Это не УПД в формате ФНС — ")
    empty = _post(client, "x.zip", _zip(("readme.txt", b"hi")), "application/zip")
    assert empty.status_code == 422 and "нет XML-документов ЭДО" in empty.json()["detail"]


def test_the_payment_documents_of_the_file_numbers_joined_first_date():
    assert payment_docs("№ 901 от 10.09.2026, № 915 от 12.09.2026") == ("901, 915", date(2026, 9, 10))
    assert payment_docs("№ 7/А-2") == ("7/А-2", None)
    assert payment_docs("от 01.10.2026") == ("", date(2026, 10, 1))
    assert payment_docs("") == ("", None)


# --- the names through a text-only model call (owner 26.10.2026: «и тип, и артикул, и наименование наше») ---------

def _model(monkeypatch, *plan) -> FakeApi:
    monkeypatch.setenv("ERP_UPD_LLM_API_KEY", "sk-test-0123456789-secret-AbCd")
    monkeypatch.setenv("ERP_UPD_LLM_BASE_URL", "https://llm.example.test/v1")
    fake = FakeApi(*plan)
    monkeypatch.setattr(receipt_llm.urllib.request, "urlopen", fake)
    return fake


def test_the_names_of_an_edo_file_get_our_name_type_maker_and_article_from_one_text_call(stand, monkeypatch):  # noqa: F811
    client, _ = stand
    fake = _model(monkeypatch, _reply({"lines": [
        {"n": 2, "article": "", "maker": "", "item_type": "Провод Монтажный", "short_name": "Провод монтажный 0,5 мм²"},
        {"n": 1, "article": "RT-2", "maker": "test", "item_type": "соединитель", "short_name": "Разъём РТ-2, 2 конт."},
    ]}))
    body = _post(client, "upd.xml", UPD).json()["result"]
    [sent] = fake.requests
    content = sent["body"]["messages"][0]["content"]
    assert isinstance(content, str), "text only — no picture"
    assert "1. Разъём тестовый РТ-2, 2 контакта" in content and "«Термоусадочная трубка»" in content
    first, second = body["lines"]
    assert (first["name"], first["upd_name"], first["item_type"], first["manufacturer"], first["article"]) == (
        "Разъём РТ-2, 2 конт.", "Разъём тестовый РТ-2, 2 контакта", "Соединитель", "TEST", "RT-2")
    assert (second["name"], second["upd_name"], second["item_type"]) == (
        "Провод монтажный 0,5 мм²", "Провод монтажный тестовый 0,5 мм2", "Провод Монтажный")


def test_a_failing_model_leaves_the_edo_lines_as_the_file_gives_them(stand, monkeypatch):  # noqa: F811
    client, _ = stand
    _model(monkeypatch, 401, 401)
    response = _post(client, "upd.xml", UPD)
    assert response.status_code == 200, response.text
    lines = response.json()["result"]["lines"]
    assert [(line["name"], line["upd_name"], line["item_type"]) for line in lines] == [
        ("Разъём тестовый РТ-2, 2 контакта", "Разъём тестовый РТ-2, 2 контакта", ""),
        ("Провод монтажный тестовый 0,5 мм2", "Провод монтажный тестовый 0,5 мм2", ""),
    ]


def test_without_a_model_key_no_call_is_made(stand, monkeypatch):  # noqa: F811
    client, _ = stand
    monkeypatch.delenv("ERP_UPD_LLM_API_KEY", raising=False)
    fake = FakeApi()
    monkeypatch.setattr(receipt_llm.urllib.request, "urlopen", fake)
    assert _post(client, "upd.xml", UPD).status_code == 200
    assert fake.requests == []
