"""Миграция 0082: таблица задач арбитра — уникальность (kind, source_event_id), статусы, откат."""

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0081_production_operation_inputs"
AFTER = "0082_project_tasks"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _task(connection: sqlite3.Connection, *, kind: str, event: str, status: str = "open") -> None:
    connection.execute(
        "INSERT INTO project_tasks (id, project, module, kind, title, source_event_id, source_type, status, created_at)"
        " VALUES (?, '708К.4', 'production', ?, 'Поставить в план: 708К.4 КБ1', ?, 'techcards.project_issued', ?,"
        " '2026-10-04 12:00:00')",
        (uuid4().hex, kind, event, status),
    )


def test_upgrade_creates_the_task_table_and_downgrade_drops_it(tmp_path):
    database_path = tmp_path / "tasks.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    command.upgrade(config, AFTER)

    event = uuid4().hex
    with sqlite3.connect(database_path) as connection:
        _task(connection, kind="plan_item", event=event)
        _task(connection, kind="launch_task", event=event)  # другое правило на то же событие — можно
        with pytest.raises(sqlite3.IntegrityError):
            _task(connection, kind="plan_item", event=event)
        with pytest.raises(sqlite3.IntegrityError):
            _task(connection, kind="ship_goods", event=uuid4().hex, status="maybe")
        assert connection.execute("SELECT item_index, closed_by FROM project_tasks LIMIT 1").fetchone() == ("", "")
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(project_tasks)")}
    assert {"ix_project_tasks_project", "ix_project_tasks_module_status", "ix_project_tasks_status_kind"} <= indexes

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "project_tasks" not in tables
