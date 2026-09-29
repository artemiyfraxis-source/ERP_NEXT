"""Файл счёта в хранилище ERP (Invoices.gs uploadInvoice): № и дата из файла, ссылка, «В оплату», замена."""

import base64
from urllib.parse import unquote

from sqlalchemy import select

from app.modules.procurement import application
from app.shared.persistence import AuditEventRow
from tests.pdf_samples import identity_pdf
from tests.procurement.test_api import (  # noqa: F401 — fixtures
    KP_ROW, awaiting_invoice, kp_row, order_lines, procurement_api, rates, second_line,
)

INVOICE = identity_pdf(["ООО Поставка", "Счет на оплату № 58 от 14 сентября 2026 г."])


def _upload(client, line_id, content=INVOICE, name="счёт.pdf"):
    return client.post("/api/procurement/invoice-files", json={
        "line_id": str(line_id), "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })


def _money(client, line, version):
    return client.patch(f"/api/procurement/{line.id}/money", json={
        "version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "Разъём SMA",
    }).json()["line"]


def test_uploaded_invoice_is_stored_recognised_and_moves_the_line_to_payment(procurement_api, rates):
    client, line, sessions = procurement_api
    version = awaiting_invoice(client, line)
    saved = _money(client, line, version)

    uploaded = _upload(client, line.id)
    body = uploaded.json()
    downloaded = client.get(body["url"])
    invoiced = client.post(f"/api/procurement/{line.id}/invoice", json={
        "version": saved["version"], "invoice_url": body["url"], "expected_arrival": "2026-09-25",
        "invoice_number": body["invoice_number"], "invoice_date": body["invoice_date"],
    })

    assert uploaded.status_code == 200
    assert body["url"].startswith("/api/procurement/files/")
    assert (body["invoice_number"], body["invoice_date"], body["hint"]) == ("58", "2026-09-14", "")
    assert body["file_name"] == f"Счёт № 58 от 14.09.2026 ООО Поставка RND.001 {saved['order_number']}.pdf"
    assert downloaded.status_code == 200 and downloaded.content == INVOICE
    assert downloaded.headers["content-type"] == "application/pdf"
    assert unquote(downloaded.headers["content-disposition"]) == f"inline; filename*=UTF-8''{body['file_name']}"
    line_body = invoiced.json()["line"]
    assert (line_body["status"], line_body["invoice_url"], line_body["invoice_number"], line_body["invoice_date"]) == (
        "В оплату", body["url"], "58", "2026-09-14",
    )
    with sessions() as session:
        event = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "procurement.invoice_file_uploaded")
        ).one()
    assert event.details["name"] == body["file_name"]


def test_scan_without_text_is_stored_and_asks_for_manual_number_and_date(procurement_api, rates):
    client, line, _ = procurement_api
    awaiting_invoice(client, line)

    body = _upload(client, line.id, content=b"\xff\xd8\xff\xe0 scan", name="scan.JPG").json()

    assert (body["invoice_number"], body["invoice_date"]) == ("", "")
    assert "впишите вручную" in body["hint"]
    assert body["file_name"].startswith("Счёт ООО Поставка RND.001 ") and body["file_name"].endswith(".JPG")


def test_replacing_with_an_uploaded_file_keeps_payment_and_status(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    saved = _money(client, line, version)
    invoiced = client.post(f"/api/procurement/{line.id}/invoice", json={
        "version": saved["version"], "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25",
    }).json()["line"]

    body = _upload(client, line.id).json()
    replaced = client.post(f"/api/procurement/{line.id}/invoice/replace", json={
        "version": invoiced["version"], "invoice_url": body["url"],
        "invoice_number": body["invoice_number"], "invoice_date": body["invoice_date"],
    }).json()["line"]

    assert (replaced["status"], replaced["invoice_url"], replaced["invoice_number"]) == ("В оплату", body["url"], "58")


def test_one_uploaded_invoice_for_the_whole_order_in_kp_form(procurement_api, rates):
    client, line, sessions = procurement_api
    other = second_line(client, sessions)
    number, (first_version, second_version) = order_lines(client, line, other)

    body = _upload(client, line.id).json()
    row = {**KP_ROW, "invoice_number": body["invoice_number"], "invoice_date": body["invoice_date"]}
    saved = client.post("/api/procurement/kp", json={
        "order_reference": number, "invoice_url": body["url"],
        "rows": [kp_row(line, first_version, **row), kp_row(other, second_version, **row)],
    }).json()

    assert saved["invoice_error"] == ""
    assert [(item["status"], item["invoice_url"], item["invoice_number"]) for item in saved["lines"]] == [
        ("В оплату", body["url"], "58"), ("В оплату", body["url"], "58"),
    ]


def test_upload_refuses_foreign_line_unknown_line_empty_damaged_or_too_large_file(procurement_api, rates, monkeypatch):
    client, line, _ = procurement_api
    fresh = _upload(client, line.id)  # nobody took the line yet — it is not the buyer's own
    awaiting_invoice(client, line)
    monkeypatch.setattr(application, "INVOICE_FILE_LIMIT", 4)

    assert fresh.status_code == 422 and fresh.json()["detail"] == "Закупщик может изменять только свои строки"
    assert _upload(client, "00000000-0000-0000-0000-000000000000", content=b"1").status_code == 404
    assert _upload(client, line.id, content=b"12345").json()["detail"] == "Файл счёта больше 20 МБ"
    damaged = client.post("/api/procurement/invoice-files", json={
        "line_id": str(line.id), "file_name": "a.pdf", "content_base64": "не base64",
    })
    assert damaged.status_code == 422 and damaged.json()["detail"] == "Файл счёта повреждён"
    assert client.get("/api/procurement/files/00000000-0000-0000-0000-000000000000").status_code == 404


def test_invoice_link_accepts_only_stored_file_paths_besides_http(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    saved = _money(client, line, version)

    refused = client.post(f"/api/procurement/{line.id}/invoice", json={
        "version": saved["version"], "invoice_url": "/api/other/files/1", "expected_arrival": "2026-09-25",
    })

    assert refused.status_code == 422
