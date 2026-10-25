"""One holder at a time across every process that shares the database: the API's worker processes
(ERP_API_WORKERS > 1, deploy/README.md «Ядра и память»), the outbox worker and the one-off CLIs.

PostgreSQL: a session-level advisory lock on a connection of its own, in AUTOCOMMIT, so nothing stays «idle in
transaction» while the holder works; the server drops the lock by itself when the connection dies. Elsewhere (SQLite:
tests, the local preview, the shop PC — always a single process) a process lock per key stands in."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

log = logging.getLogger(__name__)
_guard = threading.Lock()
_local: dict[int, tuple[threading.Lock, int]] = {}  # key → (lock, holders and waiters)


def lock_key(namespace: str, value: object) -> int:
    """A stable signed 64-bit key (PostgreSQL bigint) for `namespace:value`."""
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def engine_of(bind: Engine | Connection) -> Engine:
    return bind.engine if isinstance(bind, Connection) else bind


@contextmanager
def advisory_lock(bind: Engine | Connection, key: int, *, wait: bool = True) -> Iterator[bool]:
    """Hold `key` for the block. `wait=False` does not queue: the block gets False when someone else holds it."""
    engine = engine_of(bind)
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            connection.execution_options(isolation_level="AUTOCOMMIT")
            if wait:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
                got = True
            else:
                got = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
            try:
                yield got
            finally:
                if got:
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    except Exception:  # noqa: BLE001 — a dead connection has released the lock already
                        log.exception("pg_advisory_unlock(%s) failed", key)
        return
    with _guard:
        lock, users = _local.get(key, (threading.Lock(), 0))
        _local[key] = (lock, users + 1)
    try:
        got = lock.acquire(blocking=wait)
        try:
            yield got
        finally:
            if got:
                lock.release()
    finally:
        with _guard:
            lock, users = _local[key]
            if users <= 1:
                del _local[key]
            else:
                _local[key] = (lock, users - 1)
