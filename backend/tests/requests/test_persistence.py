from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft, RequestStatus
from app.modules.requests.infrastructure import (
    AuditEventRow,
    OutboxMessageRow,
    RequestDesignationRow,
    RequestRow,
    SqlAlchemyUnitOfWork,
    create_schema,
)
from tests.factories import Actor


def draft(name: str = "Разъём") -> RequestDraft:
    return RequestDraft(
        purpose="RND",
        delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
        name=name,
        item_type="Комплектующие",
        quantity=Decimal("2.500000"),
        unit="шт",
        quality_document="Нет",
        procurement_deadline=date(2026, 9, 18),
        planned_date=date(2026, 9, 25),
        price_with_vat=Decimal("1000.50"),
    )


@pytest.fixture()
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield engine, lambda: SqlAlchemyUnitOfWork(factory)
    engine.dispose()


def test_create_and_retry_round_trip_through_sqlite(database):
    engine, uow_factory = database
    command = CreateRequestsCommand(
        creation_key="api:create:one",
        drafts=(draft(), draft("Кабель")),
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
    )

    first = create_requests(command, uow_factory())
    retry = create_requests(command, uow_factory())

    assert [item.id for item in retry] == [item.id for item in first]
    assert retry[0].quantity == Decimal("2.500000")
    assert retry[0].status is RequestStatus.NEW
    with sessionmaker(bind=engine)() as session:
        assert session.scalar(select(func.count()).select_from(RequestRow)) == 2
        assert session.scalar(select(func.count()).select_from(RequestDesignationRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 2


def test_handoff_persists_status_audit_and_one_outbox_message(database):
    engine, uow_factory = database
    request = create_requests(
        CreateRequestsCommand(
            creation_key="api:create:handoff",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow_factory(),
    )[0]
    command = HandoffRequestsCommand(
        request_ids=(request.id,),
        actor=Actor(name="Петров", department="Закупки"),
        now=datetime(2026, 9, 15, 11, 0),
    )

    first = handoff_requests(command, uow_factory())
    retry = handoff_requests(command, uow_factory())

    assert retry == first
    with sessionmaker(bind=engine)() as session:
        stored = session.get(RequestRow, request.id)
        assert stored.status == RequestStatus.IN_WORK.value
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxMessageRow)) == 1
        event = session.scalar(select(OutboxMessageRow))
        assert event.payload["calculated_name"] == "Разъём"


def test_uncommitted_unit_of_work_rolls_back(database):
    engine, uow_factory = database
    request = create_requests(
        CreateRequestsCommand(
            creation_key="api:create:rollback-source",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        uow_factory(),
    )[0]

    with pytest.raises(RuntimeError):
        with uow_factory() as uow:
            changed = request.start_procurement(Actor(name="Петров", department="Закупки"))
            uow.requests.add(changed)
            raise RuntimeError("abort transaction")

    with sessionmaker(bind=engine)() as session:
        assert session.get(RequestRow, request.id).status == RequestStatus.NEW.value


def test_by_initiator_paginates_newest_first_and_ignores_other_actors(database):
    engine, uow_factory = database
    ivanov = Actor(name="Иванов", department="РНД")
    petrov = Actor(name="Петров", department="Закупки")
    create_requests(
        CreateRequestsCommand(
            creation_key="mine:sql:1",
            drafts=(draft("Разъём"), draft("Кабель")),
            actor=ivanov,
            now=datetime(2026, 9, 15, 10, 0),
        ),
        uow_factory(),
    )
    create_requests(
        CreateRequestsCommand(
            creation_key="mine:sql:2",
            drafts=(draft("Труба"),),
            actor=petrov,
            now=datetime(2026, 9, 15, 11, 0),
        ),
        uow_factory(),
    )

    with uow_factory() as uow:
        total = uow.requests.count_by_initiator(ivanov.id)
        first_page = uow.requests.by_initiator(ivanov.id, limit=1, offset=0)
        second_page = uow.requests.by_initiator(ivanov.id, limit=1, offset=1)

    assert total == 2
    assert [item.name for item in first_page] == ["Кабель"]
    assert [item.name for item in second_page] == ["Разъём"]


def test_initial_migration_builds_a_database_usable_by_unit_of_work(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "erp-next.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(database_url)
    assert {"requests", "request_designations", "audit_events", "outbox_messages", "procurement_lines", "alembic_version"}.issubset(
        inspect(engine).get_table_names()
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    created = create_requests(
        CreateRequestsCommand(
            creation_key="migration:smoke",
            drafts=(draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        SqlAlchemyUnitOfWork(sessions),
    )
    assert created[0].legacy_id == "SM-20260915-01"
    engine.dispose()
