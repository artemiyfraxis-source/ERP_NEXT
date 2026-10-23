"""«Закупка» (owner 23.10): decided positions by supplier → «Запросить счёт» (letter, «Жду счёт») → the invoice read and
confirmed («Ожидает оплаты») → «Внести ПП» or «Оплачено» → the archive. Every write carries the line versions."""

import base64
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementFileRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork
from app.modules.purchasing.purchase_board import card_stage, stage_of
from app.modules.requests.api import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from tests.factories import Actor
from tests.pdf_samples import identity_pdf
from tests.procurement.test_api import MOSCOW, StubRates, message
from tests.procurement.test_document_items import ELITAN_INVOICE

BUY = "/api/purchasing/buy"
RFQ = "/api/purchasing/rfqs"
NOW = datetime(2026, 9, 15, 11, 30)
ELITAN_PDF = identity_pdf(ELITAN_INVOICE.split("\n"))
PARTS = [
    {"calculated_name": "Корпус разъёма 2 конт.", "design_article": "SHR-02V-S-B", "calculated_quantity": "200", "price": "3.66"},
    {"calculated_name": "Разъём Picoblade 10 конт.", "design_article": "51047-1000", "calculated_quantity": "50", "price": "12.2"},
    {"calculated_name": "Контакт обжимной", "design_article": "SSHL-002T-P0.2", "calculated_quantity": "1000", "price": "1.22"},
]


def _as(name: str, mode: str = "edit") -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Закупки"), email=f"{name}@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": mode},
    )


@pytest.fixture()
def buy_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_stock_uow_factory = lambda: SqlAlchemyProcurementStockUnitOfWork(sessions)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.procurement_exchange_rates = StubRates()
    clock = {"now": NOW}
    app.dependency_overrides[moscow_now] = lambda: clock["now"]
    _as("Петров")
    yield TestClient(app), sessions, clock
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("procurement_uow_factory", "procurement_stock_uow_factory", "purchasing_uow_factory",
                 "procurement_exchange_rates"):
        delattr(app.state, name)
    engine.dispose()


def decided(client, sessions, parts=PARTS, supplier="ООО Элитан Трейд", first=2, quote_number="КП-55") -> list[str]:
    """Lines taken in work, asked of one supplier, quoted and decided: «Готов к запросу» with the supplier written."""
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    ids = []
    for offset, part in enumerate(parts):
        base = message(first + offset)
        spec = {key: value for key, value in part.items() if key != "price"}
        payload = {**base.payload, "delivery_point": MOSCOW, **spec}
        ids.append(str(consume_procurement_requested(OutboxMessage(**{**base.__dict__, "payload": payload}), factory()).id))
    assert client.post("/api/procurement/take", json={"line_ids": ids}).status_code == 200
    rfq = client.post(RFQ, json={"line_ids": ids, "title": "Разъёмы"}).json()
    supplier_id = client.post(f"{RFQ}/{rfq['id']}/suppliers", json={"name": supplier}).json()["supplier_id"]
    matrix = client.get(f"{RFQ}/{rfq['id']}").json()
    by_line = {line["procurement_line_id"]: line for line in matrix["lines"]}
    quoted = client.post(f"{RFQ}/{rfq['id']}/quotes", json={
        "supplier_id": supplier_id, "number": quote_number, "received_on": "2026-09-14", "lines": [
            {"rfq_line_id": by_line[line_id]["id"], "unit_price": part["price"]} for line_id, part in zip(ids, parts)
        ],
    })
    assert quoted.status_code == 201, quoted.text
    matrix = client.get(f"{RFQ}/{rfq['id']}").json()
    for line in matrix["lines"]:
        made = client.post(f"{RFQ}/{rfq['id']}/decisions", json={
            "rfq_line_id": line["id"], "quote_line_id": line["cells"][supplier_id]["quote_line_id"],
            "gate_override_code": "single_source",
        })
        assert made.status_code == 201, made.text
    return ids


def board(client, scope: str = "mine") -> dict:
    response = client.get(BUY, params={"scope": scope})
    assert response.status_code == 200, response.text
    return response.json()


def versions(card: dict) -> dict[str, int]:
    return {line["line_id"]: line["version"] for line in card["lines"]}


def refs(card: dict) -> list[dict]:
    return [{"line_id": line["line_id"], "version": line["version"]} for line in card["lines"]]


