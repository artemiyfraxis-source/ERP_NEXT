"""0186 is additive; database constraints protect group history even outside the application."""

import sqlite3
from uuid import uuid4

import pytest
from alembic import command

from tests.production.test_box_luhn_migration import _config, _insert

BEFORE = "0185_shared_accessories"
AFTER = "0186_cutting_groups"


@pytest.fixture()
def migrated(tmp_path):
    path = tmp_path / "cutting.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        task = _insert(connection, "production_tasks", {"number": "ПЗ-0001", "source_idempotency_key": "k",
            "status": "Запущено", "quantity": 10, "flow_mode": "mixed", "transfer_batch": None,
            "early_quantity": None, "techcard_revision": None})
        operation = _insert(connection, "production_operations", {"task_id": task, "position": 1, "code": "CUT",
            "stage": "Резка", "seconds_per_unit": 10, "setup_seconds": 0, "norm_seconds": 100})
        before = connection.execute("SELECT * FROM production_operations").fetchall()
    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM production_operations").fetchall() == before
        assert connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 1
    return path, config, task, operation


def _group(connection, **changes):
    return _insert(connection, "production_cutting_groups", {"state": "open", "recipe": "{}",
        "number": str(uuid4()), "idempotency_key": str(uuid4()), "version": 1, "recipe_version": 1, **changes})


def _member(connection, group, task, operation, **changes):
    return _insert(connection, "production_cutting_members", {"group_id": group, "task_id": task,
        "operation_id": operation, "position": 1, "assembly_quantity": 10, "blank_quantity": 10,
        "row_snapshot": "{}", "state": "active", **changes})


def test_empty_group_history_downgrades_with_existing_tasks(migrated):
    path, config, *_ = migrated
    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM production_tasks").fetchone()[0] == 1
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='production_cutting_groups'").fetchall()


def test_constraints_and_history_refusal(migrated):
    path, config, task, operation = migrated
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        group = _group(connection)
        other = _group(connection)
        member = _member(connection, group, task, operation)
        with pytest.raises(sqlite3.IntegrityError):
            _member(connection, other, task, operation)
        with pytest.raises(sqlite3.IntegrityError):
            _member(connection, other, uuid4().hex, operation, state="split")
        for changes in ({"assembly_quantity": 0}, {"blank_quantity": -1}, {"state": "bogus"}):
            with pytest.raises(sqlite3.IntegrityError):
                _member(connection, other, task, operation, **changes)
        for changes in ({"version": 0}, {"recipe_version": 0}, {"state": "bogus"}):
            with pytest.raises(sqlite3.IntegrityError):
                _group(connection, **changes)
        for column in ("number", "idempotency_key"):
            duplicate = connection.execute(f"SELECT {column} FROM production_cutting_groups WHERE id=?", (group,)).fetchone()[0]
            with pytest.raises(sqlite3.IntegrityError):
                _group(connection, **{column: duplicate})
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM production_operations WHERE id=?", (operation,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM production_cutting_groups WHERE id=?", (group,))
        connection.execute("UPDATE production_cutting_members SET state='split' WHERE id=?", (member,))
        _member(connection, other, task, operation)  # inactive history does not monopolize active membership
    with pytest.raises(RuntimeError, match="история групп резки"):
        command.downgrade(config, BEFORE)
