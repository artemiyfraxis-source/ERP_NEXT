"""Migration 0208: a label marked «напечатана вне ERP» — serial >= 1, pieces >= 1, one mark per (task, serial, text),
a task of production_tasks (RESTRICT); the downgrade drops the marks."""

import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0208_marking_outside_prints"
INSERT = ("INSERT INTO marking_outside_prints (id, task_id, serial, text, quantity, actor_id, actor_name, at)"
          " VALUES (?, ?, ?, ?, ?, ?, 'Мастер', '2026-11-20 10:00:00')")


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _tables(path):
    with sqlite3.connect(path) as connection:
        return {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_marks_refuse_impossible_rows_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "outside.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    task, person = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(INSERT, (uuid.uuid4().hex, task, 1, "FRX.1", 1, person))
        connection.execute(INSERT, (uuid.uuid4().hex, task, 1, "Разъём", 2, person))
        for bad in (
            (uuid.uuid4().hex, task, 0, "FRX.0", 1, person),  # no item №0
            (uuid.uuid4().hex, task, 2, "FRX.2", 0, person),  # a mark of no label
            (uuid.uuid4().hex, task, 1, "FRX.1", 1, person),  # the same label twice
            (uuid.uuid4().hex, task, 3, None, 1, person),  # a label without its text
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, bad)
        assert connection.execute("SELECT COUNT(*) FROM marking_outside_prints").fetchone()[0] == 2
        keys = connection.execute("PRAGMA foreign_key_list(marking_outside_prints)").fetchall()
        assert [(key[2], key[3], key[4], key[6]) for key in keys] == [("production_tasks", "task_id", "id", "RESTRICT")]
    command.downgrade(config, "-1")
    assert "marking_outside_prints" not in _tables(path)
    command.upgrade(config, REVISION)
    assert "marking_outside_prints" in _tables(path)
