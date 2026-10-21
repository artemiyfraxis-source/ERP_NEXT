"""Акты приёмки ГП (владелец 19.10.2026: «когда идёт приёмка разными частями — ГРН один и тот же»): у каждой
приёмки КБ свой ГРН, первый — ГРН самой КБ; отгруженное до ERP акта не получает; откат снимает штуки с нового акта."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db_integrity import take_snapshot
from app.modules.shipping.application import (
    AcceptGoodCommand, ShipBeforeErpCommand, accept_good, ship_before_erp,
)
from app.modules.shipping.domain import AcceptanceAct, FinishedGood, MoveContext, roll_back_acts
from app.modules.shipping.infrastructure import FinishedGoodsActRow, ShippingSequenceRow, SqlAlchemyShippingUnitOfWork
from app.shared.persistence import create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 15, 9, 0)
STOREKEEPER = Actor(name="Кладовщик", department="Склад")


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    yield engine, sessions, lambda: SqlAlchemyShippingUnitOfWork(sessions)
    engine.dispose()


def _register(factory, item: str = "КБ1", ordered: int = 1000) -> FinishedGood:
    good = FinishedGood.create(
        project="533К.11", item_index=item, customer="Заказчик", article="", name="Жгут", ordered=Decimal(ordered), unit="шт",
    )
    with factory() as uow:
        uow.goods.add(good)
        uow.commit()
    return good


def _accept(factory, good_id, quantity: int, now: datetime = NOW, location: str = "") -> FinishedGood:
    with factory() as uow:
        good = uow.goods.get(good_id)
    return accept_good(AcceptGoodCommand(
        good_id=good.id, expected_version=good.version, quantity=Decimal(quantity), received_at=now.date(),
        actor=STOREKEEPER, now=now, location=location,
    ), factory())


def _acts(factory, good_id) -> tuple[AcceptanceAct, ...]:
    with factory() as uow:
        return uow.goods.acts([good_id]).get(good_id, ())


def _sequence(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(ShippingSequenceRow.value).where(ShippingSequenceRow.name == "grn-26"))


def test_each_partial_acceptance_gets_its_own_grn_and_the_first_is_the_kbs(shop):
    engine, sessions, factory = shop
    good = _register(factory)

    first = _accept(factory, good.id, 100, NOW, location="Стеллаж 1")
    _accept(factory, good.id, 350, NOW + timedelta(days=1))
    last = _accept(factory, good.id, 350, NOW + timedelta(days=2), location="Стеллаж 2")

    acts = _acts(factory, good.id)
    assert [(act.grn, act.quantity, act.received_at, act.location) for act in acts] == [
        ("ГП.26.0000001", Decimal(100), date(2026, 10, 15), "Стеллаж 1"),
        ("ГП.26.0000002", Decimal(350), date(2026, 10, 16), "Стеллаж 1"),
        ("ГП.26.0000003", Decimal(350), date(2026, 10, 17), "Стеллаж 2"),
    ]
    assert first.grn == last.grn == acts[0].grn  # этикетки, PL/DN и отправки видят ГРН КБ — первый акт
    assert len({act.move_id for act in acts}) == 3 and all(act.actor == "Кладовщик" for act in acts)
    assert _sequence(sessions) == 3
    assert take_snapshot(engine)["counts"]["finished_goods_acts_mismatch"] == 0


def test_two_kbs_each_take_their_own_number(shop):
    _engine, _sessions, factory = shop
    one, two = _register(factory, "КБ1"), _register(factory, "КБ2")
    _accept(factory, one.id, 5)
    _accept(factory, two.id, 7)
    assert [act.grn for act in _acts(factory, one.id)] == ["ГП.26.0000001"]
    assert [act.grn for act in _acts(factory, two.id)] == ["ГП.26.0000002"]


def test_shipped_before_erp_gets_no_act_and_the_next_acceptance_is_the_first(shop):
    engine, sessions, factory = shop
    good = _register(factory)
    with factory() as uow:
        before = ship_before_erp(ShipBeforeErpCommand(
            project=good.project, item_index=good.item_index, total=Decimal(40), actor=STOREKEEPER, now=NOW,
        ), uow)
    assert before.grn is None and _acts(factory, good.id) == ()
    assert take_snapshot(engine)["counts"]["finished_goods_acts_mismatch"] == 0

    accepted = _accept(factory, good.id, 10)
    assert [(act.grn, act.quantity) for act in _acts(factory, good.id)] == [(accepted.grn, Decimal(10))]
    assert accepted.grn == "ГП.26.0000001" and _sequence(sessions) == 1
    assert take_snapshot(engine)["counts"]["finished_goods_acts_mismatch"] == 0


def test_a_rollback_to_production_takes_pieces_off_the_newest_act_first(shop):
    engine, _sessions, factory = shop
    good = _register(factory)
    _accept(factory, good.id, 400)
    _accept(factory, good.id, 600, NOW + timedelta(days=1))
    with factory() as uow:
        current = uow.goods.get(good.id)
        uow.goods.add(replace(current, accepted=current.accepted - 650, version=current.version + 1), MoveContext(
            actor="Руководитель", occurred_at=NOW + timedelta(days=2), reason="пересорт", correction=True,
        ))
        uow.commit()

    acts = _acts(factory, good.id)
    assert [(act.quantity, act.rolled_back, act.net) for act in acts] == [
        (Decimal(400), Decimal(50), Decimal(350)), (Decimal(600), Decimal(600), Decimal(0)),
    ]
    assert take_snapshot(engine)["counts"]["finished_goods_acts_mismatch"] == 0  # акт — запись приёмки, откат её не стирает


def test_a_rollback_only_reaches_acts_accepted_before_it():
    def act(quantity, day):
        return AcceptanceAct(
            id=uuid4(), finished_good_id=uuid4(), move_id=uuid4(), grn=f"ГП.26.{day:07d}", quantity=Decimal(quantity),
            received_at=date(2026, 10, day), location="", actor="", created_at=datetime(2026, 10, day, 9),
        )

    acts = (act(100, 1), act(200, 3))
    rolled = roll_back_acts(acts, ((datetime(2026, 10, 2, 9), Decimal(30)), (datetime(2026, 10, 3, 9), Decimal(250))))
    assert [item.net for item in rolled] == [Decimal(20), Decimal(0)]  # 30 — до второго акта; 250: 200 + 50 старого
    assert roll_back_acts(acts, ()) == acts


def test_the_database_holds_one_act_per_move_and_unique_numbers(shop):
    _engine, sessions, factory = shop
    good = _register(factory)
    _accept(factory, good.id, 3)
    act = _acts(factory, good.id)[0]
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(FinishedGoodsActRow(
            id=uuid4(), finished_good_id=good.id, move_id=act.move_id, grn="ГП.26.0000099", quantity=1,
            received_at=NOW.date(), location="", actor="x", created_at=NOW,
        ))
        session.flush()
    with sessions() as session, pytest.raises(IntegrityError):
        session.add(FinishedGoodsActRow(
            id=uuid4(), finished_good_id=good.id, move_id=uuid4(), grn="ГП.26.0000098", quantity=0,
            received_at=NOW.date(), location="", actor="x", created_at=NOW,
        ))
        session.flush()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(FinishedGoodsActRow)) == 1
