import pytest

from app import outbox_worker


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
