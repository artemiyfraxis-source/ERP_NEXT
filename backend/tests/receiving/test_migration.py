import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_act_date_migration_backfills_the_last_inspection_from_the_audit(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "receiving.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0030_warehouse_lot_details")
    inspected, in_transit = "6" * 32, "7" * 32
    with sqlite3.connect(database_path) as connection:
        for line_id, number, status in ((inspected, 1, "Ожидание"), (in_transit, 2, "В пути")):
            connection.execute(
                """
                INSERT INTO receiving_lines (
                    id, source_event_id, source_idempotency_key, procurement_line_id, legacy_id,
                    status, version, created_at, warehouse, article, series, name, item_type,
                    purpose, goal, source_link, supplier, ordered_quantity, unit,
                    quality_document_required, nonconformities, backorder_quantity, package,
                    warehouse_event_emitted
                ) VALUES (?, ?, ?, ?, ?, ?, 2, '2026-09-21 10:00:00', 'ЦАР', 'CON-001', '',
                    'Разъём', 'Комплектующие', 'FRX.001', '', '', 'ООО Поставка', 10, 'шт',
                    0, '[]', 0, '', 0)
                """,
                (line_id, str(number) * 32, f"key-{number}", str(number + 2) * 32, f"SM-{number}", status),
            )
        for event_id, occurred_at in (("8" * 32, "2026-09-22 09:00:00"), ("9" * 32, "2026-09-24 15:30:00")):
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, aggregate_id, event_type, actor_id, actor_name, actor_department,
                    occurred_at, details
                ) VALUES (?, ?, 'receiving.inspected', ?, 'Приёмщик', 'ОТК', ?, '{}')
                """,
                (event_id, inspected, "0" * 32, occurred_at),
            )

    command.upgrade(config, "0031_receiving_inspected_on")
    with sqlite3.connect(database_path) as connection:
        assert dict(connection.execute("SELECT legacy_id, inspected_on FROM receiving_lines")) == {
            "SM-1": "2026-09-24",
            "SM-2": None,
        }

    command.downgrade(config, "0030_warehouse_lot_details")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(receiving_lines)")}
    assert "inspected_on" not in columns


BEFORE = "0031_receiving_inspected_on"
AFTER = "0032_receiving_vk_revisions"


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(receiving_lines)")}


def _unique_columns(connection: sqlite3.Connection) -> dict[str, list[str]]:
    result = {}
    for row in connection.execute("PRAGMA index_list(receiving_lines)"):
        if row[2] == 1:
            result[row[1]] = [info[2] for info in connection.execute(f"PRAGMA index_info('{row[1]}')")]
    return result


def _insert(connection: sqlite3.Connection, suffix: str, status: str) -> None:
    connection.execute(
        "INSERT INTO receiving_lines (id, source_event_id, source_idempotency_key, procurement_line_id, legacy_id,"
        " status, version, created_at, warehouse, article, series, name, item_type, purpose, goal, source_link,"
        " supplier, ordered_quantity, unit, quality_document_required, nonconformities, backorder_quantity,"
        " package, warehouse_event_emitted) VALUES (?, ?, ?, 'p1', ?, ?, 1, '2026-09-21', 'ЦАР', 'A', '', 'N',"
        " 'T', 'FRX', '', '', 'S', 10, 'шт', 0, '[]', 0, '', 0)",
        (f"id{suffix}", f"ev{suffix}", f"key{suffix}", f"SM-{suffix}", status),
    )


def test_vk_revision_migration_keeps_one_live_line_per_procurement_line(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "receiving.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert ["procurement_line_id"] in _unique_columns(connection).values()
        assert "revision_of_id" not in _columns(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        assert {"revision_of_id", "revision_root_id"} <= _columns(connection)
        uniques = _unique_columns(connection)
        assert uniques["ux_receiving_live_procurement_line"] == ["procurement_line_id"]
        assert ["procurement_line_id"] not in [
            columns for name, columns in uniques.items() if name != "ux_receiving_live_procurement_line"
        ]
        assert ["revision_of_id"] in uniques.values()
        _insert(connection, "1", "Архив ВК")
        _insert(connection, "2", "В пути")
        try:
            _insert(connection, "3", "Ожидание")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a second live line for one procurement line was accepted")
        connection.execute("DELETE FROM receiving_lines")
        connection.commit()

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert ["procurement_line_id"] in _unique_columns(connection).values()
        assert "revision_of_id" not in _columns(connection)

    command.upgrade(config, AFTER)


def test_supplier_return_migration_adds_description_and_counter_with_defaults(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "receiving.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0049_references_erp_edit_provenance")
    with sqlite3.connect(database_path) as connection:
        assert "supplier_returns" not in _columns(connection)
        _insert(connection, "1", "Принято")

    command.upgrade(config, "0050_receiving_supplier_returns")
    with sqlite3.connect(database_path) as connection:
        assert {"nonconformity_description", "supplier_returns"} <= _columns(connection)
        assert connection.execute(
            "SELECT nonconformity_description, supplier_returns FROM receiving_lines"
        ).fetchall() == [("", 0)]

    command.downgrade(config, "0049_references_erp_edit_provenance")
    with sqlite3.connect(database_path) as connection:
        assert "supplier_returns" not in _columns(connection)


def test_document_details_migration_adds_columns_and_lets_batches_share_the_procurement_line(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "receiving.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0050_receiving_supplier_returns")
    with sqlite3.connect(database_path) as connection:
        _insert(connection, "1", "Принято")

    command.upgrade(config, "0051_receiving_document_details")
    with sqlite3.connect(database_path) as connection:
        assert {
            "order_number", "expected_arrival", "document_number", "document_date", "document_name",
            "inspection_note", "manufacture_date", "split_of_id",
        } <= _columns(connection)
        index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_receiving_live_procurement_line'"
        ).fetchone()[0]
        assert "split_of_id IS NULL" in index

    command.downgrade(config, "0050_receiving_supplier_returns")
    with sqlite3.connect(database_path) as connection:
        assert "split_of_id" not in _columns(connection)


def test_acts_migration_adds_numbers_and_dates(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "receiving.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "0051_receiving_document_details")
    with sqlite3.connect(database_path) as connection:
        _insert(connection, "1", "Принято")

    command.upgrade(config, "0052_receiving_acts")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT vk_act_number, vk_act_date, use_permit_number FROM receiving_lines"
        ).fetchall() == [("", None, "")]

    command.downgrade(config, "0051_receiving_document_details")
    with sqlite3.connect(database_path) as connection:
        assert "vk_act_number" not in _columns(connection)
