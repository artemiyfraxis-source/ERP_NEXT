"""«Счета и поставки» (option procurement.orders) over HTTP: the board's columns, the order's facts, the order-wide
expected date, the promised date from the audit, «Дожать», the port for «Сегодня»."""

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import OrderFactRow, SqlAlchemyPurchasingUnitOfWork
from app.modules.purchasing.orders import column_of
from app.modules.requests.api import moscow_now
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from app.workflows.purchasing_order_facts import order_facts
from app.workflows.purchasing_orders import OrderLineFacts, Receipt
from tests.factories import Actor
from app.modules.procurement.application import consume_procurement_requested
from app.shared.events import OutboxMessage
from tests.procurement.test_api import MOSCOW, message, price_rows, refs

BASE = "/api/purchasing/orders"
NOW = datetime(2026, 9, 20, 11, 30)


def _as(name: str, mode: str = "edit") -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Закупки"), email=f"{name}@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": mode},
    )


def _options(monkeypatch, tmp_path, **flags) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.orders": True, **flags}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


@pytest.fixture()
def orders_api(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_stock_uow_factory = lambda: SqlAlchemyProcurementStockUnitOfWork(sessions)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    _as("Петров")
    app.dependency_overrides[moscow_now] = lambda: NOW
    _options(monkeypatch, tmp_path)
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("procurement_uow_factory", "procurement_stock_uow_factory", "purchasing_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


_NEXT = {"n": 2}


def formed_order(client, sessions, count: int = 2) -> tuple[str, dict[str, dict]]:
    """`count` fresh lines of one supplier order «Жду счет» (tests.procurement.test_api.formed_order, new lines each call)."""
    start = _NEXT["n"]
    _NEXT["n"] += count
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    created = [
        consume_procurement_requested(
            OutboxMessage(**{**message(n).__dict__, "payload": {**message(n).payload, "delivery_point": MOSCOW}}),
            factory(),
        )
        for n in range(start, start + count)
    ]
    ids = [str(line.id) for line in created]
    taken = client.post("/api/procurement/take", json={"line_ids": ids}).json()["lines"]
    saved = client.post("/api/procurement/facts", json={"rows": [
        {"line_id": item["id"], "version": item["version"], "supplier": "ООО Поставка"} for item in taken
    ]}).json()
    assert saved["ready"] == count, saved
    formed = client.post("/api/procurement/supplier-requests", json={"line_ids": ids}).json()
    (order,) = formed["orders"]
    return order["order_number"], {line["id"]: line for line in formed["lines"]}


def _order(client, number: str, scope: str = "mine") -> dict:
    response = client.get(BASE, params={"scope": scope})
    assert response.status_code == 200, response.text
    return next(order for order in response.json()["orders"] if order["order_number"] == number)


def _invoiced(client, sessions, count: int = 2) -> tuple[str, dict]:
    number, lines = formed_order(client, sessions, count)
    lines = price_rows(client, lines)
    body = client.post(f"/api/procurement/orders/{number}/invoice", json={
        "invoice_url": "https://files.test/invoice.pdf", "invoice_number": "СЧ-7", "invoice_date": "2026-09-16",
        "expected_arrival": "2026-09-25", "lines": refs(lines),
    })
    assert body.status_code == 200, body.text
    return number, {line["id"]: line for line in body.json()["lines"]}


def _facts(client, number: str, version: int, **fields):
    return client.patch(f"{BASE}/{number}/facts", json={"version": version, **fields})


def test_option_off_answers_404_and_a_viewer_cannot_write(orders_api, monkeypatch, tmp_path):
    client, sessions = orders_api
    number, _ = formed_order(client, sessions)
    _options(monkeypatch, tmp_path, **{"procurement.orders": False})
    for path in (BASE, f"{BASE}/followups", f"{BASE}/decisions", f"{BASE}/{number}"):
        assert client.get(path).status_code == 404, path
    assert _facts(client, number, 0, note="x").status_code == 404
    assert client.post(f"/api/procurement/orders/{number}/expected-arrival", json={
        "expected_arrival": "2026-09-30", "lines": [{"line_id": str(uuid4()), "version": 1}],
    }).status_code == 404
    _options(monkeypatch, tmp_path)
    _as("Петров", "view")
    assert client.get(BASE, params={"scope": "all"}).status_code == 200
    assert _facts(client, number, 0, note="x").status_code == 403


def test_the_order_walks_the_columns_with_its_facts_and_payment(orders_api):
    client, sessions = orders_api
    number, lines = formed_order(client, sessions)
    order = _order(client, number)
    assert (order["column"], order["supplier"], order["mine"], order["lines_count"]) == ("awaiting", "ООО Поставка", True, 2)
    assert order["sum_estimated"] is True and order["facts_version"] == 0

    number, lines = _invoiced(client, sessions)
    order = _order(client, number)
    assert order["column"] == "unpaid"
    assert order["invoices"] == [{"number": "СЧ-7", "date": "2026-09-16", "url": "https://files.test/invoice.pdf"}]
    assert (order["sum"], order["sum_estimated"], order["promised"], order["expected"]) == ("4880", False, "2026-09-25", "2026-09-25")
    assert all(line["pay_missing"] == [] for line in order["lines"])

    sent = _facts(client, number, 0, sent_to_pay_on="2026-09-20")
    assert sent.status_code == 200, sent.text
    order = sent.json()["order"]
    assert (order["column"], order["sent_to_pay_on"], order["facts_version"], order["facts_updated_by"]) == (
        "to_pay", "2026-09-20", 1, "Петров")
    # «Отменить» of the drop: the date cleared on the version the drop left.
    undone = _facts(client, number, 1, sent_to_pay_on=None).json()["order"]
    assert (undone["column"], undone["facts_version"]) == ("unpaid", 2)

    paid = client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(lines)})
    assert paid.status_code == 200, paid.text
    order = _order(client, number)
    assert (order["column"], order["paid_count"], order["paid_on"]) == ("waiting", 2, "2026-09-20")

    shipped = _facts(client, number, 2, shipped_on="2026-09-20", shipment_note="  СДЭК   1234567890 ")
    assert shipped.status_code == 200, shipped.text
    order = shipped.json()["order"]
    assert (order["column"], order["shipment_note"]) == ("transit", "СДЭК 1234567890")
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "purchasing.order_facts_saved")).all()
    assert len(events) == 3 and events[-1].details["fields"] == "Отгрузка, Транспорт, трек-номер"


