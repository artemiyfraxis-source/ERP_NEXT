from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.procurement.application import (
    SaveProcurementFactsCommand,
    TakeProcurementInWorkCommand,
    consume_procurement_requested,
    PoisonOutboxMessage,
    dispatch_next_procurement_requested,
    save_procurement_facts,
    take_procurement_in_work,
)
from app.modules.procurement.domain import ProcurementFacts, ProcurementStatus
from app.modules.procurement.infrastructure import (
    ProcurementLineRow,
    SqlAlchemyProcurementUnitOfWork,
)
from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor


def message() -> OutboxMessage:
    return OutboxMessage(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        topic="procurement.requested",
        idempotency_key="request:one:procurement:v2",
        created_at=datetime(2026, 9, 15, 11, 0),
        payload={
            "source_request_id": "10000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-20260915-01",
            "request_date": "2026-09-15",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": "RND.001",
            "project_reference": None,
            "priority": "Очень срочно",
            "delivery_point": "Москва",
            "planned_date": "2026-09-25",
            "link": None,
            "item_type": "Комплектующие",
            "design_article": None,
            "category": "Общепром",
            "calculated_name": "Разъём",
            "calculated_quantity": "2",
            "calculated_unit": "шт",
            "calculated_price": None,
            "calculated_price_with_vat": None,
            "calculated_sum": None,
        },
    )


def test_procurement_round_trip_and_idempotent_consume():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)

    first = consume_procurement_requested(message(), factory())
    retry = consume_procurement_requested(message(), factory())
    buyer = Actor(name="Петров", department="Закупки")
    taken = take_procurement_in_work(
        TakeProcurementInWorkCommand(
            line_ids=(first.id,),
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 30),
        ),
        factory(),
    )[0]
    ready = save_procurement_facts(
        SaveProcurementFactsCommand(
            line_id=taken.id,
            facts=ProcurementFacts(
                supplier="ООО Поставка",
                actual_quantity=Decimal("2"),
                actual_unit="шт",
            ),
            expected_version=taken.version,
            actor=buyer,
            now=datetime(2026, 9, 15, 12, 0),
        ),
        factory(),
    )

    assert retry == first
    assert ready.status is ProcurementStatus.READY
    assert ready.version == 3
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 3
        stored = session.get(ProcurementLineRow, first.id)
        assert stored.status == ProcurementStatus.READY.value
        assert stored.supplier == "ООО Поставка"
    engine.dispose()


def test_dispatch_consumes_request_outbox_and_marks_it_in_same_commit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    request_uow = lambda: SqlAlchemyUnitOfWork(sessions)
    procurement_uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    request = create_requests(
        CreateRequestsCommand(
            creation_key="dispatcher:create",
            drafts=(
                RequestDraft(
                    purpose="RND",
                    delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
                    name="Разъём",
                    item_type="Комплектующие",
                    quantity=Decimal("2"),
                    unit="шт",
                    quality_document="Нет",
                    procurement_deadline=datetime(2026, 9, 18).date(),
                    planned_date=datetime(2026, 9, 25).date(),
                ),
            ),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        request_uow(),
    )[0]
    handoff_requests(
        HandoffRequestsCommand(
            request_ids=(request.id,),
            actor=Actor(name="Петров", department="Закупки"),
            now=datetime(2026, 9, 15, 11, 0),
        ),
        request_uow(),
    )

    dispatched = dispatch_next_procurement_requested(
        datetime(2026, 9, 15, 11, 1), procurement_uow()
    )
    nothing_left = dispatch_next_procurement_requested(
        datetime(2026, 9, 15, 11, 2), procurement_uow()
    )

    assert dispatched is not None
    assert dispatched.source_request_id == request.id
    assert nothing_left is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 1
        outbox = session.scalar(select(OutboxMessageRow))
        assert outbox.published_at == datetime(2026, 9, 15, 11, 1)
    engine.dispose()


def test_poison_outbox_is_backed_off_without_blocking_next_message():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        session.add_all(
            [
                OutboxMessageRow(
                    id=UUID("00000000-0000-0000-0000-000000000010"),
                    topic="procurement.requested",
                    idempotency_key="poison:first",
                    created_at=datetime(2026, 9, 15, 10, 0),
                    payload={"legacy_id": "broken"},
                ),
                OutboxMessageRow(
                    id=message().id,
                    topic=message().topic,
                    idempotency_key=message().idempotency_key,
                    created_at=message().created_at,
                    payload=message().payload,
                ),
            ]
        )
        session.commit()
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)

    with pytest.raises(PoisonOutboxMessage):
        dispatch_next_procurement_requested(datetime(2026, 9, 15, 11, 0), factory())
    valid = dispatch_next_procurement_requested(datetime(2026, 9, 15, 11, 0), factory())

    assert valid is not None
    with sessions() as session:
        poison = session.get(
            OutboxMessageRow,
            UUID("00000000-0000-0000-0000-000000000010"),
        )
        assert poison.attempt_count == 1
        assert poison.next_attempt_at == datetime(2026, 9, 15, 11, 0, 2)
        assert poison.last_error
        assert poison.published_at is None
    engine.dispose()


