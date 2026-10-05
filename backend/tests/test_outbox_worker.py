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
