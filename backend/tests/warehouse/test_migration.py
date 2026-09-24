import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_article_migration_moves_legacy_name_and_downgrade_restores_identity(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "warehouse.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0021_production_scheduling")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO warehouse_lots (
                id, version, warehouse, grn, package, name, item_type, purpose,
                unit, storage_location, inbound, written_off, reserved,
                source_receiving_ids
            ) VALUES (?, 1, 'ЦАР', 'OLD.26.0000001', '', 'GHR-04V-S',
                'Комплектующие', 'Склад', 'шт', '1F4', 1, 0, 0, '[]')
            """,
            ("4" * 32,),
        )

    command.upgrade(config, "0022_warehouse_articles")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT article, name FROM warehouse_lots"
        ).fetchone() == ("GHR-04V-S", "")
        connection.execute("UPDATE warehouse_lots SET name = 'Разъём питания'")

    command.downgrade(config, "0021_production_scheduling")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT name FROM warehouse_lots"
        ).fetchone() == ("GHR-04V-S",)
