from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    MarkReadyCommand,
    OffBookCommand,
    bind_lot,
    create_fulfillment,
    mark_ready,
    set_off_book,
)
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentStatus
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.warehouse.domain import StockLot
from app.shared.persistence import OutboxMessageRow, create_schema
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
        deadline=date(2026, 10, 1),
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
        held_by_kitting = uow.fulfillment.reserved_on_lot(lot.id)
        held_elsewhere = uow.fulfillment.reserved_on_lot(created.id)

    assert stored == bound
    assert stored.status is FulfillmentStatus.COMPLETE  # the only line got its lot: «Укомплектован» by itself
    assert stored.at_work == bound.at_work is not None
    assert stored_lot.reserved == Decimal("6")
    # SK-7: the warehouse row edit may not put РЕЗЕРВ under what kitting holds on the lot.
    assert (held_by_kitting, held_elsewhere) == (Decimal("6"), Decimal("0"))
    engine.dispose()


def _user(session, name, *, site, master=True, active=True):
    user_id = uuid4()
    session.add(IdentityUserRow(
        id=user_id, login=f"u{user_id.hex[:8]}", email=f"{user_id.hex[:8]}@example.test", name=name,
        department="Монтаж", role="master" if master else "staff", site=site, active=active,
        access_level="foreman", shop_worker=not master, site_master=master,
    ))
    return user_id


def _task(session, *, project, item, handed_to="", kit=None, status="Запущено", minute=0):
    session.add(ProductionTaskRow(
        id=uuid4(), number=f"PT-{uuid4().hex[:6]}", source_idempotency_key=str(uuid4()), source_fingerprint="f",
        version=1, created_at=datetime(2026, 10, 5, 8, minute), project=project, item=item, name="Жгут", quantity=1,
        fulfillment_request_id=kit, deadline=None, status=status, launched_by="Мастер", handed_to=handed_to,
    ))


def test_kit_site_masters_and_last_handout_place_are_read_from_production_and_staff():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    actor = Actor(name="Технолог", department="КБ")
    now = datetime(2026, 10, 5, 9, 0)

    def kit(purpose):
        return create_fulfillment(CreateFulfillmentCommand(
            idempotency_key=f"kit:{uuid4()}", purpose=purpose, deadline=date(2026, 10, 10),
            lines=(FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("1")),),
            actor=actor, now=now,
        ), factory())

    zhuk_by_item, linked, plain, manual = (
        kit("728К.2 · КБ1 · Жгут"), kit("728К.2 · КБ2 · Жгут"), kit("728К.2 · КБ3 · Жгут"), kit("Ручная заявка"),
    )
    with sessions() as session:
        stepan = _user(session, "Степан", site="ЖУК")
        car_master = _user(session, "Мастер ЦАР", site="ЦАР")
        blank_master = _user(session, "Мастер без площадки", site=None)
        _user(session, "Монтажник ЖУК", site="ЖУК", master=False)
        _user(session, "Уволенный мастер", site="ЖУК", active=False)
        _task(session, project="728К.2", item="728К.2.КБ1", handed_to="ЖУК")
        _task(session, project="728К.2", item="КБ2", handed_to="ЖУК", status="Отменено")
        _task(session, project="728К.2", item="КБ2", kit=linked.id, minute=1)
        session.commit()

    with factory() as uow:
        sites = uow.kit_sites
        assert sites.site_of(zhuk_by_item) == "ЖУК"  # a task of the same project and КБ handed to ЖУК
        assert sites.site_of(linked) == "ЦАР"  # the task launched on the kit is distributed by people
        assert sites.site_of(plain) == sites.site_of(manual) == "ЦАР"  # no task yet / no КБ
        assert sites.masters("ЖУК") == [stepan]
        assert sorted(sites.masters("ЦАР"), key=str) == sorted([car_master, blank_master], key=str)
        assert sites.last_handout("ЖУК") is None

    for request in (zhuk_by_item, plain):
        current = request
        for line in request.lines:
            current = set_off_book(OffBookCommand(current.id, current.version, actor, now, line.id), factory())
        mark_ready(MarkReadyCommand(current.id, current.version, actor, now, f"место {request.number}"), factory())

    with factory() as uow:
        assert uow.kit_sites.last_handout("ЖУК") == f"место {zhuk_by_item.number}"
        assert uow.kit_sites.last_handout("ЦАР") == f"место {plain.number}"
    with sessions() as session:
        recipients = {
            row.payload["title"]: row.payload["recipient_user_ids"].split(",")
            for row in session.scalars(select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested"))
        }
    assert recipients[f"Заявка {zhuk_by_item.number} готова к получению"] == [str(actor.id), str(stepan)]
    assert sorted(recipients[f"Заявка {plain.number} готова к получению"]) == sorted(
        [str(actor.id), str(car_master), str(blank_master)]
    )
    engine.dispose()
