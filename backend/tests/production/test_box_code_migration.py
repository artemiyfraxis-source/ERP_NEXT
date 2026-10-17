"""0119: код коробки — пустая колонка с UNIQUE и таблица кодов сотрудников; старые коробки не меняются."""

import sqlite3

import pytest
from alembic import command

from tests.production.test_box_luhn_migration import _config, _insert

BEFORE = "0118_plan_baseline"
AFTER = "0119_box_code"


def test_box_code_migration_adds_unique_code_and_keeps_old_boxes(tmp_path):
    database_path = tmp_path / "boxes.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        old_box = _insert(connection, "production_boxes", {"number": "000001-8"})
        before = connection.execute("SELECT COUNT(*) FROM production_boxes").fetchone()[0]

    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM production_boxes").fetchone()[0] == before
        assert connection.execute("SELECT number, code FROM production_boxes WHERE id = ?", (old_box,)).fetchone() == ("000001-8", None)
        assert connection.execute("SELECT COUNT(*) FROM production_operator_codes").fetchone()[0] == 0
        _insert(connection, "production_boxes", {"number": "000002-6", "code": "180К.11·КБ1·13.10·Б07·1"})
        _insert(connection, "production_boxes", {"number": "000003-4", "code": None})
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "production_boxes", {"number": "000004-2", "code": "180К.11·КБ1·13.10·Б07·1"})
        user = _insert(connection, "identity_users", {"login": "belov", "email": "belov@example.test"})
        connection.execute("INSERT INTO production_operator_codes (operator_id, code) VALUES (?, ?)", (user, "Б07"))
        other = _insert(connection, "identity_users", {"login": "bykov", "email": "bykov@example.test"})
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO production_operator_codes (operator_id, code) VALUES (?, ?)", (other, "Б07"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO production_operator_codes (operator_id, code) VALUES (?, ?)", (other, ""))
        connection.commit()

    command.downgrade(config, BEFORE)

    with sqlite3.connect(database_path) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(production_boxes)")]
        assert "code" not in columns
        assert connection.execute("SELECT COUNT(*) FROM production_boxes").fetchone()[0] == before + 2
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "production_operator_codes" not in tables
