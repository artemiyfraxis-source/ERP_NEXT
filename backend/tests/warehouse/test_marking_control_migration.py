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
        versions = dict(db.execute("SELECT id, version FROM warehouse_marking_profiles"))
        inventory = dict(db.execute("SELECT id, inventory_number FROM warehouse_marking_profiles"))
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        # A profile that got peel = true has a new version (a stale editor gets 409); the others are untouched.
        assert dict(db.execute("SELECT id, version FROM warehouse_marking_profiles")) == {
            key: version + (inventory[key] == "ОБ.101") for key, version in versions.items()}
        assert "ОБ.101" in inventory.values() and "ОБ.102" in inventory.values()
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
        # The downgrade takes the ОБ.101 bump back: a roundtrip leaves the profiles exactly as they were.
        assert dict(db.execute("SELECT id, version FROM warehouse_marking_profiles")) == versions
    command.upgrade(config, REVISION)


def _at_0203(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "control.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, REVISION)
    return path, config


def test_0203_downgrade_strips_peel_from_every_profile(tmp_path, monkeypatch):
    path, config = _at_0203(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        # The owner ticked «отделитель» on an ОБ.102 profile too: older code forbids the key on any profile.
        row_id, raw = db.execute("SELECT id, geometry FROM warehouse_marking_profiles WHERE inventory_number = 'ОБ.102' LIMIT 1").fetchone()
        db.execute("UPDATE warehouse_marking_profiles SET geometry = ? WHERE id = ?", (json.dumps({**json.loads(raw), "peel": True}), row_id))
    command.downgrade(config, "0201_instrument_reference_curve")
    with sqlite3.connect(path) as db:
        assert all("peel" not in json.loads(raw) for (raw,) in db.execute("SELECT geometry FROM warehouse_marking_profiles"))
        assert "hold" not in [row[1] for row in db.execute("PRAGMA table_info(warehouse_agent_printers)")]


def test_0203_downgrade_is_refused_while_cancelled_jobs_exist(tmp_path, monkeypatch):
    path, config = _at_0203(tmp_path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("INSERT INTO warehouse_label_jobs (id, created_at, created_by, kind, copies, tspl, status, finished_at, error, format, png, "
                   "target_machine_id, target_printer, width_mm, height_mm, orientation, batch_id, sequence) VALUES "
                   "(?, '2026-11-19', 'test', 'marking', 1, x'', 'cancelled', '2026-11-19', 'Отменено: тест', 'gdi_png', x'01', "
                   "'pc', 'Godex', 92.5, 20, 'portrait', ?, 1)", ("a" * 32, "b" * 32))
    with pytest.raises(RuntimeError, match="отменённые задания"):
        command.downgrade(config, "0201_instrument_reference_curve")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (REVISION,)
