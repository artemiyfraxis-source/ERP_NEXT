"""0205_vna_bench_sessions on a database that already holds ОТК (protocol v1) sessions."""

from pathlib import Path
import sqlite3
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0203_marking_printer_control"
REVISION = "0205_vna_bench_sessions"
TABLES = ("production_vna_sessions", "production_vna_commands", "production_instruments", "warehouse_vna_control")


def _first(db, table, key="id"):
    return db.execute(f"SELECT {key} FROM {table} LIMIT 1").fetchone()[0]


def _session(db, **changes):
    values = dict(id=uuid4().hex, state="review", version=3, step=2, operator_id=_first(db, "identity_users"),
                  machine_id="pc-1", instrument_id=_first(db, "production_instruments"), instrument_serial="26030095",
                  task_id=_first(db, "production_tasks"), operation_id=_first(db, "production_operations"),
                  product_position=1, snapshot="{}", steps="[]", created_at="2026-11-16 10:00:00",
                  updated_at="2026-11-16 10:00:00")
    values.update(changes)
    db.execute("INSERT INTO production_vna_sessions (" + ",".join(values) + ") VALUES (" +
               ",".join("?" for _ in values) + ")", list(values.values()))
    return values


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "bench.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        legacy = _session(db)
        db.execute("INSERT INTO production_vna_commands (id, session_id, expected_version, expected_step, op, args, status,"
                   " result, result_hash, created_at) VALUES (?, ?, 1, 0, 'prepare', '{}', 'done', '{}', '', '2026-11-16')",
                   (uuid4().hex, legacy["id"]))

    def snapshot():
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in TABLES}
    before = snapshot()
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    yield config, path, snapshot, before, legacy


def test_v1_rows_survive_as_assembly_sessions_and_round_trip(migrated):
    config, path, snapshot, before, legacy = migrated
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT id, purpose, calibrated_at, task_id FROM production_vna_sessions").fetchall()
        assert {purpose for _, purpose, _, _ in rows} == {"assembly"} and all(r[2] is None and r[3] for r in rows)
        assert (legacy["id"], "assembly", None, legacy["task_id"]) in rows
        assert {ops for (ops,) in db.execute("SELECT ops FROM warehouse_vna_control")} <= {"[]"}
    command.downgrade(config, PARENT)
    assert snapshot() == before
    command.upgrade(config, REVISION)


def test_session_constraints(migrated):
    _, path, _, _, legacy = migrated
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("UPDATE production_vna_sessions SET state='recorded'")
        bench = dict(purpose="bench", task_id=None, operation_id=None, product_position=None, state="ready")
        for changes in ({"purpose": "other", "task_id": None, "operation_id": None, "product_position": None},
                        {**bench, "task_id": legacy["task_id"]},                        # a bench session has no task
                        {"task_id": None},                                               # an ОТК session needs it
                        {"state": "ready"},                                              # ready/releasing: bench only
                        {**bench, "calibrated_at": "2026-11-19 10:00:00"},               # all or nothing
                        {**bench, "profile_hash": "short"},
                        {**bench, "profile_hash": "a" * 64},                            # NULL iff calibrated_at NULL
                        {"calibration_temps": "{}"},                                     # bench only
                        {**bench, "calibration_check_id": uuid4().hex,
                         "calibrated_at": "2026-11-19 10:00:00", "calibration_temps": "{}", "profile_hash": "a" * 64}):
            with pytest.raises(sqlite3.IntegrityError):
                _session(db, **changes)
        _session(db, **bench)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):   # the physical lease still holds for bench
            _session(db, **bench)


def test_command_ops_and_item_constraints(migrated):
    _, path, _, _, legacy = migrated
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("UPDATE production_vna_sessions SET state='recorded'")
        bench = _session(db, purpose="bench", task_id=None, operation_id=None, product_position=None, state="ready")

        def command_row(op):
            command_id = uuid4().hex
            db.execute("INSERT INTO production_vna_commands (id, session_id, expected_version, expected_step, op, args,"
                       " status, result, result_hash, created_at) VALUES (?, ?, ?, 0, ?, '{}', 'done', '{}', '', '2026-11-19')",
                       (command_id, bench["id"], abs(hash(command_id)) % 10_000 + 10, op))
            return command_id
        for op in ("device_status", "apply_screen", "acm_confidence", "store_memory", "release", "read_kit"):
            command_row(op)
        with pytest.raises(sqlite3.IntegrityError):
            command_row("scpi_text")
        capture = command_row("capture")
        artifact = uuid4().hex
        db.execute("INSERT INTO production_vna_artifacts (id, session_id, command_id, file_name, content, sha256, evidence,"
                   " created_at) VALUES (?, ?, ?, 'trace.s2p', x'01', ?, '{}', '2026-11-19')",
                   (artifact, bench["id"], capture, "a" * 64))
        instrument = _first(db, "production_instruments")
        group = uuid4().hex

        def item(**changes):
            values = dict(id=uuid4().hex, session_id=bench["id"], command_id=capture, artifact_id=artifact,
                          run_id=uuid4().hex, group_id=group, connection_no=1, kind="instrument", target="dut",
                          instrument_id=instrument, result="{}", created_at="2026-11-19 10:00:00")
            values.update(changes)
            db.execute("INSERT INTO production_vna_bench_items (" + ",".join(values) + ") VALUES (" +
                       ",".join("?" for _ in values) + ")", list(values.values()))
        for changes in ({"kind": "invented"}, {"target": "port1"}, {"connection_no": 4}, {"connection_no": 0},
                        {"verdict": "maybe"}, {"instrument_id": None}, {"kind": "load", "target": "port1"},
                        {"kind": "flex", "target": "thru", "instrument_id": None, "connection_no": 2},
                        {"kind": "kit_standards", "target": "thru", "standard": "OPEN"},
                        {"kind": "assembly", "instrument_id": None}, {"standard": "OPEN"},
                        {"recorded_at": "2026-11-19 10:00:00"}, {"command_id": uuid4().hex}):
            with pytest.raises(sqlite3.IntegrityError):
                item(**changes)
        item(verdict="border")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            item(connection_no=1, command_id=command_row("capture"))


def test_downgrade_refuses_bench_history(migrated):
    config, path, snapshot, before, _ = migrated
    with sqlite3.connect(path) as db:
        db.execute("UPDATE production_vna_sessions SET state='recorded'")
        _session(db, purpose="bench", task_id=None, operation_id=None, product_position=None, state="recorded")
    with pytest.raises(RuntimeError, match="рабочего места"):
        command.downgrade(config, PARENT)
