"""Migration 0157 (owner 28.10.2026, «Сканер с любого ПК»): the agent registry, the install tokens, a job's target."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def test_the_agent_tables_hold_their_rules_and_the_migration_goes_both_ways(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        agent = "INSERT INTO warehouse_scan_agents (machine_id, first_seen, last_seen) VALUES (?, '2026-10-28', '2026-10-28')"
        connection.execute(agent, ("m-1",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(agent, ("",))  # an agent has an id
        job = ("INSERT INTO warehouse_scan_jobs (id, status, requested_by, created_at, target_machine_id)"
               " VALUES (?, 'pending', 'Кладовщик', '2026-10-28', ?)")
        connection.execute(job, ("a" * 32, "m-1"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(job, ("b" * 32, "ghost"))  # a job's target is a registered agent
        install = ("INSERT INTO warehouse_scan_agent_installs (id, token_hash, created_by, created_by_name, created_at,"
                   " expires_at, used_at, used_by_machine) VALUES (?, ?, ?, 'Админ', '2026-10-28', '2026-10-28', ?, ?)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(install, ("c" * 32, "h1", "d" * 32, "2026-10-28", None))  # used_at goes with used_by
        connection.execute(install, ("c" * 32, "h1", "d" * 32, None, None))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(install, ("e" * 32, "h1", "d" * 32, None, None))  # the hash is unique
    command.downgrade(config, "0156_invoice_procurement_source")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(warehouse_scan_jobs)")}
    assert "warehouse_scan_agents" not in tables and "target_machine_id" not in columns
