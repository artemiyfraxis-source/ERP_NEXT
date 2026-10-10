"""Миграция project_keys (U06): существующие строки получают ключ № проекта и индексы, ничего не меняется и не
теряется; откат снимает колонки и индексы, частичный уникальный индекс приёмки остаётся."""

import sqlite3

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests.production.test_operation_inputs_migration import _config, _insert

SLUG = "_project_keys"


def _migration(config: Config):
    """Эта миграция по имени (номер и предыдущая ревизия меняются при слиянии)."""
    script = ScriptDirectory.from_config(config)
    return next(item for item in script.walk_revisions() if item.revision.endswith(SLUG))


def _indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_existing_rows_get_exact_keys_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database_path = tmp_path / "keys.db"
    config = _config(database_path)
    migration = _migration(config)
    command.upgrade(config, migration.down_revision)
    with sqlite3.connect(database_path) as connection:
        _insert(connection, "production_tasks", {"number": "ПЗ-1", "source_idempotency_key": "k1", "project": " 719k.2 ",
                                                 "handed_to": "", "flow_mode": "mixed"})
        _insert(connection, "production_tasks", {"number": "ПЗ-2", "source_idempotency_key": "k2", "project": "719К.21",
                                                 "handed_to": "", "flow_mode": "mixed"})
        _insert(connection, "finished_goods", {"project": "719К", "item_index": "КБ1"})
        _insert(connection, "techcard_projects", {"project": "719К.2", "item_index": "КБ1"})
        _insert(connection, "procurement_lines", {"source_idempotency_key": "p1", "project_reference": None})
        _insert(connection, "procurement_lines", {"source_idempotency_key": "p2", "project_reference": "719К.2"})
        _insert(connection, "requests", {"legacy_id": "SM-1", "project_reference": None,
                                         "purpose": "719К.21 · КБ1 · Жгут", "designation": "719К.2.004"})
        _insert(connection, "receiving_lines", {"source_idempotency_key": "r1", "purpose": "Обеспечение",
                                                "goal": "719К.2 · КБ2", "status": "Ожидание", "package": ""})
        before = connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone()

    command.upgrade(config, migration.revision)
    with sqlite3.connect(database_path) as connection:
        assert dict(connection.execute("SELECT number, project_key FROM production_tasks")) == {
            "ПЗ-1": "719К.2", "ПЗ-2": "719К.21",
        }
        assert connection.execute("SELECT project, project_key FROM production_tasks WHERE number = 'ПЗ-1'").fetchone() \
            == (" 719k.2 ", "719К.2")  # текст проекта не тронут
        assert connection.execute("SELECT project_key FROM finished_goods").fetchone() == ("719К.1",)
        assert connection.execute("SELECT project_key FROM techcard_projects").fetchone() == ("719К.2",)
        assert sorted(row[0] for row in connection.execute("SELECT project_key FROM procurement_lines")) == [
            "", "719К.2",
        ]
        assert connection.execute(
            "SELECT project_key, purpose_project_key, designation_project_key FROM requests",
        ).fetchone() == ("", "719К.21", "719К.2")
        assert connection.execute(
            "SELECT purpose_project_key, goal_project_key FROM receiving_lines",
        ).fetchone() == ("ОБЕСПЕЧЕНИЕ", "719К.2")
        assert connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone() == before
        assert "ix_production_tasks_project_key" in _indexes(connection, "production_tasks")
        assert {"ix_requests_project_key", "ix_requests_purpose_project_key", "ix_requests_designation_project_key"} \
            <= _indexes(connection, "requests")
        assert {"ix_receiving_lines_procurement_line_id", "ix_receiving_lines_goal_project_key",
                "ux_receiving_live_procurement_line"} <= _indexes(connection, "receiving_lines")

    command.downgrade(config, migration.down_revision)
    with sqlite3.connect(database_path) as connection:
        assert "project_key" not in _columns(connection, "production_tasks")
        assert not {"project_key", "purpose_project_key", "designation_project_key"} & _columns(connection, "requests")
        assert not {"purpose_project_key", "goal_project_key"} & _columns(connection, "receiving_lines")
        assert "ix_receiving_lines_procurement_line_id" not in _indexes(connection, "receiving_lines")
        assert "ux_receiving_live_procurement_line" in _indexes(connection, "receiving_lines")
        assert connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone() == before
