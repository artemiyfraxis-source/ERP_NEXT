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
    command.upgrade(config, "0057_shipping_optional_terms")
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
    command.upgrade(config, "0058_shipping_upd_files")
    with sqlite3.connect(database_path) as connection:
        assert {row[1] for row in connection.execute("PRAGMA table_info(shipping_files)")} >= {"content", "size", "uploaded_by"}
    command.downgrade(config, "0057_shipping_optional_terms")
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'shipping_files'").fetchall()


def test_sent_migration_backfills_from_shipments_that_left(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "sent.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0070_purpose_rnd_merge")
    with sqlite3.connect(database_path) as connection:
        for good in ("g1", "g2"):
            connection.execute(
                "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit,"
                " accepted, shipped, note) VALUES (?, 1, '591К.3', ?, '', '', 'Жгут', 10, 'шт', 10, 7, '')", (good, good))
        for shipment, status in (("s1", "Закрыта"), ("s2", "Отгружена"), ("s3", "Упакована")):
            connection.execute(
                "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact,"
                " phone, carrier, upd_number, upd_url, pl_number, dn_number, track_number, manager, note)"
                " VALUES (?, ?, 1, '2026-09-22', ?, '', '', '', '', '', '', '', '', '', '', '', '', '')", (shipment, shipment, status))
        for line, shipment, good, picked in (("l1", "s1", "g1", 2), ("l2", "s2", "g1", 3), ("l3", "s3", "g1", 2), ("l4", "s3", "g2", 7)):
            connection.execute(
                "INSERT INTO shipment_lines (id, shipment_id, finished_good_id, position, project, item_index, article, name,"
                " requested, picked, unit) VALUES (?, ?, ?, 1, '591К.3', '', '', '', ?, ?, 'шт')", (line, shipment, good, picked, picked))

    command.upgrade(config, "0071_finished_goods_sent")
    with sqlite3.connect(database_path) as connection:
        rows = dict(connection.execute("SELECT id, sent FROM finished_goods").fetchall())
        before = {row[0] for row in connection.execute("SELECT before_erp FROM finished_goods")}
    assert rows == {"g1": 5, "g2": 0} and before == {0}

    command.downgrade(config, "0070_purpose_rnd_merge")
    with sqlite3.connect(database_path) as connection:
        assert "sent" not in {row[1] for row in connection.execute("PRAGMA table_info(finished_goods)")}