def test_poison_outbox_is_dead_lettered_after_five_attempts():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    poison_id = UUID("00000000-0000-0000-0000-000000000099")
    with sessions() as session:
        session.add(
            OutboxMessageRow(
                id=poison_id,
                topic="procurement.requested",
                idempotency_key="poison:dead-letter",
                created_at=datetime(2026, 9, 15, 10, 0),
                payload={},
            )
        )
        session.commit()
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)

    for minute in range(5):
        attempted_at = datetime(2026, 9, 15, 11, minute)
        with pytest.raises(PoisonOutboxMessage):
            dispatch_next_procurement_requested(attempted_at, factory())

    assert dispatch_next_procurement_requested(datetime(2026, 9, 15, 12, 0), factory()) is None
    with sessions() as session:
        poison = session.get(OutboxMessageRow, poison_id)
        assert poison.attempt_count == 5
        assert poison.dead_lettered_at == datetime(2026, 9, 15, 11, 4)
    engine.dispose()


def test_repeated_transfer_appends_next_revision_with_new_system_id_and_keeps_old_line():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    request_uow = lambda: SqlAlchemyUnitOfWork(sessions)
    procurement_uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    requests = create_requests(
        CreateRequestsCommand(
            creation_key="retransfer:create",
            drafts=tuple(
                RequestDraft(
                    purpose="RND",
                    delivery_point="г. Москва, ул. 6-я Радиальная, д. 17",
                    name=name,
                    item_type="Комплектующие",
                    quantity=Decimal("2"),
                    unit="шт",
                    quality_document="Нет",
                    procurement_deadline=datetime(2026, 9, 18).date(),
                    planned_date=datetime(2026, 9, 25).date(),
                )
                for name in ("Разъём", "Кабель")
            ),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        request_uow(),
    )
    first_request, second_request = requests
    assert first_request.designation == second_request.designation
    buyer = Actor(name="Петров", department="Закупки")

    def handoff(request_ids, now, retransfer=False):
        handoff_requests(
            HandoffRequestsCommand(request_ids=request_ids, actor=buyer, now=now, retransfer=retransfer),
            request_uow(),
        )
        lines = []
        while (line := dispatch_next_procurement_requested(now, procurement_uow())) is not None:
            lines.append(line)
        return lines

    originals = handoff((first_request.id, second_request.id), datetime(2026, 9, 15, 11, 0))
    taken = take_procurement_in_work(
        TakeProcurementInWorkCommand(line_ids=(originals[0].id,), actor=buyer, now=datetime(2026, 9, 15, 11, 30)),
        procurement_uow(),
    )[0]
    retransferred = handoff(
        (first_request.id, second_request.id), datetime(2026, 9, 16, 9, 0), retransfer=True
    )

    # The outbox dispatches messages of one moment in id order, so compare in dispatch order.
    assert sorted((line.legacy_id, line.revision) for line in originals) == [
        ("SM-20260915-01", "A"),
        ("SM-20260915-02", "A"),
    ]
    # Owner, 05.10: whoever pressed «В закупку» gets the new revision on the desk «В работе».
    assert [(line.legacy_id, line.revision, line.status, line.buyer.id) for line in retransferred] == [
        ("SM-20260916-01", "B", ProcurementStatus.IN_WORK, buyer.id),
        ("SM-20260916-02", "C", ProcurementStatus.IN_WORK, buyer.id),
    ], "same НАЗНАЧЕНИЕ: max letter + 1, the next line in the batch sees the previous one"
    assert {line.source_request_id for line in retransferred} == {first_request.id, second_request.id}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 4
        kept = session.get(ProcurementLineRow, taken.id)
        assert kept.status == ProcurementStatus.IN_WORK.value
        assert kept.buyer_id == buyer.id
        assert kept.revision == "A"
    engine.dispose()


def test_sql_revisions_for_designation_fold_cyrillic_case_and_spaces():
    # PR-2: SQLite's lower() folds only ASCII, so the repository compares НАЗНАЧЕНИЕ in Python.
    from tests.procurement.test_application import transfer

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    consume_procurement_requested(transfer(1, designation="Проект 591К.3.001"), uow())
    consume_procurement_requested(transfer(1, 2, designation="Проект 591К.3.001"), uow())
    consume_procurement_requested(transfer(2, designation="  проект   591к.3.001 "), uow())

    again = consume_procurement_requested(transfer(2, 2, designation="  проект   591к.3.001 "), uow())

    assert (again.legacy_id, again.revision) == ("SM-20260915-04", "C")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProcurementLineRow)) == 4
    engine.dispose()
