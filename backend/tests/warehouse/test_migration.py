import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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


def test_location_migration_widens_the_place_to_hold_an_old_address_as_is(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "warehouse.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0026_production_output_corrections")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO warehouse_lots (
                id, version, warehouse, grn, package, article, series, name, item_type,
                purpose, unit, storage_location, inbound, written_off, reserved,
                source_receiving_ids
            ) VALUES (?, 1, 'ЖУК', 'OLD.25.0000001', '', 'CON-001', '', '', '',
                'Склад', 'шт', '1F4', 3, 0, 0, '[]')
            """,
            ("5" * 32,),
        )

    command.upgrade(config, "0027_warehouse_location_as_is")
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    columns = {
        table: {column["name"]: column["type"] for column in inspect(engine).get_columns(table)}
        for table in ("warehouse_lots", "fulfillment_lines")
    }
    indexes = {index["name"] for index in inspect(engine).get_indexes("warehouse_lots")}
    engine.dispose()
    assert columns["warehouse_lots"]["storage_location"].length == 255
    assert columns["fulfillment_lines"]["lot_storage_location"].length == 255
    assert "ix_warehouse_location" in indexes
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT storage_location, inbound FROM warehouse_lots"
        ).fetchone() == ("1F4", 3)

    command.downgrade(config, "0026_production_output_corrections")
