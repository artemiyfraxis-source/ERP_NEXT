"""0214: every scan job keeps its meaning (УПД or ВАЦ), a plain scan can't be read or lose its options, a document print
job can't skip its status machine, and the downgrade never drops what people scanned or sent to print."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "0214_warehouse_document_devices"
PARENT = "0213_equipment_setup_by"
NOW = "2026-11-20 12:00:00"


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "devices.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return path, config


def _scan(db, job_id: str, **values) -> None:
    row = {"id": job_id, "status": "pending", "requested_by": "Кладовщик", "created_at": NOW, "error": "", "result": "",
           "target_machine_id": "pc", "target_scanner": "scanner", **values}
    db.execute(f"INSERT INTO warehouse_scan_jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))


def _print_job(db, job_id: str, **values) -> None:
    row = {"id": job_id, "created_at": NOW, "created_by": "Кладовщик", "title": "Паспорт.pdf", "source": "upload",
           "pdf": b"%PDF-1.4", "sha256": "a" * 64, "pages": 1, "copies": 1, "target_machine_id": "pc",
           "target_printer": "Pantum", "status": "pending", "error": "", **values}
    db.execute(f"INSERT INTO warehouse_document_print_jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
               list(row.values()))


def test_0214_follows_0213(database):
    _path, config = database
    assert ScriptDirectory.from_config(config).get_revision(REVISION).down_revision == PARENT


def test_upgrade_keeps_every_job_and_names_its_purpose_and_constraints_hold(database):
    path, config = database
    command.upgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO warehouse_scan_agents (machine_id, first_seen, last_seen) VALUES ('pc', ?, ?)", (NOW, NOW))
        db.execute("INSERT INTO warehouse_agent_printers (machine_id, name, driver, port, kind, present, last_seen) "
                   "VALUES ('pc', 'TSC', '', '', 'tspl', 1, ?)", (NOW,))
        _scan(db, "upd-pending")
        _scan(db, "vna-pending", kind="vna", target_scanner=None)
        before = {row[0]: row for row in db.execute("SELECT * FROM warehouse_scan_jobs ORDER BY id")}
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        after = {row[0]: row for row in db.execute("SELECT * FROM warehouse_scan_jobs ORDER BY id")}
        assert {key: row[:-1] for key, row in after.items()} == before, "no column of an old row changed"
        assert {key: row[-1] for key, row in after.items()} == {"upd-pending": "upd", "vna-pending": "vna"}

        db.execute("INSERT INTO warehouse_agent_printers (machine_id, name, driver, port, kind, present, last_seen) "
                   "VALUES ('pc', 'Pantum', '', '', 'document', 1, ?)", (NOW,))
        _scan(db, "plain", purpose="plain", capture_settings='{"color": "gray", "dpi": 300}')
        refused_scans = [
            {"purpose": "plain"},                                                    # a plain scan without its options
            {"purpose": "plain", "capture_settings": "{}", "result": "{}"},        # a plain scan read as a УПД
            {"purpose": "upd", "capture_settings": "{}"},                           # a УПД scan with options
            {"purpose": "vna"},                                                      # ВАЦ purpose on a scanner job
            {"purpose": "copy"},
        ]
        for number, values in enumerate(refused_scans):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _scan(db, f"bad-{number}", **values)

        _print_job(db, "pending")
        _print_job(db, "spooled", status="spooled", taken_at=NOW, finished_at=NOW, attempt="a1")
        _print_job(db, "cancelled", status="cancelled", finished_at=NOW, error="Отменено")
        refused_jobs = [
            {"copies": 51},
            {"pages": 11, "copies": 50},                                             # 550 sheets
            {"status": "taken", "taken_at": NOW, "attempt": "a"},                   # taken without a lease
            {"status": "spooled", "finished_at": NOW},                               # finished without being taken
            {"status": "failed", "taken_at": NOW, "attempt": "a", "finished_at": NOW},  # failed without a reason
            {"status": "printed"},
            {"title": " "},
            {"source": "mail"},
            {"pdf": b""},
        ]
        for number, values in enumerate(refused_jobs):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _print_job(db, f"bad-{number}", **values)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _print_job(db, "nowhere", target_printer="Нет такого")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            db.execute("DELETE FROM warehouse_agent_printers WHERE name = 'Pantum'")


def test_downgrade_refuses_while_plain_scans_or_print_jobs_exist(database):
    path, config = database
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO warehouse_scan_agents (machine_id, first_seen, last_seen) VALUES ('pc', ?, ?)", (NOW, NOW))
        db.execute("INSERT INTO warehouse_agent_printers (machine_id, name, driver, port, kind, present, last_seen) "
                   "VALUES ('pc', 'Pantum', '', '', 'document', 1, ?)", (NOW,))
        _scan(db, "plain", purpose="plain", capture_settings='{"color": "gray", "dpi": 300}')
    with pytest.raises(RuntimeError, match="Plain scans"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM warehouse_scan_jobs")
        _print_job(db, "pending")
    with pytest.raises(RuntimeError, match="print jobs"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM warehouse_document_print_jobs")
        _scan(db, "upd")
    command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "warehouse_document_print_jobs" not in tables
        assert db.execute("SELECT count(*) FROM warehouse_agent_printers").fetchone() == (0,), "document queues forgotten"
        assert db.execute("SELECT id FROM warehouse_scan_jobs").fetchall() == [("upd",)]
