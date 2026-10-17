"""OP-1: migration 0039 backfills recipient ids from request authors and unique name matches."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

BEFORE = "0038_identity_view_defaults"
AFTER = "0039_notification_recipients"
IVANOV, PETROVA, TWIN_1, TWIN_2 = "a" * 32, "b" * 32, "c" * 32, "d" * 32
REQUEST = "e" * 32


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _filler(declared: str) -> object:
    kind = declared.upper()
    if kind.startswith("CHAR(32)"):
        return uuid4().hex
    if "INT" in kind or "NUMERIC" in kind or "BOOL" in kind:
        return 0
    if kind == "DATE":
        return "2026-09-01"
    if kind.startswith("DATETIME"):
        return "2026-09-01 10:00:00"
    return f"x-{uuid4().hex[:8]}"


def _insert(connection, table: str, **values) -> None:
    """Fills every NOT NULL column without a default, so the test names only what it checks."""
    for _, name, declared, notnull, default, primary in connection.execute(f"PRAGMA table_info({table})"):
        if name not in values and (notnull or primary) and default is None:
            values[name] = _filler(declared)
    names = ", ".join(values)
    connection.execute(
        f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' for _ in values)})", tuple(values.values()),
    )


def _seed(connection) -> None:
    for user_id, name in ((IVANOV, "Иванов Иван"), (PETROVA, "Петрова  Мария"), (TWIN_1, "Дубль"), (TWIN_2, "дубль")):
        _insert(connection, "identity_users", id=user_id, name=name, role="staff", active=1)
    _insert(connection, "requests", id=REQUEST, actor_id=IVANOV, actor_name="Иванов Иван")
    for legacy_id, source, initiator, status in (
        ("SM-1", REQUEST, "Кто-то другой", "В пути"),   # the request author wins over the text
        ("SM-2", uuid4().hex, " иванов   иван ", "Новый"),  # name match: spaces and case ignored
        ("SM-3", uuid4().hex, "Никто", "Готово"),        # no such user → NULL, not notified
        ("SM-4", uuid4().hex, "Дубль", "Отменено"),      # two users of that name → ambiguous → NULL
    ):
        _insert(connection, "procurement_lines", legacy_id=legacy_id, source_request_id=source,
                initiator=initiator, status=status)
    for number, requester in (("REQ.26.0000001", "Петрова Мария"), ("REQ.26.0000002", "Никто")):
        _insert(connection, "fulfillment_requests", number=number, requester=requester, status="Новая")
    for number, manager in (("ОТГ-1", "ПЕТРОВА МАРИЯ"), ("ОТГ-2", "Дубль")):
        _insert(connection, "shipments", number=number, manager=manager, status="Новая")


def test_backfill_matches_request_authors_and_unique_names_and_arms_the_status_mark(tmp_path):
    database_path = tmp_path / "op1.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        _seed(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        procurement = {
            row[0]: row[1:] for row in connection.execute(
                "SELECT legacy_id, initiator_id, notified_status, status FROM procurement_lines"
            )
        }
        assert procurement == {
            "SM-1": (IVANOV, "В пути", "В пути"),
            "SM-2": (IVANOV, "Новый", "Новый"),
            "SM-3": (None, "Готово", "Готово"),
            "SM-4": (None, "Отменено", "Отменено"),
        }
        assert dict(connection.execute("SELECT number, requester_id FROM fulfillment_requests")) == {
            "REQ.26.0000001": PETROVA, "REQ.26.0000002": None,
        }
        assert dict(connection.execute("SELECT number, manager_id FROM shipments")) == {
            "ОТГ-1": PETROVA, "ОТГ-2": None,
        }

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(procurement_lines)")}
        assert not {"initiator_id", "notified_status"} & columns
        assert "requester_id" not in {row[1] for row in connection.execute("PRAGMA table_info(fulfillment_requests)")}
        assert "manager_id" not in {row[1] for row in connection.execute("PRAGMA table_info(shipments)")}
