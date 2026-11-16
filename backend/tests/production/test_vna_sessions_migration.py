from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, create_engine, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.modules.production.vna_session_infrastructure import VnaArtifactRow, VnaCommandRow, VnaSessionRow
from tests.migrations.seed_any_revision import seed

PARENT = "0180_equipment_catalogs_cutover"
REVISION = "0181_vna_sessions"


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "vna.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    def snapshot():
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in
                    ("production_instruments", "production_vswr_measurements", "warehouse_scan_jobs")}
    before = snapshot()
    command.upgrade(config, REVISION)
    assert snapshot() == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    yield config, path, snapshot, before


def test_migration_preserves_existing_rows_and_empty_downgrade(migrated):
    config, _, snapshot, before = migrated
    command.downgrade(config, PARENT)
    assert snapshot() == before
    command.upgrade(config, REVISION)
    assert snapshot() == before


def _session_values(db):
    first = lambda table, key="id": db.execute(f"SELECT {key} FROM {table} LIMIT 1").fetchone()[0]
    return dict(id=uuid4().hex, state="draft", version=1, step=0,
        operator_id=first("identity_users"), machine_id=first("warehouse_scan_agents", "machine_id"),
        instrument_id=first("production_instruments"), instrument_serial="26030095",
        task_id=first("production_tasks"), operation_id=first("production_operations"), product_position=1,
        snapshot="{}", steps="[]", created_at="2026-11-16 10:00:00", updated_at="2026-11-16 10:00:00")


def _insert(db, values):
    db.execute("INSERT INTO production_vna_sessions (" + ",".join(values) + ") VALUES (" +
               ",".join("?" for _ in values) + ")", list(values.values()))


@pytest.mark.parametrize("state", ["draft", "planned", "preparing", "awaiting_standard", "acquiring_standard",
    "saving_calibration", "awaiting_dut", "acquiring_dut", "review", "aborting", "recovery_required"])
def test_db_lease_covers_every_nonterminal_state_and_duplicate_physical_serial(migrated, state):
    _, path, _, _ = migrated
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        values = {**_session_values(db), "state": state}
        _insert(db, values)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert(db, {**values, "id": uuid4().hex, "instrument_serial": "different"})
        second = db.execute("SELECT id FROM production_instruments WHERE id <> ? LIMIT 1", (values["instrument_id"],)).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            _insert(db, {**values, "id": uuid4().hex, "instrument_id": second})
        db.execute("UPDATE production_vna_sessions SET state='aborted'")
        _insert(db, {**values, "id": uuid4().hex})


def test_db_constraints_and_populated_downgrade_refusal(migrated):
    config, path, snapshot, before = migrated
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        values = _session_values(db)
        for changes in ({"state": "invented"}, {"version": 0}, {"step": -1}, {"product_position": 0},
                        {"operator_id": uuid4().hex}, {"instrument_id": uuid4().hex},
                        {"machine_id": " "}, {"machine_id": ""}, {"machine_id": None},
                        {"instrument_serial": " "}, {"snapshot": None}):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(db, {**values, **changes})
        _insert(db, values)
    with pytest.raises(RuntimeError, match="историю измерений"):
        command.downgrade(config, PARENT)
    assert snapshot() == before


def test_postgres_lease_indexes_have_partial_predicates():
    indexes = [str(CreateIndex(index).compile(dialect=postgresql.dialect())) for index in VnaSessionRow.__table__.indexes]
    assert len(indexes) == 2
    assert all("UNIQUE INDEX" in ddl and "WHERE state NOT IN ('recorded','failed','aborted')" in ddl for ddl in indexes)


def test_session_machine_is_a_nonempty_workflow_reference_not_a_cross_module_fk(migrated):
    _, path, _, _ = migrated
    assert not VnaSessionRow.__table__.c.machine_id.foreign_keys
    with sqlite3.connect(path) as db:
        assert not any(fk[2] == "warehouse_scan_agents" for fk in db.execute("PRAGMA foreign_key_list(production_vna_sessions)"))
        # Unlike the workflow, the DB intentionally does not require a warehouse schema/row.
        _insert(db, {**_session_values(db), "machine_id": "detached-pc-reference"})


def test_production_session_subset_has_no_warehouse_dependencies():
    from app.modules.identity.infrastructure import IdentityUserRow
    from app.modules.production.infrastructure import ProductionInstrumentRow, ProductionOperationRow, ProductionTaskRow

    rows = (IdentityUserRow, ProductionInstrumentRow, ProductionTaskRow, ProductionOperationRow,
            VnaSessionRow, VnaCommandRow, VnaArtifactRow)
    metadata = MetaData()
    for row in rows:
        row.__table__.to_metadata(metadata)
    assert all(fk.column.table.name in metadata.tables for table in metadata.tables.values() for fk in table.foreign_keys)
    statements = []
    engine = create_mock_engine("postgresql://", lambda statement, *args, **kwargs:
                                statements.append(str(statement.compile(dialect=postgresql.dialect()))))
    metadata.create_all(engine)
    assert any("CREATE TABLE production_vna_sessions" in sql for sql in statements)
    assert not any("REFERENCES warehouse_" in sql for sql in statements)


def test_alembic_imports_register_session_and_control_tables_in_a_fresh_process():
    backend = Path(__file__).parents[2]
    script = """
import ast
from pathlib import Path
source = ast.parse(Path('migrations/env.py').read_text(encoding='utf-8'))
imports = [node for node in source.body if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')]
exec(compile(ast.Module(body=imports, type_ignores=[]), 'alembic-model-imports', 'exec'))
from app.shared.persistence import Base
expected = {'production_vna_sessions', 'production_vna_commands', 'production_vna_artifacts', 'warehouse_vna_control'}
assert expected <= Base.metadata.tables.keys(), expected - Base.metadata.tables.keys()
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
