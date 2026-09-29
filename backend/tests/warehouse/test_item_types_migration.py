"""SK-14: migration 0034 adds the empty item type dictionary and leaves the lot types as they are."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BEFORE = "0033_warehouse_writeoff_journal"
AFTER = "0034_warehouse_item_types"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_item_types_migration_adds_an_empty_dictionary_and_downgrades(tmp_path):
    database_path = tmp_path / "types.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type,"
            " purpose, unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
            " VALUES (?, 1, 'ЦАР', 'OLD.26.0000001', '', 'A-1', '', 'Трубка', 'ТуТ', 'Склад', 'шт', '1F4',"
            " 10, 0, 0, '[]')",
            ("a" * 32,),
        )

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM warehouse_item_types").fetchone() == (0,)
        connection.execute("INSERT INTO warehouse_item_types (position, name) VALUES (0, 'Кабель')")
        # An old spelling on the stock is not rewritten by the migration: renaming is an audited action.
        assert connection.execute("SELECT item_type FROM warehouse_lots").fetchall() == [("ТуТ",)]

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "warehouse_item_types" not in tables
    assert "warehouse_lots" in tables
