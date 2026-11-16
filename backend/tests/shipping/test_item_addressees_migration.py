"""Additive migration retains every old column and starts with no overrides or snapshots."""

import importlib.util
from pathlib import Path
import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

from tests.shipping.test_pack_decision_migration import _config
from tests.migrations.seed_any_revision import seed

path = next((Path(__file__).resolve().parents[2] / "migrations" / "versions").glob("*_shipping_item_addressees.py"))
spec = importlib.util.spec_from_file_location("delivery_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def snapshot(connection, columns=None):
    if columns is None:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'")]
        columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] for table in tables}
    data = {}
    for table, names in columns.items():
        fields = ",".join(f'"{name}"' for name in names)
        data[table] = connection.execute(f'SELECT {fields} FROM "{table}"').fetchall()
    return columns, data


def test_upgrade_preserves_history_and_enforces_constraints(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    db = tmp_path / "delivery.db"
    config = _config(db)
    command.upgrade(config, migration.down_revision)
    engine = create_engine(f"sqlite+pysqlite:///{db.as_posix()}")
    try:
        assert seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(db) as connection:
        columns, before = snapshot(connection)
    command.upgrade(config, migration.revision)
    assert migration.DESTRUCTIVE is False
    with sqlite3.connect(db) as connection:
        assert snapshot(connection, columns)[1] == before
        assert connection.execute("SELECT count(*) FROM shipping_item_addressees").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM shipments WHERE delivery_snapshot_at IS NOT NULL").fetchone()[0] == 0
        connection.execute("INSERT INTO shipping_item_addressees VALUES ('591К.3','КБ1',1,'','','','',NULL,'',NULL,'','Тест','2026-11-16')")
        for sql in ("UPDATE shipping_item_addressees SET version=0", "UPDATE shipping_item_addressees SET item_index=''",
                    "UPDATE shipping_item_addressees SET phone=NULL",
                    "INSERT INTO shipping_item_addressees SELECT * FROM shipping_item_addressees"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
    with pytest.raises(RuntimeError, match="Cannot erase"):
        command.downgrade(config, migration.down_revision)
