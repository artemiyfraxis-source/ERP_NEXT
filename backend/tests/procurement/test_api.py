from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested, dispatch_next_backorder_requested
from app.modules.procurement.infrastructure import (
    ProcurementLineRow,
    SqlAlchemyProcurementUnitOfWork,
)
from app.modules.requests.api import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor


def message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        topic="procurement.requested",
        idempotency_key=f"request:{suffix}:procurement:v2",
        created_at=datetime(2026, 9, 15, 11, 0),
        payload={
            "source_request_id": f"10000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260915-{suffix:02d}",
            "request_date": "2026-09-15",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": f"RND.{suffix:03d}",
            "project_reference": None,
            "priority": "Очень срочно",
            "delivery_point": "Москва",
            "planned_date": "2026-09-25",
            "link": None,
            "item_type": "Комплектующие",
            "design_article": None,
            "category": "Общепром",
            "calculated_name": "Разъём",
            "calculated_quantity": "2",
            "calculated_unit": "шт",
            "calculated_price": None,
            "calculated_price_with_vat": None,
            "calculated_sum": None,
        },
    )


@pytest.fixture()
def procurement_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 11, 30)
    line = consume_procurement_requested(message(), factory())
    yield TestClient(app), line, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    engine.dispose()


def test_procurement_routes_require_authenticated_buyer(procurement_api):
    client, _, _ = procurement_api
    app.dependency_overrides.pop(authenticated_principal)
    assert client.get("/api/procurement/available").status_code == 401

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"employee"}),
        permissions={"procurement": "edit"},
    )
    assert client.get("/api/procurement/available").status_code == 403


def test_available_queue_uses_bounded_cursor_pages(procurement_api):
    client, first_line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    second_line = consume_procurement_requested(message(2), factory())

    first_page = client.get("/api/procurement/available?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/procurement/available?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    ids = {first_page.json()["lines"][0]["id"], second_page.json()["lines"][0]["id"]}
    assert ids == {str(first_line.id), str(second_line.id)}
    assert client.get("/api/procurement/available?limit=0").status_code == 422

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={},
    )
    assert client.get("/api/procurement/available").status_code == 403


def test_buyer_can_take_and_complete_own_line_idempotently(procurement_api):
    client, line, sessions = procurement_api

    available = client.get("/api/procurement/available")
    first_take = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    retry_take = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    mine = client.get("/api/procurement/mine")
    facts = {
        "version": first_take.json()["lines"][0]["version"],
        "supplier": "ООО Поставка",
        "actual_quantity": "2",
        "actual_unit": "шт",
    }
    first_save = client.patch(f"/api/procurement/{line.id}/facts", json=facts)
    retry_save = client.patch(f"/api/procurement/{line.id}/facts", json=facts)
    stale_overwrite = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={**facts, "supplier": "Устаревшее значение"},
    )

    assert available.status_code == 200
    assert available.json()["lines"][0]["status"] == "Новый"
    assert first_take.status_code == 200, first_take.text
    assert retry_take.json() == first_take.json()
    assert mine.json()["lines"][0]["buyer"] == "Петров"
    assert first_save.status_code == 200, first_save.text
    assert first_save.json()["line"]["status"] == "Готов к запросу"
    assert retry_save.json() == first_save.json()
    assert stale_overwrite.status_code == 409
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 3


def test_other_buyer_cannot_edit_claimed_line(procurement_api):
    client, line, _ = procurement_api
    client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"),
        email="sidorov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )

    response = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": 2, "supplier": "Чужой поставщик"},
    )

    assert response.status_code == 422
    assert "только свои" in response.json()["detail"]


def test_explicit_empty_or_oversized_supplier_is_rejected(procurement_api):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    version = taken.json()["lines"][0]["version"]

    empty = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": version, "supplier": "   "},
    )
    oversized = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": version, "supplier": "x" * 513},
    )

    assert empty.status_code == 422
    assert oversized.status_code == 422


@pytest.mark.parametrize(
    "invoice_url",
    ["x", "javascript:alert(1)", "https://user:password@files.test/invoice.pdf"],
)
def test_invoice_requires_safe_absolute_http_url(procurement_api, invoice_url):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    )
    requested = client.post(
        f"/api/procurement/{line.id}/supplier-request",
        json={"version": ready.json()["line"]["version"], "reference": "REQ-2026-1"},
    )

    response = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={
            "version": requested.json()["line"]["version"],
            "invoice_url": invoice_url,
            "expected_arrival": "2026-09-25",
        },
    )

    assert response.status_code == 422


class StubRates:
    def __init__(self, rates=None):
        self.rates = dict(rates or {})

    def rate_to_rub(self, currency, day):
        return self.rates.get((currency, day.isoformat()))


def awaiting_invoice(client, line) -> int:
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    ready = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={
            "version": taken.json()["lines"][0]["version"],
            "supplier": "ООО Поставка",
            "actual_quantity": "2",
            "actual_unit": "шт",
        },
    )
    requested = client.post(
        f"/api/procurement/{line.id}/supplier-request",
        json={"version": ready.json()["line"]["version"], "reference": "REQ-2026-1"},
    )
    return requested.json()["line"]["version"]


@pytest.fixture()
def rates():
    stub = StubRates()
    app.state.procurement_exchange_rates = stub
    yield stub
    del app.state.procurement_exchange_rates


