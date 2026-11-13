"""0177 closes unattributable in-flight jobs and enforces sticky serial ownership."""

from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest


def test_device_migration_closes_old_jobs_and_enforces_device_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "devices.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    assert ScriptDirectory.from_config(config).get_current_head() == "0177_agent_devices"
    assert ScriptDirectory.from_config(config).get_revision("0177_agent_devices").down_revision == "0176_vna_capture_jobs"
    command.upgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO warehouse_scan_agents(machine_id,first_seen,last_seen) VALUES ('pc','2026-11-12','2026-11-12')")
        for kind in ("scan", "vna"):
            for status in ("pending", "claimed", "failed"):
                db.execute("INSERT INTO warehouse_scan_jobs(id,kind,status,requested_by,created_at,target_machine_id,target_scanner,"
                           "claimed_at,finished_at,error) VALUES (?,?,?,'Operator','2026-11-12','pc',?,?,?,?)",
                           (f"{kind}-{status}", kind, status, "scanner" if kind == "scan" else None,
                            "2026-11-12" if status != "pending" else None,
                            "2026-11-12" if status == "failed" else None, "Старая ошибка" if status == "failed" else ""))
        db.execute("INSERT INTO warehouse_scan_jobs(id,kind,status,requested_by,created_at,target_machine_id,"
                   "claimed_at,finished_at,capture_content) VALUES ('vna-done','vna','done','Operator',"
                   "'2026-11-12','pc','2026-11-12','2026-11-12',X'01')")
        db.execute("INSERT INTO warehouse_receipt_files(id,file_name,content_type,size,content,uploaded_by,uploaded_at) "
                   "VALUES ('file','scan.pdf','application/pdf',3,X'255044','Operator','2026-11-12')")
        db.execute("INSERT INTO warehouse_scan_jobs(id,status,requested_by,created_at,claimed_at,finished_at,file_id,result) "
                   "VALUES ('scan-done','done','Operator','2026-11-12','2026-11-12','2026-11-12','file','recognized')")
        db.execute("INSERT INTO warehouse_scan_jobs(id,status,requested_by,created_at) "
                   "VALUES ('untargeted-pending','pending','Operator','2026-11-12')")
        before = {row[0]: row for row in db.execute("SELECT * FROM warehouse_scan_jobs")}
    command.upgrade(config, "head")
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        columns = [column[1] for column in db.execute("PRAGMA table_info(warehouse_scan_jobs)")]
        rows = {row[0]: row for row in db.execute("SELECT * FROM warehouse_scan_jobs")}
        assert rows.keys() == before.keys()
        for key, row in rows.items():
            values = dict(zip(columns, row))
            assert values["requester_id"] is None
            if key.endswith(("done", "failed")):
                assert row == before[key] + (None,)
            else:
                assert values["status"] == "failed"
                assert values["error"] == "Прервано обновлением ERP — запустите сканирование ещё раз"
                assert values["finished_at"] is not None
                for field in ("requested_by", "created_at", "target_machine_id", "target_scanner", "claimed_at"):
                    assert values[field] == before[key][columns.index(field)]
        assert db.execute("SELECT * FROM warehouse_agent_devices").fetchall() == []
        insert = "INSERT INTO warehouse_agent_devices VALUES (?, ?, ?, 'Scanner', 1, '2026-11-12')"
        db.execute(insert, ("pc", "scan", "wia"))
        for row in (("pc", "scan", "wia"), ("pc", "unknown", "x"), ("pc", "scan", ""), ("missing", "scan", "wia")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(insert, row)
        db.execute("INSERT INTO warehouse_scan_agents(machine_id,first_seen,last_seen) VALUES ('other','2026-11-12','2026-11-12')")
        db.execute("INSERT INTO warehouse_vna_bindings VALUES ('26030095','pc')")
        for row in (("26030095", "other"), ("", "pc"), ("   ", "pc"), ("other", "missing")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO warehouse_vna_bindings VALUES (?,?)", row)
    command.downgrade(config, "0176_vna_capture_jobs")
    with sqlite3.connect(database) as db:
        assert {row[0]: row for row in db.execute("SELECT * FROM warehouse_scan_jobs")} == {
            key: row[:-1] for key, row in rows.items()}
