"""0207_vna_screens: the six code presets become rows, and the bench draws exactly the same screens from them."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.production.vna_screen_infrastructure import VnaScreenRepository, VnaScreenRow, definition_of
from app.modules.production.vna_screens import PRESET_PURPOSE, presets
from tests.migrations.seed_any_revision import seed
from tests.production.test_vna_screens import GOLDEN, drawn

PARENT = "0206_equipment_show_flag"
REVISION = "0207_vna_screens"


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "screens.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    command.upgrade(config, REVISION)
    yield config, path


def test_seed_is_the_code_presets_and_draws_the_same_screens(migrated):
    config, _ = migrated
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with Session(engine) as session:
            rows = session.query(VnaScreenRow).all()
            assert len(rows) == 6 and all(r.is_default and r.active and r.version == 1 for r in rows)
            stored = {r.purpose: definition_of(r) for r in rows}
            assert {p: d.model_dump(mode="json") for p, d in stored.items()} == {
                p: d.model_dump(mode="json") for p, d in presets().items()}
            for key, purpose in PRESET_PURPOSE.items():
                assert drawn(stored[purpose], key) == GOLDEN[key], key   # byte for byte: the same screen_hash
                assert VnaScreenRepository(session).default(purpose) == stored[purpose]
    finally:
        engine.dispose()


def _insert(db, **changes):
    values = dict(id=uuid4().hex, name=f"Экран {uuid4().hex[:6]}", purpose="adapter", is_default=0, split=4,
                  traces=json.dumps([]), markers_hz="[]", note="", active=1, version=1, updated_by=None,
                  updated_by_name="", updated_at=datetime.now(timezone.utc).isoformat())
    values.update(changes)
    db.execute("INSERT INTO production_vna_screens (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values)
               + ")", list(values.values()))


@pytest.mark.parametrize("changes", [
    dict(is_default=1),                         # a second default of «adapter»
    dict(purpose="other"), dict(split=0), dict(split=17), dict(version=0), dict(name="  "),
    dict(name="ОТК — переход"),                 # the name is unique
    dict(purpose="other_kind", is_default=0),
])
def test_constraints_refuse_bad_rows(migrated, changes):
    _, path = migrated
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
        _insert(db, **changes)


def test_a_default_stays_active_and_an_extra_screen_is_allowed(migrated):
    _, path = migrated
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE production_vna_screens SET active = 0 WHERE purpose = 'adapter'")
        _insert(db)
        assert db.execute("SELECT count(*) FROM production_vna_screens").fetchone()[0] == 7


def test_downgrade_drops_untouched_presets_and_refuses_after_an_edit(migrated):
    config, path = migrated
    command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'production_vna_screens'").fetchall()
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE production_vna_screens SET version = 2 WHERE purpose = 'flex'")
    with pytest.raises(RuntimeError, match="правили"):
        command.downgrade(config, PARENT)
