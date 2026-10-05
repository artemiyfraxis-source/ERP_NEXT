"""«Вставить КП» / «Вставить счёт» на заказ (owner, 05.10): цены из файла, затем одной транзакцией в строки."""

import base64

from sqlalchemy import select

from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.persistence import AuditEventRow
from tests.pdf_samples import identity_pdf
from tests.procurement.test_api import (  # noqa: F401 — fixtures
    MOSCOW, message, procurement_api, rates,
)
from tests.procurement.test_document_items import ELITAN_INVOICE, KP_NET

ELITAN_PDF = identity_pdf(ELITAN_INVOICE.split("\n"))
KP_PDF = identity_pdf(KP_NET.split("\n"))

PARTS = [
    {"calculated_name": "Корпус разъёма 2 конт.", "design_article": "SHR-02V-S-B", "calculated_quantity": "200"},
    {"calculated_name": "Разъём Picoblade 10 конт.", "design_article": "51047-1000", "calculated_quantity": "50"},
    {"calculated_name": "Контакт обжимной", "design_article": "SSHL-002T-P0.2", "calculated_quantity": "1000"},
]
WIRES = [
    {"calculated_name": "Провод ПуГВ 1х0,75 черный", "calculated_quantity": "300", "calculated_unit": "м"},
    {"calculated_name": "Провод ПуГВ 1х0,75 красный", "calculated_quantity": "300", "calculated_unit": "м"},
    {"calculated_name": "Хомут нейлоновый 2,5х100", "calculated_quantity": "100"},
]


def order(client, sessions, specs, first: int = 2) -> tuple[str, dict[str, dict]]:
    """One supplier order «Жду счет» of lines with these names / articles / quantities."""
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    created = []
    for offset, spec in enumerate(specs):
        base = message(first + offset)
        payload = {**base.payload, "delivery_point": MOSCOW, **spec}
        created.append(consume_procurement_requested(OutboxMessage(**{**base.__dict__, "payload": payload}), factory()))
    ids = [str(line.id) for line in created]
    taken = client.post("/api/procurement/take", json={"line_ids": ids}).json()["lines"]
    saved = client.post("/api/procurement/facts", json={"rows": [
        {"line_id": item["id"], "version": item["version"], "supplier": "ООО Элитан Трейд"} for item in taken
    ]}).json()
    assert saved["ready"] == len(specs), saved
    formed = client.post("/api/procurement/supplier-requests", json={"line_ids": ids}).json()
    (made,) = formed["orders"]
    by_id = {line["id"]: line for line in formed["lines"]}
    return made["order_number"], {line_id: by_id[line_id] for line_id in ids}


def recognize(client, number, content, kind="invoice", name="счёт.pdf"):
    return client.post(f"/api/procurement/orders/{number}/recognize", json={
        "kind": kind, "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })


def apply_rows(found):
    return [
        {"line_id": row["line_id"], "version": row["version"], "price_with_vat": row["price_with_vat"],
         "price": row["price"], "vat_rate": row["vat_rate"], "invoice_item_name": row["matched_name"]}
        for row in found["lines"]
    ]


def events(sessions, kind):
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == kind)))


def test_invoice_is_recognised_matched_by_part_numbers_and_lines_stay_untouched(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS)

    response = recognize(client, number, ELITAN_PDF)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["kind"], body["invoice_number"], body["invoice_date"], body["currency"], body["auto"]) == (
        "invoice", "ЭТ-10234", "2026-10-02", "RUB", True,
    )
    assert (body["vat_mode"], body["vat_rate"], body["hint"]) == ("included", "22", "")
    assert body["file"]["url"].startswith("/api/procurement/files/")
    assert body["file"]["name"] == f"Счёт № ЭТ-10234 от 02.10.2026 ООО Элитан Трейд RND.002 {number}.pdf"
    ids = list(lines)
    assert [(row["line_id"], row["price_with_vat"], row["price"], row["sum"], row["confidence"]) for row in body["lines"]] == [
        (ids[0], "3.66", "3", "732", "article"),
        (ids[1], "12.2", "10", "610", "article"),
        (ids[2], "1.22", "1", "1220", "article"),
    ]
    assert body["lines"][1]["matched_name"] == "Разъём Molex 51047-1000 Picoblade 10 pin"
    assert body["lines"][1]["legacy_id"] == lines[ids[1]]["legacy_id"]
    assert body["lines"][0]["warnings"] == []
    assert (body["unmatched_items"], body["unmatched_lines"]) == ([], [])
    assert body["totals"] == {"sum": "2100", "vat": "462", "sum_with_vat": "2562"}
    mine = {line["id"]: line for line in client.get("/api/procurement/mine").json()["lines"]}
    assert all(mine[line_id]["actual_price_with_vat"] is None and mine[line_id]["version"] == lines[line_id]["version"]
               for line_id in ids)
    assert client.get(body["file"]["url"]).content == ELITAN_PDF
    (event,) = events(sessions, "procurement.document_file_uploaded")
    assert (event.details["kind"], event.details["items"], event.details["order_reference"]) == ("invoice", "3", number)


