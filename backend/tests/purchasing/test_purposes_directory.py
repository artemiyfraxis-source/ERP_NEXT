"""Purposes directory («Настройки закупки», key `purposes`) and the project-owned purpose of a request
: commercial project → «Коммерческий проект», internal ВП → its own purpose."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.purchasing import settings
from app.modules.purchasing.infrastructure import PurchasingSettingRow, SqlAlchemyPurchasingUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import Base, create_schema
from app.workflows.project_registry import InternalAwareProjectRegistry
from tests.factories import Actor
from tests.requests.test_write_api import request_payload

NOW = datetime(2026, 10, 6, 10, 0)
COMMERCIAL = "Коммерческий проект"


def test_default_directory_and_labels_source():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[PurchasingSettingRow.__table__])
    with Session(engine) as session:
        purposes = settings.load(session)["purposes"]
    assert purposes[:2] == ["RND", "FRX"] and "Оснастка и инструмент" in purposes and "ИТ и офис" in purposes
    assert len(purposes) == 12 and COMMERCIAL not in purposes


def test_save_adds_removes_and_protects():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[PurchasingSettingRow.__table__])
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    with Session(engine) as session:
        base = settings.load(session)["purposes"]
        saved = settings.save(session, {"purposes": [*base, " Лаборатория  "]}, None, now)["purposes"]
        assert saved[-1] == "Лаборатория"
        with pytest.raises(settings.InvalidSetting, match="повторяется"):
            settings.save(session, {"purposes": [*saved, "лаборатория"]}, None, now)
        with pytest.raises(settings.InvalidSetting, match="Коммерческий"):
            settings.save(session, {"purposes": [*saved, COMMERCIAL]}, None, now)
        with pytest.raises(settings.InvalidSetting, match="убрать нельзя"):
            settings.save(session, {"purposes": [name for name in saved if name != "RND"]}, None, now)
        # a used purpose cannot go, an unused one can
        in_use = lambda _session: {"склад"}
        with pytest.raises(settings.InvalidSetting, match="используется"):
            settings.save(session, {"purposes": [name for name in saved if name != "Склад"]}, None, now, in_use)
        trimmed = settings.save(session, {"purposes": [name for name in saved if name != "Лаборатория"]}, None, now, in_use)
        assert "Лаборатория" not in trimmed["purposes"]


def _as(roles: set[str], permissions: dict) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="petrov@example.test", roles=frozenset(roles),
        permissions=permissions,
    )


class PlanRegistry:
    def open_projects(self):
        return ["591К.3"]


@pytest.fixture()
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.project_registry = InternalAwareProjectRegistry(PlanRegistry(), app.state.purchasing_uow_factory)
    _as({"admin"}, {})
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), monkeypatch, tmp_path
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("purchasing_uow_factory", "uow_factory", "project_registry"):
        delattr(app.state, name)
    engine.dispose()


def _post(client, key, **fields):
    return client.post(
        "/api/requests", headers={"Idempotency-Key": key}, json={"requests": [{**request_payload(), **fields}]},
    )


def test_commercial_project_takes_only_the_commercial_purpose(api):
    client, _, _ = api
    assert _post(client, "a", purpose=COMMERCIAL).status_code == 201
    refused = _post(client, "b", purpose="Склад")
    assert refused.status_code == 422 and "коммерческий" in refused.json()["detail"]
    assert _post(client, "c", purpose="RND").status_code == 422
    assert _post(client, "d", purpose=COMMERCIAL, project_reference="").status_code == 422


def test_internal_project_uses_a_directory_purpose(api):
    client, _, _ = api
    base = "/api/purchasing/internal-projects"
    created = client.post(base, json={"name": "Оснастка цеха", "purpose": "Оснастка и инструмент"})
    assert created.status_code == 201, created.text
    ok = _post(client, "e", purpose="Оснастка и инструмент", project_reference="ВП-001")
    assert ok.status_code == 201, ok.text
    assert _post(client, "f", purpose="Склад", project_reference="ВП-001").status_code == 422
    assert client.post(base, json={"name": "x", "purpose": COMMERCIAL}).status_code == 422
    assert "Оснастка и инструмент" in client.get("/api/requests/schema").json()["purposes"]


def test_used_purpose_cannot_be_removed_over_http(api):
    client, _, _ = api
    client.post("/api/purchasing/internal-projects", json={"name": "Оснастка", "purpose": "Оснастка и инструмент"})
    rows = client.get("/api/purchasing/settings").json()
    current = next(row for row in rows if row["key"] == "purposes")["value"]
    without = [name for name in current if name != "Оснастка и инструмент"]
    refused = client.put("/api/purchasing/settings", json={"changes": {"purposes": without}})
    assert refused.status_code == 422 and "используется" in refused.json()["detail"]
    free = [name for name in current if name != "ИТ и офис"]
    assert client.put("/api/purchasing/settings", json={"changes": {"purposes": free}}).status_code == 200


