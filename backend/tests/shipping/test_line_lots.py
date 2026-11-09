"""ГРН партии в строке отправки (владелец 06.11.2026, аудит прода): набор берёт акты приёмки ГП по FIFO (дата акта, номер)
или явно выбранный ГРН, строка помнит, сколько штук из какого акта; возврат и отмена отдают штуки тем же актам;
PL/DN печатают ГРН партий."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db_integrity import take_snapshot
from app.modules.shipping.api import _shipment_result
from app.modules.shipping.application import (
    AcceptGoodCommand, CreateShipmentCommand, PackProjectCommand, PickCommand, RequestedGood, ShipmentCommand, UnpickCommand, accept_good,
    cancel_shipment, create_shipment, pack_project, pick_shipment, unpick_line,
)
from app.modules.shipping.documents import build_delivery_note, build_packing_lists
from app.modules.shipping.domain import (
    ActStock, FinishedGood, GoodLots, LineLot, MoveContext, PackPermission, ShippingValidationError,
)
from app.modules.shipping.infrastructure import ShipmentLineLotRow, SqlAlchemyShippingUnitOfWork
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.shipping.test_domain import good as domain_good, shipment as domain_shipment

NOW = datetime(2026, 10, 15, 9, 0)
PROJECT = "533К.12"
STOREKEEPER = Actor(name="Кладовщик", department="Склад")
G1, G2, G3 = "ГП.26.0000001", "ГП.26.0000002", "ГП.26.0000003"


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)  # noqa: E731
    good = FinishedGood.create(
        project=PROJECT, item_index="КБ1", customer="Заказчик", article="", name="Жгут", ordered=Decimal(2000), unit="шт",
    )
    with factory() as uow:
        uow.goods.add(good)
        uow.pack_permissions.add(PackPermission(PROJECT, "КБ1").change(Decimal(2000), Decimal(0), "Менеджер", NOW))
        uow.commit()
    # Акты: 100 шт 15.10 (ГРН КБ), 350 шт 16.10.
    for days, quantity in ((0, 100), (1, 350)):
        with factory() as uow:
            current = uow.goods.get(good.id)
        accept_good(AcceptGoodCommand(
            good_id=good.id, expected_version=current.version, quantity=Decimal(quantity),
            received_at=(NOW + timedelta(days=days)).date(), actor=STOREKEEPER, now=NOW + timedelta(days=days),
        ), factory())
    yield engine, factory, good.id
    engine.dispose()


def _pack(factory, good_id, quantity, grn: str = ""):
    return pack_project(PackProjectCommand(
        project=PROJECT, lines=(RequestedGood(good_id=good_id, quantity=Decimal(quantity), grn=grn),),
        actor=STOREKEEPER, now=NOW + timedelta(days=3),
    ), factory())


def _lots(shipment) -> list[tuple[str, Decimal]]:
    return [(lot.grn, lot.quantity) for lot in shipment.lines[0].lots]


def _stored(factory, shipment_id):
    with factory() as uow:
        return uow.shipments.get(shipment_id)


def test_fifo_pick_spans_two_acts_and_the_line_shows_both_grns(shop):
    engine, factory, good_id = shop

    first = _pack(factory, good_id, 300)
    second = _pack(factory, good_id, 100)

    assert _lots(first) == [(G1, Decimal(100)), (G2, Decimal(200))]
    stored = _stored(factory, first.id)
    assert _lots(stored) == _lots(first) and stored.lines[0].grns == (G1, G2) and stored.lines[0].grn == G1
    assert _lots(second) == [(G2, Decimal(100))]  # первый акт уже весь в первой отправке
    line = _shipment_result(stored)["lines"][0]
    assert line["grn"] == G1 and line["grns"] == [G1, G2]
    assert [(lot["grn"], lot["quantity"]) for lot in line["lots"]] == [(G1, "100"), (G2, "200")]
    assert all(lot["act_id"] for lot in line["lots"])
    assert take_snapshot(engine)["counts"]["shipment_line_lots_mismatch"] == 0


def test_explicit_grn_takes_only_that_act_and_unknown_grn_is_refused(shop):
    _engine, factory, good_id = shop

    with pytest.raises(ShippingValidationError, match="ГП.26.0000099 — не акт приёмки этой КБ"):
        _pack(factory, good_id, 1, grn="ГП.26.0000099")

    chosen = _pack(factory, good_id, 400, grn=G2)
    assert _lots(chosen) == [(G2, Decimal(350))] and chosen.lines[0].shortage == Decimal(50)
    with pytest.raises(ShippingValidationError, match="свободных штук нет"):
        _pack(factory, good_id, 1, grn=G2)

    rest = _pack(factory, good_id, 100)
    assert _lots(rest) == [(G1, Decimal(100))]  # FIFO берёт то, что осталось свободным


def test_repick_with_chosen_grn_per_line(shop):
    _engine, factory, good_id = shop
    blocker = _pack(factory, good_id, 450)  # всё занято
    waiting = create_shipment(CreateShipmentCommand(
        customer="Заказчик", receiver="Получатель", shipping_point="", contact="", phone="", terms=None, carrier="",
        deadline=None, upd_type=None, upd_number="", upd_date=None, upd_url="", note="",
        lines=(RequestedGood(good_id=good_id, quantity=Decimal(50)),), actor=STOREKEEPER, now=NOW, no_docs=True,
    ), factory())
    assert not waiting.lines[0].lots and waiting.lines[0].grn is None
    unpick_line(UnpickCommand(
        shipment_id=blocker.id, expected_version=blocker.version, line_id=blocker.lines[0].id, actor=STOREKEEPER, now=NOW,
    ), factory())

    picked = pick_shipment(PickCommand(
        shipment_id=waiting.id, expected_version=waiting.version, actor=STOREKEEPER, now=NOW,
        grns={waiting.lines[0].id: G2},
    ), factory())

    assert _lots(picked) == [(G2, Decimal(50))]


def test_unpick_returns_pieces_to_the_same_act(shop):
    _engine, factory, good_id = shop
    first = _pack(factory, good_id, 300)   # 100 из ГП…1 + 200 из ГП…2
    second = _pack(factory, good_id, 100)  # 100 из ГП…2

    unpick_line(UnpickCommand(
        shipment_id=first.id, expected_version=first.version, line_id=first.lines[0].id, actor=STOREKEEPER, now=NOW,
    ), factory())
    assert not _stored(factory, first.id).lines[0].lots
    third = _pack(factory, good_id, 150)

    assert _lots(_stored(factory, second.id)) == [(G2, Decimal(100))]  # чужую строку возврат не трогает
    assert _lots(third) == [(G1, Decimal(100)), (G2, Decimal(50))]  # ГП…1 снова свободен первым по FIFO


def test_cancel_gives_pieces_back_and_deletes_the_lots(shop):
    engine, factory, good_id = shop
    first = _pack(factory, good_id, 120)

    cancelled = cancel_shipment(ShipmentCommand(
        shipment_id=first.id, expected_version=first.version, actor=STOREKEEPER, now=NOW,
    ), factory())

    assert not cancelled.lines[0].lots
    with factory() as uow:
        assert uow.session.scalars(select(ShipmentLineLotRow)).all() == []
    assert _lots(_pack(factory, good_id, 120)) == [(G1, Decimal(100)), (G2, Decimal(20))]
    assert take_snapshot(engine)["counts"]["shipment_line_lots_mismatch"] == 0


def test_rolled_back_pieces_of_an_act_are_not_picked(shop):
    _engine, factory, good_id = shop
    with factory() as uow:  # руководитель откатил 50 шт «принято → в производство»: снимаются с нового акта
        current = uow.goods.get(good_id)
        uow.goods.add(replace(current, accepted=current.accepted - 50, version=current.version + 1), MoveContext(
            actor="Руководитель", occurred_at=NOW + timedelta(days=2), reason="ошибка приёмки", correction=True,
        ))
        uow.commit()

    picked = _pack(factory, good_id, 400)

    assert _lots(picked) == [(G1, Decimal(100)), (G2, Decimal(300))]
    with factory() as uow:
        assert [act.net for act in uow.goods.acts([good_id])[good_id]] == [Decimal(100), Decimal(300)]


def test_pl_and_dn_print_the_lot_grns():
    item = domain_good().accept(Decimal("10"), date(2026, 9, 22), G1)
    stock = {item.id: GoodLots(
        acts=(ActStock(None, G1, Decimal(4)), ActStock(None, G3, Decimal(10))), fallback=ActStock(None, G1, Decimal(0)),
    )}
    picked, _ = domain_shipment(item).pick({item.id: Decimal(10)}, stock)
    packed = picked.pack("PL591К.3-1", "DN591К.3-1", date(2026, 9, 22))

    assert picked.lines[0].lots == (LineLot(G1, Decimal(4)), LineLot(G3, Decimal(6)))
    assert [row.batch for form in build_packing_lists(packed) for row in form.rows] == [f"{G1}, {G3}"]
    assert [row.batch for row in build_delivery_note(packed, "Заказчик").rows] == [f"{G1}, {G3}"]


def test_pieces_beyond_the_acts_go_to_the_first_grn():
    item = domain_good().accept(Decimal("10"), date(2026, 9, 22), G1)
    stock = {item.id: GoodLots(acts=(ActStock(None, G2, Decimal(3)),), fallback=ActStock(None, G1, Decimal(0)))}

    picked, _ = domain_shipment(item).pick({item.id: Decimal(10)}, stock)

    assert picked.lines[0].lots == (LineLot(G2, Decimal(3)), LineLot(G1, Decimal(7)))
    assert picked.lines[0].grn == G2
