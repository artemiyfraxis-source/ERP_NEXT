"""ЦЕЛЬ строки листа закупки читается из заявки одним пакетным запросом на список."""

from datetime import date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import dispatch_next_procurement_requested
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.requests.application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    create_requests,
    handoff_requests,
)
from app.modules.requests.domain import RequestDraft
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.request_purpose import RequestPurposeLookup
from tests.factories import Actor


def _draft(purpose: str, name: str) -> RequestDraft:
    return RequestDraft(
        purpose=purpose, delivery_point="г. Москва, ул. 6-я Радиальная, д. 17", name=name, item_type="Комплектующие",
        quantity=Decimal("2"), unit="шт", quality_document="Нет",
        procurement_deadline=date(2026, 9, 18), planned_date=date(2026, 9, 25),
    )


def test_lines_carry_purpose_of_their_request_with_one_batched_lookup():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    request_uow = lambda: SqlAlchemyUnitOfWork(sessions)
    procurement_uow = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    created = create_requests(
        CreateRequestsCommand(
            creation_key="purpose:1",
            drafts=(_draft("RND", "Разъём"), _draft("FRX", "Кабель")),
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
        ),
        request_uow(),
    )
    handoff_requests(
        HandoffRequestsCommand(
            request_ids=tuple(item.id for item in created),
            actor=Actor(name="Петров", department="Закупки"),
            now=datetime(2026, 9, 15, 11, 0),
        ),
        request_uow(),
    )
    for _ in created:
        dispatch_next_procurement_requested(datetime(2026, 9, 15, 11, 1), procurement_uow())

    calls: list[list] = []
    real = RequestPurposeLookup(request_uow)

    class Spy:
        def purposes_for(self, ids):
            calls.append(list(ids))
            return real.purposes_for(ids)

    app.state.procurement_uow_factory = procurement_uow
    app.state.request_purpose_lookup = Spy()
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 15, 11, 30)
    try:
        response = TestClient(app).get("/api/procurement/lines")
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        app.dependency_overrides.pop(moscow_now, None)
        del app.state.request_purpose_lookup
        del app.state.procurement_uow_factory
        engine.dispose()

    assert response.status_code == 200
    purposes = {line["calculated_name"]: line["purpose"] for line in response.json()["lines"]}
    assert purposes == {"Разъём": "RND", "Кабель": "FRX"}
    assert len(calls) == 1 and len(calls[0]) == 2
