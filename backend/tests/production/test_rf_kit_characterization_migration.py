"""Run the actual Alembic chain and preserve existing loads-only registry data."""

from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.production.rf_infrastructure import RfDetailsRow
from app.modules.production.rf_kit_characterization_infrastructure import RfKitRepository, RfKitStandardRow, RfStandardCharacterizationRow
from tests.production.test_rf_kit_characterization import NOW, piece
from tests.production.test_rf_kit_characterization_storage import append

PARENT = "0219_arinst_control"
REVISION = "0220_rf_kit_characterization"


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "migration.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    kit_id = uuid4()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO production_instruments (id,version,inventory_number,name,instrument_type,kind,status) "
                   "VALUES (?,1,'СИ.004','SMA-MF06','Набор','Принадлежность','Действует')", (kit_id.hex,))
        db.execute("INSERT INTO production_rf_details (instrument_id,version,role,confirmed,source,kit_definition) "
                   "VALUES (?,1,'load_kit',0,'existing','SMA-MF06-loads')", (kit_id.hex,))
        db.execute("INSERT INTO production_rf_interfaces (instrument_id,position,series,body,center,f_confirmed,f_source) "
                   "VALUES (?,1,'SMA','розетка','гнездо',0,'existing')", (kit_id.hex,))
        columns = {table: [row[1] for row in db.execute(f"PRAGMA table_info({table})")] for table in
                   ("production_instruments", "production_rf_details", "production_rf_interfaces", "production_vna_artifacts")}

    def snapshot():
        with sqlite3.connect(path) as db:
            return {table: db.execute(f"SELECT {','.join(names)} FROM {table} ORDER BY 1").fetchall()
                    for table, names in columns.items()}

    before = snapshot()
    command.upgrade(config, REVISION)
    assert snapshot() == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT characterization_generation,characterization_hash FROM production_rf_details").fetchall() == [(0, None)]
        assert db.execute("SELECT count(*) FROM production_rf_kit_standards").fetchone() == (0,)
    return config, path, kit_id, snapshot, before


def test_empty_downgrade_roundtrip_preserves_registry_and_matches_metadata(migrated):
    config, _, _, snapshot, before = migrated
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        inspector = inspect(engine)
        for model in (RfKitStandardRow, RfStandardCharacterizationRow, RfDetailsRow):
            table = model.__table__
            assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.c.keys())
            assert {constraint["name"] for constraint in inspector.get_check_constraints(table.name)} == {
                constraint.name for constraint in table.constraints if constraint.__class__.__name__ == "CheckConstraint"}
        assert "fk_rf_kit_current_revision" in {fk["name"] for fk in inspector.get_foreign_keys(RfKitStandardRow.__tablename__)}
    finally:
        engine.dispose()
    command.downgrade(config, PARENT)
    assert snapshot() == before
    command.upgrade(config, REVISION)
    assert snapshot() == before


def test_populated_downgrade_refuses_without_deleting_rows_and_real_triggers_protect(migrated):
    config, path, kit_id, snapshot, before = migrated
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with Session(engine) as session:
            repo = RfKitRepository(session)
            standard = repo.register(kit_id, piece(), expected_kit_version=1, actor="QC", now=NOW)
            revision = append(repo, standard)
            revision_id = revision.id
            session.commit()
        with pytest.raises(RuntimeError, match="Откат запрещён"):
            command.downgrade(config, PARENT)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (REVISION,)
            assert db.execute("SELECT count(*) FROM production_rf_standard_characterizations").fetchone() == (1,)
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            for sql in ("UPDATE production_rf_standard_characterizations SET revision=2", "DELETE FROM production_rf_standard_characterizations"):
                with pytest.raises(sqlite3.IntegrityError, match="неизменяемы"):
                    db.execute(sql)
        with Session(engine) as session:
            assert session.get(RfStandardCharacterizationRow, revision_id).revision == 1
            with pytest.raises(IntegrityError):
                session.execute(update(RfDetailsRow).where(RfDetailsRow.instrument_id == kit_id).values(
                    role="solt_kit", kit_definition="SMA-MF06-characterized", characterization_generation=1))
    finally:
        engine.dispose()
