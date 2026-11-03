"""0169 preserves historical rows and grants zero new packing quantity at cutover."""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

from tests.shipping.test_pack_decision_migration import _CARD, _GOOD, _config, _id
from tests.migrations.seed_any_revision import seed


BEFORE = "0168_requests_calc_compositions"
AFTER = "0169_shipping_pack_permissions"


def snapshot(connection):
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN ('alembic_version', 'shipping_pack_permissions')",
    )]
    return {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}


def test_cutover_sums_packed_once_per_canonical_kb_and_preserves_all_old_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "cutover.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(_CARD, ("600K", 4))
        connection.execute("UPDATE shipping_project_addressees SET pack_decided = 1, pack_decided_by = 'Менеджер',"
                           " pack_decided_at = '2026-11-02 12:00:00' WHERE project = '600K'")
        # Canonical aliases share one ceiling. Sent is already included in shipped.
        connection.execute(_GOOD, (_id(1), "600K", 5, 3, 2, "600К.1"))
        connection.execute(_GOOD, (_id(2), "600К.1", 6, 4, 1, "600К.1"))
        connection.execute(_GOOD, (_id(3), "600К.10", 5, 2, 1, "600К.10"))
        connection.execute(_GOOD, (_id(4), "601К.1", 5, 0, 0, "601К.1"))
        before = snapshot(connection)
    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        assert snapshot(connection) == before
        assert connection.execute(
            "SELECT project_key, item_index, quantity, version FROM shipping_pack_permissions ORDER BY project_key",
        ).fetchall() == [("600К.1", "КБ1", 7, 1), ("600К.10", "КБ1", 2, 1), ("601К.1", "КБ1", 0, 1)]
        assert connection.execute("SELECT count(*) FROM shipping_pack_permissions WHERE updated_by = '' OR updated_at IS NULL").fetchone()[0] == 0
        for sql in (
            "UPDATE shipping_pack_permissions SET quantity = -1",
            "UPDATE shipping_pack_permissions SET version = 0",
            "UPDATE shipping_pack_permissions SET project_key = ''",
            "UPDATE shipping_pack_permissions SET item_index = ''",
            "UPDATE shipping_pack_permissions SET quantity = NULL",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
    with pytest.raises(RuntimeError, match="Cannot erase packing permissions"):
        command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == AFTER
        assert snapshot(connection) == before


def test_empty_permission_table_can_be_downgraded_without_touching_other_tables(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "empty.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        before = snapshot(connection)
    command.upgrade(config, AFTER)
    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert snapshot(connection) == before
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'shipping_pack_permissions'").fetchall()


def test_ci_generic_seed_at_previous_revision_upgrades_without_rewriting_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "seeded.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        assert seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        before = snapshot(connection)
    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        assert snapshot(connection) == before
        assert connection.execute("SELECT count(*) FROM shipping_pack_permissions").fetchone()[0] > 0
