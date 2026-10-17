"""0126: версии карточек ЭТК на первом начале — пустая JSON-колонка; операции до миграции не меняются."""

import sqlite3

from alembic import command

from tests.production.test_box_luhn_migration import _config, _insert

BEFORE = "0125_addressee_upd_file"
AFTER = "0126_operation_card_versions"


def test_card_versions_migration_adds_an_empty_column_and_keeps_the_rows(tmp_path):
    database_path = tmp_path / "operations.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        task = _insert(connection, "production_tasks", {
            "number": "ПЗ-0001", "source_idempotency_key": "k", "status": "Запущено", "quantity": 5,
            "flow_mode": "mixed", "transfer_batch": None, "early_quantity": None, "techcard_revision": None,
        })
        operation = _insert(connection, "production_operations", {
            "task_id": task, "position": 1, "code": "OP-1", "stage": "Резка", "seconds_per_unit": 10,
            "setup_seconds": 0, "norm_seconds": 50,
        })
        before = connection.execute("SELECT * FROM production_operations").fetchall()

    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT card_versions FROM production_operations WHERE id = ?", (operation,)).fetchone() == (None,)
        assert [row[:-1] for row in connection.execute("SELECT * FROM production_operations").fetchall()] == before

    command.downgrade(config, BEFORE)

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(production_operations)")]
        assert "card_versions" not in columns
        assert connection.execute("SELECT COUNT(*) FROM production_operations").fetchone()[0] == 1
