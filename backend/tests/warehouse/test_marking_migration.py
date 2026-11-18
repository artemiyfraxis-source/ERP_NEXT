import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed


def test_migration_retains_existing_jobs_and_seeds_only_uncalibrated_profiles(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "marking.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0189_cutting_group_numbers")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute("PRAGMA table_info(warehouse_label_jobs)")]
        before = db.execute("SELECT * FROM warehouse_label_jobs ORDER BY id").fetchall()
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        counts = {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}
    command.upgrade(config, "0190_godex_marking")
    with sqlite3.connect(path) as db:
        assert db.execute(f"SELECT {','.join(columns)} FROM warehouse_label_jobs ORDER BY id").fetchall() == before
        assert {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables} == counts
        assert db.execute("SELECT count(*) FROM warehouse_marking_profiles WHERE calibrated = 0 AND equipment_id IS NULL").fetchone() == (5,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE warehouse_label_jobs SET format = 'gdi_png'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE warehouse_label_jobs SET status = 'unknown'")
        assert db.execute("SELECT DISTINCT format FROM warehouse_label_jobs").fetchall() == [("tspl",)]
        valid = dict(format="gdi_png", kind="marking", lot_id=None, copies=1, png=b"png", target_machine_id="pc",
                     target_printer="Godex", batch_id="1" * 32, sequence=1, width_mm=20, height_mm=50,
                     orientation="portrait", status="pending", taken_at=None, finished_at=None, lease_until=None, error=None)
        for missing in ("sequence", "width_mm", "height_mm", "orientation"):
            values = {**valid, missing: None}
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE warehouse_label_jobs SET " + ", ".join(f"{key} = ?" for key in values), list(values.values()))
