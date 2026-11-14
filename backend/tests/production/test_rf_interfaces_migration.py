import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0176_vna_capture_jobs"
REVISION = "0179_rf_interfaces"


def test_migration_preserves_registry_jobs_and_measurements_and_enforces_constraints(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "rf.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    tables = ("production_instruments", "production_vswr_measurements", "warehouse_scan_jobs")
    def snapshot():
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables}
    before = snapshot()
    command.upgrade(config, REVISION)
    assert snapshot() == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM production_rf_details").fetchone() == (0,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # Exercise the clean downgrade before adding evidence (populated downgrades must fail).
    command.downgrade(config, PARENT)
    assert snapshot() == before
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        instrument_id = before["production_instruments"][0][0]
        db.execute("INSERT INTO production_rf_details VALUES (?,1,'adapter',0,'Паспорт',0,NULL,'')", (instrument_id,))
        good = (instrument_id, 1, "N", "вилка", "штырь", 50, 18, 1, "Паспорт")
        sql = "INSERT INTO production_rf_interfaces VALUES (?,?,?,?,?,?,?,?,?)"
        for field, value in ((1, 3), (2, "guess"), (3, "male"), (4, "unknown"), (5, -50), (6, 0), (8, " ")):
            bad = list(good)
            bad[field] = value
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, bad)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, (uuid4().hex, *good[1:]))
        db.execute(sql, good)
        db.execute(sql, (instrument_id, 2, *good[2:]))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, good)
    with pytest.raises(RuntimeError, match="ВЧ-данные"):
        command.downgrade(config, PARENT)
    assert snapshot() == before
