import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

MONEY_COLUMNS = {
    "currency", "actual_price", "actual_vat_rate", "actual_price_with_vat", "actual_sum", "money_driver",
    "source_currency", "source_price", "source_price_with_vat", "source_sum",
    "exchange_rate", "exchange_rate_date", "exchange_rate_manual",
    "invoice_item_name", "invoice_number", "invoice_date",
}


def _columns(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(procurement_lines)")}


def test_money_migration_adds_nullable_money_and_downgrade_removes_it(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "procurement.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0023_production_plan_kit_marks")
    assert not MONEY_COLUMNS & _columns(database_path)

    command.upgrade(config, "0024_procurement_money")
    assert MONEY_COLUMNS <= _columns(database_path)

    command.downgrade(config, "0023_production_plan_kit_marks")
    assert not MONEY_COLUMNS & _columns(database_path)


def test_replacement_migration_adds_nullable_columns_and_downgrade_removes_them(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "procurement.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    columns = {"replacement", "replacement_decision"}

    command.upgrade(config, "0034_warehouse_item_types")
    assert not columns & _columns(database_path)

    command.upgrade(config, "0035_procurement_replacement")
    assert columns <= _columns(database_path)

    command.downgrade(config, "0034_warehouse_item_types")
    assert not columns & _columns(database_path)


def test_replacement_permission_migration_adds_nullable_columns_and_downgrade_removes_them(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "procurement.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    columns = {"permission_number", "permission_date"}

    command.upgrade(config, "0036_identity_departments")
    assert not columns & _columns(database_path)

    command.upgrade(config, "0037_procurement_replacement_permission")
    assert columns <= _columns(database_path)

    command.downgrade(config, "0036_identity_departments")
    assert not columns & _columns(database_path)


def test_invoice_files_migration_creates_and_drops_the_storage(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "files.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0067_procurement_invoice_files")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(procurement_files)")}
    assert columns >= {"line_id", "content", "size", "uploaded_by"}
    command.downgrade(config, "0066_identity_pin_admin_mode")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'procurement_files'").fetchall()


def test_stock_cover_migration_adds_covers_and_zero_covered_then_downgrade_removes_them(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "covers.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0078_wipe_projects_once")
    assert "stock_covered" not in _columns(database_path)

    command.upgrade(config, "0079_procurement_stock_covers")
    assert "stock_covered" in _columns(database_path)
    with sqlite3.connect(database_path) as connection:
        covers = {row[1] for row in connection.execute("PRAGMA table_info(procurement_stock_covers)")}
    assert covers >= {"line_id", "lot_id", "quantity", "trimmed", "consumed", "consumed_by", "project_reference"}

    command.downgrade(config, "0078_wipe_projects_once")
    assert "stock_covered" not in _columns(database_path)
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'procurement_stock_covers'"
        ).fetchall()


def _insert(connection: sqlite3.Connection, table: str, **values) -> None:
    """A row with every NOT NULL column filled by a placeholder of its type; `values` override."""
    row = {}
    for _, name, kind, not_null, default, _ in connection.execute(f"PRAGMA table_info({table})"):
        if not not_null or default is not None:
            continue
        kind = kind.upper()
        if kind.startswith("CHAR(32)"):
            row[name] = uuid4().hex
        elif kind in {"INTEGER", "BOOLEAN"} or kind.startswith("NUMERIC"):
            row[name] = 1
        elif kind == "DATE":
            row[name] = "2026-09-15"
        elif kind == "DATETIME":
            row[name] = "2026-09-15 11:00:00.000000"
        elif kind == "JSON":
            row[name] = "[]"
        else:
            row[name] = uuid4().hex
    row.update(values)
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))


def test_sheet_fields_migration_adds_note_and_backfills_actual_arrival_then_downgrade_removes_them(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "sheet.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0082_project_tasks")
    assert not {"note", "actual_arrival"} & _columns(database_path)

    received, waiting = uuid4().hex, uuid4().hex
    with sqlite3.connect(database_path) as connection:
        _insert(connection, "procurement_lines", id=received, status="Готово", version=4)
        _insert(connection, "procurement_lines", id=waiting, status="В пути", version=3)
        # The first inspection was archived by a ВК revision; the latest one decides ФАКТ. ДАТА.
        _insert(connection, "receiving_lines", procurement_line_id=received, status="Архив ВК", inspected_on="2026-09-20")
        _insert(connection, "receiving_lines", procurement_line_id=received, status="Принято", inspected_on="2026-09-24")
        _insert(connection, "receiving_lines", procurement_line_id=waiting, status="В пути", inspected_on=None)

    command.upgrade(config, "0083_procurement_sheet_fields")
    assert {"note", "actual_arrival"} <= _columns(database_path)
    with sqlite3.connect(database_path) as connection:
        rows = dict(connection.execute("SELECT id, actual_arrival FROM procurement_lines"))
        versions = dict(connection.execute("SELECT id, version FROM procurement_lines"))
        notes = {row[0] for row in connection.execute("SELECT note FROM procurement_lines")}
    assert rows == {received: "2026-09-24", waiting: None}
    assert versions == {received: 4, waiting: 3}
    assert notes == {None}

    command.downgrade(config, "0082_project_tasks")
    assert not {"note", "actual_arrival"} & _columns(database_path)
