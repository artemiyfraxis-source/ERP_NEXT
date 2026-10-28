"""Закупка: вложения не исполняются в origin ERP (audit F04), счёт читается только после проверки строки (F05)."""

import base64
import time

import pytest
from sqlalchemy import select, update

from app.modules.procurement import application
from app.modules.procurement.infrastructure import ProcurementFileRow
from tests.pdf_samples import flate_bomb_pdf
from tests.procurement.test_api import awaiting_invoice, procurement_api, rates  # noqa: F401 — fixtures
from tests.procurement.test_invoice_upload import INVOICE, _upload
from tests.procurement.test_line_documents import attach
from tests.procurement.test_order_document import ELITAN_PDF, PARTS, order, recognize
from tests.test_stored_files import PNG

HTML = b"<!doctype html><script src='/api/procurement/files/evil'></script>"
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
JS = b"fetch('/api/identity/me').then(r => r.text())"


def _assert_download(response, content):
    assert response.status_code == 200 and response.content == content
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def _post_file(client, line_id, content, declared, name):
    return client.post(f"/api/procurement/{line_id}/files", json={
        "kind": "permit", "file_name": name, "content_type": declared,
        "content_base64": base64.b64encode(content).decode(),
    })


@pytest.mark.parametrize("content,declared,name", [
    (HTML, "text/html", "разрешение.html"),
    (SVG, "image/svg+xml", "схема.svg"),
    (JS, "text/javascript", "evil.js"),
    (HTML, "application/pdf", "подмена.pdf"),  # MIME spoofed by the uploader
    (SVG, "image/png", "подмена.png"),
])
def test_active_uploads_are_kept_and_served_as_downloads(procurement_api, rates, content, declared, name):
    client, line, sessions = procurement_api
    awaiting_invoice(client, line)

    stored = _post_file(client, line.id, content, declared, name)
    assert stored.status_code == 200, stored.text
    with sessions() as session:
        kept_type = session.scalar(select(ProcurementFileRow.content_type))

    assert kept_type == "application/octet-stream"
    _assert_download(client.get(stored.json()["file"]["url"]), content)


def test_old_rows_stored_with_the_uploaders_mime_are_sniffed_again_on_serve(procurement_api, rates):
    client, line, sessions = procurement_api
    awaiting_invoice(client, line)
    url = _post_file(client, line.id, HTML, "text/html", "старый.html").json()["file"]["url"]
    with sessions() as session:  # a row from before the fix: the uploader's MIME as typed
        session.execute(update(ProcurementFileRow).values(content_type="text/html"))
        session.commit()

    _assert_download(client.get(url), HTML)


def test_pdf_and_pictures_still_open_in_the_tab(procurement_api, rates):
    client, line, _ = procurement_api
    awaiting_invoice(client, line)
    pdf = client.get(attach(client, line.id, "quality").json()["file"]["url"])
    # a picture declared as something else is a picture all the same
    picture = client.get(_post_file(client, line.id, PNG, "application/octet-stream", "фото").json()["file"]["url"])

    assert (pdf.headers["content-type"], pdf.headers["content-disposition"][:6]) == ("application/pdf", "inline")
    assert pdf.headers["content-security-policy"].startswith("default-src 'none'")
    assert "sandbox" not in pdf.headers["content-security-policy"]
    assert pdf.headers["x-content-type-options"] == "nosniff"
    assert (picture.headers["content-type"], picture.headers["content-disposition"][:6]) == ("image/png", "inline")
    assert picture.content == PNG


def test_foreign_line_is_refused_403_before_the_file_is_read(procurement_api, rates, monkeypatch):
    client, line, _ = procurement_api  # nobody took the line yet — it is not the buyer's own
    read = []
    monkeypatch.setattr(application, "recognise_invoice_file", lambda content: read.append(content))

    refused = _upload(client, line.id)

    assert refused.status_code == 403 and refused.json()["detail"] == "Закупщик может изменять только свои строки"
    assert read == []


def test_foreign_order_document_is_refused_before_it_is_read(procurement_api, monkeypatch):
    client, _, sessions = procurement_api
    order(client, sessions, PARTS[:1])
    read = []
    monkeypatch.setattr(application, "read_supplier_document", lambda *args: read.append(args))

    unknown = recognize(client, "PO-404", ELITAN_PDF)

    assert unknown.status_code == 404 and read == []


def test_pdf_bomb_invoice_is_stored_with_a_limit_hint_quickly(procurement_api, rates):
    client, line, _ = procurement_api
    awaiting_invoice(client, line)

    started = time.perf_counter()
    body = _upload(client, line.id, content=flate_bomb_pdf()).json()

    assert time.perf_counter() - started < 10
    assert (body["invoice_number"], body["invoice_date"]) == ("", "")
    assert "превышен лимит обработки" in body["hint"]
    assert _upload(client, line.id, content=INVOICE).json()["invoice_number"] == "58"
