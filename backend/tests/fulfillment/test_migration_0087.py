"""Миграция 0087: off_book у строк комплектации, таблица резервов техкарт, lot_id «Со склада» допускает пусто."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BEFORE = "0086_identity_access_levels"
AFTER = "0087_fulfillment_off_book_kit_reserves"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, tuple]:
    return {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}


def test_upgrade_adds_off_book_kit_reserves_and_nullable_cover_lot_then_downgrade_reverts(tmp_path):
    database_path = tmp_path / "erp.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO fulfillment_requests (id, number, source_idempotency_key, source_fingerprint, version,"
            " created_at, requester, purpose, status) VALUES ('r1', 'REQ.26.0000001', 'k', 'f', 1,"
            " '2026-10-05 12:00:00', 'Мастер', '728К.2 · КБ1 · Жгут', 'Новая')"
        )
        connection.execute(
            "INSERT INTO fulfillment_lines (id, request_id, position, requirement_id, article, name, unit,"
            " requirement, reserved, issued, returned_good, defective_returned, defective_missing)"
            " VALUES ('l1', 'r1', 0, 'l1', 'КД-1', 'Разъём', 'шт', 4, 0, 0, 0, 0, 0)"
        )

    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT off_book FROM fulfillment_lines WHERE id = 'l1'").fetchone() == (0,)
        off_book = _columns(connection, "fulfillment_lines")["off_book"]
        assert off_book[3] == 1  # NOT NULL with a default
        reserves = _columns(connection, "fulfillment_kit_reserves")
        assert {
            "id", "project", "item_index", "article", "lot_id", "warehouse", "grn", "package", "unit", "quantity",
            "consumed", "consumed_by", "created_at", "created_by", "released_at", "version",
        } == set(reserves)
        assert _columns(connection, "procurement_stock_covers")["lot_id"][3] == 0  # nullable now
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(fulfillment_kit_reserves)")}
        assert {"ix_fulfillment_kit_reserves_lot_quantity", "ix_fulfillment_kit_reserves_project_item"} <= indexes
        cover_indexes = {row[1] for row in connection.execute("PRAGMA index_list(procurement_stock_covers)")}
        assert "ix_procurement_stock_covers_lot_quantity" in cover_indexes

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "fulfillment_kit_reserves" not in tables
        assert "off_book" not in _columns(connection, "fulfillment_lines")
        assert _columns(connection, "procurement_stock_covers")["lot_id"][3] == 1
        assert connection.execute("SELECT name FROM fulfillment_lines WHERE id = 'l1'").fetchone() == ("Разъём",)
