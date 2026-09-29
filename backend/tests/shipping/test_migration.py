import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_boxes_and_requisites_migration_upgrades_and_downgrades(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "shipping.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0049_references_erp_edit_provenance")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO shipping_project_addressees (project, version, receiver, shipping_point, contact, phone,"
            " carrier, updated_by) VALUES ('591К.3', 1, '', 'Москва', '', '', '', '')"
        )

    command.upgrade(config, "0050_shipping_boxes_requisites")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT po, hours FROM shipping_project_addressees").fetchall() == [("", "")]
        assert "tk_note" in {row[1] for row in connection.execute("PRAGMA table_info(shipments)")}
        assert {row[1] for row in connection.execute("PRAGMA table_info(shipment_boxes)")} >= {"place", "composition"}

    command.downgrade(config, "0049_references_erp_edit_provenance")
    with sqlite3.connect(database_path) as connection:
        assert "po" not in {row[1] for row in connection.execute("PRAGMA table_info(shipping_project_addressees)")}
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'shipment_boxes'").fetchall()


def test_optional_terms_migration_allows_empty_terms_and_upd_type(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "terms.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0051_shipping_optional_terms")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(shipments)")}
    assert columns["terms"] == 0 and columns["upd_type"] == 0
    command.downgrade(config, "0050_shipping_boxes_requisites")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1]: row[3] for row in connection.execute("PRAGMA table_info(shipments)")}
    assert columns["terms"] == 1 and columns["upd_type"] == 1


def test_upd_files_migration_creates_and_drops_the_storage(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "files.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0052_shipping_upd_files")
    with sqlite3.connect(database_path) as connection:
        assert {row[1] for row in connection.execute("PRAGMA table_info(shipping_files)")} >= {"content", "size", "uploaded_by"}
    command.downgrade(config, "0051_shipping_optional_terms")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'shipping_files'").fetchall()
