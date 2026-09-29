"""SK-13: handing out a kitting request writes one СПИСАНИЕ row per issued position (Issue.gs:475-502)."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import (
    BindLotCommand,
    ConcurrentFulfillmentUpdate,
    CreateFulfillmentCommand,
    MarkReadyCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    create_fulfillment,
    issue_fulfillment,
    mark_ready,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentValidationError
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot, WarehouseValidationError, WriteOffEntry
from app.modules.warehouse.infrastructure import WriteOffEntryRow
from app.shared.persistence import create_schema
from tests.factories import Actor


ACTOR = Actor(name="Кладовщик", department="Склад")
NOW = datetime(2026, 9, 29, 11, 30)


def lot(grn: str, quantity: str) -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=grn, package="П1", name="Разъём на складе", item_type="Комплектующие",
        purpose="Склад", unit="шт", quantity=Decimal(quantity), storage_location="1F4",
        source_receiving_ids=(f"receiving-{grn}",),
    )


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    engine.dispose()


def journal(factory) -> list[WriteOffEntry]:
    with factory() as uow:
        rows = uow.session.scalars(select(WriteOffEntryRow).order_by(WriteOffEntryRow.grn, WriteOffEntryRow.name))
        return [WriteOffEntry(**{c.name: getattr(row, c.name) for c in WriteOffEntryRow.__table__.columns})
                for row in rows]


def bind(factory, request, line, stock):
    return bind_lot(BindLotCommand(
        request_id=request.id, expected_version=request.version, line_id=line.id, lot_id=stock.id,
        actor=ACTOR, now=NOW,
    ), factory())


def open_line(request, name):
    return next(line for line in request.lines if line.lot is None and line.name == name)


def ready_request(factory):
    """Разъём 8 шт from two lots (6 from A, 2 from B) and Гайка 3 шт from B: B serves two positions."""
    lot_a, lot_b = lot("FRX.26.0000001", "6"), lot("FRX.26.0000002", "10")
    with factory() as uow:
        uow.warehouse.add(lot_a)
        uow.warehouse.add(lot_b)
        uow.commit()
    request = create_fulfillment(CreateFulfillmentCommand(
        idempotency_key="kitset:SK-13", purpose="591К.3 / КБ-1", deadline=None,
        lines=(
            FulfillmentLine.create(article="КД-001", name="Разъём", unit="шт", requirement=Decimal("8")),
            FulfillmentLine.create(article="КД-002", name="Гайка", unit="шт", requirement=Decimal("3")),
        ),
        actor=ACTOR, now=NOW,
    ), factory())
    request = bind(factory, request, open_line(request, "Разъём"), lot_a)
    request = bind(factory, request, open_line(request, "Разъём"), lot_b)
    request = bind(factory, request, open_line(request, "Гайка"), lot_b)
    request = mark_ready(MarkReadyCommand(
        request_id=request.id, expected_version=request.version, handout_location="Полка выдачи 1",
        actor=ACTOR, now=NOW,
    ), factory())
    return request, lot_a, lot_b


def test_issue_writes_one_writeoff_row_per_position_with_the_lot_remainder(factory):
    ready, lot_a, lot_b = ready_request(factory)

    issued = issue_fulfillment(VersionedFulfillmentCommand(ready.id, ready.version, ACTOR, NOW), factory())

    rows = journal(factory)
    assert [(row.grn, row.name, row.quantity, row.remaining_after) for row in rows] == [
        ("FRX.26.0000001", "Разъём", Decimal("6"), Decimal("0")),
        # Two positions on one lot: both show the lot balance once the whole request is posted.
        ("FRX.26.0000002", "Гайка", Decimal("3"), Decimal("5")),
        ("FRX.26.0000002", "Разъём", Decimal("2"), Decimal("5")),
    ]
    for row in rows:
        assert row.kind == "issue"
        assert row.reason == f"Выдача {ready.number}"
        assert row.author == "выдача по заявке"
        assert row.purpose == ""
        assert row.posted is True
        assert (row.warehouse, row.package, row.unit) == ("ЦАР", "П1", "шт")
        assert (row.actor_id, row.actor_name) == (ACTOR.id, ACTOR.name)
        assert row.occurred_at == NOW
    assert {row.lot_id for row in rows} == {lot_a.id, lot_b.id}
    assert {row.source_key for row in rows} == {f"fulfillment-issue:{line.id}" for line in issued.lines}
    # Stock semantics are unchanged: the reserve is written off, the journal only records it.
    with factory() as uow:
        assert (uow.warehouse.get(lot_a.id).balance, uow.warehouse.get(lot_a.id).reserved) == (0, 0)
        assert (uow.warehouse.get(lot_b.id).balance, uow.warehouse.get(lot_b.id).reserved) == (5, 0)


def test_a_repeated_issue_adds_no_writeoff_rows(factory):
    ready, _, _ = ready_request(factory)
    issued = issue_fulfillment(VersionedFulfillmentCommand(ready.id, ready.version, ACTOR, NOW), factory())

    with pytest.raises(ConcurrentFulfillmentUpdate):
        issue_fulfillment(VersionedFulfillmentCommand(ready.id, ready.version, ACTOR, NOW), factory())
    with pytest.raises(FulfillmentValidationError):
        issue_fulfillment(VersionedFulfillmentCommand(issued.id, issued.version, ACTOR, NOW), factory())

    assert len(journal(factory)) == 3


def test_nothing_is_journalled_before_the_issue(factory):
    ready_request(factory)

    assert journal(factory) == []


def test_the_journal_keeps_one_row_per_source_key(factory):
    stock = lot("FRX.26.0000009", "4")
    entry = WriteOffEntry.for_lot(
        stock, kind="issue", quantity=Decimal("1"), reason="Выдача REQ.26.0000001", purpose="",
        author="выдача по заявке", actor_id=ACTOR.id, actor_name=ACTOR.name, occurred_at=NOW,
        remaining_after=Decimal("3"), source_key="fulfillment-issue:one",
    )
    with factory() as uow:
        uow.writeoffs.add(entry)
        uow.writeoffs.add(WriteOffEntry.for_lot(
            stock, kind="issue", quantity=Decimal("1"), reason="Выдача REQ.26.0000001", purpose="",
            author="выдача по заявке", actor_id=ACTOR.id, actor_name=ACTOR.name, occurred_at=NOW,
            remaining_after=Decimal("3"), source_key="fulfillment-issue:one",
        ))
        uow.commit()
    with factory() as uow:
        assert uow.session.scalar(select(func.count()).select_from(WriteOffEntryRow)) == 1
        assert uow.writeoffs.by_source_key("fulfillment-issue:one").id == entry.id


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_a_writeoff_row_needs_a_positive_quantity(quantity):
    with pytest.raises(WarehouseValidationError, match="больше нуля"):
        WriteOffEntry.for_lot(
            lot("FRX.26.0000010", "1"), kind="issue", quantity=Decimal(quantity), reason="", purpose="",
            author="", actor_id=ACTOR.id, actor_name=ACTOR.name, occurred_at=NOW, remaining_after=None,
        )


def test_a_writeoff_row_kind_is_one_of_the_journal_kinds():
    with pytest.raises(WarehouseValidationError, match="Неизвестный вид"):
        WriteOffEntry.for_lot(
            lot("FRX.26.0000011", "1"), kind="shipment", quantity=Decimal("1"), reason="", purpose="",
            author="", actor_id=ACTOR.id, actor_name=ACTOR.name, occurred_at=NOW, remaining_after=None,
        )
