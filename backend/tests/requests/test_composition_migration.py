"""The additive snapshot migration preserves every pre-existing business row."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed
from app.modules.requests.infrastructure import CalcCompositionRow

PARENT = "0167_instrument_verification_pending"
REVISION = "0168_requests_calc_compositions"


def test_generic_seed_supports_nonempty_composition_constraint():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        CalcCompositionRow.__table__.create(engine)
        assert seed(engine, rows=2) == {"requests_calc_compositions": 2}
    finally:
        engine.dispose()


def test_additive_upgrade_preserves_business_data_and_protects_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "composition.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0] != "alembic_version"]
        before = {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() for table in tables}
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
                for table in tables} == before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        insert = "INSERT INTO requests_calc_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        values = [uuid4().hex, "708К.4", "source", 1, "a" * 64, "2026-11-02", uuid4().hex, json.dumps([{}])]
        connection.execute(insert, values)
        for index, bad in ((1, ""), (2, " "), (3, 0), (7, "[]"), (7, "{}"), (7, "null"),
                           (7, "3"), (7, '"text"')):
            invalid = list(values)
            invalid[0] = uuid4().hex
            invalid[1] = "709К.1"
            invalid[index] = bad
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, invalid)
    with pytest.raises(RuntimeError, match="Cannot drop saved"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (REVISION,)
        assert connection.execute("SELECT count(*) FROM requests_calc_compositions").fetchone() == (1,)
