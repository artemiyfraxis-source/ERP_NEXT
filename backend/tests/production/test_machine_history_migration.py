"""0208_machine_speed_history over a seeded database: the new table starts empty, no other table changes, every
constraint refuses its bad row, and the downgrade drops the table and nothing else."""

import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PREVIOUS = "0207_vna_screens"
REVISION = "0208_machine_speed_history"
TABLE = "production_machine_speed_rows"
INSERT = (f"INSERT INTO {TABLE} (equipment_id, recorded_at, boot, seq, counter_total, counter_batch, counter_order, uph,"
          " state_code, alarm_code, run_write_id, run_kind, wire_key, setup_key, reason)"
          " VALUES (:equipment_id, '2026-11-20 10:00:00', :boot, :seq, :total, 0, 0, 0, :state, :alarm, :run, :kind,"
          " '', :setup, :reason)")


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name <> ?", (TABLE,))]
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables}


def test_speed_rows_start_empty_refuse_bad_rows_and_roundtrip_leaves_other_tables(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "speed.db"
    config = _config(path)
    command.upgrade(config, PREVIOUS)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        seed(engine)
    finally:
        engine.dispose()
    before = _counts(path)
    assert before["equipment_units"] > 0
    command.upgrade(config, REVISION)
    assert {name: count for name, count in _counts(path).items() if name != "alembic_version"} == {
        name: count for name, count in before.items() if name != "alembic_version"}
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0] == 0
        connection.execute("PRAGMA foreign_keys = ON")
        unit = connection.execute("SELECT id FROM equipment_units LIMIT 1").fetchone()[0]
        good = {"equipment_id": unit, "boot": "boot-1", "seq": 1, "total": 4294967295, "state": 103, "alarm": None,
                "run": None, "kind": None, "setup": "|L325|S5/5|P0/0|M0", "reason": "start"}
        connection.execute(INSERT, good)
        connection.execute(INSERT, {**good, "seq": 2, "total": 0, "alarm": 65535, "reason": "counter"})
        for bad in (
            {"seq": 1},  # the same snapshot twice
            {"boot": "  "},
            {"seq": -1},
            {"total": 4294967296},
            {"total": -1},
            {"state": 65536},
            {"alarm": -1},
            {"kind": "cutting"},  # a run kind without its write
            {"run": uuid.uuid4().hex},  # a write without its kind (and not in the queue)
            {"kind": "press", "run": uuid.uuid4().hex},
            {"setup": " "},
            {"reason": "uph"},
            {"equipment_id": uuid.uuid4().hex},  # not in БД.ОБ
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, {**good, "seq": 3, **bad})
        ids = [row_id for (row_id,) in connection.execute(f"SELECT id FROM {TABLE} ORDER BY id")]
        assert ids == [1, 2]
        indexes = {name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", (TABLE,))}
        assert {"ix_production_machine_speed_rows_time", "ix_production_machine_speed_rows_equipment_id_id"} <= indexes
    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLE not in tables
    assert _counts(path) == before
    command.upgrade(config, REVISION)
    # The generic seeder of the CI migration job fills the table once a later revision makes 0208 the head-1.
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        assert seed(engine).get(TABLE, 0) > 0
    finally:
        engine.dispose()
