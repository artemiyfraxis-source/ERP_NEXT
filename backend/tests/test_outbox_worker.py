import pytest

from app import outbox_worker


@pytest.fixture(autouse=True)
def no_receipt_sync(monkeypatch):
    """The timer tests below pass a bare object as sessions; the receipt sync gets its own test."""
    monkeypatch.setattr(outbox_worker, "sync_receipt_stages", lambda now, uow, receipts: 0)


def test_drain_available_reads_every_batch(monkeypatch):
    batches = iter((2, 2, 1))
    calls = []

    def dispatch(sessions, *, limit):
        calls.append((sessions, limit))
        return next(batches)

    monkeypatch.setattr(outbox_worker, "dispatch_available", dispatch)

    sessions = object()
    assert outbox_worker.drain_available(sessions, limit=2) == 5
    assert calls == [(sessions, 2), (sessions, 2), (sessions, 2)]


def test_dispatch_puts_a_handed_off_line_on_the_buyers_desk_and_an_old_message_in_new():
    """Owner, 05.10: the worker's consumer builds the line «В работе» for the buyer in the event (with the page fill
    queued in the same transaction); a message sent before the switch carries nobody and lands «Новый»."""
    from dataclasses import replace
    from datetime import datetime

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.modules.procurement.domain import AUTOFILL_TOPIC, ProcurementStatus
    from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
    from app.shared.persistence import OutboxMessageRow, create_schema
    from tests.factories import Actor
    from tests.procurement.test_application import requested_message

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    buyer = Actor(name="Петров", department="Закупки")
    old = requested_message(1)
    new = requested_message(2)
    new = replace(new, payload={
        **new.payload, "link": "https://shop.test/item/2",
        "buyer_id": str(buyer.id), "buyer_name": buyer.name, "buyer_department": buyer.department,
    })
    with sessions() as session:
        for message in (old, new):
            session.add(OutboxMessageRow(
                id=message.id, topic=message.topic, idempotency_key=message.idempotency_key,
                created_at=datetime(2026, 9, 15, 11, 0), payload=message.payload,
            ))
        session.commit()

    assert outbox_worker.dispatch_available(sessions) >= 2

    with SqlAlchemyProcurementUnitOfWork(sessions) as uow:
        lines = {line.legacy_id: line for line in uow.procurement.list()}
    assert (lines["SM-20260915-01"].status, lines["SM-20260915-01"].buyer) == (ProcurementStatus.NEW, None)
    taken = lines["SM-20260915-02"]
    assert (taken.status, taken.buyer, taken.actual_quantity) == (ProcurementStatus.IN_WORK, buyer, 2)
    with sessions() as session:
        queued = session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == AUTOFILL_TOPIC)).all()
    assert [(row.payload["line_id"], row.published_at) for row in queued] == [(str(taken.id), None)]
    engine.dispose()


def test_dispatch_available_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="positive"):
        outbox_worker.dispatch_available(object(), limit=0)


def test_run_timers_moves_overdue_waiting_and_survives_a_concurrent_edit(monkeypatch):
    from sqlalchemy.orm.exc import StaleDataError

    calls = []

    def sweep(now, uow):
        calls.append((now, uow))
        return ["line"]

    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", sweep)
    monkeypatch.setattr(outbox_worker, "notify_initiators", lambda now, uow: 0)
    assert outbox_worker.run_timers(object()) == 1
    assert len(calls) == 1

    def stale(now, uow):
        raise StaleDataError("changed")

    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", stale)
    assert outbox_worker.run_timers(object()) == 0


def test_run_timers_notifies_initiators_even_when_the_quarantine_timer_fails(monkeypatch):
    from sqlalchemy.orm.exc import StaleDataError

    def stale(now, uow):
        raise StaleDataError("changed")

    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", stale)
    monkeypatch.setattr(outbox_worker, "notify_initiators", lambda now, uow: 2)
    assert outbox_worker.run_timers(object()) == 2


def test_owner_jobs_run_after_the_timers_and_one_failure_does_not_stop_the_rest(monkeypatch):
    calls = []

    def broken(now, sessions):
        calls.append("digest")
        raise RuntimeError("boom")

    monkeypatch.setattr(outbox_worker, "daily_overdue_digest", broken)
    monkeypatch.setattr(outbox_worker, "quarantine_escalations", lambda now, sessions: calls.append("disposal") or 1)
    monkeypatch.setattr(outbox_worker, "alert_on_dead_letters", lambda now, sessions: calls.append("alert") or 2)
    monkeypatch.setattr(outbox_worker, "_last_error_alert", 0.0)
    monkeypatch.setattr(outbox_worker.time, "monotonic", lambda: 1000.0)
    assert outbox_worker.run_owner_jobs(object()) == 3
    # The failure alert is throttled to once a minute; the day-gated jobs run every loop.
    assert outbox_worker.run_owner_jobs(object()) == 1
    assert calls == ["digest", "disposal", "alert", "digest", "disposal"]


