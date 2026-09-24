from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.operations.domain import Notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import SqlAlchemyAuditRepository, create_schema


@pytest.fixture()
def operations_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyOperationsUnitOfWork(sessions)
    app.state.operations_uow_factory = factory
    actor = Actor(UUID("10000000-0000-0000-0000-000000000001"), "Сотрудник", "КБ")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor,
        email="staff@example.test",
        roles=frozenset({"staff"}),
        permissions={},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 12, 0)
    with factory() as uow:
        uow.notifications.add(Notification.create(
            recipient_email="staff@example.test",
            created_at=datetime(2026, 9, 22, 11, 0),
            kind="status",
            reference="SM-1",
            title="Ваш статус изменился",
            body="Заявка передана в закупку",
            dedupe_key="one:staff",
        ))
        uow.notifications.add(Notification.create(
            recipient_email="other@example.test",
            created_at=datetime(2026, 9, 22, 11, 0),
            kind="status",
            reference="SM-2",
            title="Чужое уведомление",
            body="Скрыто",
            dedupe_key="one:other",
        ))
        uow.commit()
    with sessions() as session:
        SqlAlchemyAuditRepository(session).add(AuditEvent(
            id=UUID("30000000-0000-0000-0000-000000000001"),
            aggregate_id=UUID("40000000-0000-0000-0000-000000000001"),
            event_type="request.created",
            actor=actor,
            occurred_at=datetime(2026, 9, 22, 10, 0),
            details={"legacy_id": "SM-1"},
        ))
        session.commit()
    yield TestClient(app), actor
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.operations_uow_factory
    engine.dispose()


def test_inbox_is_scoped_to_session_and_can_mark_all_read(operations_api):
    client, _actor = operations_api
    listed = client.get("/api/notifications")
    read = client.post("/api/notifications/read", json={"ids": []})

    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()["notifications"]] == ["Ваш статус изменился"]
    assert listed.json()["unread"] == 1
    assert read.json() == {"unread": 0}


def test_audit_is_restricted_to_management_and_admin(operations_api):
    client, actor = operations_api
    forbidden = client.get("/api/operations/audit")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor,
        email="admin@example.test",
        roles=frozenset({"admin"}),
        permissions={"operations": "edit"},
    )
    allowed = client.get("/api/operations/audit", params={"event_type": "request"})
    health = client.get("/api/operations/outbox")

    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["events"][0]["event_type"] == "request.created"
    assert health.json()["dead"] == 0
