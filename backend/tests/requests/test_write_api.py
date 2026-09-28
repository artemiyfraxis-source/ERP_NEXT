from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.requests.api import Principal, authenticated_principal, moscow_now
from app.modules.requests.infrastructure import (
    OutboxMessageRow,
    RequestRow,
    SqlAlchemyUnitOfWork,
    create_schema,
)
from tests.factories import Actor


def request_payload(name: str = "Разъём") -> dict:
    return {
        "purpose": "RND",
        "project_reference": "591К.3",
        "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
        "name": name,
        "item_type": "Комплектующие",
        "quantity": "2",
        "unit": "шт",
        "quality_document": "Нет",
        "procurement_deadline": "2026-09-18",
        "planned_date": "2026-09-25",
    }


@pytest.fixture()
def write_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"employee"}),
        permissions={"requests": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 10, 30)
    yield TestClient(app), engine, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.uow_factory
    engine.dispose()


def test_create_requires_authenticated_server_session(write_api):
    client, _, _ = write_api
    app.dependency_overrides.pop(authenticated_principal)

    response = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:unauthenticated"},
        json={"requests": [request_payload()]},
    )

    assert response.status_code == 401


def test_create_uses_server_actor_and_is_idempotent(write_api):
    client, engine, sessions = write_api
    headers = {"Idempotency-Key": "create:browser:42"}
    payload = {"requests": [request_payload(), request_payload("Кабель")]}

    first = client.post("/api/requests", headers=headers, json=payload)
    retry = client.post("/api/requests", headers=headers, json=payload)

    assert first.status_code == 201, first.text
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()
    listed = client.get("/api/requests")
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["requests"]] == ["Разъём", "Кабель"]
    with sessions() as session:
        rows = list(session.scalars(select(RequestRow).order_by(RequestRow.creation_index)))
        assert len(rows) == 2
        assert all(row.actor_name == "Иванов" for row in rows)


def test_create_requires_idempotency_key(write_api):
    client, _, _ = write_api

    response = client.post("/api/requests", json={"requests": [request_payload()]})

    assert response.status_code == 422


def test_request_body_limit_rejects_oversized_payload(write_api):
    client, _, _ = write_api
    response = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:oversized"},
        content="x" * 1_048_577,
    )

    assert response.status_code == 413


def test_handoff_requires_buyer_and_retry_does_not_duplicate_outbox(write_api):
    client, engine, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:handoff:42"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]

    forbidden = client.post("/api/requests/handoff", json={"request_ids": [request_id]})
    assert forbidden.status_code == 403

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    first = client.post("/api/requests/handoff", json={"request_ids": [request_id]})
    retry = client.post("/api/requests/handoff", json={"request_ids": [request_id]})

    assert first.status_code == 202, first.text
    assert retry.status_code == 202, retry.text
    assert retry.json() == first.json()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 1
        assert session.get(RequestRow, UUID(request_id)).procurement_actor_name == "Петров"


def test_retransfer_is_an_explicit_flag_and_refused_for_a_new_request(write_api):
    client, engine, sessions = write_api
    created = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "create:retransfer:1"},
        json={"requests": [request_payload()]},
    ).json()
    request_id = created["requests"][0]["id"]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )

    too_early = client.post("/api/requests/handoff", json={"request_ids": [request_id], "retransfer": True})
    first = client.post("/api/requests/handoff", json={"request_ids": [request_id]})
    again = client.post("/api/requests/handoff", json={"request_ids": [request_id], "retransfer": True})

    assert too_early.status_code == 422, too_early.text
    assert first.status_code == 202, first.text
    assert again.status_code == 202, again.text
    assert again.json() != first.json()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 2


def test_create_rejects_project_outside_open_registry_and_stores_canonical_number(write_api):
    client, _, _ = write_api

    class Registry:
        def open_projects(self):
            return ["591К.3", "2000К.1"]

    app.state.project_registry = Registry()
    try:
        rejected = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:closed"},
            json={"requests": [{**request_payload(), "project_reference": "48К.13"}]},
        )
        latin = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:latin"},
            json={"requests": [{**request_payload(), "project_reference": "591K.3"}]},
        )
        accepted = client.post(
            "/api/requests",
            headers={"Idempotency-Key": "create:project:open"},
            json={"requests": [{**request_payload(), "project_reference": "2000к"}]},
        )
    finally:
        del app.state.project_registry

    assert rejected.status_code == 422
    assert "48К.13" in rejected.json()["detail"]
    assert latin.status_code == 422
    assert "№ ПРОЕКТА" in latin.json()["detail"]
    assert accepted.status_code == 201
    assert accepted.json()["requests"][0]["project_reference"] == "2000К.1"