def test_the_whole_path_from_the_decision_to_the_archive(buy_api):
    client, sessions, _ = buy_api
    ids = decided(client, sessions)

    (card,) = board(client)["cards"]
    assert (card["supplier"], card["stage"], card["tag"], card["count"], card["order_number"], card["archived"]) == (
        "ООО Элитан Трейд", "ready", "Готов к запросу", 3, None, False)
    assert card["sums"] == [{"currency": "RUB", "sum": "2562"}]
    assert [(quote["number"], quote["date"]) for quote in card["quotes"]] == [("КП-55", "2026-09-14")]
    assert {line["line_id"] for line in card["lines"]} == set(ids) and card["mine"] is True

    # The letter before the request: the intent to buy by the КП, no order yet.
    (letter,) = client.get(f"{BUY}/letters", params={"line_ids": ids}).json()["letters"]
    assert letter["subject"] == "Закупка по КП № КП-55 от 14.09.2026 — просим счёт, 3 поз."
    assert "По вашему КП № КП-55 от 14.09.2026 мы приняли решение о закупке" in letter["body"]
    assert "1. Корпус разъёма 2 конт. (SHR-02V-S-B) — 200 шт × 3,66 ₽ = 732,00 ₽" in letter["body"]
    assert "Итого по ценам КП: 2 562,00 ₽ с НДС." in letter["body"]
    assert "Просим выставить счёт на оплату со ссылкой на КП." in letter["body"]

    # «Запросить счёт» with a stale version: 409, nothing formed.
    stale = {**versions(card), ids[0]: versions(card)[ids[0]] - 1}
    refused = client.post("/api/procurement/supplier-requests", json={"line_ids": ids, "versions": stale})
    assert refused.status_code == 409 and "изменилась" in refused.json()["detail"]
    assert board(client)["cards"][0]["stage"] == "ready"

    formed = client.post("/api/procurement/supplier-requests", json={"line_ids": ids, "versions": versions(card)})
    assert formed.status_code == 200, formed.text
    (order,) = formed.json()["orders"]
    number = order["order_number"]
    (card,) = board(client)["cards"]
    assert (card["stage"], card["tag"], card["order_number"]) == ("awaiting", "Жду счёт", number)
    (letter,) = client.get(f"{BUY}/letters", params={"line_ids": ids}).json()["letters"]
    assert letter["orders"] == [number] and f"на наш заказ № {number} со ссылкой на КП" in letter["body"]
    assert letter["subject"].endswith(f"просим счёт, заказ № {number}, 3 поз.")

    # The invoice: read, matched, checked by the buyer, confirmed → «Ожидает оплаты».
    found = client.post(f"/api/procurement/orders/{number}/recognize", json={
        "kind": "invoice", "file_name": "счёт.pdf", "content_type": "application/pdf",
        "content_base64": base64.b64encode(ELITAN_PDF).decode(),
    })
    assert found.status_code == 200, found.text
    found = found.json()
    rows = [{"line_id": row["line_id"], "version": row["version"], "price_with_vat": row["price_with_vat"],
             "price": row["price"], "vat_rate": row["vat_rate"], "invoice_item_name": row["matched_name"]}
            for row in found["lines"]]
    apply = {"kind": "invoice", "file_url": found["file"]["url"], "expected_arrival": "2026-09-25"}
    stale_rows = [{**rows[0], "version": rows[0]["version"] + 5}, *rows[1:]]
    assert client.post(f"/api/procurement/orders/{number}/apply-document", json={**apply, "lines": stale_rows}).status_code == 409
    applied = client.post(f"/api/procurement/orders/{number}/apply-document", json={**apply, "lines": rows})
    assert applied.status_code == 200, applied.text
    (card,) = board(client)["cards"]
    assert (card["stage"], card["tag"], card["invoices"]) == ("to_pay", "Ожидает оплаты", ["ЭТ-10234"])

    # «Внести ПП»: the payment order is kept with the order's line (kind «payment»), then the payment.
    uploaded = client.post(f"/api/procurement/{ids[0]}/files", json={
        "kind": "payment", "file_name": "ПП 512.pdf", "content_type": "application/pdf",
        "content_base64": base64.b64encode(b"%PDF-1.4 pp").decode(),
    })
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["file"]["kind"] == "payment"
    listed = client.get(f"/api/procurement/{ids[0]}/files").json()["files"]
    assert [(item["kind"], item["file_name"]) for item in listed] == [("payment", "ПП 512.pdf")]
    (card,) = board(client)["cards"]
    assert card["stage"] == "to_pay"  # a file moves nothing
    stale_pay = [{**refs(card)[0], "version": refs(card)[0]["version"] - 1}, *refs(card)[1:]]
    assert client.post(f"/api/procurement/orders/{number}/paid", json={"lines": stale_pay}).status_code == 409
    paid = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(card)})
    assert paid.status_code == 200, paid.text
    assert {line["status"] for line in paid.json()["lines"]} == {"В пути"}

    result = board(client)
    (card,) = result["cards"]
    assert (card["stage"], card["tag"], card["archived"], result["archived_total"]) == ("paid", "Оплачено", True, 1)
    # From now on the order lives in «Счета и поставки».
    orders = client.get("/api/purchasing/orders").json()["orders"]
    assert [(item["order_number"], item["column"]) for item in orders] == [(number, "waiting")]


