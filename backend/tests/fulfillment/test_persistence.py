from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    bind_lot,
    create_fulfillment,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.persistence import create_schema
from tests.factories import Actor


def test_request_and_stock_reservation_persist_in_one_database_transaction():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    lot = StockLot.create(
        warehouse="ЦАР",
        grn="FRX.26.0000001",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="Склад",
        unit="шт",
        quantity=Decimal("10"),
        storage_location="1F4",
        source_receiving_ids=("receiving-one",),
    )
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    actor = Actor(name="Кладовщик", department="Склад")
    now = datetime(2026, 9, 22, 12, 0)
    created = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key="kitset:35К.С.13:КБ-1",
        purpose="35К.С.13 / КБ-1",
        deadline=None,
        lines=(FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("6")),),
        actor=actor,
        now=now,
    ), factory())
    bound = bind_lot(BindLotCommand(
        request_id=created.id,
        expected_version=created.version,
        line_id=created.lines[0].id,
        lot_id=lot.id,
        actor=actor,
        now=now,
    ), factory())

    with factory() as uow:
        stored = uow.fulfillment.get(created.id)
        stored_lot = uow.warehouse.get(lot.id)

    assert stored == bound
    assert stored.status is FulfillmentStatus.COMPLETE
    assert stored_lot.reserved == Decimal("6")
    engine.dispose()
