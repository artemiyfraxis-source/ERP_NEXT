"""Миграция 0127: входы и правки ЭТК. Существующие карты и ревизии не меняются (новые поля пусты, source = manual),
появляется очередь запросов на пересборку; значения source держит CHECK; откат возвращает прежнюю схему."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0126_operation_card_versions"
AFTER = "0127_etk_inputs_overrides"
CARD = "00000000000000000000000000000001"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_existing_rows_stay_and_new_columns_are_empty(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "etk.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO techcard_projects (id, version, project, item_index, product_name, source_calculation, status,"
            " revision, manual_operation_keys, created_at, updated_at) VALUES (?, 1, 'P', '1', 'Жгут', '', 'Выдана',"
            " 1, '[]', '2026-10-01 10:00:00', '2026-10-01 10:00:00')", (CARD,),
        )
        connection.execute(
            "INSERT INTO techcard_revisions (id, project_card_id, revision, reason, actor_name, occurred_at, document)"
            " VALUES ('00000000000000000000000000000002', ?, 1, 'Первая выдача', 'А', '2026-10-01 10:00:00', '{}')",
            (CARD,),
        )
    command.upgrade(config, AFTER)
    assert "techcard_projects 1 → 1, techcard_revisions 1 → 1" in capsys.readouterr().out
    with sqlite3.connect(path) as connection:
        assert {"inputs", "overrides", "review_note", "derived_hash"} <= _columns(connection, "techcard_projects")
        assert connection.execute(
            "SELECT inputs, overrides, review_note, derived_hash FROM techcard_projects").fetchall() == [(None,) * 4]
        assert connection.execute("SELECT source, diff FROM techcard_revisions").fetchall() == [("manual", None)]
        assert connection.execute("SELECT COUNT(*) FROM techcard_refresh_requests").fetchone() == (0,)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE techcard_revisions SET source = 'robot' WHERE project_card_id = ?", (CARD,))
    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert "inputs" not in _columns(connection, "techcard_projects")
        assert "source" not in _columns(connection, "techcard_revisions")
        assert connection.execute("SELECT COUNT(*) FROM techcard_projects").fetchone() == (1,)