def test_invoice_waits_for_fact_money_typed_as_in_invoices(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    invoice = {"invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"}

    refused = client.post(f"/api/procurement/{line.id}/invoice", json={"version": version, **invoice})
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Нельзя загрузить счёт — сначала заполните: ВАЛЮТА")

    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={
            "version": version, "currency": "rub", "price": "1 234,50", "vat_rate": "22%",
            "invoice_item_name": "Разъём SMA", "invoice_number": "17", "invoice_date": "2026-09-14",
        },
    )
    assert saved.status_code == 200
    body = saved.json()["line"]
    assert (body["currency"], body["actual_price"], body["actual_price_with_vat"], body["actual_sum"]) == (
        "RUB", "1234.5", "1506.09", "3012.18",
    )
    assert body["pay_missing"] == ["ОЖИД. ДАТА"]

    invoiced = client.post(f"/api/procurement/{line.id}/invoice", json={"version": body["version"], **invoice})
    assert invoiced.status_code == 200
    assert invoiced.json()["line"]["status"] == "В оплату"


@pytest.mark.parametrize("field,value", [("price", "abc"), ("price", "-1"), ("sum", "1.005")])
def test_money_rejects_garbage_negative_and_sub_kopeck_sum(procurement_api, rates, field, value):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)

    response = client.patch(
        f"/api/procurement/{line.id}/money", json={"version": version, "currency": "RUB", field: value},
    )

    assert response.status_code == 422


def test_cbr_outage_returns_manual_rate_prompt_then_marks_manual_rate(procurement_api, rates):
    client, line, _ = procurement_api
    version = awaiting_invoice(client, line)
    usd = {"currency": "USD", "price": "10", "vat_rate": "22", "invoice_item_name": "SMA"}

    prompt = client.patch(f"/api/procurement/{line.id}/money", json={"version": version, **usd})
    assert prompt.status_code == 409
    assert prompt.json()["detail"] == {
        "code": "manual_rate_required",
        "currency": "USD",
        "date": "2026-09-15",
        "message": "ЦБ не отдал курс USD на 15.09.2026 — введите курс вручную",
    }

    manual = client.patch(
        f"/api/procurement/{line.id}/money",
        json={"version": version, **usd, "manual_exchange_rate": "80,5"},
    )
    body = manual.json()["line"]
    assert (body["exchange_rate"], body["exchange_rate_manual"], body["actual_price"]) == (
        "80.5", True, "857.33",
    )

    stale = client.post(f"/api/procurement/{line.id}/exchange-rate/reconcile", json={"version": body["version"]})
    assert stale.status_code == 503
    rates.rates[("USD", "2026-09-15")] = Decimal("90")
    reconciled = client.post(
        f"/api/procurement/{line.id}/exchange-rate/reconcile", json={"version": body["version"]},
    )
    assert reconciled.status_code == 200
    assert (reconciled.json()["line"]["exchange_rate_manual"], reconciled.json()["line"]["actual_price"]) == (
        False, "958.5",
    )


def test_backorder_is_confirmed_through_api_with_version_check(procurement_api, rates):
    client, line, sessions = procurement_api
    with sessions() as session:  # payment needs a delivery point with a receiving warehouse
        session.get(ProcurementLineRow, line.id).delivery_point = "г. Москва, ул. 6-я Радиальная, д. 17"
        session.commit()
    version = awaiting_invoice(client, line)
    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={"version": version, "currency": "RUB", "price": "100", "vat_rate": "22", "invoice_item_name": "SMA"},
    ).json()["line"]
    invoiced = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={"version": saved["version"], "invoice_url": "https://files.test/invoice.pdf", "expected_arrival": "2026-09-25"},
    ).json()["line"]
    paid = client.post(f"/api/procurement/{line.id}/paid", json={"version": invoiced["version"]}).json()["line"]
    assert paid["is_backorder"] is False
    refused = client.post(
        f"/api/procurement/{line.id}/backorder/confirm", json={"version": paid["version"], "expected_arrival": "2026-10-01"},
    )
    assert refused.status_code == 422

    uow = SqlAlchemyProcurementUnitOfWork(sessions)
    with uow:
        uow.outbox.add(
            OutboxMessage(
                id=UUID("00000000-0000-0000-0000-00000000b001"),
                topic="procurement.backorder_requested",
                idempotency_key="receiving:api:backorder",
                created_at=datetime(2026, 9, 20, 10, 0),
                payload={"procurement_line_id": str(line.id), "backorder_quantity": "1"},
            )
        )
        uow.commit()
    backorder = dispatch_next_backorder_requested(datetime(2026, 9, 20, 10, 1), SqlAlchemyProcurementUnitOfWork(sessions))
    mine = client.get("/api/procurement/mine").json()["lines"]
    listed = next(item for item in mine if item["id"] == str(backorder.id))
    assert (listed["legacy_id"], listed["status"], listed["is_backorder"], listed["actual_sum"]) == (
        "SM-20260915-01-Д", "Согласование", True, "122",
    )

    stale = client.post(
        f"/api/procurement/{backorder.id}/backorder/confirm", json={"version": 9, "expected_arrival": "2026-10-01"},
    )
    confirmed = client.post(
        f"/api/procurement/{backorder.id}/backorder/confirm", json={"version": 1, "expected_arrival": "2026-10-01"},
    )
    assert stale.status_code == 409
    assert confirmed.status_code == 200
    assert (confirmed.json()["line"]["status"], confirmed.json()["line"]["expected_arrival"]) == ("В пути", "2026-10-01")