def test_an_order_sits_in_its_earliest_line_stage_and_backorders_are_not_a_stage(orders_api):
    client, sessions = orders_api
    number, lines = _invoiced(client, sessions, 3)
    assert client.post(f"/api/procurement/orders/{number}/paid", json={"lines": refs(lines)}).status_code == 200
    first, second, third = sorted(UUID(line_id) for line_id in lines)
    with sessions() as session:
        session.get(ProcurementLineRow, first).status = "Готово"
        session.get(ProcurementLineRow, second).status = "Карантин"
        session.commit()
    order = _order(client, number)
    assert (order["column"], order["stages"], order["received_count"]) == (
        "transit", {"Готово": 1, "Карантин": 1, "В пути": 1}, 1)
    with sessions() as session:
        session.get(ProcurementLineRow, third).status = "Готово"
        row = session.get(ProcurementLineRow, first)
        backorder = ProcurementLineRow(**{
            column.name: getattr(row, column.name) for column in ProcurementLineRow.__table__.columns
            if column.name not in {"id", "source_event_id", "source_idempotency_key", "legacy_id", "status"}
        })
        backorder.id, backorder.source_event_id, backorder.source_idempotency_key = uuid4(), uuid4(), "bo:1"
        backorder.legacy_id, backorder.status, backorder.actual_sum = f"{row.legacy_id}-Д", "Согласование", Decimal("500")
        session.add(backorder)
        cancelled = session.get(ProcurementLineRow, second)
        cancelled.status = "Готово"
        session.commit()
    order = _order(client, number)
    assert order["column"] == "stocked"
    assert [line["legacy_id"] for line in order["backorders"]] == [backorder.legacy_id]
    assert order["sum"] == "7320" and order["lines_count"] == 3  # the backorder's 500 is not added twice


