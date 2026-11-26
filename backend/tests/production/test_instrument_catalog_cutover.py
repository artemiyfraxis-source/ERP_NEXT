"""Frozen 108/15 export reconciliation, commit-time ownership and safe retries."""

import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

ROOT = Path(__file__).parents[2]
REVISION = "0217_unify_instrument_catalog"
PREVIOUS = "0216_equipment_screen_group"
spec = importlib.util.spec_from_file_location("cutover_0217", ROOT / "migrations" / "versions" / f"{REVISION}.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
MANIFEST = migration._manifest()


def config(path):
    value = Config(str(ROOT / "alembic.ini"))
    value.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return value


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    path = tmp_path_factory.mktemp("cutover-template") / "before.db"
    command.upgrade(config(path), PREVIOUS)
    engine = sa.create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        with engine.begin() as connection:
            data = MANIFEST["baseline"]
            models = {item["model"]["id"]: item["model"] for item in data["measuring_units"]}
            for name, rows in (
                ("equipment_models", models.values()),
                ("equipment_units", [item["unit"] for item in data["measuring_units"]]),
                ("equipment_measuring_units", [item["measuring"] for item in data["measuring_units"]]),
                ("production_instruments", data["instruments"]),
            ):
                table = migration._table(connection, name)
                connection.execute(table.insert(), [migration._values(table, row) for row in rows])
    finally:
        engine.dispose()
    return path


@pytest.fixture
def database(baseline, tmp_path):
    path = tmp_path / "cutover.db"
    shutil.copyfile(baseline, path)
    return path


def snapshot(path, tables):
    with sqlite3.connect(path) as connection:
        return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() for name in tables}


def test_export_reconciliation_preserves_identity_metrology_and_roundtrip(database):
    preserved = ("equipment_models", "equipment_units", "equipment_measuring_units",
                 "production_instrument_files", "production_qc_instruments", "production_vswr_measurements")
    old = snapshot(database, (*preserved, "production_instruments"))
    command.upgrade(config(database), REVISION)
    assert snapshot(database, preserved) == {name: old[name] for name in preserved}
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = {str(UUID(row["id"])): dict(row) for row in connection.execute("SELECT * FROM production_instruments")}
        assert len(rows) == 118
        assert sum(row["quantity"] for row in rows.values()) == 118
        for original in MANIFEST["baseline"]["instruments"]:
            row = rows[original["id"]]
            for key in ("kind", "status", "verification_number", "serial_number", "warehouse_grn", "quantity", "vna_app"):
                assert row[key] == original[key]
        for entry in MANIFEST["entries"]:
            row = rows[entry["instrument_id"]]
            assert row["article"] == entry["write"]["article"]
            assert json.loads(row["custom_fields"]) == entry["write"]["custom_fields"]
            if entry["outcome"] == "new":
                assert row["status"] == "Поверка не подтверждена" and row["version"] == 1
                assert all(row[key] is None for key in ("verified_at", "verification_due", "verification_number"))
                assert row["vna_app"] == "" and row["serial_number"] == ""
        assert connection.execute("SELECT count(*) FROM equipment_instrument_bindings").fetchone()[0] == 15
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        audit = connection.execute("SELECT details FROM audit_events WHERE event_type = 'production.instrument_catalog_migrated'").fetchall()
        assert len(audit) == 15 and all(json.loads(row[0])["source"] == "migration" for row in audit)
    command.downgrade(config(database), PREVIOUS)
    assert snapshot(database, (*preserved, "production_instruments")) == old
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM production_instruments").fetchone() == (118,)
        assert connection.execute("SELECT count(*) FROM audit_events WHERE event_type = 'production.instrument_catalog_migrated'").fetchone() == (15,)
        assert connection.execute("SELECT count(*) FROM audit_events WHERE event_type = 'production.instrument_catalog_downgraded'").fetchone() == (15,)


