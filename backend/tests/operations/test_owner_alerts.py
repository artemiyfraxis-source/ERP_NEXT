"""OP-4 daily digest, disposal escalation and OP-5 failure alert (OwnerTick.gs)."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, Uuid, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.operations.application import dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.requests.infrastructure import RequestRow
from app.shared.persistence import OutboxMessageRow, create_schema
from app.workflows.owner_alerts import (
    alert_on_dead_letters,
    daily_overdue_digest,
    overdue_digest_text,
    quarantine_escalations,
)

NOW = datetime(2026, 9, 29, 9, 0)
TODAY = NOW.date()


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _row(model, **values):
    """Fills every NOT NULL column the test does not name."""
    for column in model.__table__.columns:
        if column.name in values or column.nullable:
            continue
        kind = column.type
        if isinstance(kind, Uuid):
            values[column.name] = uuid4()
        elif isinstance(kind, Boolean):
            values[column.name] = False
        elif isinstance(kind, Integer):
            values[column.name] = 1
        elif isinstance(kind, Numeric):
            values[column.name] = Decimal("1")
        elif isinstance(kind, DateTime):
            values[column.name] = NOW
        elif isinstance(kind, Date):
            values[column.name] = TODAY
        elif isinstance(kind, JSON):
            values[column.name] = []
        else:
            values[column.name] = f"x-{uuid4().hex[:8]}"
    return model(**values)


def _add(sessions, *rows):
    with sessions() as session:
        session.add_all(rows)
        session.commit()


def _request(legacy_id, deadline, status="Новый", name="Резистор"):
    return _row(
        RequestRow, legacy_id=legacy_id, procurement_deadline=deadline, status=status, name=name,
        designation=f"DES-{legacy_id}",
    )


def _receiving(legacy_id, status, inspected_on, *, warehouse="ЦАР", nonconformities=()):
    return _row(
        ReceivingLineRow, legacy_id=legacy_id, status=status, inspected_on=inspected_on, name="Разъём",
        warehouse=warehouse, nonconformities=list(nonconformities),
    )


def _outbox(sessions, topic="notification.requested"):
    with sessions() as session:
        return list(session.scalars(
            select(OutboxMessageRow).where(OutboxMessageRow.topic == topic).order_by(OutboxMessageRow.created_at)
        ))


def _user(role, email):
    return _row(IdentityUserRow, role=role, email=email, active=True)


def test_digest_lists_overdue_requests_and_stuck_receiving(sessions):
    done = _request("SM-3", TODAY - timedelta(days=2))
    _add(
        sessions,
        _request("SM-1", TODAY - timedelta(days=3)),
        _request("SM-2", TODAY - timedelta(days=10), name=""),
        done,
        _request("SM-4", TODAY - timedelta(days=5), status="Отменено"),
        _request("SM-5", TODAY),
        _receiving("SM-7", "Ожидание", date(2026, 9, 28)),
        _receiving("SM-8", "Карантин", TODAY - timedelta(days=90), nonconformities=["Брак"]),
        _receiving("SM-9", "Карантин", TODAY - timedelta(days=4), warehouse="ЖУК"),
        _receiving("SM-10", "Принято", TODAY - timedelta(days=200)),
    )
    # A purchase line «Готово» closes its request (PURCHASE_TO_REQUEST_STATUS).
    _add(sessions, _row(ProcurementLineRow, source_request_id=done.id, status="Готово"))
    with sessions() as session:
        text = overdue_digest_text(session, TODAY)
    requests, waiting, quarantine = text.split("\n\n")
    assert requests.splitlines() == [
        "ПРОСРОЧЕННЫЕ ДЕДЛАЙНЫ (2):",
        "• SM-2 · DES-SM-2 — дедлайн 19.09.2026 (10 дн. назад), статус: Новый",
        "• SM-1 · Резистор — дедлайн 26.09.2026 (3 дн. назад), статус: Новый",
    ]
    assert waiting == "ОЖИДАНИЕ РЕШЕНИЯ ПО НЕСООТВЕТСТВИЮ (1):\n• SM-7 · Разъём — ждёт решения 1 из 2 р.д. (ЦАР)"
    assert quarantine.splitlines() == [
        "ЗАВИСШИЙ КАРАНТИН (2):",
        "• SM-8 · Разъём — в Карантине 90 дн. · ≥3 МЕС — НУЖНО РЕШЕНИЕ ОБ УТИЛИЗАЦИИ (ЦАР)",
        "• SM-9 · Разъём — в Карантине 4 дн. (ЖУК)",
    ]


def test_digest_goes_to_admins_once_a_day_and_not_when_clean(sessions):
    _add(sessions, _user("admin", "owner@example.test"), _user("buyer", "buyer@example.test"))
    assert daily_overdue_digest(NOW, sessions) is False  # nothing burning → no message, day closed
    _add(sessions, _request("SM-1", TODAY - timedelta(days=1)))
    assert daily_overdue_digest(NOW + timedelta(hours=1), sessions) is False  # the day gate holds
    assert daily_overdue_digest(NOW + timedelta(days=1), sessions) is True
    assert daily_overdue_digest(NOW + timedelta(days=1, minutes=1), sessions) is False
    [message] = _outbox(sessions)
    assert message.payload["recipient_roles"] == "admin"
    assert message.payload["type"] == "digest"
    assert message.payload["title"] == "Дайджест — просрочки и Карантин"
    assert dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions)) == 1
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        assert uow.notifications.unread_count("owner@example.test") == 1
        assert uow.notifications.unread_count("buyer@example.test") == 0


def test_disposal_escalation_is_sent_once_per_quarantine_stay(sessions):
    line = _receiving("SM-8", "Карантин", TODAY - timedelta(days=91), nonconformities=["Недостача"])
    _add(sessions, line, _receiving("SM-9", "Карантин", TODAY - timedelta(days=80)))
    assert quarantine_escalations(NOW, sessions) == 1
    assert quarantine_escalations(NOW + timedelta(hours=2), sessions) == 0  # day gate
    assert quarantine_escalations(NOW + timedelta(days=1), sessions) == 0  # already notified
    [message] = _outbox(sessions)
    assert message.payload["type"] == "disposal"
    assert message.payload["reference"] == "SM-8"
    assert set(message.payload["recipient_roles"].split(",")) == {"admin", "buyer"}
    assert message.payload["title"] == "Карантин ≥3 мес — нужно решение об утилизации — SM-8"
    assert "В Карантине 91 дн. (с 30.06.2026)." in message.payload["body"]
    assert "Характер несоответствия: Недостача" in message.payload["body"]

    # Left quarantine → the mark is dropped; a new stay is announced again.
    with sessions() as session:
        session.get(ReceivingLineRow, line.id).status = "Ожидание"
        session.commit()
    assert quarantine_escalations(NOW + timedelta(days=2), sessions) == 0
    with sessions() as session:
        session.get(ReceivingLineRow, line.id).status = "Карантин"
        session.commit()
    assert quarantine_escalations(NOW + timedelta(days=3), sessions) == 1


def _dead(sessions, topic, error):
    _add(sessions, OutboxMessageRow(
        id=uuid4(), topic=topic, idempotency_key=f"k-{uuid4()}", created_at=NOW, payload={},
        attempt_count=5, last_error=error, dead_lettered_at=NOW,
    ))


def _drop_dead(sessions, topic):
    with sessions() as session:
        for row in session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == topic)):
            session.delete(row)
        session.commit()


def test_failure_alert_seeds_first_then_alerts_new_signatures_once(sessions):
    _dead(sessions, "receiving.expected", "old failure")
    assert alert_on_dead_letters(NOW, sessions) == 0  # the first run only seeds history
    assert _outbox(sessions) == []

    _dead(sessions, "warehouse.received", "Нет такого склада")
    _dead(sessions, "warehouse.received", "Нет такого склада")
    assert alert_on_dead_letters(NOW + timedelta(minutes=1), sessions) == 1
    [alert] = _outbox(sessions)
    assert alert.payload["type"] == "error"
    assert alert.payload["recipient_roles"] == "admin"
    assert alert.payload["title"] == "Ошибки фоновых задач (1)"
    assert "• [warehouse.received] (×2)\n  Нет такого склада" in alert.payload["body"]
    assert "old failure" not in alert.payload["body"]

    # Still present → re-stamped on every run, never announced again.
    assert alert_on_dead_letters(NOW + timedelta(hours=2), sessions) == 0
    assert len(_outbox(sessions)) == 1


def test_failure_alert_repeats_a_signature_only_after_the_cooldown(sessions):
    assert alert_on_dead_letters(NOW, sessions) == 0
    _dead(sessions, "procurement.requested", "boom")
    assert alert_on_dead_letters(NOW + timedelta(minutes=1), sessions) == 1
    _drop_dead(sessions, "procurement.requested")
    _dead(sessions, "procurement.requested", "boom")
    assert alert_on_dead_letters(NOW + timedelta(hours=3), sessions) == 0  # within 6 h
    _drop_dead(sessions, "procurement.requested")
    assert alert_on_dead_letters(NOW + timedelta(hours=4), sessions) == 0
    _dead(sessions, "procurement.requested", "boom")
    assert alert_on_dead_letters(NOW + timedelta(hours=10), sessions) == 1  # 7 h since the last stamp
    assert len(_outbox(sessions)) == 2
