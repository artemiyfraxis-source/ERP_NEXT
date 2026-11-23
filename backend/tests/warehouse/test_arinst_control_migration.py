"""0219 on SQLite and an opt-in, isolated loopback PostgreSQL schema."""

from datetime import datetime, timedelta, timezone
import importlib
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

revision = importlib.import_module("migrations.versions.0219_arinst_control")


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated(request, tmp_path):
    schema = None
    if request.param == "postgresql":
        raw = os.getenv("ERP_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("ERP_TEST_POSTGRES_URL is not set; PostgreSQL constraints unverified")
        url = make_url(raw)
        if url.get_backend_name() != "postgresql" or url.host not in ("localhost", "127.0.0.1", "::1") or url.database != "erp_stock_test" or url.query:
            pytest.fail("Use loopback PostgreSQL database erp_stock_test without URL options")
        engine = sa.create_engine(url.set(drivername="postgresql+psycopg"), connect_args={"connect_timeout": 5,
            "hostaddr": "::1" if url.host == "::1" else "127.0.0.1", "options": "-cstatement_timeout=15000 -clock_timeout=5000"})
        schema = "arinst_test_" + uuid4().hex
    else:
        engine = sa.create_engine("sqlite+pysqlite:///" + (tmp_path / "migration.db").as_posix())
    with engine.connect() as connection:
        try:
            if schema:
                connection.execute(sa.schema.CreateSchema(schema))
                connection.execute(sa.text(f'SET search_path TO "{schema}"'))
                connection.commit()
            else:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            meta = sa.MetaData()
            agents = sa.Table("warehouse_scan_agents", meta, sa.Column("machine_id", sa.String(80), primary_key=True),
                sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False), sa.Column("legacy", sa.Boolean(), nullable=False))
            instruments = sa.Table("production_instruments", meta, sa.Column("id", sa.Uuid(), primary_key=True))
            devices = sa.Table("warehouse_agent_devices", meta,
                sa.Column("machine_id", sa.String(80), primary_key=True), sa.Column("kind", sa.String(8), primary_key=True),
                sa.Column("device_id", sa.String(300), primary_key=True), sa.Column("present", sa.Boolean(), nullable=False),
                sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False))
            bindings = sa.Table("warehouse_vna_bindings", meta, sa.Column("serial", sa.String(300), primary_key=True), sa.Column("machine_id", sa.String(80)))
            meta.create_all(connection)
            with Operations.context(MigrationContext.configure(connection)):
                old = revision._legacy_table()
            now = datetime.now(timezone(timedelta(hours=3)))
            ids = [uuid4(), uuid4()]
            connection.execute(instruments.insert(), [{"id": value} for value in ids])
            for pc, serials in (("exact", ["101"]), ("ambiguous", ["201", "202"]), ("stale", ["301"])):
                seen = now - timedelta(days=1) if pc == "stale" else now
                connection.execute(agents.insert(), dict(machine_id=pc, last_seen=seen, legacy=False))
                connection.execute(old.insert(), dict(machine_id=pc, version=2, methods=["acm"], ops=["release"], reported_at=seen))
                for serial in serials:
                    connection.execute(devices.insert(), dict(machine_id=pc, kind="s2vna", device_id=serial, present=True, last_seen=seen))
                    connection.execute(bindings.insert(), dict(machine_id=pc, serial=serial))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
            connection.commit()
            yield connection, ids, now
        finally:
            connection.rollback()
            if schema:
                connection.execute(sa.schema.DropSchema(schema, cascade=True))
                connection.commit()
    engine.dispose()


def test_backfill_constraints_identity_and_safe_downgrade(migrated):
    connection, ids, now = migrated
    control = sa.Table(revision.CONTROL, sa.MetaData(), autoload_with=connection)
    identity = sa.Table(revision.IDENTITIES, sa.MetaData(), autoload_with=connection)
    # SQLite reflects UUID as CHAR(32); restore the bind processors for typed fixture values.
    identity.c.instrument_id.type = sa.Uuid()
    identity.c.verified_by.type = sa.Uuid()
    assert list(connection.execute(sa.select(control.c.machine_id, control.c.serial))) == [("exact", "101")]
    item = dict(machine_id="exact", serial="arinst:1-2-3", source="arinst", version=3, methods=[], ops=["connect"],
                reported_at=now, hardware_uid="1-2-3", firmware="2.0.0", model="VNA-DL", identity_verified=True,
                license_state="not_required", trial_remaining_seconds=None, control_ready=True)
    for patch in ({"serial": "101"}, {"source": "s2vna"}, {"license_state": "LOCKED", "trial_remaining_seconds": None},
                  {"trial_remaining_seconds": -1}, {"trial_remaining_seconds": 600},
                  {"license_state": "TRIAL"}, {"license_state": "unknown"}, {"machine_id": "absent"}):
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(control.insert(), {**item, **patch})
    connection.execute(control.insert(), item)
    connection.commit()
    with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="collapse"):
        revision.downgrade()
    association = dict(instrument_id=ids[0], source="arinst", serial="arinst:1-2-3", hardware_uid="1-2-3",
                       firmware="2.0.0", verified_machine_id="exact", verified_at=now, verified_by=uuid4())
    connection.execute(identity.insert(), association)
    for patch in ({"instrument_id": ids[1]}, {"instrument_id": uuid4(), "serial": "arinst:4-5-6", "hardware_uid": "4-5-6"}):
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(identity.insert(), {**association, **patch})
    connection.commit()
    with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="history"):
        revision.downgrade()
    assert connection.scalar(sa.select(sa.func.count()).select_from(identity)) == 1
    connection.execute(identity.delete())
    connection.execute(control.delete().where(control.c.source == "arinst"))
    connection.commit()
    with Operations.context(MigrationContext.configure(connection)):
        revision.downgrade()
    assert [c["name"] for c in sa.inspect(connection).get_columns(revision.CONTROL)] == ["machine_id", "version", "methods", "ops", "reported_at"]


def test_full_sqlite_history_upgrades_to_single_0219_head(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    path = tmp_path / "history.db"
    config.set_main_option("sqlalchemy.url", "sqlite+pysqlite:///" + path.as_posix())
    command.upgrade(config, "0216_equipment_screen_group")
    engine = sa.create_engine("sqlite+pysqlite:///" + path.as_posix())
    with engine.connect() as connection:
        counts = {name: connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar() for name in
                  ("warehouse_scan_jobs", "production_instruments", "production_vna_sessions", "production_vna_artifacts")}
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == revision.revision
        for name, count in counts.items():
            assert connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar() == count
    engine.dispose()
