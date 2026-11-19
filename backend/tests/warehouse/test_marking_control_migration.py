import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

REVISION = "0203_marking_printer_control"


def test_0203_keeps_rows_sets_peel_for_ob101_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "control.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "0201_instrument_reference_curve")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        counts = {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}
        jobs = db.execute("SELECT * FROM warehouse_label_jobs ORDER BY id").fetchall()
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        assert {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables} == counts
        assert db.execute("SELECT * FROM warehouse_label_jobs ORDER BY id").fetchall() == jobs
        geometry = {inventory: json.loads(raw) for inventory, raw in
                    db.execute("SELECT inventory_number, geometry FROM warehouse_marking_profiles")}
        assert geometry["ОБ.101"]["peel"] is True and "peel" not in geometry["ОБ.102"]
        assert db.execute("SELECT count(*) FROM warehouse_printer_commands").fetchone() == (0,)
        # A TSPL job can never be «cancelled»; a pending job only with a reason.
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE warehouse_label_jobs SET status = 'cancelled', finished_at = '2026-11-19', error = 'x'")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, "0201_instrument_reference_curve")
    with sqlite3.connect(path) as db:
        assert "warehouse_printer_commands" not in {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert all("peel" not in json.loads(raw) for (raw,) in db.execute("SELECT geometry FROM warehouse_marking_profiles"))
    command.upgrade(config, REVISION)