def test_run_timers_syncs_receipt_stages_before_notifying_and_survives_a_concurrent_edit(monkeypatch):
    from sqlalchemy.orm.exc import StaleDataError

    order = []
    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", lambda now, uow: [])
    monkeypatch.setattr(outbox_worker, "sync_receipt_stages", lambda now, uow, receipts: order.append("sync") or 3)
    monkeypatch.setattr(outbox_worker, "notify_initiators", lambda now, uow: order.append("notify") or 1)
    assert outbox_worker.run_timers(object()) == 4
    assert order == ["sync", "notify"]

    def stale(now, uow, receipts):
        raise StaleDataError("changed")

    monkeypatch.setattr(outbox_worker, "sync_receipt_stages", stale)
    assert outbox_worker.run_timers(object()) == 1


def test_autofill_job_runs_on_its_own_thread_every_few_seconds_and_swallows_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(
        outbox_worker, "procurement_autofill_tick", lambda sessions, cap: calls.append((sessions, cap)) or 2
    )
    monkeypatch.setattr(outbox_worker, "_last_autofill", float("-inf"))
    monkeypatch.setattr(outbox_worker, "_autofill_thread", None)
    clock = iter([1000.0, 1000.0, 1001.0, 1004.0, 1004.0])
    monkeypatch.setattr(outbox_worker.time, "monotonic", lambda: next(clock))
    assert outbox_worker.start_autofill_job("s") is True
    outbox_worker._autofill_thread.join()
    assert outbox_worker.start_autofill_job("s") is False  # the beat is not over
    assert outbox_worker.start_autofill_job("s") is True
    outbox_worker._autofill_thread.join()
    assert calls == [("s", outbox_worker.AUTOFILL_TICK_CAP)] * 2

    def boom(sessions, cap):
        raise RuntimeError("boom")

    monkeypatch.setattr(outbox_worker, "procurement_autofill_tick", boom)
    assert outbox_worker.run_autofill_job("s") == 0


# ── Audit U08: heartbeats, poison isolation, counts that survive a restart ──


@pytest.fixture()
def queue_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.shared.persistence import create_schema

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield engine, sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _put(sessions, topic: str, key: str, payload: dict):
    from datetime import datetime
    from uuid import uuid4

    from app.shared.persistence import OutboxMessageRow

    message_id = uuid4()
    with sessions() as session:
        session.add(OutboxMessageRow(
            id=message_id, topic=topic, idempotency_key=key, created_at=datetime(2026, 10, 10, 9, 0), payload=payload,
        ))
        session.commit()
    return message_id


def _row(sessions, message_id):
    from app.shared.persistence import OutboxMessageRow

    with sessions() as session:
        return session.get(OutboxMessageRow, message_id)


def _beat(sessions, group: str):
    from app.modules.operations.infrastructure import WorkerHeartbeatRow

    with sessions() as session:
        return session.get(WorkerHeartbeatRow, group)


def _healthy_notification(sessions):
    from uuid import uuid4

    return _put(sessions, "notification.requested", f"notify:{uuid4()}", {
        "recipient_user_ids": str(uuid4()), "recipient_audiences": "", "type": "status", "reference": "",
        "title": "t", "body": "b",
    })


def test_a_crashing_message_does_not_stall_healthy_topics_and_is_dead_lettered_across_restarts(queue_db, monkeypatch):
    """An unexpected crash on one topic (not its module's Poison* error) is recorded on the message with the backoff;
    the other topics are worked off in the same pass; the attempt count lives in the database, so five passes — with a
    restart of the worker between them — dead-letter it instead of crashing the worker forever."""
    from sqlalchemy import update
    from sqlalchemy.orm import Session, sessionmaker

    from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
    from app.shared.persistence import OutboxMessageRow

    engine, sessions = queue_db
    poison = _put(sessions, "receiving.expected", "receiving:poison", {"anything": "x"})
    healthy = _healthy_notification(sessions)

    def crash(now, uow):
        """Like the real handler: claims the next due message — and crashes on it."""
        with uow:
            if uow.outbox.next_unpublished("receiving.expected", now) is None:
                return None
            raise RuntimeError("payload makes the handler crash")

    monkeypatch.setattr(outbox_worker, "dispatch_next_receiving_expected", crash)

    assert outbox_worker.dispatch_available(sessions) == 2  # one quarantine attempt + one delivered notification
    assert _row(sessions, healthy).published_at is not None
    first = _row(sessions, poison)
    assert (first.attempt_count, first.dead_lettered_at, first.last_error) == (
        1, None, "RuntimeError: payload makes the handler crash",
    )
    assert first.next_attempt_at is not None  # backed off: the next pass does not hit it again at once
    assert _beat(sessions, "dispatch").failures == 0  # the queue works; the bad message is the message's problem

    for _ in range(4):  # the worker restarts (a fresh sessionmaker), and the backoff is over each time
        sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        with sessions() as session:
            session.execute(update(OutboxMessageRow).where(OutboxMessageRow.id == poison).values(next_attempt_at=None))
            session.commit()
        outbox_worker.dispatch_available(sessions)

    final = _row(sessions, poison)
    assert final.attempt_count == 5 and final.dead_lettered_at is not None
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        counts = uow.operations.outbox_health()
    assert (counts["dead"], counts["pending"], counts["published"]) == (1, 0, 1)
    assert outbox_worker.dispatch_available(sessions) == 0  # nothing left to try: the worker idles, it does not crash


