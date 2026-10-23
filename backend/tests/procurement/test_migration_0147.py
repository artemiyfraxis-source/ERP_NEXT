"""Миграция 0147: вид файла закупки «payment» (ПП) — CHECK расширен, файлы не тронуты; откат переводит ПП в «other»."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

AFTER = "0147_procurement_payment_files"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


# The revision right before this migration (it changes when the migration is renumbered at merge).
BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision


def _file(connection: sqlite3.Connection, file_id: str, kind: str) -> None:
    connection.execute(
        "INSERT INTO procurement_files (id, line_id, kind, file_name, content_type, size, content, uploaded_by,"
        " uploaded_at) VALUES (?, 'line-1', ?, ?, 'application/pdf', 3, X'504446', 'Петров', '2026-10-23 10:00:00')",
        (file_id, kind, f"{kind}.pdf"),
    )


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), BEFORE)
    return path


def test_upgrade_keeps_every_file_and_admits_payment_orders_downgrade_keeps_them_as_other(database):
    with sqlite3.connect(database) as connection:
        for file_id, kind in (("f1", "other"), ("f2", "permit"), ("f3", "quality")):
            _file(connection, file_id, kind)
        with pytest.raises(sqlite3.IntegrityError):  # not yet allowed
            _file(connection, "f4", "payment")

    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, kind, content FROM procurement_files ORDER BY id").fetchall() == [
            ("f1", "other", b"PDF"), ("f2", "permit", b"PDF"), ("f3", "quality", b"PDF"),
        ]
        _file(connection, "f4", "payment")
        with pytest.raises(sqlite3.IntegrityError):
            _file(connection, "f5", "receipt")

    command.downgrade(_config(database), BEFORE)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id, kind FROM procurement_files ORDER BY id").fetchall() == [
            ("f1", "other"), ("f2", "permit"), ("f3", "quality"), ("f4", "other"),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            _file(connection, "f6", "payment")
