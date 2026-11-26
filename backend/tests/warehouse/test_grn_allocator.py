"""Shared allocator: transaction ownership, concurrency, retries and immutable contracts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
import os
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, create_mock_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.modules.warehouse.grn import (
    GrnCapacityExceeded, GrnCategory, GrnConflict, GrnContext, GrnRequest, fingerprint_body,
)
from app.modules.warehouse.grn_registry import GrnCounterRow, GrnRegistryRow, MaterialOwnerRow, SqlAlchemyGrnAllocator
from app.shared.persistence import Base
from app.workflows.grn_allocation import grn_allocator

NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
OWNER = uuid4()
TABLES = [MaterialOwnerRow.__table__, GrnCounterRow.__table__, GrnRegistryRow.__table__]


def request(category=GrnCategory.PPM, *, operation=None, source="warehouse_receipt", **changes):
    context = {
        GrnCategory.PPM: GrnContext(), GrnCategory.PAM: GrnContext(equipment="ОБ.099"),
        GrnCategory.GOS: GrnContext(purpose="Уборка цеха"),
        GrnCategory.SUB: GrnContext(subcontractor="contractor:42", production_order="591К.3", acceptance_document="Акт 12"),
        GrnCategory.CSM: GrnContext(owner_id=OWNER, customer_order="customer-order:17", transfer_document="Передача 6"),
        GrnCategory.FRX: GrnContext(production_order="591К.3", acceptance_document="act:12"),
        GrnCategory.RD: GrnContext(project="591К", purpose="Опытный образец"),
    }[category]
    return GrnRequest(**{"category": category, "source_kind": source, "source_op_id": operation or str(uuid4()),
                         "context": context, "body_fingerprint": fingerprint_body({"article": "A", "quantity": "2"}), **changes})


@pytest.fixture(params=["sqlite", "postgresql"])
def sessions(request, tmp_path):
    admin, schema = None, None
    if request.param == "sqlite":
        engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'grn.db').as_posix()}", connect_args={"timeout": 20})
    else:
        raw = os.environ.get("ERP_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("ERP_TEST_POSTGRES_URL is not configured; concurrency also runs on SQLite")
        url = make_url(raw)
        if url.drivername not in {"postgresql", "postgresql+psycopg"} or url.host not in {"localhost", "127.0.0.1", "::1"} or url.database != "erp_stock_test" or url.query:
            pytest.fail("GRN PostgreSQL tests require loopback erp_stock_test without URL options")
        url = url.set(drivername="postgresql+psycopg")
        schema = f"grn_test_{uuid4().hex}"
        args = {"connect_timeout": 5, "hostaddr": "::1" if url.host == "::1" else "127.0.0.1",
                "options": "-cstatement_timeout=20000 -clock_timeout=10000"}
        admin = create_engine(url, connect_args=args)
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(url, connect_args={**args, "options": args["options"] + f" -csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine, tables=TABLES)
        factory = sessionmaker(engine)
        with factory.begin() as session:
            session.add(MaterialOwnerRow(id=OWNER, display_name="Заказчик", legal_identity="ИНН:1234567890"))
        yield factory
    finally:
        engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()


def test_all_prefixes_independent_years_and_literal_ampersand(sessions):
    with sessions.begin() as session:
        allocator = grn_allocator(session)
        for category in GrnCategory:
            first = allocator.allocate(request(category), registered_at=NOW)
            second = allocator.allocate(request(category), registered_at=NOW)
            following = allocator.allocate(request(category), registered_at=NOW.replace(year=2027))
            assert (first.number, second.number, following.number) == (
                f"{category}.26.000001", f"{category}.26.000002", f"{category}.27.000001")
        assert session.scalar(select(func.count()).select_from(GrnCounterRow)) == 14


def test_moscow_new_year_and_retry_keeps_first_registration(sessions):
    command = request()
    before = datetime(2026, 12, 31, 20, 59, 59, tzinfo=UTC)
    after = datetime(2026, 12, 31, 21, 0, 0, tzinfo=UTC)
    with sessions.begin() as session:
        allocator = grn_allocator(session)
        original = allocator.allocate(command, registered_at=before)
    with sessions.begin() as session:
        allocator = grn_allocator(session)
        assert allocator.allocate(command, registered_at=after) == original
        assert allocator.allocate(request(), registered_at=after).number == "PPM.27.000001"


@pytest.mark.parametrize("change", ["category", "context", "body"])
def test_changed_retry_conflicts_without_consuming_serial(sessions, change):
    command = request()
    with sessions.begin() as session:
        original = grn_allocator(session).allocate(command, registered_at=NOW)
    changes = {"category": {"category": GrnCategory.GOS, "context": GrnContext(purpose="Уборка")},
               "context": {"context": GrnContext(project="Другой проект")},
               "body": {"body_fingerprint": fingerprint_body({"quantity": "3"})}}[change]
    with sessions.begin() as session:
        with pytest.raises(GrnConflict):
            grn_allocator(session).allocate(replace(command, **changes), registered_at=NOW)
        assert grn_allocator(session).allocate(command, registered_at=NOW) == original
        assert grn_allocator(session).allocate(request(), registered_at=NOW).serial == 2


def test_rollback_covers_counter_identity_and_other_caller_writes(sessions):
    operation = request()
    with sessions() as session:
        with pytest.raises(RuntimeError, match="caller failure"):
            with session.begin():
                grn_allocator(session).allocate(operation, registered_at=NOW)
                session.add(MaterialOwnerRow(id=uuid4(), display_name="Rollback", legal_identity="rollback"))
                session.flush()
                raise RuntimeError("caller failure")
    with sessions.begin() as session:
        assert session.scalar(select(func.count()).select_from(GrnRegistryRow)) == 0
        assert session.scalar(select(func.count()).select_from(GrnCounterRow)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialOwnerRow)) == 1
        assert grn_allocator(session).allocate(operation, registered_at=NOW).serial == 1


def test_explicit_transaction_and_aware_clock_required(sessions):
    with sessions() as session:
        with pytest.raises(RuntimeError, match="open transaction"):
            grn_allocator(session).allocate(request(), registered_at=NOW)
        with session.begin():
            with pytest.raises(ValueError, match="часовым поясом"):
                grn_allocator(session).allocate(request(), registered_at=NOW.replace(tzinfo=None))


def test_capacity_is_a_hard_limit_and_retry_is_still_allowed(sessions):
    with sessions.begin() as session:
        session.add(GrnCounterRow(category="PPM", full_year=2026, last_serial=999998))
    command = request()
    with sessions.begin() as session:
        allocator = grn_allocator(session)
        assert allocator.allocate(command, registered_at=NOW).number == "PPM.26.999999"
        with pytest.raises(GrnCapacityExceeded):
            allocator.allocate(request(), registered_at=NOW)
        assert allocator.allocate(command, registered_at=NOW).serial == 999999
        assert allocator.allocate(request(GrnCategory.GOS), registered_at=NOW).serial == 1


def test_mixed_batch_order_retry_and_atomic_failure(sessions):
    commands = (request(GrnCategory.RD), request(), request(GrnCategory.GOS), request())
    with sessions.begin() as session:
        allocator = grn_allocator(session)
        first = allocator.allocate_many(commands, registered_at=NOW)
        assert [item.category for item in first] == [item.category for item in commands]
        assert allocator.allocate_many(commands, registered_at=NOW) == first
        with pytest.raises(GrnConflict):
            allocator.allocate_many((request(GrnCategory.PAM), replace(commands[0], body_fingerprint="f" * 64)), registered_at=NOW)
        assert session.scalar(select(GrnCounterRow.last_serial).where(GrnCounterRow.category == "PAM")) is None


@pytest.mark.parametrize("same_operation", [False, True])
def test_first_and_steady_state_concurrency_across_flows(sessions, same_operation):
    barrier = Barrier(4)
    shared = request(operation="retry")

    def allocate(worker):
        barrier.wait(timeout=10)
        result = []
        for _ in range(4):
            command = shared if same_operation else request(source=("warehouse_receipt", "receiving", "finished_goods_act")[worker % 3])
            with sessions.begin() as session:
                result.append(grn_allocator(session).allocate(command, registered_at=NOW))
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [item for batch in pool.map(allocate, range(4)) for item in batch]
    with sessions.begin() as session:
        expected = 1 if same_operation else 16
        assert len({item.id for item in results}) == expected
        assert sorted({item.serial for item in results}) == list(range(1, expected + 1))
        assert session.scalar(select(GrnCounterRow.last_serial)) == expected
        assert session.scalar(select(func.count()).select_from(GrnRegistryRow)) == expected


def test_changed_body_concurrency_preserves_winner(sessions):
    barrier = Barrier(4)

    def allocate(worker):
        barrier.wait(timeout=10)
        with sessions.begin() as session:
            try:
                return grn_allocator(session).allocate(request(operation="one", body_fingerprint=fingerprint_body({"q": worker})), registered_at=NOW)
            except GrnConflict:
                return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(allocate, range(4)))
    assert sum(item is not None for item in results) == 1
    with sessions.begin() as session:
        assert session.scalar(select(GrnCounterRow.last_serial)) == 1


@pytest.mark.parametrize("category", list(GrnCategory)[1:])
def test_category_context_is_required(category):
    with pytest.raises(ValueError):
        request(category, context=GrnContext())


def test_frozen_contract_and_canonical_hash():
    command = request()
    with pytest.raises(FrozenInstanceError):
        command.category = GrnCategory.CSM
    with pytest.raises(FrozenInstanceError):
        command.context.owner_id = OWNER
    assert fingerprint_body({"a": 1, "b": "R&D"}) == fingerprint_body({"b": "R&D", "a": 1})
    with pytest.raises(ValueError):
        fingerprint_body({"quantity": float("nan")})


def test_replay_normalizes_postgresql_session_timezone_to_contract_utc():
    command = request()
    session = Mock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "id": uuid4(), "number": "PPM.26.000001", "category": "PPM", "full_year": 2026, "serial": 1,
        "request_fingerprint": command.fingerprint, "registered_at": NOW.astimezone(timezone(timedelta(hours=3))),
        "source_kind": command.source_kind, "source_op_id": command.source_op_id, **command.context.payload(),
    }
    identity = SqlAlchemyGrnAllocator(session)._existing(command)
    assert identity.registered_at == NOW
    assert identity.registered_at.tzinfo is UTC


def test_postgresql_statement_has_atomic_first_insert_and_bounded_returning():
    engine = create_mock_engine("postgresql+psycopg://", lambda *args: None)
    with Session(engine) as session:
        counter = GrnCounterRow.__table__
        statement = SqlAlchemyGrnAllocator(session)._insert(counter).values(category="PPM", full_year=2026, last_serial=1)
        statement = statement.on_conflict_do_update(index_elements=[counter.c.category, counter.c.full_year],
            set_={"last_serial": counter.c.last_serial + 1}, where=counter.c.last_serial < 999999).returning(counter.c.last_serial)
        sql = str(statement.compile(dialect=engine.dialect))
        assert "ON CONFLICT (category, full_year) DO UPDATE" in sql
        assert "WHERE warehouse_grn_counters.last_serial <" in sql
        assert "RETURNING warehouse_grn_counters.last_serial" in sql
