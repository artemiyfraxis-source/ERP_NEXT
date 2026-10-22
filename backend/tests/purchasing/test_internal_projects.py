"""«Внутренние проекты» (stage option projects.internal) over HTTP: numbering, edit, close/reopen, the option gate,
requests accepting only open projects, the schema group and the GRN prefix by the project's purpose."""

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.persistence import AuditEventRow, create_schema
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows.project_registry import InternalAwareProjectRegistry
from tests.factories import Actor
from tests.requests.test_write_api import request_payload

BASE = "/api/purchasing/internal-projects"
NOW = datetime(2026, 10, 6, 10, 0)


def _as(permissions: dict) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="petrov@example.test", roles=frozenset({"buyer"}),
        permissions=permissions,
    )


class PlanRegistry:
    def open_projects(self):
        return ["591К.3"]


@pytest.fixture()
def internal_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.project_registry = InternalAwareProjectRegistry(PlanRegistry(), app.state.purchasing_uow_factory)
    _as({"procurement": "edit", "newrequest": "edit", "requests": "view"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("purchasing_uow_factory", "uow_factory", "project_registry"):
        delattr(app.state, name)
    engine.dispose()


def _create(client, name="Стенд испытаний", purpose="RND", **extra) -> dict:
    response = client.post(BASE, json={"name": name, "purpose": purpose, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_numbers_are_sequential_unique_and_the_purpose_comes_from_the_list(internal_api):
    client, _ = internal_api
    first = _create(client)
    second = _create(client, "Ремонт цеха", "Ремонт и обслуживание")
    assert (first["number"], second["number"]) == ("ВП-001", "ВП-002")
    assert _create(client, "Ещё", "rnd")["purpose"] == "RND"
    assert client.post(BASE, json={"name": "x", "purpose": "Выдумка"}).status_code == 422
    assert client.post(BASE, json={"name": "  ", "purpose": "RND"}).status_code == 422
    assert client.post(BASE, json={"name": "x", "purpose": ""}).status_code == 422


def test_patch_needs_the_version_and_close_reopen_toggle(internal_api):
    client, sessions = internal_api
    project = _create(client, note="  заметка ")
    assert project["note"] == "заметка"
    patched = client.patch(f"{BASE}/{project['id']}", json={"version": 1, "name": "Новое имя", "note": None})
    assert patched.status_code == 200 and patched.json()["version"] == 2 and patched.json()["note"] is None
    assert client.patch(f"{BASE}/{project['id']}", json={"version": 1, "name": "Старая"}).status_code == 409
    assert client.patch(f"{BASE}/{uuid4()}", json={"version": 1}).status_code == 404

    closed = client.post(f"{BASE}/{project['id']}/close")
    assert closed.status_code == 200 and closed.json()["closed"] is True
    assert client.post(f"{BASE}/{project['id']}/close").status_code == 409
    reopened = client.post(f"{BASE}/{project['id']}/reopen")
    assert reopened.status_code == 200 and reopened.json()["closed"] is False
    assert client.post(f"{BASE}/{project['id']}/reopen").status_code == 409

    with sessions() as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow).order_by(AuditEventRow.occurred_at))]
    assert sorted(events) == sorted([
        "purchasing.internal_project_created", "purchasing.internal_project_updated",
        "purchasing.internal_project_closed", "purchasing.internal_project_reopened",
    ])


def test_list_puts_open_first_with_counts(internal_api):
    client, _ = internal_api
    one, two = _create(client), _create(client, "Второй", "FRX")
    client.post(f"{BASE}/{one['id']}/close")
    created = client.post(
        "/api/requests", headers={"Idempotency-Key": "k1"},
        json={"requests": [{**request_payload(), "purpose": "FRX", "project_reference": "ВП-002"}]},
    )
    assert created.status_code == 201, created.text
    listed = client.get(BASE).json()["projects"]
    assert [item["number"] for item in listed] == ["ВП-002", "ВП-001"]
    assert listed[0]["open_requests"] == 1 and listed[0]["lines"] == {} and listed[0]["closed"] is False
    assert listed[1]["closed"] is True and two["number"] == "ВП-002"


def test_the_gate_refuses_strangers_and_viewers_cannot_write(internal_api):
    client, _ = internal_api
    _as({})
    assert client.get(BASE).status_code == 403
    _as({"requests": "view"})
    assert client.get(BASE).status_code == 200
    assert client.post(BASE, json={"name": "x", "purpose": "RND"}).status_code == 403  # viewing is not editing


def test_requests_accept_an_open_project_and_refuse_a_closed_one(internal_api):
    client, _ = internal_api
    project = _create(client, purpose="Хоз деятельность")

    def post(key, **fields):
        return client.post(
            "/api/requests", headers={"Idempotency-Key": key},
            json={"requests": [{**request_payload(), "purpose": "Хоз деятельность", "project_reference": "вп-001", **fields}]},
        )

    accepted = post("open")
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["requests"][0]["project_reference"] == "ВП-001"
    assert post("wrong", purpose="RND").status_code == 422  # the purpose is the project's
    client.post(f"{BASE}/{project['id']}/close")
    refused = post("closed")
    assert refused.status_code == 422 and "закрыт" in refused.json()["detail"]
    assert post("unknown", project_reference="ВП-777").status_code == 422


def test_the_request_schema_lists_internal_projects_in_both_places(internal_api):
    client, _ = internal_api
    open_project, closed = _create(client, "Открытый"), _create(client, "Закрытый", "FRX")
    client.post(f"{BASE}/{closed['id']}/close")
    schema = client.get("/api/requests/schema").json()
    assert schema["projects"] == ["591К.3", "ВП-001"]
    assert schema["internal_projects"] == [{"number": "ВП-001", "name": "Открытый", "purpose": "RND"}]
    assert open_project["number"] == "ВП-001"


