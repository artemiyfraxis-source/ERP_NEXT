"""RQ-MINE: ProcurementLineStageLookup, the Requests → Procurement read-only port."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.procurement.application import (
    TakeProcurementInWorkCommand,
    dispatch_next_procurement_requested,
    take_procurement_in_work,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.persistence import create_schema
from app.workflows.procurement_stage import ProcurementLineStageLookup
from tests.factories import Actor


def _draft(name: str = "Разъём") -> RequestDraft:
    return RequestDraft(
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


def test_stages_for_reports_procurement_status_once_handed_off_and_taken_in_work():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    request_uow = lambda: SqlAlchemyUnitOfWork(sessions)
    procurement_uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    lookup = ProcurementLineStageLookup(procurement_uow)

    handed_request = create_requests(
        CreateRequestsCommand(
            creation_key="stage:handed",
            drafts=(_draft(),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        request_uow(),
    )[0]
    untouched_request = create_requests(
        CreateRequestsCommand(
            creation_key="stage:new",
            drafts=(_draft("Кабель"),),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        request_uow(),
    )[0]

    assert lookup.stages_for([]) == {}
    assert lookup.stages_for([handed_request.id, untouched_request.id]) == {}

    handoff_requests(
        HandoffRequestsCommand(
            request_ids=(handed_request.id,),
            actor=Actor(name="Петров", department="Закупки"),
            now=datetime(2026, 9, 15, 11, 0),
        ),
        request_uow(),
    )
    line = dispatch_next_procurement_requested(datetime(2026, 9, 15, 11, 1), procurement_uow())
    take_procurement_in_work(
        TakeProcurementInWorkCommand(
            line_ids=(line.id,),
            actor=Actor(name="Петров", department="Закупки"),
            now=datetime(2026, 9, 15, 11, 30),
        ),
        procurement_uow(),
    )

    stages = lookup.stages_for([handed_request.id, untouched_request.id])

    assert stages == {handed_request.id: "В работе"}
    details = lookup.details_for([handed_request.id, untouched_request.id])
    assert list(details) == [handed_request.id]
    assert details[handed_request.id].stage == "В работе"
    assert details[handed_request.id].buyer == "Петров"
    assert details[handed_request.id].order_number is None
    assert details[handed_request.id].sum is None
    assert lookup.details_for([]) == {}
    engine.dispose()
