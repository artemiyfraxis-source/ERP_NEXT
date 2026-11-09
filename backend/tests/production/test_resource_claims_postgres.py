"""Opt-in real PostgreSQL races; isolated schema in loopback erp_stock_test only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow, ResourceReservationRow, SqlAlchemyResourceClaims
from app.modules.references.infrastructure import EquipmentModelRow, TechOperationRecordRow
from app.modules.techcards.infrastructure import RefreshRequestRow

UNIT, MODEL = uuid4(), uuid4()  # the БД.ОБ unit a machine claim names by FK (0172)


@pytest.fixture
def engine():
    raw = os.environ.get("ERP_TEST_POSTGRES_URL")
    if raw is None:
        pytest.skip("ERP_TEST_POSTGRES_URL is not set (resource concurrency not verified)")
    url = make_url(raw)
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or url.host not in {"localhost", "127.0.0.1", "::1"} or url.database != "erp_stock_test" or url.query:
        pytest.fail("Resource tests require PostgreSQL on loopback, database erp_stock_test, no URL options")
    url = url.set(drivername="postgresql+psycopg")
    schema = f"erp_resources_{uuid4().hex}"
    options = {"connect_timeout": 5, "hostaddr": "::1" if url.host == "::1" else "127.0.0.1",
        "options": "-cstatement_timeout=15000 -clock_timeout=10000 -cidle_in_transaction_session_timeout=30000"}
    admin = create_engine(url, poolclass=NullPool, connect_args=options)
    result = create_engine(url, poolclass=NullPool, isolation_level="READ COMMITTED",
        connect_args={**options, "options": options["options"] + f" -csearch_path={schema}"})
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        # A БД.ОБ write queues an ETK check (etk_refresh, before_flush) — its queue table must exist too.
        for row in (RefreshRequestRow, EquipmentModelRow, TechOperationRecordRow, ResourceGenerationRow, ResourceReservationRow, ActiveResourceRow):
            row.__table__.create(result)
        with Session(result) as session:
            session.add(ResourceGenerationRow(id=1, version=0))
            session.add(EquipmentModelRow(id=MODEL, name="Автомат резки"))
            session.flush()
            session.add(TechOperationRecordRow(id=UNIT, tab="ob", display_text="ОБ.027.2", normalized_search="",
                source_sheet="БД.ОБ", sort_key="1", fields={"Инв. номер": "ОБ.027.2"}, mirror_key="", edited_in_erp=False,
                equipment_model_id=MODEL))
            session.commit()
        yield result
    finally:
        result.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.mark.parametrize("kind", [ResourceKind.MACHINE, ResourceKind.PERSON])
def test_two_connections_cannot_reserve_same_absent_resource(engine, kind):
    resource = ResourceKey(kind, str(UNIT) if kind is ResourceKind.MACHINE else str(uuid4()))
    now = datetime(2026, 11, 3, 8, tzinfo=UTC)
    window = ResourceWindow(now, now + timedelta(hours=1))
    barrier = Barrier(2, timeout=10)

    def reserve():
        with Session(engine) as session:
            barrier.wait()
            try:
                claims = SqlAlchemyResourceClaims(session)
                claims.acquire()
                claims.replace_plan(ResourceOwner("setup", uuid4()), (resource,), window)
                session.commit()
                return "reserved"
            except ResourceConflict:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == ["conflict", "reserved"]
    with Session(engine) as session:
        assert len(list(session.scalars(select(ResourceReservationRow)))) == 1
        assert session.get(ResourceGenerationRow, 1).version == 1
