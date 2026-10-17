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


def test_bell_counts_only_the_session_users_unread(operations_api):
    client, _actor = operations_api
    assert client.get("/api/notifications/unread").json() == {"unread": 1}
    client.post("/api/notifications/read", json={"ids": []})
    assert client.get("/api/notifications/unread").json() == {"unread": 0}


def _inbox(email: str):
    with app.state.operations_uow_factory() as uow:
        return uow.notifications.list_for(email, 100)


def _add_notification(email: str, key: str, *, read: bool) -> None:
    item = Notification.create(
        recipient_email=email, created_at=datetime(2026, 9, 22, 11, 30), kind="digest",
        reference="", title=f"Сводка {key}", body="…", dedupe_key=key,
    )
    with app.state.operations_uow_factory() as uow:
        uow.notifications.add(item.mark_read(datetime(2026, 9, 22, 12, 0)) if read else item)
        uow.commit()


def test_user_deletes_own_notification_and_the_bell_count_follows(operations_api):
    client, _actor = operations_api
    _add_notification("staff@example.test", "two:staff", read=True)
    own = next(item for item in _inbox("staff@example.test") if item.title == "Ваш статус изменился")

    deleted = client.delete(f"/api/notifications/{own.id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"unread": 0}
    assert [item.title for item in _inbox("staff@example.test")] == ["Сводка two:staff"]
    assert client.get("/api/notifications/unread").json() == {"unread": 0}
    assert client.delete(f"/api/notifications/{own.id}").status_code == 404


def test_user_cannot_delete_someone_elses_notification(operations_api):
    client, _actor = operations_api
    foreign = _inbox("other@example.test")[0]

    response = client.delete(f"/api/notifications/{foreign.id}")

    assert response.status_code == 404
    assert [item.id for item in _inbox("other@example.test")] == [foreign.id]
    assert client.delete("/api/notifications/00000000-0000-0000-0000-00000000dead").status_code == 404
    assert client.delete("/api/notifications/not-a-uuid").status_code == 422


def test_delete_read_keeps_unread_and_never_touches_other_users(operations_api):
    client, _actor = operations_api
    _add_notification("staff@example.test", "two:staff", read=True)
    _add_notification("other@example.test", "two:other", read=True)

    response = client.delete("/api/notifications", params={"scope": "read"})

    assert response.json() == {"deleted": 1, "unread": 1}
    assert [item.title for item in _inbox("staff@example.test")] == ["Ваш статус изменился"]
    assert len(_inbox("other@example.test")) == 2


def test_delete_all_empties_only_the_own_inbox(operations_api):
    client, _actor = operations_api
    _add_notification("staff@example.test", "two:staff", read=True)

    response = client.delete("/api/notifications", params={"scope": "all"})

    assert response.json() == {"deleted": 2, "unread": 0}
    assert _inbox("staff@example.test") == []
    assert [item.title for item in _inbox("other@example.test")] == ["Чужое уведомление"]
    assert client.delete("/api/notifications", params={"scope": "all"}).json() == {"deleted": 0, "unread": 0}
    assert client.get("/api/notifications").json() == {"notifications": [], "unread": 0}


def test_delete_selected_skips_foreign_ids(operations_api):
    client, _actor = operations_api
    _add_notification("staff@example.test", "two:staff", read=True)
    _add_notification("staff@example.test", "three:staff", read=False)
    own = [item for item in _inbox("staff@example.test") if item.title != "Ваш статус изменился"]
    foreign = _inbox("other@example.test")[0]

    response = client.delete("/api/notifications", params={"id": [str(own[0].id), str(own[1].id), str(foreign.id)]})

    assert response.json() == {"deleted": 2, "unread": 1}
    assert [item.title for item in _inbox("staff@example.test")] == ["Ваш статус изменился"]
    assert [item.id for item in _inbox("other@example.test")] == [foreign.id]


def test_delete_many_needs_exactly_one_selector(operations_api):
    client, _actor = operations_api
    own = _inbox("staff@example.test")[0]
    assert client.delete("/api/notifications").status_code == 422
    assert client.delete("/api/notifications", params={"scope": "everything"}).status_code == 422
    assert client.delete("/api/notifications", params={"scope": "all", "id": str(own.id)}).status_code == 422
    assert client.delete("/api/notifications", params={"id": "not-a-uuid"}).status_code == 422
    assert len(_inbox("staff@example.test")) == 1


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
