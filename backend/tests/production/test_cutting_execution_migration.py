"""Additive execution schema preserves history and rejects concurrent open group sessions."""

import sqlite3
from uuid import uuid4

import pytest
from alembic import command

from tests.production.test_box_luhn_migration import _config, _insert
from tests.production.test_cutting_groups_migration import _group


def test_execution_migration_constraints_and_history(tmp_path):
    path = tmp_path / "execution.db"
    config = _config(path)
    command.upgrade(config, "0186_cutting_groups")
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT count(*) FROM production_work_sessions").fetchone()
    command.upgrade(config, "0187_cutting_execution")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM production_work_sessions").fetchone() == before
    command.downgrade(config, "0186_cutting_groups")
    command.upgrade(config, "0187_cutting_execution")
    with sqlite3.connect(path) as db:
        group = _group(db)
        # Isolate unique/check behavior; FK targets are tested by the runtime fixture.
        db.execute("INSERT INTO production_cutting_work VALUES (?, ?, 0, 0, NULL)", (uuid4().hex, group))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO production_cutting_work VALUES (?, ?, 0, 0, NULL)", (uuid4().hex, group))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO production_cutting_work VALUES (?, ?, 1, -1, NULL)", (uuid4().hex, group))
        db.execute("INSERT INTO production_cutting_work VALUES (?, ?, 1, 0, NULL)", (uuid4().hex, group))
    with pytest.raises(RuntimeError, match="history"):
        command.downgrade(config, "0186_cutting_groups")