@pytest.mark.parametrize("mutation", ["name", "serial", "unit", "binding", "missing"])
def test_changed_sources_abort_before_schema_or_backfill(database, mutation):
    entry = MANIFEST["entries"][0]
    with sqlite3.connect(database) as connection:
        if mutation in ("name", "serial"):
            field = "name" if mutation == "name" else "serial_number"
            connection.execute(f"UPDATE production_instruments SET {field} = ? WHERE id = ?",
                               ("DIFFERENT PHYSICAL OBJECT", UUID(entry["instrument_id"]).hex))
        elif mutation == "unit":
            connection.execute("UPDATE equipment_measuring_units SET display_text = 'changed'")
        elif mutation == "binding":
            connection.execute("INSERT INTO equipment_instrument_bindings VALUES (?, ?)",
                               (UUID(entry["unit_id"]).hex, UUID(entry["instrument_id"]).hex))
        else:
            connection.execute("DELETE FROM equipment_measuring_units WHERE id = ?", (UUID(entry["unit_id"]).hex,))
    before = snapshot(database, ("production_instruments", "audit_events", "equipment_instrument_bindings"))
    with pytest.raises(RuntimeError, match="0217:"):
        command.upgrade(config(database), REVISION)
    assert snapshot(database, tuple(before)) == before
    with sqlite3.connect(database) as connection:
        assert "article" not in {row[1] for row in connection.execute("PRAGMA table_info(production_instruments)")}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PREVIOUS,)


def test_failure_after_first_backfill_rolls_back_sqlite_ddl_data_and_audit(database, monkeypatch):
    engine = sa.create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    original = migration._event
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected after first instrument and audit")
    monkeypatch.setattr(migration, "_event", fail)
    before = snapshot(database, ("production_instruments", "equipment_instrument_bindings", "audit_events"))
    try:
        with pytest.raises(RuntimeError, match="injected"):
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
    finally:
        engine.dispose()
    assert snapshot(database, tuple(before)) == before
    command.upgrade(config(database), REVISION)


@pytest.mark.parametrize("bad_json", ["[]", "null", '"text"', "123"])
def test_custom_fields_must_be_a_json_object(database, bad_json):
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE production_instruments SET custom_fields = ?", (bad_json,))


@pytest.mark.parametrize("mutation", ["unbound", "orphan", "duplicate_instrument", "delete_instrument", "wrong_catalog"])
def test_binding_constraints_enforce_total_one_to_one_ownership_at_commit(database, mutation):
    command.upgrade(config(database), REVISION)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = ON")
    entry, other = MANIFEST["entries"][:2]
    unit, instrument = UUID(entry["unit_id"]).hex, UUID(entry["instrument_id"]).hex
    try:
        with pytest.raises(sqlite3.IntegrityError):
            if mutation == "unbound":
                connection.execute("DELETE FROM equipment_instrument_bindings WHERE unit_id = ?", (unit,))
            elif mutation == "orphan":
                connection.execute("INSERT INTO equipment_instrument_bindings VALUES (?, ?)", (uuid4().hex, uuid4().hex))
            elif mutation == "duplicate_instrument":
                connection.execute("UPDATE equipment_instrument_bindings SET instrument_id = ? WHERE unit_id = ?",
                                   (instrument, UUID(other["unit_id"]).hex))
            elif mutation == "delete_instrument":
                connection.execute("DELETE FROM production_instruments WHERE id = ?", (instrument,))
            else:
                connection.execute("UPDATE equipment_units SET catalog = 'ob' WHERE id = ?", (unit,))
            connection.commit()
    finally:
        connection.rollback()
        connection.close()


@pytest.mark.parametrize("mutation", ["version", "catalog", "new_reference"])
def test_downgrade_refuses_cutover_edits_and_new_dependents(database, mutation):
    command.upgrade(config(database), REVISION)
    with sqlite3.connect(database) as connection:
        if mutation == "version":
            connection.execute("UPDATE production_instruments SET version = version + 1 WHERE id = (SELECT id FROM production_instruments LIMIT 1)")
        elif mutation == "catalog":
            connection.execute("UPDATE production_instruments SET notes = 'edited' WHERE id = (SELECT id FROM production_instruments LIMIT 1)")
        else:
            new_id = UUID(next(e["instrument_id"] for e in MANIFEST["entries"] if e["outcome"] == "new")).hex
            connection.execute("CREATE TABLE test_new_reference (id TEXT PRIMARY KEY, instrument_id CHAR(32) REFERENCES production_instruments(id))")
            connection.execute("INSERT INTO test_new_reference VALUES ('dependent', ?)", (new_id,))
    before = snapshot(database, ("production_instruments", "equipment_instrument_bindings", "audit_events"))
    with pytest.raises(RuntimeError, match="unsafe downgrade"):
        command.downgrade(config(database), PREVIOUS)
    assert snapshot(database, tuple(before)) == before


