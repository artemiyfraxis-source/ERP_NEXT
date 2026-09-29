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
