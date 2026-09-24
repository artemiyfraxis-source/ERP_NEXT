from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.receiving.application import consume_receiving_expected
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


def expected_message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"10000000-0000-0000-0000-{suffix:012d}"),
        topic="receiving.expected",
        idempotency_key=f"procurement:{suffix}:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": f"20000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260921-{suffix:02d}",
            "warehouse": "ЦАР",
            "article": "CON-001",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "goal": "",
            "source_link": "",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
            "quality_document_required": False,
        },
    )


@pytest.fixture()
def receiving_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    app.state.receiving_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Приёмщик", department="ОТК"),
        email="receiver@example.test",
        roles=frozenset({"receiver"}),
        permissions={"receiving": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 21, 11, 0)
    line = consume_receiving_expected(expected_message(), factory())
    yield TestClient(app), line
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.receiving_uow_factory
    engine.dispose()


def test_receiving_requires_receiver_role(receiving_api):
    client, _ = receiving_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"receiving": "edit"},
    )

    assert client.get("/api/receiving").status_code == 403


def test_receiving_queue_has_stable_cursor_pages(receiving_api):
    client, first_line = receiving_api
    factory = app.state.receiving_uow_factory
    second_line = consume_receiving_expected(expected_message(2), factory())

    first_page = client.get("/api/receiving?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/receiving?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    assert {
        first_page.json()["lines"][0]["id"],
        second_page.json()["lines"][0]["id"],
    } == {str(first_line.id), str(second_line.id)}


def test_documents_are_derived_from_urls_and_accepted_line_is_placed(receiving_api):
    client, line = receiving_api
    base = {
        "fact_quantity": "10",
        "document_quantity": "10",
        "expiry_date": "2027-01-01",
    }

    waiting = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={**base, "version": line.version, "has_shipping_document": True},
    )
    accepted = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            **base,
            "version": waiting.json()["line"]["version"],
            "shipping_document_url": "https://files.test/upd.pdf",
            "series": "LOT-2409",
        },
    )
    placed = client.post(
        f"/api/receiving/{line.id}/place",
        json={
            "version": accepted.json()["line"]["version"],
            "storage_location": "1F4",
        },
    )

    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["line"]["status"] == "Ожидание"
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["line"]["status"] == "Принято"
    assert accepted.json()["line"]["article"] == "CON-001"
    assert accepted.json()["line"]["series"] == "LOT-2409"
    assert placed.status_code == 200, placed.text
    assert placed.json()["line"]["warehouse_event_emitted"] is True
    assert placed.json()["line"]["storage_location"] == "1F4"

    reinspection = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            **base,
            "version": placed.json()["line"]["version"],
            "shipping_document_url": "https://files.test/replacement.pdf",
        },
    )
    relocation = client.post(
        f"/api/receiving/{line.id}/place",
        json={
            "version": placed.json()["line"]["version"],
            "storage_location": "1F5",
        },
    )

    assert reinspection.status_code == 422
    assert relocation.status_code == 422


@pytest.mark.parametrize(
    "document_url",
    ["x", "javascript:alert(1)", "file:///tmp/upd.pdf"],
)
def test_receiving_rejects_non_http_document_urls(receiving_api, document_url):
    client, line = receiving_api

    response = client.post(
        f"/api/receiving/{line.id}/inspect",
        json={
            "version": line.version,
            "fact_quantity": "10",
            "document_quantity": "10",
            "shipping_document_url": document_url,
        },
    )

    assert response.status_code == 422