def test_facts_follow_roller_buyer_date_rules_and_belong_to_the_orders_buyer(orders_api):
    client, sessions = orders_api
    awaiting, _ = formed_order(client, sessions)
    no_invoice = _facts(client, awaiting, 0, sent_to_pay_on="2026-09-20")
    assert no_invoice.status_code == 422 and "ещё нет счёта" in no_invoice.json()["detail"]
    assert _facts(client, awaiting, 0, note="позвонить").status_code == 200  # a note needs no invoice

    number, _ = _invoiced(client, sessions)
    future = _facts(client, number, 0, shipped_on="2026-09-21")
    early = _facts(client, number, 0, sent_to_pay_on="2026-09-15")
    assert future.status_code == 422 and future.json()["detail"] == {"message": "Отгрузка не может быть в будущем", "field": "shipped_on"}
    assert early.status_code == 422 and "раньше даты счёта (16.09.2026)" in early.json()["detail"]["message"]
    assert _facts(client, number, 0, delivery_cost="-1").status_code == 422
    assert _facts(client, number, 0, note="x" * 2001).status_code == 422
    assert client.patch(f"{BASE}/PO-404/facts", json={"version": 0, "note": "x"}).status_code == 404

    saved = _facts(client, number, 0, delivery_cost="350.5")
    assert saved.status_code == 200 and saved.json()["order"]["delivery_cost"] == "350.5"
    stale = _facts(client, number, 0, note="опять с нуля")
    assert stale.status_code == 409 and "обновите" in stale.json()["detail"]

    _as("Сидоров")
    foreign = _facts(client, number, 1, note="чужой")
    assert foreign.status_code == 403 and "другого закупщика" in foreign.json()["detail"]
    others = _order(client, number, scope="all")
    assert others["mine"] is False and others["buyer"] == "Петров"
    assert client.get(BASE).json()["orders"] == []
    with sessions() as session:
        assert session.get(OrderFactRow, number).note == ""


def test_the_order_wide_expected_date_moves_every_line_and_the_promise_stays(orders_api):
    client, sessions = orders_api
    number, lines = _invoiced(client, sessions)
    url = f"/api/procurement/orders/{number}/expected-arrival"
    partial = client.post(url, json={"expected_arrival": "2026-09-30", "lines": refs(lines)[:1]})
    assert partial.status_code == 409 and "(2)" in partial.json()["detail"]
    moved = client.post(url, json={"expected_arrival": "2026-09-30", "lines": refs(lines)})
    assert moved.status_code == 200, moved.text
    assert {line["expected_arrival"] for line in moved.json()["lines"]} == {"2026-09-30"}
    order = _order(client, number)
    assert (order["promised"], order["expected"]) == ("2026-09-25", "2026-09-30")
    assert {line["promised"] for line in order["lines"]} == {"2026-09-25"}

    _as("Сидоров")
    foreign = client.post(url, json={"expected_arrival": "2026-10-01", "lines": refs({line["id"]: line for line in moved.json()["lines"]})})
    assert foreign.status_code == 404 and foreign.json()["detail"] == f"Заказ {number}: нет ваших строк"

    with sessions() as session:
        facts = order_facts(session, [number, "PO-404"])
    assert facts == {number: {"sent_to_pay_at": None, "shipped_at": None, "shipment_note": "", "promised_date": "2026-09-25"}}


def test_the_card_lists_lines_and_deliveries_and_followups_reuse_today(orders_api):
    client, sessions = orders_api
    number, lines = _invoiced(client, sessions)
    card = client.get(f"{BASE}/{number}")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["order"]["order_number"] == number and body["receiving"] == [] and body["upds"] is None
    assert client.get(f"{BASE}/PO-404").status_code == 404

    followups = client.get(f"{BASE}/followups").json()
    assert [(item["order_number"], item["count"]) for item in followups["unpaid"]] == [(number, 2)]
    assert followups["late"] == [] and followups["awaiting_quotes"] == []


def test_open_decisions_are_empty_without_requests_for_quotes(orders_api):
    client, _ = orders_api
    assert client.get(f"{BASE}/decisions").json() == {"suppliers": []}


