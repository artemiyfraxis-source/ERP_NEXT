import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from app.modules.production import resource_assignment_infrastructure, resource_infrastructure
from app.modules.equipment_setup import infrastructure, recipe_infrastructure
from tests.migrations.seed_any_revision import seed
from tests.shipping.test_pack_decision_migration import _config

BEFORE = "0169_shipping_pack_permissions"
AFTER = "0170_equipment_setup_resources"
TABLES = {table.name for module in (resource_assignment_infrastructure, resource_infrastructure, infrastructure, recipe_infrastructure)
    for item in vars(module).values() if isinstance(item, type) and (table := getattr(item, "__table__", None)) is not None
} - {"production_resource_allocation_machines"}  # 0172
assert len(TABLES) == 12


def snapshot(connection, names):
    return {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}


def test_additive_schema_matches_models_and_preserves_seeded_history(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "resources.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
        old = set(inspect(engine).get_table_names()) - {"alembic_version"}
        with sqlite3.connect(path) as connection:
            before = snapshot(connection, old)
        command.upgrade(config, AFTER)
        # The tables as 0170 made them; their shape at head (machines by БД.ОБ unit id) — test_unit_ids_migration.
        assert set(inspect(engine).get_table_names()) - old - {"alembic_version"} == TABLES
        with sqlite3.connect(path) as connection:
            assert snapshot(connection, old) == before
            assert connection.execute("SELECT id, version FROM production_resource_generation").fetchall() == [(1, 0)]
            for name in TABLES - {"production_resource_generation"}:
                assert connection.execute(f'SELECT count(*) FROM "{name}"').fetchone() == (0,)
        command.downgrade(config, BEFORE)
        with sqlite3.connect(path) as connection:
            assert snapshot(connection, old) == before
    finally:
        engine.dispose()


def test_downgrade_refuses_used_resource_history(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "used.db"
    config = _config(path)
    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE production_resource_generation SET version = 1 WHERE id = 1")
    with pytest.raises(RuntimeError, match="generation history"):
        command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (AFTER,)
