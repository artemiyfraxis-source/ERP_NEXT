"""0116_reference_stock_links: a new append-only journal; bad rows are refused by the database itself, a deleted
record leaves the row (record_id → NULL), a lot with a journal row cannot be deleted; nothing existing changes."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

LOT = "11111111111111111111111111111111"
RECORD = "22222222222222222222222222222222"
ACTOR = "33333333333333333333333333333333"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _insert(connection, row_id: str, *, event="linked", lot=LOT, record=RECORD, tab="mat", grn="FRX.26.0000042",
            text="Контакт SXH", actor="Кладовщик"):
    connection.execute(
        "INSERT INTO reference_stock_links (id, occurred_at, event, lot_id, warehouse, grn, package, article, name,"
        " record_id, record_tab, record_text, changes, actor_id, actor_name)"
        " VALUES (?, '2026-10-13 11:30:00', ?, ?, 'ЦАР', ?, '', 'SXH-001T', 'Контакт', ?, ?, ?, '[]', ?, ?)",
        (row_id, event, lot, grn, record, tab, text, ACTOR, actor))


def test_migration_creates_the_journal_with_its_rules_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
            " unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
            " VALUES (?, 1, 'ЦАР', 'FRX.26.0000042', '', 'SXH-001T', '', 'Контакт', '', '', 'шт', '', 5, 0, 0, '[]')", (LOT,))
        connection.execute(
            "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields)"
            " VALUES (?, 'mat', 'Контакт SXH', '', '', 'Контакт SXH', '{}')", (RECORD,))
        lots_before = connection.execute("SELECT * FROM warehouse_lots").fetchall()

    command.upgrade(config, "0116_reference_stock_links")
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT * FROM warehouse_lots").fetchall() == lots_before
        _insert(connection, "a1")
        _insert(connection, "a2", event="created", record=None)
        for bad in (
            {"event": "deleted"}, {"event": ""}, {"tab": "kab_coax"}, {"tab": "zzz"}, {"grn": " "}, {"text": ""},
            {"actor": "  "}, {"lot": None}, {"lot": "44444444444444444444444444444444"},
            {"record": "55555555555555555555555555555555"},
        ):
            try:
                _insert(connection, "bad", **bad)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f"accepted: {bad}")
        try:
            connection.execute("DELETE FROM warehouse_lots WHERE id = ?", (LOT,))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a lot with a journal row was deleted")
        connection.execute("DELETE FROM tech_operation_records WHERE id = ?", (RECORD,))
        kept = connection.execute("SELECT id, record_id, record_text FROM reference_stock_links ORDER BY id").fetchall()
        assert kept == [("a1", None, "Контакт SXH"), ("a2", None, "Контакт SXH")]
        connection.commit()
    finally:
        connection.close()

    command.downgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "reference_stock_links" not in tables
        assert connection.execute("SELECT * FROM warehouse_lots").fetchall() == lots_before
