"""0177 retains existing jobs and requires a heartbeat for the first device inventory."""

from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest


def test_device_migration_preserves_jobs_and_enforces_device_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "devices.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    assert ScriptDirectory.from_config(config).get_current_head() == "0177_agent_devices"
    command.upgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO warehouse_scan_agents(machine_id,first_seen,last_seen) VALUES ('pc','2026-11-12','2026-11-12')")
        db.execute("INSERT INTO warehouse_scan_jobs(id,status,requested_by,created_at,target_machine_id,target_scanner) "
                   "VALUES ('old','pending','Operator','2026-11-12','pc','scanner')")
        before = db.execute("SELECT * FROM warehouse_scan_jobs").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        assert db.execute("SELECT * FROM warehouse_scan_jobs").fetchall() == [row + (None,) for row in before]
        assert db.execute("SELECT * FROM warehouse_agent_devices").fetchall() == []
        insert = "INSERT INTO warehouse_agent_devices VALUES (?, ?, ?, 'Scanner', 1, '2026-11-12')"
        db.execute(insert, ("pc", "scan", "wia"))
        for row in (("pc", "scan", "wia"), ("pc", "unknown", "x"), ("pc", "scan", ""), ("missing", "scan", "wia")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(insert, row)
    command.downgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT * FROM warehouse_scan_jobs").fetchall() == before
