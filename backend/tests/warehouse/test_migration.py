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


LOT_PASSPORT = {
    "unit_mass_g", "expiry_date", "manufacturer", "description", "storage_conditions", "datasheet", "certificate",
}


def _table_columns(database_path, table):
    with sqlite3.connect(database_path) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_lot_passport_migration_adds_empty_fields_to_existing_lots_and_downgrade_removes_them(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "warehouse.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0029_procurement_supplier_orders")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO warehouse_lots (
                id, version, warehouse, grn, package, article, series, name, item_type,
                purpose, unit, storage_location, inbound, written_off, reserved,
                source_receiving_ids
            ) VALUES (?, 1, 'ЦАР', 'OLD.25.0000002', '', 'WSH-M3', '', '', '',
                'Склад', 'шт', '1F4', 500, 0, 0, '[]')
            """,
            ("6" * 32,),
        )
    assert not LOT_PASSPORT & _table_columns(database_path, "warehouse_lots")

    command.upgrade(config, "0030_warehouse_lot_details")
    assert LOT_PASSPORT <= _table_columns(database_path, "warehouse_lots")
    assert "unit_mass_g" in _table_columns(database_path, "receiving_lines")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT unit_mass_g, expiry_date, manufacturer, description, storage_conditions, datasheet, certificate,"
            " inbound FROM warehouse_lots"
        ).fetchone() == (None, None, "", "", "", "", "", 500)
        connection.execute("UPDATE warehouse_lots SET unit_mass_g = 0.00123, expiry_date = '2027-01-01'")

    command.downgrade(config, "0029_procurement_supplier_orders")
    assert not LOT_PASSPORT & _table_columns(database_path, "warehouse_lots")
    assert "unit_mass_g" not in _table_columns(database_path, "receiving_lines")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT grn, inbound FROM warehouse_lots").fetchone() == ("OLD.25.0000002", 500)
