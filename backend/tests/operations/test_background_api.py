"""Audit U08 over HTTP: «Системный журнал» shows the worker's heartbeats and the age of the queue, an administrator
retries a stopped job with a journalled, idempotent command, and /api/health?detail=1 turns red for a monitor."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app, signed_in_principal
from app.modules.operations.background import JOB_GROUPS
from app.modules.operations.infrastructure import WorkerHeartbeatRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.operations.test_api import operations_api  # noqa: F401  (fixture)

NOW_UTC = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)  # the fixture's clock (naive = UTC)


def _as(actor, *, roles=("admin",), permissions=None) -> Principal:
    principal = Principal(
        actor=actor, email="someone@example.test", roles=frozenset(roles),
        permissions=permissions if permissions is not None else {"operations": "edit"},
    )
    app.dependency_overrides[authenticated_principal] = lambda: principal
    app.dependency_overrides[signed_in_principal] = lambda: principal
    return principal


@pytest.fixture(autouse=True)
def health_principal_reset():
    yield
    app.dependency_overrides.pop(signed_in_principal, None)


def _sessions():
    factory = app.state.operations_uow_factory
    return factory().session_factory


def _beats(sessions, *, age=timedelta(seconds=10), **overrides):
    with sessions() as session:
        for group in JOB_GROUPS:
            values = {"success_at": NOW_UTC - age, "started_at": NOW_UTC - age, "duration_ms": 120, "failures": 0}
            values.update(overrides.get(group.key, {}))
            session.add(WorkerHeartbeatRow(job_group=group.key, **values))
        session.commit()


def _message(sessions, *, topic="warehouse.received", created=NOW_UTC, dead=False, published=False, attempts=0):
    message_id = uuid4()
    with sessions() as session:
        session.add(OutboxMessageRow(
            id=message_id, topic=topic, idempotency_key=f"k:{message_id}", created_at=created,
            payload={"lot": "x"}, attempt_count=attempts, last_error="invalid payload" if attempts else None,
            dead_lettered_at=NOW_UTC if dead else None, published_at=NOW_UTC if published else None,
        ))
        session.commit()
    return message_id


def test_the_journal_shows_heartbeats_the_age_of_the_queue_and_who_may_retry(operations_api):  # noqa: F811
    client, actor = operations_api
    sessions = _sessions()
    _beats(sessions, autofill={"failures": 2, "error": "autofill: RuntimeError: x", "failure_at": NOW_UTC})
    _message(sessions, topic="receiving.expected", created=NOW_UTC - timedelta(minutes=20))
    _as(actor, roles=("operator",), permissions={"operations": "view"})

    body = client.get("/api/operations/outbox").json()

    assert body["ok"] is False and body["pending"] == 1
    assert [group["key"] for group in body["groups"]] == [group.key for group in JOB_GROUPS]
    autofill = next(group for group in body["groups"] if group["key"] == "autofill")
    assert (autofill["status"], autofill["failures"], autofill["duration_ms"]) == ("warn", 2, 120)
    assert body["topics"] == [{
        "topic": "receiving.expected", "count": 1, "oldest_at": (NOW_UTC - timedelta(minutes=20)).isoformat(),
        "age_seconds": 1200, "status": "red",
    }]
    assert body["oldest_pending_seconds"] == 1200
    assert body["can_retry"] is False and body["tls_exceptions"] == []


def test_an_administrator_retries_a_stopped_job_once_and_it_is_journalled(operations_api):  # noqa: F811
    client, actor = operations_api
    sessions = _sessions()
    dead = _message(sessions, dead=True, attempts=5)
    _as(actor, roles=("operator",), permissions={"operations": "edit"})
    assert client.post(f"/api/operations/outbox/{dead}/retry").status_code == 403

    _as(actor)
    assert client.get("/api/operations/outbox").json()["can_retry"] is True
    first = client.post(f"/api/operations/outbox/{dead}/retry")
    second = client.post(f"/api/operations/outbox/{dead}/retry")

    assert first.json() == {"id": str(dead), "requeued": True}
    assert second.status_code == 200 and second.json()["requeued"] is False  # a double click retries once
    with sessions() as session:
        row = session.get(OutboxMessageRow, dead)
        events = list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "operations.outbox_retried")))
    assert (row.dead_lettered_at, row.attempt_count, row.payload) == (None, 0, {"lot": "x"})  # payload untouched
    assert row.next_attempt_at is not None
    assert len(events) == 1 and events[0].aggregate_id == dead
    assert events[0].details == {"topic": "warehouse.received", "attempts": "5", "error": "invalid payload"}


def test_retrying_a_done_or_unknown_job_is_refused(operations_api):  # noqa: F811
    client, actor = operations_api
    sessions = _sessions()
    done = _message(sessions, published=True)
    _as(actor)
    assert client.post(f"/api/operations/outbox/{done}/retry").status_code == 409
    assert client.post(f"/api/operations/outbox/{uuid4()}/retry").status_code == 404


def test_health_detail_is_for_administrators_and_red_without_heartbeats(operations_api):  # noqa: F811
    client, actor = operations_api
    assert client.get("/api/health?detail=1").status_code == 401
    _as(actor, roles=("operator",))
    assert client.get("/api/health?detail=1").status_code == 403

    _as(actor)
    red = client.get("/api/health?detail=1")
    assert red.status_code == 503
    assert red.json()["ok"] is False and "«Очередь событий модулей»: ни одного успешного запуска" in red.json()["problems"]

    _beats(_sessions())
    green = client.get("/api/health?detail=1")
    assert green.status_code == 200 and green.json()["ok"] is True and green.json()["service"] == "erp-next"
    assert client.get("/api/health").json() == {"ok": True, "service": "erp-next"}  # the plain check is unchanged


def test_health_detail_turns_red_on_a_silent_group_or_an_old_message(operations_api):  # noqa: F811
    client, actor = operations_api
    sessions = _sessions()
    _beats(sessions, dispatch={"success_at": NOW_UTC - timedelta(minutes=6)})
    _as(actor)
    body = client.get("/api/health?detail=1").json()
    assert body["ok"] is False and body["problems"] == ["«Очередь событий модулей»: нет успешного запуска 6 мин"]

    with sessions() as session:
        session.get(WorkerHeartbeatRow, "dispatch").success_at = NOW_UTC
        session.commit()
    _message(sessions, created=NOW_UTC - timedelta(minutes=16))
    body = client.get("/api/health?detail=1").json()
    assert body["ok"] is False and body["problems"] == ["Очередь «warehouse.received»: сообщение ждёт 16 мин"]


def test_the_tls_exception_list_is_visible(operations_api):  # noqa: F811
    client, actor = operations_api
    _as(actor)
    app.state.tls_exception_hosts = ("old-shop.example",)
    try:
        assert client.get("/api/operations/outbox").json()["tls_exceptions"] == ["old-shop.example"]
        assert client.get("/api/health?detail=1").json()["tls_exceptions"] == ["old-shop.example"]
    finally:
        app.state.tls_exception_hosts = ()


def test_ids_in_the_journal_payload_are_strings(operations_api):  # noqa: F811
    client, actor = operations_api
    sessions = _sessions()
    dead = _message(sessions, dead=True, attempts=5)
    _as(actor)
    (failure,) = client.get("/api/operations/outbox").json()["failures"]
    assert UUID(failure["id"]) == dead and failure["dead"] is True