def test_empty_database_upgrade_downgrade_upgrade(tmp_path):
    path = tmp_path / "empty.db"
    command.upgrade(config(path), REVISION)
    command.downgrade(config(path), PREVIOUS)
    command.upgrade(config(path), REVISION)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM production_instruments").fetchone() == (0,)


def test_unresolved_manifest_entry_cannot_run(database, monkeypatch):
    bad = copy.deepcopy(MANIFEST)
    bad["entries"][0]["review_status"] = "unresolved_conflict"
    monkeypatch.setattr(migration, "_manifest", lambda: bad)
    engine = sa.create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with pytest.raises(RuntimeError, match="unresolved entry"):
            with engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
    finally:
        engine.dispose()


@pytest.mark.parametrize("column,value", [("inventory_number", " си-001 "), ("serial_number", "nct-1.1")])
def test_normalized_identity_collision_aborts_before_writes(database, column, value):
    with sqlite3.connect(database) as connection:
        connection.execute(f"UPDATE production_instruments SET {column} = ? WHERE inventory_number = 'СИ.002'", (value,))
    with pytest.raises(RuntimeError, match="duplicate normalized"):
        command.upgrade(config(database), REVISION)


def test_existing_blob_rf_mating_and_active_session_are_unchanged(database):
    from sqlalchemy.orm import Session
    from app.modules.identity.infrastructure import IdentityUserRow

    engine = sa.create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    actor, moment = uuid4(), datetime(2026, 11, 23, 12)
    instrument = next(row["id"] for row in MANIFEST["baseline"]["instruments"] if row["inventory_number"] == "СИ.001")
    try:
        with Session(engine) as session:
            session.add(IdentityUserRow(id=actor, login="cutover-test", email="cutover@example.test", name="ОТК",
                                        department="ОТК", role="qc", active=True))
            session.commit()
        with engine.begin() as connection:
            records = {
                "production_instrument_files": {
                    "id": str(uuid4()), "instrument_id": instrument, "kind": "Прочее", "file_name": "reference.s2p",
                    "content": b"# HZ S RI R 50\n1000000 0 0 1 0 1 0 0 0\n", "uploaded_at": moment,
                    "uploaded_by": str(actor), "is_reference": True,
                },
                "production_rf_details": {
                    "instrument_id": instrument, "version": 3, "role": "vna", "confirmed": True,
                    "source": "test evidence", "kit_definition": "", "f_min_ghz": 0.001, "max_points": 201,
                },
                "production_instrument_matings": {
                    "id": str(uuid4()), "instrument_id": instrument, "side": "A", "seq": 1, "delta": 7,
                    "total_after": 7, "kind": "initial", "note": "test", "source": None,
                    "actor": "ОТК", "created_at": moment,
                },
                "production_vna_sessions": {
                    "id": str(uuid4()), "state": "ready", "version": 2, "step": 0, "operator_id": str(actor),
                    "machine_id": "test-agent", "instrument_id": instrument, "instrument_serial": "test-serial",
                    "purpose": "bench", "snapshot": {"instrument_id": instrument}, "steps": [],
                    "created_at": moment, "updated_at": moment,
                },
            }
            for name, values in records.items():
                table = migration._table(connection, name)
                connection.execute(table.insert().values(**migration._values(table, values)))
    finally:
        engine.dispose()
    old = snapshot(database, tuple(records))
    command.upgrade(config(database), REVISION)
    assert snapshot(database, tuple(records)) == old
    command.downgrade(config(database), PREVIOUS)
    assert snapshot(database, tuple(records)) == old


def test_sqlite_migration_with_foreign_keys_enabled(database):
    engine = sa.create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            connection.commit()
            with connection.begin():
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            connection.commit()
            with connection.begin():
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()
