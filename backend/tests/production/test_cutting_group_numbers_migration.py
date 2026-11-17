"""Existing groups keep their identities and history while receiving chronological global numbers."""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.production.cutting_infrastructure import SqlAlchemyCuttingRepository
from tests.production.test_box_luhn_migration import _config, _insert
from tests.production.test_cutting_groups_migration import _group, _member

BEFORE = "0188_instrument_labels"
AFTER = "0189_cutting_group_numbers"


@pytest.mark.parametrize("count", [0, 5])
def test_backfill_preserves_history_unique_numbers_and_continues_the_series(tmp_path, count):
    path = tmp_path / "group-numbers.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO production_sequences (name, value) VALUES ('task', 42)")
        for index in reversed(range(count)):
            task = _insert(connection, "production_tasks", {"number": f"ПЗ-{index + 1:04d}",
                "source_idempotency_key": f"task-{index}",
                "status": "Запущено", "quantity": 10, "flow_mode": "mixed", "transfer_batch": None,
                "early_quantity": None, "techcard_revision": None})
            operation = _insert(connection, "production_operations", {"task_id": task, "position": 1})
            group = _group(connection, number="РЗ-0001" if index == 4 else f"РЗ-HASH{index}",
                           site="ЦАР" if index % 2 else "ЖУК", state="open" if index % 2 else "cancelled",
                           created_at=f"2026-11-{index // 2 + 1:02d} 09:00:00")
            _member(connection, group, task, operation, state="active" if index % 2 else "cancelled")
        groups_before = connection.execute("SELECT * FROM production_cutting_groups ORDER BY created_at, id").fetchall()
        columns = [column[1] for column in connection.execute("PRAGMA table_info(production_cutting_groups)")]
        number_index = columns.index("number")
        history = {table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in (
            "production_tasks", "production_operations", "production_cutting_members")}
    command.upgrade(config, AFTER)
    command.upgrade(config, AFTER)  # Alembic replay does not renumber or advance the counter.
    with sqlite3.connect(path) as connection:
        groups_after = connection.execute("SELECT * FROM production_cutting_groups ORDER BY created_at, id").fetchall()
        assert [row[number_index] for row in groups_after] == [f"РЗ-{i:04d}" for i in range(1, count + 1)]
        without_number = lambda rows: [row[:number_index] + row[number_index + 1:] for row in rows]
        assert without_number(groups_after) == without_number(groups_before)
        for table, rows in history.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert dict(connection.execute("SELECT name, value FROM production_sequences")) == {"task": 42, "cutting_group": count}
        if count:
            with pytest.raises(sqlite3.IntegrityError):
                _group(connection, number="РЗ-0001")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with Session(engine) as session:
            assert SqlAlchemyCuttingRepository(session).reserve_number() == f"РЗ-{count + 1:04d}"
            session.rollback()
            assert SqlAlchemyCuttingRepository(session).reserve_number() == f"РЗ-{count + 1:04d}"
            session.commit()
    finally:
        engine.dispose()