def _facts_line(status: str, **extra) -> OrderLineFacts:
    base = dict(
        id=uuid4(), legacy_id="SM-1", version=1, status=status, order_number="PO-1", order_date=date(2026, 9, 1),
        buyer_id=None, buyer_name="", supplier="", name="", calculated_name="", invoice_item_name=None, article=None,
        project=None, quantity=Decimal("1"), unit="шт", price_with_vat=None, actual_sum=None, calculated_sum=None,
        currency=None, source_currency=None, source_sum=None, expected_arrival=None, promised=None,
        invoice_number=None, invoice_date=None, invoice_url=None, paid_on=None, actual_arrival=None,
        created_on=date(2026, 9, 1), note=None, is_backorder=False,
    )
    return OrderLineFacts(**{**base, **extra})


def test_column_rules():
    facts = OrderFactRow(order_number="PO-1", sent_to_pay_on=date(2026, 9, 2), shipped_on=None)
    assert column_of([_facts_line("В оплату"), _facts_line("Жду счет")], facts) == "awaiting"
    assert column_of([_facts_line("В оплату")], None) == "unpaid"
    assert column_of([_facts_line("В оплату"), _facts_line("В пути")], facts) == "to_pay"
    assert column_of([_facts_line("В пути")], facts) == "waiting"
    assert column_of([_facts_line("В пути", receipt=Receipt(accepted=Decimal("1"), rows=1))], None) == "transit"
    assert column_of([_facts_line("Карантин"), _facts_line("Готово")], None) == "receiving"
    assert column_of([_facts_line("Готово"), _facts_line("Согласование", is_backorder=True)], None) == "stocked"
    assert column_of([_facts_line("Согласование", is_backorder=True)], None) is None


def test_open_decisions_list_the_decided_lines_until_the_supplier_request(orders_api, monkeypatch, tmp_path):
    client, sessions = orders_api
    _options(monkeypatch, tmp_path, **{"procurement.rfq": True})
    start = _NEXT["n"]
    _NEXT["n"] += 1
    line = consume_procurement_requested(message(start), SqlAlchemyProcurementUnitOfWork(sessions))
    assert client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).status_code == 200
    rfq = client.post("/api/purchasing/rfqs", json={"line_ids": [str(line.id)]}).json()
    supplier = client.post(f"/api/purchasing/rfqs/{rfq['id']}/suppliers", json={"name": "ООО Опт"}).json()["supplier_id"]
    rfq_line = client.get(f"/api/purchasing/rfqs/{rfq['id']}").json()["lines"][0]
    quote = client.post(f"/api/purchasing/rfqs/{rfq['id']}/quotes", json={
        "supplier_id": supplier, "min_order_sum": "5000", "delivery_cost": "300",
        "lines": [{"rfq_line_id": rfq_line["id"], "unit_price": "100", "vat_included": False, "vat_rate": "20", "pack": "5"}],
    })
    assert quote.status_code == 201, quote.text
    cell = client.get(f"/api/purchasing/rfqs/{rfq['id']}").json()["lines"][0]["cells"][supplier]
    decided = client.post(f"/api/purchasing/rfqs/{rfq['id']}/decisions", json={
        "rfq_line_id": rfq_line["id"], "quote_line_id": cell["quote_line_id"], "gate_override_reason": "один поставщик",
    })
    assert decided.status_code == 201, decided.text

    (group,) = client.get(f"{BASE}/decisions").json()["suppliers"]
    assert (group["supplier"], group["min_order_sum"], group["delivery_cost"], group["currency"]) == ("ООО Опт", "5000", "300", "RUB")
    (item,) = group["lines"]
    assert (item["line_id"], item["need"], item["decided_quantity"], item["quantity"], item["unit_gross"]) == (
        str(line.id), "2", "5", "5", "120")
    assert (item["vat_rate"], item["vat_included"], item["pack"], item["blockers"]) == ("20", False, "5", [])

    formed = client.post("/api/procurement/supplier-requests", json={"line_ids": [str(line.id)]})
    assert formed.status_code == 200 and formed.json()["orders"], formed.text
    assert client.get(f"{BASE}/decisions").json() == {"suppliers": []}
