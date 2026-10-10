"""Миграция 0089: проект/КБ/вид заявки из НАЗНАЧЕНИЯ, одна живая основная заявка на КБ, счётчик номеров."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

AFTER = "0097_fulfillment_kit_request_unique"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


# The revision right before this migration (it changes when the migration is renumbered at merge).
BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _request(connection: sqlite3.Connection, key: str, number: str, purpose: str, status: str = "Новая") -> None:
    connection.execute(
        "INSERT INTO fulfillment_requests (id, number, source_idempotency_key, source_fingerprint, version,"
        " created_at, requester, purpose, status) VALUES (?, ?, ?, 'f', 1, '2026-10-05 12:00:00', 'Мастер', ?, ?)",
        (key, number, key, purpose, status),
    )


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), BEFORE)
    return path


def test_upgrade_backfills_project_and_kb_seeds_the_counter_and_guards_live_kits(database):
    with sqlite3.connect(database) as connection:
        _request(connection, "r1", "REQ.26.0000018", "180К.11 · КБ1 · Жгут")
        _request(connection, "r2", "REQ.26.0000029", "180K.11 · КБ-1 · Жгут", status="Отменена")  # cancelled twin
        _request(connection, "r3", "REQ.26.0000030", "728К · кб 2 · Жгут")
        _request(connection, "r4", "REQ.26.0000031", "Ремонт стенда")
        _request(connection, "r5", "REQ.25.0000007", "Ремонт стенда")

    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id, project_key, item_index, kind FROM fulfillment_requests ORDER BY id"
        ).fetchall()
        assert rows == [
            ("r1", "180К.11", "КБ1", "main"), ("r2", "180К.11", "КБ1", "main"), ("r3", "728К.1", "КБ2", "main"),
            ("r4", None, None, "main"), ("r5", None, None, "main"),
        ]
        assert connection.execute(
            "SELECT year, last_number FROM fulfillment_request_counters ORDER BY year"
        ).fetchall() == [(2025, 7), (2026, 31)]
        with pytest.raises(sqlite3.IntegrityError):  # a second live main request for КБ1
            _request(connection, "r6", "REQ.26.0000032", "x")
            connection.execute("UPDATE fulfillment_requests SET project_key = '180К.11', item_index = 'КБ1' WHERE id = 'r6'")
        connection.rollback()
        _request(connection, "r7", "REQ.26.0000033", "x")  # an extra one for the same КБ is allowed
        connection.execute(
            "UPDATE fulfillment_requests SET project_key = '180К.11', item_index = 'КБ1', kind = 'extra' WHERE id = 'r7'"
        )
        with pytest.raises(sqlite3.IntegrityError):  # CHECK on the kind
            connection.execute("UPDATE fulfillment_requests SET kind = 'other' WHERE id = 'r7'")

    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert not {"project_key", "item_index", "kind"} & _columns(connection, "fulfillment_requests")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "fulfillment_request_counters" not in tables


def test_two_live_requests_for_one_kb_stop_the_migration_before_any_change(database):
    with sqlite3.connect(database) as connection:
        _request(connection, "r1", "REQ.26.0000018", "180К.11 · КБ1 · Жгут")
        _request(connection, "r2", "REQ.26.0000029", "180К.11 · КБ1 · Жгут", status="Недоукомплектован")

    with pytest.raises(RuntimeError) as stopped:
        command.upgrade(_config(database), AFTER)

    assert "180К.11 КБ1: REQ.26.0000018, REQ.26.0000029" in str(stopped.value)
    assert "Отмените лишние" in str(stopped.value)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)
        assert "project_key" not in _columns(connection, "fulfillment_requests")
        assert connection.execute("SELECT status FROM fulfillment_requests ORDER BY number").fetchall() == [
            ("Новая",), ("Недоукомплектован",),
        ]
