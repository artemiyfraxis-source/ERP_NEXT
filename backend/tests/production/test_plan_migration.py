import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_kit_mark_migration_preserves_existing_plan_events(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "production-plan.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0022_warehouse_articles")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO production_plan_events (
                id, project, sequence, occurred_at, who, kind, blocker, text, deadline
            ) VALUES (?, '720К', 1, '2026-09-24 00:00:00', 'Тест',
                'comment', '', 'До миграции', NULL)
            """,
            ("4" * 32,),
        )

    command.upgrade(config, "0023_production_plan_kit_marks")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(production_plan_events)")
        }
        assert {"item_index", "value"}.issubset(columns)
        assert connection.execute(
            "SELECT item_index, value FROM production_plan_events"
        ).fetchone() == ("", "")

    command.downgrade(config, "0022_warehouse_articles")
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(production_plan_events)")
        }
        assert "item_index" not in columns
        assert "value" not in columns
        assert connection.execute(
            "SELECT project, text FROM production_plan_events"
        ).fetchone() == ("720К", "До миграции")
