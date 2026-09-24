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