def test_applied_invoice_prices_every_row_and_moves_the_order_to_payment_atomically(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS)
    found = recognize(client, number, ELITAN_PDF).json()

    applied = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": found["file"]["url"], "expected_arrival": "2026-10-12",
        "lines": apply_rows(found),
    })

    assert applied.status_code == 200, applied.text
    result = {line["id"]: line for line in applied.json()["lines"]}
    assert set(result) == set(lines)
    first = result[next(iter(lines))]
    assert (first["status"], first["invoice_url"], first["invoice_number"], first["invoice_date"]) == (
        "В оплату", found["file"]["url"], "ЭТ-10234", "2026-10-02",
    )
    assert (first["actual_price"], first["actual_price_with_vat"], first["actual_vat_rate"], first["actual_sum"]) == (
        "3", "3.66", "22", "732",
    )
    assert first["invoice_item_name"] == "Корпус разъёма SHR-02V-S-B (JST)"
    assert first["expected_arrival"] == "2026-10-12" and first["pay_missing"] == []
    priced = events(sessions, "procurement.fact_price_saved")
    assert len(priced) == 3
    assert {(e.details["document_kind"], e.details["document_url"]) for e in priced} == {("invoice", found["file"]["url"])}
    assert len(events(sessions, "procurement.invoice_recorded")) == 3


def test_offer_prices_without_vat_keep_rows_awaiting_invoice(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, WIRES)
    found = recognize(client, number, KP_PDF, kind="kp", name="КП.pdf").json()

    assert (found["invoice_number"], found["vat_mode"]) == ("145", "on_top")
    assert found["file"]["name"] == f"КП № 145 от 01.10.2026 ООО Элитан Трейд {number}.pdf"
    ids = list(lines)
    assert [(row["line_id"], row["price"], row["price_with_vat"]) for row in found["lines"]] == [
        (ids[0], "18.5", "22.57"), (ids[1], "18.5", "22.57"),
    ]
    assert found["unmatched_lines"] == [lines[ids[2]]["legacy_id"]]
    assert [item["name"] for item in found["unmatched_items"]] == ["Трубка термоусадочная ТУТ 6/3 черная"]
    # «Хомут» is not in the offer: the row is not sent, the КП prices the other two only.
    applied = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "kp", "file_url": found["file"]["url"], "lines": apply_rows(found),
    })

    assert applied.status_code == 200, applied.text
    result = applied.json()["lines"]
    assert [(line["status"], line["actual_price"], line["actual_price_with_vat"], line["actual_sum"]) for line in result] == [
        ("Жду счет", "18.5", "22.57", "6771"), ("Жду счет", "18.5", "22.57", "6771"),
    ]
    assert all(line["invoice_url"] is None for line in result)
    assert not events(sessions, "procurement.invoice_recorded")


def test_foreign_currency_document_is_shown_but_not_applied(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS[:1])
    usd = identity_pdf([
        "Invoice № 88 от 01.10.2026",
        "№ Артикул Наименование Кол-во Ед. Цена, USD Сумма, USD",
        "1 SHR-02V-S-B Корпус разъёма SHR-02V-S-B 200 шт 0,04 8,00",
        "Итого, USD: 8,00",
    ])

    found = recognize(client, number, usd).json()
    refused = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": found["file"]["url"], "expected_arrival": "2026-10-12",
        "lines": apply_rows(found),
    })

    assert (found["currency"], found["auto"]) == ("USD", False)
    assert "«Внести КП»" in found["hint"] and len(found["lines"]) == 1
    assert refused.status_code == 422 and refused.json()["detail"] == "Документ в USD: цены и курс вносите через «Внести КП»"


