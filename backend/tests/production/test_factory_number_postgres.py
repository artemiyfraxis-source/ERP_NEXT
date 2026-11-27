"""Opt-in real PostgreSQL race of factory-number allocation (0222); isolated schema in loopback erp_stock_test only.

Two transactions number the same task at once — the loser waits on the row lock and takes the winner's numbers; two
tasks reaching for the same number — exactly one wins, the other is refused (pre-check or UNIQUE). The repository is driven without the project lock the print holds, so
the row lock and UNIQUE (factory_number) alone are proven."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionSerialRow, ProductionTaskRow, SqlAlchemySerialRepository
from app.shared.persistence import Base

NOW = datetime(2026, 11, 27, 9, tzinfo=UTC)


def _closure(table):
    seen, stack = {}, [table]
    while stack:
        current = stack.pop()
        if current.name not in seen:
            seen[current.name] = current
            stack.extend(key.column.table for key in current.foreign_keys)
    return list(seen.values())


@pytest.fixture
def engine():
    raw = os.environ.get("ERP_TEST_POSTGRES_URL")
    if raw is None:
        pytest.skip("ERP_TEST_POSTGRES_URL is not set (factory-number concurrency not verified)")
    url = make_url(raw)
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or url.host not in {"localhost", "127.0.0.1", "::1"} \
            or url.database != "erp_stock_test" or url.query:
        pytest.fail("Factory-number tests require PostgreSQL on loopback, database erp_stock_test, no URL options")
    url = url.set(drivername="postgresql+psycopg")
    schema = f"erp_factory_{uuid4().hex}"
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
        Base.metadata.create_all(result, tables=_closure(ProductionSerialRow.__table__))
        yield result
    finally:
        result.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _task(engine, number, quantity=3):
    task_id = uuid4()
    with Session(engine) as session:
        session.add(ProductionTaskRow(id=task_id, number=number, source_idempotency_key=f"key-{number}",
            source_fingerprint="0" * 64, version=1, created_at=NOW, project="831К.", item="КБ1", name="Сборка",
            quantity=quantity, status="Запущено", launched_by="Мастер"))
        session.flush()
        SqlAlchemySerialRepository(session).ensure_task(task_id, number, quantity)
        session.commit()
    return task_id


def _numbers(first, quantity=3):
    return {position: (k, f"FRX.831К.КБ1.{k}") for position, k in enumerate(range(first, first + quantity), 1)}


def _race(engine, *jobs):
    barrier = Barrier(len(jobs))

    def run(task_id, numbers):
        with Session(engine) as session:
            barrier.wait()
            try:
                issued = SqlAlchemySerialRepository(session).assign_factory_numbers(task_id, "КБ1", numbers)
                session.commit()
                return len(issued)
            except (ProductionValidationError, IntegrityError) as error:   # both refuse the print
                session.rollback()
                return error
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return [future.result() for future in [pool.submit(run, *job) for job in jobs]]


def _stored(engine):
    with Session(engine) as session:
        return sorted((str(row.task_id), row.position, row.factory_number) for row in session.scalars(
            select(ProductionSerialRow).where(ProductionSerialRow.factory_number.is_not(None))))


def test_one_task_printed_twice_at_once_gets_its_numbers_once(engine):
    task = _task(engine, "ПЗ-0001")
    outcomes = _race(engine, (task, _numbers(1)), (task, _numbers(1)))
    assert sorted(outcomes) == [0, 3]
    assert [number for *_rest, number in _stored(engine)] == ["FRX.831К.КБ1.1", "FRX.831К.КБ1.2", "FRX.831К.КБ1.3"]


def test_two_tasks_reaching_for_one_number_never_share_it(engine):
    first, second = _task(engine, "ПЗ-0001"), _task(engine, "ПЗ-0002")
    outcomes = _race(engine, (first, _numbers(1)), (second, _numbers(1)))
    assert sum(item == 3 for item in outcomes) == 1
    assert sum(isinstance(item, (ProductionValidationError, IntegrityError)) for item in outcomes) == 1
    stored = _stored(engine)
    assert len(stored) == 3 and len({number for *_rest, number in stored}) == 3
    assert len({task_id for task_id, *_rest in stored}) == 1
