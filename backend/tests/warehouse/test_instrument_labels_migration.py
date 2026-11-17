"""New label kinds preserve stored jobs/templates; serial uniqueness never rewrites existing evidence."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0187_cutting_execution"
REVISION = "0188_instrument_labels"
TABLES = ("production_instruments", "warehouse_label_templates", "warehouse_label_jobs")


def configuration(path):
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def seeded_parent(tmp_path_factory):
    path = tmp_path_factory.mktemp("instrument-label-migration") / "parent.db"
    command.upgrade(configuration(path), PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    return path


def snapshot(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in TABLES}


def test_migration_preserves_rows_and_enforces_new_kinds_and_serial_uniqueness(seeded_parent, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "upgrade.db"
    shutil.copyfile(seeded_parent, path)
    before = snapshot(path)
    command.upgrade(configuration(path), REVISION)
    assert snapshot(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        db.execute("UPDATE production_instruments SET serial_number = ''")
        ids = db.execute("SELECT id FROM production_instruments ORDER BY id").fetchall()
        assert len(ids) == 2
        db.execute("UPDATE production_instruments SET serial_number = 'ФР-034' WHERE id = ?", ids[0])
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE production_instruments SET serial_number = 'ФР-034' WHERE id = ?", ids[1])
        # Copy an existing valid row so unrelated constraints remain satisfied.
        db.execute("UPDATE warehouse_label_templates SET kind = 'instrument'")
        db.execute("UPDATE warehouse_label_jobs SET kind = 'instrument', lot_id = NULL")
    with pytest.raises(RuntimeError, match="этикеток СИ"):
        command.downgrade(configuration(path), PARENT)


def test_duplicate_preflight_reports_cards_without_mutating_any_rows(seeded_parent, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "duplicates.db"
    shutil.copyfile(seeded_parent, path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE production_instruments SET serial_number = 'DUPLICATE'")
        numbers = [row[0] for row in db.execute("SELECT inventory_number FROM production_instruments")]
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="Повторяющиеся серийные номера") as error:
        command.upgrade(configuration(path), REVISION)
    assert "DUPLICATE" in str(error.value)
    assert all(number in str(error.value) for number in numbers)
    assert snapshot(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)


def test_clean_downgrade_preserves_rows(seeded_parent, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "downgrade.db"
    shutil.copyfile(seeded_parent, path)
    before = snapshot(path)
    command.upgrade(configuration(path), REVISION)
    command.downgrade(configuration(path), PARENT)
    assert snapshot(path) == before
