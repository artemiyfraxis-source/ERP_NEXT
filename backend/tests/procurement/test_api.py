from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
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