def test_paid_without_a_payment_order_asks_for_a_manual_rate_when_the_cbr_is_silent(buy_api):
    client, sessions, clock = buy_api
    ids = decided(client, sessions, parts=PARTS[:1])
    (card,) = board(client)["cards"]
    number = client.post("/api/procurement/supplier-requests", json={
        "line_ids": ids, "versions": versions(card)}).json()["orders"][0]["order_number"]
    line = board(client)["cards"][0]["lines"][0]
    # A dollar invoice: the buyer typed the rate the day the invoice came (the CBR said nothing).
    money = client.patch(f"/api/procurement/{ids[0]}/money", json={
        "version": line["version"], "currency": "USD", "price": "0.04", "vat_rate": "22",
        "invoice_item_name": "SHR-02V-S-B", "manual_exchange_rate": "80",
    })
    assert money.status_code == 200, money.text
    invoiced = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice.pdf", "invoice_number": "INV-9", "invoice_date": "2026-09-15",
        "expected_arrival": "2026-09-25", "lines": [{"line_id": ids[0], "version": money.json()["line"]["version"]}],
    })
    assert invoiced.status_code == 200, invoiced.text
    (card,) = board(client)["cards"]
    assert card["stage"] == "to_pay"

    clock["now"] = datetime(2026, 9, 16, 10, 0)  # paid a day later: the invoice-day rate no longer serves
    asked = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(card)})
    assert asked.status_code == 409
    assert asked.json()["detail"]["code"] == "manual_rate_required" and asked.json()["detail"]["currency"] == "USD"
    assert board(client)["cards"][0]["stage"] == "to_pay"
    paid = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(card), "manual_exchange_rate": "81,5"})
    assert paid.status_code == 200, paid.text
    assert paid.json()["lines"][0]["exchange_rate"] == "81.5"
    (card,) = board(client)["cards"]
    assert card["archived"] is True
    with sessions() as session:
        assert session.scalars(select(ProcurementFileRow).where(ProcurementFileRow.kind == "payment")).all() == []


def test_cards_are_per_supplier_then_per_order_and_mine_only_unless_asked(buy_api):
    client, sessions, _ = buy_api
    elitan = decided(client, sessions, parts=PARTS[:2])
    other = decided(client, sessions, parts=PARTS[2:], supplier="ООО Контакт", first=10, quote_number="сайт")
    cards = board(client)["cards"]
    assert [(card["supplier"], card["count"]) for card in cards] == [("ООО Контакт", 1), ("ООО Элитан Трейд", 2)]
    contact = cards[0]
    assert contact["quotes"][0]["number"] == "сайт"
    (letter,) = client.get(f"{BUY}/letters", params={"line_ids": other}).json()["letters"]
    assert letter["subject"].startswith("Закупка по предложению от 14.09.2026 (сайт) — просим счёт")
    assert "По вашему предложению от 14.09.2026 (сайт) мы приняли решение" in letter["body"]

    # One line of Элитан requested: the requested one moves to its order's card, the other stays to be requested.
    card = cards[1]
    first = card["lines"][0]
    formed = client.post("/api/procurement/supplier-requests", json={
        "line_ids": [first["line_id"]], "versions": {first["line_id"]: first["version"]}})
    assert formed.status_code == 200, formed.text
    cards = board(client)["cards"]
    assert [(card["supplier"], card["stage"], card["count"]) for card in cards] == [
        ("ООО Контакт", "ready", 1), ("ООО Элитан Трейд", "ready", 1), ("ООО Элитан Трейд", "awaiting", 1)]
    assert set(elitan) == {cards[1]["lines"][0]["line_id"], cards[2]["lines"][0]["line_id"]}

    # Another buyer sees nothing of Петров's by default; «Все закупщики» shows them, not his own.
    _as("Сидоров")
    assert board(client)["cards"] == []
    assert [card["mine"] for card in board(client, "all")["cards"]] == [False, False, False]
    _as("Сидоров", "view")
    assert client.get(BUY, params={"scope": "all"}).status_code == 200
    assert client.post("/api/procurement/supplier-requests", json={"line_ids": elitan}).status_code == 403


def test_letters_skip_lines_without_a_decision_or_past_the_invoice(buy_api):
    client, sessions, _ = buy_api
    ids = decided(client, sessions, parts=PARTS[:1])
    loose = str(consume_procurement_requested(message(40), SqlAlchemyProcurementUnitOfWork(sessions)).id)
    answer = client.get(f"{BUY}/letters", params={"line_ids": [*ids, loose]}).json()
    assert len(answer["letters"]) == 1 and answer["skipped"] == [loose]
    assert client.get(f"{BUY}/letters").status_code == 422  # no lines asked


def test_the_card_sits_at_its_earliest_stage():
    assert [stage_of(status) for status in ("В работе", "Готов к запросу", "Жду счет", "В оплату", "В пути", "Готово")] == [
        "draft", "ready", "awaiting", "to_pay", "paid", "paid"]
    assert stage_of("Отменено") is None and stage_of("Согласование") is None and stage_of("Новый") is None
    assert card_stage(["paid", "to_pay", "awaiting"]) == "awaiting"
    assert card_stage(["draft", "ready"]) == "ready"  # an unfilled position does not hold the card back
    assert card_stage(["draft"]) == "draft"
    assert card_stage(["paid"]) == "paid"
