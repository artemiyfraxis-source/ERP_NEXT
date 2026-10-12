"""Миграция 0106: отметка «нет на складе» на строке комплектации — кто/когда и CHECK на сочетания."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

AFTER = "0106_fulfillment_line_absent"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


# The revision right before this migration (it changes when the migration is renumbered at merge).
BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(fulfillment_lines)")}


def _line(connection: sqlite3.Connection, line_id: str, off_book: int = 0) -> None:
    connection.execute(
        "INSERT INTO fulfillment_lines (id, request_id, position, requirement_id, article, name, unit, requirement,"
        " reserved, issued, returned_good, defective_returned, defective_missing, off_book)"
        " VALUES (?, 'r1', 0, ?, 'КД-1', 'Разъём', 'шт', 4, 0, 0, 0, 0, 0, ?)",
        (line_id, line_id, off_book),
    )


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), BEFORE)
    return path


def test_upgrade_keeps_lines_unmarked_guards_the_mark_and_downgrade_drops_it(database):
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO fulfillment_requests (id, number, source_idempotency_key, source_fingerprint, version,"
            " created_at, requester, purpose, status) VALUES ('r1', 'REQ.26.0000012', 'k', 'f', 1,"
            " '2026-10-12 09:00:00', 'Мастер', '719К.2 · КБ12 · Жгут', 'Недоукомплектован')"
        )
        _line(connection, "l1")
        _line(connection, "l2", off_book=1)

    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT id, absent_at, absent_by, off_book FROM fulfillment_lines ORDER BY id"
        ).fetchall() == [("l1", None, None, 0), ("l2", None, None, 1)]
        connection.execute(
            "UPDATE fulfillment_lines SET absent_at = '2026-10-12 09:30:00', absent_by = 'Кладовщик' WHERE id = 'l1'"
        )
        with pytest.raises(sqlite3.IntegrityError):  # both marks on one line
            connection.execute(
                "UPDATE fulfillment_lines SET absent_at = '2026-10-12 09:30:00', absent_by = 'Кладовщик' WHERE id = 'l2'"
            )
        with pytest.raises(sqlite3.IntegrityError):  # when without who
            connection.execute("UPDATE fulfillment_lines SET absent_by = NULL WHERE id = 'l1'")
        with pytest.raises(sqlite3.IntegrityError):  # a marked line takes no lot
            connection.execute("UPDATE fulfillment_lines SET lot_id = 'lot', reserved = 4 WHERE id = 'l1'")

    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert not {"absent_at", "absent_by"} & _columns(connection)
        assert connection.execute("SELECT COUNT(*) FROM fulfillment_lines").fetchone() == (2,)
