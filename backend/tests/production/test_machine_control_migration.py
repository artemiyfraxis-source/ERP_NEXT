"""Migration 0196: machine commands on the write queue — a command only with purpose `control`, known commands and
statuses, finished ⇔ finish time, one open non-Stop command per machine (writes included), one queued Stop; the
downgrade drops the commands and restores the 0195 table."""

import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0196_machine_control"
INSERT = ("INSERT INTO production_machine_writes (id, equipment_id, purpose, command, status, payload, summary, created_by,"
          " created_at, taken_at, finished_at) VALUES (?, ?, ?, ?, ?, '{}', 'Пуск', ?, '2026-11-19 10:00:00', ?, ?)")
T = "2026-11-19 10:00:01"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_command_rows_refuse_impossible_states_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "control.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    unit, other, person = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex

    def row(machine, purpose, name, status, taken=None, finished=None):
        return (uuid.uuid4().hex, machine, purpose, name, status, person, taken, finished)

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(INSERT, row(unit, "settings", None, "taken", T))  # a write runs
        connection.execute(INSERT, row(unit, "control", "stop", "queued"))  # a Stop may queue meanwhile
        connection.execute(INSERT, row(unit, "control", "stop", "taken", T))
        connection.execute(INSERT, row(unit, "control", "start", "unknown", T, T))
        connection.execute(INSERT, row(unit, "transfer", None, "cancelled", None, T))
        connection.execute(INSERT, row(other, "control", "reset", "queued"))
        for bad in (
            row(other, "control", "estop", "done", T, T),  # no ERP E-stop
            row(other, "control", None, "done", T, T),  # a machine command without its command
            row(other, "settings", "stop", "done", T, T),  # a write with a command
            row(other, "control", "stop", "lost", T, T),  # unknown status
            row(other, "control", "stop", "unknown", T),  # finished without a time
            row(unit, "control", "start", "queued"),  # second non-Stop command while the write runs
            row(other, "settings", None, "queued"),  # a write while Reset waits — one controller
            row(unit, "control", "stop", "queued"),  # second queued Stop
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, bad)
        assert connection.execute("SELECT COUNT(*) FROM production_machine_writes").fetchone()[0] == 6
    command.downgrade(config, "-1")
    with sqlite3.connect(path) as connection:
        columns = {name for _, name, *_ in connection.execute("PRAGMA table_info(production_machine_writes)")}
        statuses = [status for (status,) in connection.execute("SELECT status FROM production_machine_writes")]
    assert "command" not in columns
    assert sorted(statuses) == ["expired", "taken"]  # commands dropped; the cancelled write reads as expired
