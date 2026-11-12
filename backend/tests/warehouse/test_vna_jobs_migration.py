"""Exercise 0176 over existing scan jobs with Alembic, including database constraints."""

from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest


def test_upgrade_preserves_scans_and_holds_vna_payload_invariants(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "migration.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    assert ScriptDirectory.from_config(config).get_revision("0176_vna_capture_jobs").down_revision == "0175_instrument_kinds_files"
    command.upgrade(config, "0175_instrument_kinds_files")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO warehouse_scan_agents(machine_id, first_seen, last_seen) VALUES ('pc', '2026-11-12', '2026-11-12')")
        db.execute("INSERT INTO warehouse_scan_jobs(id,status,requested_by,created_at,target_machine_id,target_scanner) "
                   "VALUES ('old','pending','Operator','2026-11-12','pc','scanner')")
        before = db.execute("SELECT id,status,requested_by,created_at,target_machine_id,target_scanner FROM warehouse_scan_jobs").fetchall()
    command.upgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        after = db.execute("SELECT id,status,requested_by,created_at,target_machine_id,target_scanner FROM warehouse_scan_jobs").fetchall()
        assert after == before
        assert db.execute("SELECT kind,capture_content,vna_name FROM warehouse_scan_jobs").fetchone() == ("scan", None, "")
        assert db.execute("SELECT source,capture_file_name FROM warehouse_scan_jobs").fetchone() == ("s2vna", "")
        db.execute("INSERT INTO warehouse_agent_vna(machine_id,name,ready,reported_at) VALUES ('pc','S2VNA',0,'2026-11-12')")
        assert db.execute("SELECT reason FROM warehouse_agent_vna WHERE machine_id='pc'").fetchone() == ("",)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE warehouse_scan_jobs SET kind='other'")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE warehouse_scan_jobs SET source='unknown'")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE warehouse_scan_jobs SET source='arinst'")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.execute("UPDATE warehouse_scan_jobs SET capture_content=x'01'")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute("INSERT INTO warehouse_agent_vna(machine_id,name,ready,reported_at) VALUES ('missing','S2VNA',0,'2026-11-12')")
    command.downgrade(config, "0175_instrument_kinds_files")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id,status,requested_by,created_at,target_machine_id,target_scanner FROM warehouse_scan_jobs").fetchall() == before
    command.upgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE warehouse_scan_jobs SET kind='vna',target_scanner=NULL")
    with pytest.raises(RuntimeError, match="preserve their evidence"):
        command.downgrade(config, "0175_instrument_kinds_files")
