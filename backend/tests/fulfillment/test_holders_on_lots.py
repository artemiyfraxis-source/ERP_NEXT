"""`holders_on_lots`: kit lines holding reserve on given lots, read from a real (SQLite) database."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import CreateFulfillmentCommand, create_fulfillment
from app.modules.fulfillment.domain import FulfillmentLine
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.persistence import create_schema
from tests.factories import Actor


def test_holders_on_lots_lists_only_kit_lines_holding_reserve_on_the_asked_lots():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    lots = [
        StockLot.create(
            warehouse="ЦАР", grn=f"FRX.26.000000{index}", package="", name="Разъём", item_type="Комплектующие",
            purpose="Склад", unit="шт", quantity=Decimal("10"), storage_location="1F4",
            source_receiving_ids=(f"receiving-{index}",),
        )
        for index in (1, 2)
    ]
    with factory() as uow:
        for lot in lots:
            uow.warehouse.add(lot)
        uow.commit()
    actor = Actor(name="Кладовщик", department="Склад")
    made = [
        create_fulfillment(CreateFulfillmentCommand(
            idempotency_key=key, purpose=purpose, deadline=date(2026, 10, 20),
            lines=(FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("4")),),
            actor=actor, now=datetime(2026, 10, 6, 12, minute), lot_ids=(lot.id,),
        ), factory())
        for minute, (key, purpose, lot) in enumerate((
            ("a", "728К.2 · КБ1 · Жгут", lots[0]), ("b", "729К.1 · КБ2 · Жгут", lots[1]),
        ))
    ]

    with factory() as uow:
        assert uow.fulfillment.holders_on_lots([]) == []
        found = uow.fulfillment.holders_on_lots([lots[0].id])
        assert [(row[1], row[2], row[3], row[4], row[5]) for row in found] == [
            (made[0].number, "728К.2 · КБ1 · Жгут", lots[0].id, "шт", Decimal("4")),
        ]
        both = uow.fulfillment.holders_on_lots([lot.id for lot in lots])
        assert {row[3] for row in both} == {lot.id for lot in lots}
        for lot in lots:
            assert sum(row[5] for row in both if row[3] == lot.id) == uow.fulfillment.reserved_on_lot(lot.id)
