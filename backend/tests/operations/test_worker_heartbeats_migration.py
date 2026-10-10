"""Migration 0099: the worker heartbeat table — non-negative run time and failure count, a clean downgrade."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0096_cable_structure"
AFTER = "0099_worker_heartbeats"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_upgrade_creates_the_heartbeat_table_and_downgrade_drops_it(tmp_path):
    database_path = tmp_path / "beats.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        connection.execute("INSERT INTO worker_heartbeats (job_group) VALUES ('dispatch')")
        assert connection.execute("SELECT duration_ms, failures, success_at FROM worker_heartbeats").fetchone() == (0, 0, None)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO worker_heartbeats (job_group) VALUES ('dispatch')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO worker_heartbeats (job_group, failures) VALUES ('timers', -1)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO worker_heartbeats (job_group, duration_ms) VALUES ('owner', -5)")

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "worker_heartbeats" not in tables
