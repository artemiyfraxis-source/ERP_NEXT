import sqlite3
from pathlib import Path

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
    command.upgrade(config, "0062_procurement_invoice_files")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(procurement_files)")}
    assert columns >= {"line_id", "content", "size", "uploaded_by"}
    command.downgrade(config, "0061_procurement_traceability")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'procurement_files'").fetchall()