def test_a_database_outage_leaves_the_message_alone_and_marks_the_group(queue_db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    _engine, sessions = queue_db
    waiting = _put(sessions, "receiving.expected", "receiving:1", {"x": 1})

    def down(now, uow):
        raise OperationalError("SELECT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(outbox_worker, "dispatch_next_receiving_expected", down)
    assert outbox_worker.dispatch_available(sessions) == 0

    assert (_row(sessions, waiting).attempt_count, _row(sessions, waiting).last_error) == (0, None)
    beat = _beat(sessions, "dispatch")
    assert beat.failures == 1 and beat.success_at is None and "OperationalError" in beat.error

    monkeypatch.setattr(outbox_worker, "dispatch_next_receiving_expected", lambda now, uow: None)
    outbox_worker.dispatch_available(sessions)
    beat = _beat(sessions, "dispatch")
    assert beat.failures == 0 and beat.success_at is not None and beat.error and beat.failure_at is not None


def test_module_poison_errors_still_quarantine_through_their_module(queue_db, monkeypatch):
    from uuid import uuid4

    from app.modules.receiving.application import PoisonReceivingMessage

    _engine, sessions = queue_db
    calls = []

    def poison(now, uow):
        calls.append(now)
        if len(calls) == 1:
            raise PoisonReceivingMessage(uuid4(), "invalid")
        return None

    monkeypatch.setattr(outbox_worker, "dispatch_next_receiving_expected", poison)
    assert outbox_worker.dispatch_available(sessions) == 1
    assert len(calls) == 2  # the next turn asks the topic again; nothing left → the pass ends


def test_every_job_group_stamps_its_heartbeat(queue_db, monkeypatch):
    _engine, sessions = queue_db
    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", lambda now, uow: [])
    monkeypatch.setattr(outbox_worker, "notify_initiators", lambda now, uow: 0)
    for name in ("daily_overdue_digest", "quarantine_escalations", "alert_on_dead_letters", "feed_triggers",
                 "close_followed_up", "leave_calendar_tick", "invoice_status_tick"):
        monkeypatch.setattr(outbox_worker, name, lambda now, sessions: 0)
    monkeypatch.setattr(outbox_worker, "kitset_names_tick", lambda now, sessions: 0)
    monkeypatch.setattr(outbox_worker, "procurement_autofill_tick", lambda sessions, cap: 0)
    for gate in ("_last_error_alert", "_last_arbiter_close", "_last_leave_calendar", "_last_invoice_status"):
        monkeypatch.setattr(outbox_worker, gate, float("-inf"))

    outbox_worker.run_timers(sessions)
    outbox_worker.run_tail_jobs(sessions)
    outbox_worker.run_autofill_job(sessions)
    outbox_worker.dispatch_available(sessions)

    from app.modules.operations.background import JOB_GROUP_KEYS

    for group in JOB_GROUP_KEYS:
        beat = _beat(sessions, group)
        assert beat is not None and beat.success_at is not None and beat.failures == 0, group

    def boom(now, sessions):
        raise RuntimeError("digest broke")

    monkeypatch.setattr(outbox_worker, "daily_overdue_digest", boom)
    outbox_worker.run_owner_jobs(sessions)
    outbox_worker.run_owner_jobs(sessions)
    owner = _beat(sessions, "owner")
    assert owner.failures == 2 and owner.error == "daily digest: RuntimeError: digest broke"


def test_a_crashing_timer_does_not_stop_the_other_timers(monkeypatch):
    calls = []

    def broken(now, uow):
        raise RuntimeError("bug")

    monkeypatch.setattr(outbox_worker, "quarantine_overdue_waiting", broken)
    monkeypatch.setattr(outbox_worker, "notify_initiators", lambda now, uow: calls.append("notify") or 1)
    assert outbox_worker.run_timers(object()) == 1 and calls == ["notify"]
