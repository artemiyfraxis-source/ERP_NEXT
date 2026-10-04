from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.domain import ProductionOperation
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.shared.persistence import create_schema
from tests.factories import Actor


def test_task_route_assignment_and_calendar_persist_together():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    operator_id = UUID("10000000-0000-0000-0000-000000000001")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=operator_id, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        session.commit()
    actor = Actor(name="Мастер", department="Производство")
    now = datetime(2026, 9, 22, 9, 0)
    created = launch_production(LaunchProductionCommand(
        idempotency_key="project:591K.3:KB-1:v1", project="591K.3", item="591K.3.КБ1",
        name="Жгут", quantity=10, fulfillment_request_id=None, deadline=date(2026, 10, 1),
        operations=(ProductionOperation.create(
            position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
            seconds_per_unit=Decimal("60"), setup_seconds=Decimal("120"), quantity=10,
        ),), actor=actor, now=now,
    ), factory())
    assigned, _ = assign_production(AssignProductionCommand(
        task_id=created.id, expected_version=created.version,
        operation_id=created.operations[0].id, operator_ids=(operator_id,),
        requested_start=None, actor=actor, now=now,
    ), factory())

    with factory() as uow:
        stored = uow.production.get(created.id)

    assert stored == assigned
    assert stored.number == "ПЗ-0001"
    assert stored.operations[0].operator_ids == (operator_id,)
    assert stored.operations[0].planned_finish == datetime(2026, 9, 22, 9, 14)
    engine.dispose()