def test_apply_is_all_or_none_and_refuses_a_partial_invoice_or_a_stale_row(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS)
    found = recognize(client, number, ELITAN_PDF).json()
    rows = apply_rows(found)
    url = found["file"]["url"]

    partial = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": url, "expected_arrival": "2026-10-12", "lines": rows[:2],
    })
    stale = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": url, "expected_arrival": "2026-10-12",
        "lines": [{**rows[0], "version": rows[0]["version"] - 1}, *rows[1:]],
    })
    # The third row has no price in the document and none stored: the pay gate refuses it, nothing is written.
    gate = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": url, "expected_arrival": "2026-10-12",
        "lines": [*rows[:2], {**rows[2], "price_with_vat": None, "price": None}],
    })
    foreign_file = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "kp", "file_url": "https://files.test/kp.pdf", "lines": rows,
    })
    unknown = client.post("/api/procurement/orders/PO-404/apply-document", json={
        "kind": "kp", "file_url": url, "lines": rows,
    })

    assert partial.status_code == 409 and "«Жду счет» (3)" in partial.json()["detail"]
    assert stale.status_code == 409 and "изменилась" in stale.json()["detail"]
    assert gate.status_code == 422
    assert gate.json()["detail"].startswith(f"{lines[rows[2]['line_id']]['legacy_id']}: Нельзя загрузить счёт")
    assert foreign_file.status_code == 422 and "не найден" in foreign_file.json()["detail"]
    assert unknown.status_code == 404
    mine = {line["id"]: line for line in client.get("/api/procurement/mine").json()["lines"]}
    assert {(mine[i]["status"], mine[i]["actual_price_with_vat"]) for i in lines} == {("Жду счет", None)}
    assert not events(sessions, "procurement.fact_price_saved")


def test_recognize_refuses_an_order_without_rows_to_price_and_reads_scans_as_hints(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS[:1])

    scan = recognize(client, number, b"%PDF-1.4\n%%EOF", name="scan.pdf").json()
    unknown = recognize(client, "PO-404", ELITAN_PDF)
    damaged = client.post(f"/api/procurement/orders/{number}/recognize", json={
        "kind": "kp", "file_name": "a.pdf", "content_base64": "не base64",
    })
    bad_kind = client.post(f"/api/procurement/orders/{number}/recognize", json={
        "kind": "act", "file_name": "a.pdf", "content_base64": base64.b64encode(ELITAN_PDF).decode(),
    })

    assert scan["lines"] == [] and "впишите вручную" in scan["hint"]
    assert scan["unmatched_lines"] == [lines[next(iter(lines))]["legacy_id"]]
    assert unknown.status_code == 404 and "нет ваших строк" in unknown.json()["detail"]
    assert damaged.status_code == 422 and damaged.json()["detail"] == "Файл повреждён"
    assert bad_kind.status_code == 422


def test_corrected_invoice_on_rows_in_payment_replaces_it_and_reprices(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS)
    found = recognize(client, number, ELITAN_PDF).json()
    first = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": found["file"]["url"], "expected_arrival": "2026-10-12", "lines": apply_rows(found),
    }).json()["lines"]
    corrected = identity_pdf(
        ELITAN_INVOICE.replace("ЭТ-10234", "ЭТ-10301").replace("3,66 732,00", "3,05 610,00").split("\n")
    )

    again = recognize(client, number, corrected).json()
    replaced = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "invoice", "file_url": again["file"]["url"], "lines": apply_rows(again),
    })

    assert [row["version"] for row in again["lines"]] == [line["version"] for line in first]
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()["lines"]
    assert [(line["status"], line["invoice_number"], line["invoice_url"]) for line in body] == [
        ("В оплату", "ЭТ-10301", again["file"]["url"])
    ] * 3
    assert (body[0]["actual_price_with_vat"], body[0]["actual_price"], body[0]["actual_sum"]) == ("3.05", "2.5", "610")
    assert len(events(sessions, "procurement.invoice_replaced")) == 3


def test_document_without_vat_stores_rate_zero_and_equal_prices(procurement_api):
    client, _, sessions = procurement_api
    number, lines = order(client, sessions, PARTS[:1])
    no_vat = identity_pdf([
        "Счет № 5 от 01.10.2026",
        "№ Артикул Наименование Кол-во Ед. Цена Сумма",
        "1 SHR-02V-S-B Корпус разъёма 200 шт 3,10 620,00",
        "Итого: 620,00",
        "Без налога (НДС)",
    ])
    found = recognize(client, number, no_vat).json()

    applied = client.post(f"/api/procurement/orders/{number}/apply-document", json={
        "kind": "kp", "file_url": found["file"]["url"], "lines": apply_rows(found),
    }).json()["lines"]

    assert (found["vat_mode"], found["lines"][0]["vat_rate"]) == ("none", "0")
    assert (applied[0]["actual_vat_rate"], applied[0]["actual_price"], applied[0]["actual_price_with_vat"]) == (
        "0", "3.1", "3.1",
    )
    assert applied[0]["actual_sum"] == "620"
