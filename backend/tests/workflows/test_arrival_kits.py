"""An arrival binds itself to the project's waiting kit (owner 05.10.2026), end to end on SQLite."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import outbox_worker
from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    bind_lot,
    create_fulfillment,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.receiving.application import (
    InspectReceivingCommand,
    PlaceReceivingCommand,
    consume_receiving_expected,
    inspect_receiving,
    place_receiving,
)
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.application import LOT_ARRIVED_TOPIC, dispatch_next_warehouse_received
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from app.workflows.arrival_kits import (
    ArrivalSource,
    PoisonArrivalMessage,
    ReceivingArrivalLookup,
    dispatch_next_lot_arrived,
)
from tests.factories import Actor
from tests.receiving.test_supplier_return import expected_message, inspection

NOW = datetime(2026, 10, 5, 12, 0)
STOREKEEPER = Actor(name="Кладовщик", department="Склад")
PROJECT = "591К.3"
RESISTOR = ArrivalSource(project=PROJECT, articles=("КД-1",), names=("Разъём",))


@pytest.fixture()
def erp():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield SimpleNamespace(
        sessions=sessions,
        fulfillment=lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
        receiving=lambda: SqlAlchemyReceivingUnitOfWork(sessions),
        warehouse=lambda: SqlAlchemyWarehouseUnitOfWork(sessions),
        procurement=lambda: SqlAlchemyProcurementUnitOfWork(sessions),
    )
    engine.dispose()


class Sources:
    def __init__(self, source: ArrivalSource | None):
        self.source = source
        self.asked: list[UUID] = []

    def source_of(self, receiving_line_id):
        self.asked.append(receiving_line_id)
        return self.source


def kit(erp, purpose: str, *lines: tuple[str, str, str]) -> FulfillmentRequest:
    return create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"test-{uuid4()}",
        purpose=purpose,
        deadline=date(2026, 10, 20),
        lines=tuple(
            FulfillmentLine.create(article=article, name=name, unit="шт", requirement=Decimal(need))
            for article, name, need in lines
        ),
        actor=STOREKEEPER,
        now=NOW,
    ), erp.fulfillment())


def put_lot(erp, grn: str, quantity: str, *, reserved: str = "0") -> StockLot:
    lot = StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name="Разъём", item_type="Комплектующие", purpose="Склад",
        unit="шт", quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receipt-{grn}",),
    )
    if Decimal(reserved) > 0:
        lot = lot.reserve(Decimal(reserved))
    with erp.warehouse() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return lot


def announce(erp, lot: StockLot, quantity: str, receipt: str | None = None, **payload) -> OutboxMessage:
    receipt = receipt or str(uuid4())
    message = OutboxMessage(
        id=uuid4(), topic=LOT_ARRIVED_TOPIC, idempotency_key=f"test:{uuid4()}", created_at=NOW,
        payload={
            "lot_id": str(lot.id), "receipt_id": receipt, "receiving_line_id": str(uuid4()),
            "warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package, "quantity": quantity, **payload,
        },
    )
    with erp.fulfillment() as uow:
        uow.outbox.add(message)
        uow.commit()
    return message


def dispatch(erp, sources):
    return dispatch_next_lot_arrived(NOW, erp.fulfillment, sources)


def request(erp, request_id) -> FulfillmentRequest:
    with erp.fulfillment() as uow:
        return uow.fulfillment.get(request_id)


def lot_now(erp, lot_id) -> StockLot:
    with erp.warehouse() as uow:
        return uow.warehouse.get(lot_id)


def item_lines(current: FulfillmentRequest, article: str = "КД-1"):
    return [
        ((line.lot.grn if line.lot else None), line.requirement, line.reserved)
        for line in current.lines if line.article == article
    ]


def notifications(erp) -> list[dict]:
    with erp.sessions() as session:
        return list(session.scalars(
            select(OutboxMessageRow.payload).where(OutboxMessageRow.topic == "notification.requested")
        ))


def message_state(erp, message_id):
    with erp.sessions() as session:
        row = session.get(OutboxMessageRow, message_id)
        return row.published_at, row.last_error


def test_a_project_lot_binds_fifo_across_kb_in_natural_order_and_tells_the_storekeepers_once(erp):
    kb10 = kit(erp, f"{PROJECT} · КБ10 · Жгут", ("КД-1", "Разъём", "4"))
    kb2 = kit(erp, f"{PROJECT} · КБ2 · Жгут", ("КД-1", "Разъём", "4"), ("КД-2", "Провод", "3"))
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    other = kit(erp, "729К.1 · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = put_lot(erp, "FRX.26.0000100", "10")
    message = announce(erp, lot, "10")

    outcome = dispatch(erp, Sources(RESISTOR))

    assert [(item.kb, item.quantity) for item in outcome.result.bound] == [
        ("КБ1", Decimal("4")), ("КБ2", Decimal("4")), ("КБ10", Decimal("2")),
    ]
    assert item_lines(request(erp, kb1.id)) == [("FRX.26.0000100", Decimal("4"), Decimal("4"))]
    assert item_lines(request(erp, kb2.id)) == [("FRX.26.0000100", Decimal("4"), Decimal("4"))]
    assert item_lines(request(erp, kb10.id)) == [
        ("FRX.26.0000100", Decimal("2"), Decimal("2")), (None, Decimal("2"), Decimal("0")),
    ]
    assert request(erp, kb2.id).version == kb2.version + 1  # one version step per request
    # КБ1 закрыта целиком — «Укомплектован» ставится сам; у КБ2 ждёт ещё одна позиция.
    assert request(erp, kb1.id).status.value == "Укомплектован"
    assert request(erp, kb2.id).status.value == "Недоукомплектован"
    assert item_lines(request(erp, kb2.id), "КД-2") == [(None, Decimal("3"), Decimal("0"))]  # another item waits
    assert request(erp, other.id) == other  # another project's kit is not touched
    assert lot_now(erp, lot.id).reserved == Decimal("10")
    published, error = message_state(erp, message.id)
    assert published is not None and error is None

    (note,) = notifications(erp)
    assert note["recipient_audiences"] == "storekeeper"
    assert note["title"] == f"Пришло под {PROJECT}: КБ1, КБ2, КБ10"
    assert note["body"].splitlines()[:3] == [
        f"Пришло под {PROJECT} КБ1: 1 позиция; комплект готов к сборке",
        f"Пришло под {PROJECT} КБ2: 1 позиция; ещё не хватает 1 позиции",
        f"Пришло под {PROJECT} КБ10: 1 позиция; ещё не хватает 1 позиции",
    ]
    assert "ГРН FRX.26.0000100" in note["body"]
    with erp.sessions() as session:
        bound_events = session.scalars(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "fulfillment.lot_bound")
        ).all()
    assert len(bound_events) == 3 and all(item["source"] == "arrival" for item in bound_events)


def test_partial_coverage_binds_what_arrived_and_the_rest_keeps_waiting(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "10"))
    lot = put_lot(erp, "FRX.26.0000101", "4")
    announce(erp, lot, "4")

    outcome = dispatch(erp, Sources(RESISTOR))

    assert outcome.result.short == Decimal("6")
    assert item_lines(request(erp, kb1.id)) == [
        ("FRX.26.0000101", Decimal("4"), Decimal("4")), (None, Decimal("6"), Decimal("0")),
    ]
    (note,) = notifications(erp)
    assert note["title"] == f"Пришло под {PROJECT} КБ1: 1 позиция; ещё не хватает 1 позиции"


def test_a_single_kit_completed_by_the_arrival_says_so(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "3"), ("", "Разъём", "2"))
    lot = put_lot(erp, "FRX.26.0000102", "5")
    announce(erp, lot, "5")

    dispatch(erp, Sources(RESISTOR))

    assert request(erp, kb1.id).open_lines() == ()
    (note,) = notifications(erp)
    assert note["title"] == f"Пришло под {PROJECT} КБ1: 2 позиции; комплект готов к сборке"


@pytest.mark.parametrize("source", [
    None,  # the receiving line is unknown
    ArrivalSource(project="", articles=("КД-1",), names=("Разъём",)),  # bought for the warehouse
    ArrivalSource(project="777К.1", articles=("КД-1",), names=("Разъём",)),  # bought for another project
])
def test_stock_not_bought_for_the_project_is_not_bound(erp, source):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = put_lot(erp, "FRX.26.0000103", "10")
    message = announce(erp, lot, "10")

    outcome = dispatch(erp, Sources(source))

    assert outcome.result.bound == () and outcome.notified is False
    assert request(erp, kb1.id) == kb1
    assert lot_now(erp, lot.id).reserved == Decimal("0")
    assert notifications(erp) == []
    assert message_state(erp, message.id)[0] is not None  # done, not retried
    assert dispatch(erp, Sources(source)) is None


def test_only_the_arrived_item_binds_article_first_name_when_the_line_has_no_article(erp):
    kb1 = kit(
        erp, f"{PROJECT} · КБ1 · Жгут",
        ("КД-9", "Разъём", "1"),  # same name, another КД article: another item
        ("", " разъём ", "1"),  # no article: by name, case and spaces aside
        ("кд-1", "Разъём угловой", "1"),  # the article decides
    )
    lot = put_lot(erp, "FRX.26.0000104", "10")
    announce(erp, lot, "10")

    dispatch(erp, Sources(RESISTOR))

    current = request(erp, kb1.id)
    assert [(line.article, line.reserved) for line in current.lines] == [
        ("КД-9", Decimal("0")), ("", Decimal("1")), ("кд-1", Decimal("1")),
    ]


def test_the_same_arrival_twice_changes_nothing_and_notifies_once(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = put_lot(erp, "FRX.26.0000105", "10")
    first = announce(erp, lot, "10", receipt="receipt-105")
    dispatch(erp, Sources(RESISTOR))
    bound = request(erp, kb1.id)
    late = kit(erp, f"{PROJECT} · КБ2 · Жгут", ("КД-1", "Разъём", "4"))  # a kit created after the arrival

    with erp.sessions() as session:  # the same message delivered once more
        session.get(OutboxMessageRow, first.id).published_at = None
        session.commit()
    again = dispatch(erp, Sources(RESISTOR))
    duplicate = announce(erp, lot, "10", receipt="receipt-105")  # a second message for the same receipt
    third = dispatch(erp, Sources(RESISTOR))

    assert again.message_id == first.id and again.result.bound == () and again.notified is False
    assert third.message_id == duplicate.id and third.result.bound == ()
    assert request(erp, kb1.id) == bound
    assert request(erp, late.id) == late
    assert lot_now(erp, lot.id).reserved == Decimal("4")
    assert len(notifications(erp)) == 1
    assert message_state(erp, first.id)[0] is not None and message_state(erp, duplicate.id)[0] is not None


def test_a_kit_already_covered_gets_nothing_more(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    picked = put_lot(erp, "FRX.26.0000106", "4")
    covered = bind_lot(BindLotCommand(
        request_id=kb1.id, expected_version=kb1.version, actor=STOREKEEPER, now=NOW,
        line_id=kb1.lines[0].id, lot_id=picked.id,
    ), erp.fulfillment())
    lot = put_lot(erp, "FRX.26.0000107", "10")
    announce(erp, lot, "10")

    outcome = dispatch(erp, Sources(RESISTOR))

    assert outcome.result.bound == ()
    assert request(erp, kb1.id) == covered
    assert lot_now(erp, lot.id).reserved == Decimal("0")
    assert notifications(erp) == []


def test_never_more_than_arrived_and_never_another_holders_reserve(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "20"))
    # A topped-up lot: 3 arrived now, 12 lay there; 6 of it is someone else's reserve.
    lot = put_lot(erp, "FRX.26.0000108", "15", reserved="6")
    announce(erp, lot, "3")

    outcome = dispatch(erp, Sources(RESISTOR))

    assert [item.quantity for item in outcome.result.bound] == [Decimal("3")]
    assert lot_now(erp, lot.id).reserved == Decimal("9")

    # Another arrival larger than what is free: the lot gives only ДОСТУПНО, the other reserve stays whole.
    announce(erp, lot, "20")
    outcome = dispatch(erp, Sources(RESISTOR))
    after = lot_now(erp, lot.id)
    assert [item.quantity for item in outcome.result.bound] == [Decimal("6")]
    assert (after.reserved, after.available) == (Decimal("15"), Decimal("0"))
    assert item_lines(request(erp, kb1.id)) == [
        ("FRX.26.0000108", Decimal("3"), Decimal("3")),
        ("FRX.26.0000108", Decimal("6"), Decimal("6")),
        (None, Decimal("11"), Decimal("0")),
    ]


def test_a_broken_message_is_quarantined_without_binding(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = put_lot(erp, "FRX.26.0000109", "10")
    message = announce(erp, lot, "0")

    with pytest.raises(PoisonArrivalMessage):
        dispatch(erp, Sources(RESISTOR))

    published, error = message_state(erp, message.id)
    assert published is None and "quantity" in error
    assert request(erp, kb1.id) == kb1


# ── through receiving and the warehouse ──────────────────────────────────


def stock_delivery(erp, *, goal: str) -> StockLot:
    place_delivery(erp, goal=goal)
    return dispatch_next_warehouse_received(NOW, erp.warehouse())


def place_delivery(erp, *, goal: str, article: str = "КД-1", name: str = "Разъём") -> None:
    """A delivery accepted and placed: receiving hands it to the warehouse (`warehouse.received`)."""
    base = expected_message()
    line = consume_receiving_expected(
        replace(base, payload={**base.payload, "goal": goal, "article": article, "name": name}), erp.receiving(),
    )
    accepted = inspect_receiving(InspectReceivingCommand(
        line_id=line.id, expected_version=line.version, inspection=inspection(), actor=STOREKEEPER, now=NOW,
    ), erp.receiving())
    place_receiving(PlaceReceivingCommand(
        line_id=accepted.id, expected_version=accepted.version, storage_location="1F4", actor=STOREKEEPER, now=NOW,
    ), erp.receiving())


def test_a_delivery_for_the_project_binds_when_the_warehouse_takes_it(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = stock_delivery(erp, goal=PROJECT)
    lookup = ReceivingArrivalLookup(erp.receiving, erp.procurement)

    outcome = dispatch_next_lot_arrived(NOW, erp.fulfillment, lookup)

    assert outcome.project == PROJECT
    assert item_lines(request(erp, kb1.id)) == [(lot.grn, Decimal("4"), Decimal("4"))]
    assert lot_now(erp, lot.id).reserved == Decimal("4")
    (note,) = notifications(erp)
    assert note["title"] == f"Пришло под {PROJECT} КБ1: 1 позиция; комплект готов к сборке"
    assert dispatch_next_lot_arrived(NOW, erp.fulfillment, lookup) is None


def test_a_delivery_for_the_warehouse_stays_free(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    lot = stock_delivery(erp, goal="")

    outcome = dispatch_next_lot_arrived(NOW, erp.fulfillment, ReceivingArrivalLookup(erp.receiving, erp.procurement))

    assert outcome.result.bound == ()
    assert request(erp, kb1.id) == kb1
    assert lot_now(erp, lot.id).reserved == Decimal("0")


class Purchases:
    def __init__(self, purchase):
        self.procurement = SimpleNamespace(get=lambda line_id: purchase)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_purchase_names_the_project_and_its_kd_article(erp):
    stock_delivery(erp, goal="")
    purchase = SimpleNamespace(project_reference=PROJECT, design_article="КД-1", calculated_name="Разъём SMA")
    lookup = ReceivingArrivalLookup(erp.receiving, lambda: Purchases(purchase))
    with erp.sessions() as session:
        payload = session.scalars(
            select(OutboxMessageRow.payload).where(OutboxMessageRow.topic == LOT_ARRIVED_TOPIC)
        ).one()

    source = lookup.source_of(UUID(payload["receiving_line_id"]))

    assert source.project == PROJECT
    assert "КД-1" in source.articles and "Разъём SMA" in source.names
    assert lookup.source_of(uuid4()) is None


def test_the_outbox_worker_binds_the_kit_right_after_the_warehouse_takes_the_delivery(erp):
    kb1 = kit(erp, f"{PROJECT} · КБ1 · Жгут", ("КД-1", "Разъём", "4"))
    place_delivery(erp, goal=PROJECT)

    outbox_worker.drain_available(erp.sessions)

    assert [line.reserved for line in request(erp, kb1.id).lines] == [Decimal("4")]
    with erp.sessions() as session:
        pending = session.scalars(select(OutboxMessageRow.topic).where(OutboxMessageRow.published_at.is_(None))).all()
    assert LOT_ARRIVED_TOPIC not in pending
