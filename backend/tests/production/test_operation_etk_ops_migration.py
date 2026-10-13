"""0120: операции ЭТК полуфабриката — пустая JSON-колонка; запущенные задания не меняются."""

import json
import sqlite3
from uuid import UUID

from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from tests.production.test_box_luhn_migration import _config, _insert

BEFORE = "0119_box_code"
AFTER = "0120_operation_etk_ops"


def test_etk_ops_migration_adds_an_empty_column_and_old_operations_keep_working(tmp_path):
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
        assert connection.execute("SELECT etk_ops FROM production_operations WHERE id = ?", (operation,)).fetchone() == (None,)
        assert [row[:-1] for row in connection.execute("SELECT * FROM production_operations").fetchall()] == before
    # Текущий код читает старую операцию: без ПФ — одна операция ЭТК.
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    with SqlAlchemyProductionUnitOfWork(sessionmaker(bind=engine, class_=Session)) as uow:
        stored = uow.production.get(UUID(task))
    engine.dispose()
    assert stored.operations[0].etk_ops == () and stored.operations[0].skill_codes == ("OP-1",)

    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE production_operations SET etk_ops = ? WHERE id = ?",
                           (json.dumps([{"position": 1, "code": "OP-1", "stage": "Резка"}]), operation))
        connection.commit()

    command.downgrade(config, BEFORE)

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(production_operations)")]
        assert "etk_ops" not in columns
        assert connection.execute("SELECT COUNT(*) FROM production_operations").fetchone()[0] == 1
