import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0166_kb_progress"
REVISION = "0167_instrument_verification_pending"
TABLE = "production_instruments"
RELATED = (TABLE, "production_qc_instruments", "production_vswr_measurements")
COLUMNS = ("id", "version", "inventory_number", "name", "instrument_type", "verification_number", "verified_at", "verification_due", "status")
INSERT = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})"


def _config(path):
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _row(**changes):
    values = dict(zip(COLUMNS, (uuid4().hex, 1, f"TEST-{uuid4().hex}", "Тестовый прибор", "Тестер", "TEST-PV", "2026-01-01 00:00:00", "2027-01-01 00:00:00", "Действует")))
    values.update(changes)
    return tuple(values[key] for key in COLUMNS)


def _snapshot(path):
    with sqlite3.connect(path) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in RELATED}


def test_upgrade_and_clean_downgrade_preserve_instruments_and_references(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "instruments.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine, rows=2)
    finally:
        engine.dispose()
    assert all(inserted[table] == 2 for table in RELATED)
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, _row(status="Не используется"))
        before_fk = {table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall() for table in RELATED}
    before = _snapshot(path)
    command.upgrade(config, REVISION)
    assert _snapshot(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall() for table in RELATED} == before_fk
        indexes = {row[1] for row in connection.execute(f"PRAGMA index_list({TABLE})")}
        assert {"ix_production_instruments_status", "ix_production_instruments_verification_due"} <= indexes
    command.downgrade(config, PARENT)
    assert _snapshot(path) == before
    with sqlite3.connect(path) as connection:
        columns = {row[1]: row[3] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        assert all(columns[key] for key in ("verification_number", "verified_at", "verification_due"))


def test_constraints_and_blocked_downgrade_keep_pending_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "pending.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    pending = _row(status="Поверка не подтверждена", verification_number=None, verified_at=None, verification_due=None)
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, pending)
        for bad in (
            _row(status="unknown"), _row(verification_number=None), _row(verification_number=" "),
            _row(verified_at=None), _row(verification_due=None), _row(verification_due="2025-01-01 00:00:00"),
            _row(status="Поверка не подтверждена"),
            _row(status="Поверка не подтверждена", verification_number=None, verified_at=None),
            _row(inventory_number=pending[2]),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, bad)
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE}").fetchall() == [pending]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (REVISION,)
        columns = {row[1]: row[3] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        assert all(not columns[key] for key in ("verification_number", "verified_at", "verification_due"))


def test_upgrade_refuses_invalid_old_data_without_rewriting_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "invalid.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    invalid = _row(status="unknown")
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, invalid)
    with pytest.raises(RuntimeError, match="Existing instruments violate"):
        command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE}").fetchall() == [invalid]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)
