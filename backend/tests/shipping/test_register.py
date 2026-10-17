"""Регистр готовой продукции (владелец 12.10.2026): ходы журнала из сдвига счётчиков строки ГП, откат «Отгружено»,
проверки базы, сумма журнала = счётчики на всех путях отгрузки."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.shipping.domain import (
    FinishedGood,
    GoodMove,
    GoodsRegister,
    GoodsState,
    MoveContext,
    MoveKind,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    ShippingValidationError,
    UpdType,
    balance_moves,
    journal_balance,
)
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 12, 9, 0)
CTX = MoveContext(actor="Кладовщик", occurred_at=NOW)


def _good(**counters) -> FinishedGood:
    good = FinishedGood.create(
        project="708К.4", item_index="КБ1", customer="Заказчик", article="", name="Жгут", ordered=Decimal("50"), unit="шт",
    )
    return good.__class__(**{**good.__dict__, **{key: Decimal(value) for key, value in counters.items()}})


def _steps(moves):
    return [(move.kind, move.quantity, move.from_state, move.to_state) for move in moves]


# ── Чистые правила ──────────────────────────────────────────────────────────────────────────────────────────────


def test_forward_counters_become_accept_pack_ship_moves():
    before = _good()
    after = _good(accepted=30, shipped=20, sent=5)
    moves = balance_moves(before, after, CTX, "708К.4")
    assert _steps(moves) == [
        (MoveKind.ACCEPT, Decimal(30), GoodsState.PRODUCTION, GoodsState.ACCEPTED),
        (MoveKind.PACK, Decimal(20), GoodsState.ACCEPTED, GoodsState.PACKED),
        (MoveKind.SHIP, Decimal(5), GoodsState.PACKED, GoodsState.SHIPPED),
    ]
    assert {move.project_key for move in moves} == {"708К.4"}
    assert balance_moves(after, after, None, "708К.4") == ()  # ничего не сдвинулось — журнал не нужен


def test_counters_never_move_without_a_journal_context():
    with pytest.raises(ShippingValidationError, match="только с записью в журнал"):
        balance_moves(_good(), _good(accepted=1), None, "708К.4")


def test_backward_moves_are_unpack_or_head_correction_with_a_reason():
    packed = _good(accepted=30, shipped=20)
    assert _steps(balance_moves(packed, _good(accepted=30, shipped=15), CTX, "708К.4")) == [
        (MoveKind.UNPACK, Decimal(5), GoodsState.PACKED, GoodsState.ACCEPTED),
    ]
    gone = _good(accepted=30, shipped=20, sent=20)
    with pytest.raises(ShippingValidationError, match="только откат руководителя"):
        balance_moves(gone, _good(accepted=30, shipped=20, sent=10), CTX, "708К.4")
    with pytest.raises(ShippingValidationError, match="без причины"):
        balance_moves(gone, _good(accepted=30, shipped=20, sent=10), MoveContext("Админ", NOW, correction=True), "708К.4")
    rollback = MoveContext("Админ", NOW, correction=True, reason="ошибочно нажали «Отгрузить»")
    assert _steps(balance_moves(gone, _good(accepted=30, shipped=20, sent=10), rollback, "708К.4")) == [
        (MoveKind.CORRECTION, Decimal(10), GoodsState.SHIPPED, GoodsState.PACKED),
    ]


def test_a_move_only_goes_along_its_route_and_with_a_positive_quantity():
    base = dict(id=uuid4(), finished_good_id=uuid4(), project_key="708К.4", item_index="КБ1", actor="x", occurred_at=NOW)
    with pytest.raises(ShippingValidationError, match="не ведёт"):
        GoodMove(**base, kind=MoveKind.SHIP, quantity=Decimal(1), from_state=GoodsState.ACCEPTED, to_state=GoodsState.SHIPPED)
    with pytest.raises(ShippingValidationError, match="положительное"):
        GoodMove(**base, kind=MoveKind.ACCEPT, quantity=Decimal(0), from_state=GoodsState.PRODUCTION, to_state=GoodsState.ACCEPTED)


def test_idempotent_moves_take_their_ids_from_the_key():
    seed = uuid4()
    after = _good(accepted=5)
    first = balance_moves(_good(), after, MoveContext("x", NOW, id_seed=seed), "708К.4")
    again = balance_moves(_good(), after, MoveContext("x", NOW, id_seed=seed), "708К.4")
    assert [move.id for move in first] == [move.id for move in again]


def test_register_splits_pieces_by_state_partly_in_each():
    # Тираж 50: 30 принято, из них 20 упаковано, из них 5 уехало; цех сдал 35.
    register = GoodsRegister(needed=Decimal(50), accepted_total=Decimal(30), packed_total=Decimal(20),
                             shipped_total=Decimal(5), delivered=Decimal(35))
    assert (register.in_production, register.accepted, register.packed, register.shipped) == (20, 10, 15, 5)
    assert register.waiting == 5
    assert _good(accepted=30, shipped=20, sent=5).register.accepted == 10
    assert journal_balance({("production", "accepted"): Decimal(30), ("accepted", "production"): Decimal(2),
                            ("accepted", "packed"): Decimal(9), ("packed", "accepted"): Decimal(1),
                            ("packed", "shipped"): Decimal(4), ("shipped", "packed"): Decimal(4)}) == (28, 8, 0)


def test_unship_returns_a_shipment_to_packed_and_clears_the_date():
    good = _good(accepted=10)
    shipment = Shipment.create(
        number="ОТГ-1", created_at=NOW, customer="", receiver="Получатель", shipping_point="Москва", contact="",
        phone="", terms=None, carrier="", deadline=None, upd_type=UpdType.EDO, upd_number="", upd_date=None, upd_url="",
        manager="М", note="", lines=(ShipmentLine.create(good, Decimal(4)),),
    )
    picked, _ = shipment.pick({good.id: Decimal(10)}, {good.id: "ГП.26.0000001"})
    packed = picked.pack("PL1", "DN1", date(2026, 10, 12))
    shipped = packed.mark_shipped(date(2026, 10, 12))
    back = shipped.unship()
    assert (back.status, back.shipped_at) == (ShipmentStatus.PACKED, None)
    with pytest.raises(ShippingValidationError, match="только отгруженную"):
        back.unship()
    assert picked.mark_shipped(date(2026, 10, 12)).unship().status is ShipmentStatus.ASSEMBLED


# ── Через HTTP и базу ───────────────────────────────────────────────────────────────────────────────────────────


ADMIN = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP")


@pytest.fixture()
def register_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ADMIN, email="admin@example.test", roles=frozenset({"admin"}), permissions={},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions, engine
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def _journal_matches_counters(sessions) -> None:
    with SqlAlchemyShippingUnitOfWork(sessions) as uow:
        goods = uow.goods.list()
        totals = uow.goods.journal_totals()
    for good in goods:
        assert journal_balance(totals.get(good.id, {})) == (good.accepted, good.shipped, good.sent), good.item_index


def _kinds(sessions) -> list[tuple[str, str, str]]:
    with sessions() as session:
        return [tuple(row) for row in session.execute(text(
            "SELECT kind, quantity, reason FROM finished_goods_moves ORDER BY occurred_at, kind"
        ))]


def test_every_shipping_path_writes_the_journal_and_it_sums_to_the_counters(register_api):
    client, sessions, _ = register_api
    good = client.post("/api/shipping/goods", json={
        "project": "708К.4", "item_index": "КБ1", "customer": "Заказчик", "name": "Жгут", "ordered": "50",
    }).json()["good"]
    # КБ без заданий в ERP: принять можно до тиража, причина — в журнале.
    accepted = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "30"})
    assert accepted.status_code == 200, accepted.text
    good = accepted.json()["good"]
    over = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "21"})
    assert over.status_code == 422 and "тираж 50 шт, уже принято 30 — принять можно ещё 20" in over.json()["detail"]

    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "upd_type": "ЭДО",
        "lines": [{"good_id": good["id"], "quantity": "20"}],
    }).json()["shipment"]
    line = made["lines"][0]["id"]
    made = client.post(f"/api/shipping/shipments/{made['id']}/unship", json={"version": made["version"], "reason": "x"})
    assert made.status_code == 422  # ещё не уехала
    shipment = client.get("/api/shipping/shipments").json()["shipments"][0]
    shipment = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]}).json()["shipment"]
    assert shipment["status"] == "Отгружена"

    second = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "upd_type": "ЭДО",
        "lines": [{"good_id": good["id"], "quantity": "6"}],
    }).json()["shipment"]
    second = client.post(f"/api/shipping/shipments/{second['id']}/unpick", json={
        "version": second["version"], "line_id": second["lines"][0]["id"],
    })
    assert second.status_code == 422  # упакованную отправку не разбирают — сначала отмена
    second = client.get("/api/shipping/shipments").json()["shipments"]
    second = next(item for item in second if item["id"] != shipment["id"])
    cancelled = client.post(f"/api/shipping/shipments/{second['id']}/cancel", json={"version": second["version"]})
    assert cancelled.status_code == 200

    reason = "ошибочно нажали «Отгрузить»"
    rolled = client.post(f"/api/shipping/shipments/{shipment['id']}/unship", json={"version": shipment["version"], "reason": reason})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["shipment"]["status"] == "Упакована" and rolled.json()["shipment"]["shipped_at"] is None
    assert line == rolled.json()["shipment"]["lines"][0]["id"]

    kinds = [kind for kind, _, _ in _kinds(sessions)]
    assert sorted(kinds) == sorted(["accept", "pack", "ship", "pack", "unpack", "correction"])
    assert ("correction", 20, reason) in [(kind, int(quantity), why) for kind, quantity, why in _kinds(sessions)]
    _journal_matches_counters(sessions)
    good = client.get("/api/shipping/goods").json()["goods"][0]
    assert (good["accepted"], good["shipped"], good["sent"]) == ("30", "20", "0")


def test_only_the_head_rolls_back_shipped_and_never_without_a_reason(register_api):
    client, sessions, _ = register_api
    good = client.post("/api/shipping/goods", json={
        "project": "708К.4", "item_index": "КБ1", "name": "Жгут", "ordered": "5",
    }).json()["good"]
    good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "5"}).json()["good"]
    shipment = client.post("/api/shipping/shipments", json={
        "receiver": "П", "shipping_point": "Москва", "upd_type": "ЭДО", "lines": [{"good_id": good["id"], "quantity": "5"}],
    }).json()["shipment"]
    shipment = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]}).json()["shipment"]

    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Менеджер", department="Продажи"), email="pm@example.test", roles=frozenset({"commercial"}),
        permissions={"shipping": "edit", "shipping_goods": "edit"},
    )
    refused = client.post(f"/api/shipping/shipments/{shipment['id']}/unship", json={"version": shipment["version"], "reason": "x"})
    assert refused.status_code == 403 and "руководитель" in refused.json()["detail"]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ADMIN, email="admin@example.test", roles=frozenset({"admin"}), permissions={},
    )
    blank = client.post(f"/api/shipping/shipments/{shipment['id']}/unship", json={"version": shipment["version"], "reason": "  "})
    assert blank.status_code == 422
    assert [kind for kind, _, _ in _kinds(sessions)].count("correction") == 0


def test_the_database_refuses_counters_out_of_order_and_moves_off_their_route(register_api):
    client, sessions, engine = register_api
    good = client.post("/api/shipping/goods", json={
        "project": "708К.4", "item_index": "КБ1", "name": "Жгут", "ordered": "5",
    }).json()["good"]
    with engine.begin() as connection, pytest.raises(IntegrityError, match="ck_finished_goods_accepted_over_packed"):
        connection.execute(text("UPDATE finished_goods SET shipped = 1"))
    insert = text(
        "INSERT INTO finished_goods_moves (id, finished_good_id, project_key, item_index, kind, quantity, from_state,"
        " to_state, actor, occurred_at, reason) VALUES (:id, :good, '708К.4', 'КБ1', :kind, :quantity, :source,"
        " :target, 'x', '2026-10-12 09:00:00', :reason)"
    )
    good_id = UUID(good["id"]).hex
    cases = (
        ("ship", 1, "accepted", "shipped", "", "ck_finished_goods_moves_route"),
        ("accept", 0, "production", "accepted", "", "ck_finished_goods_moves_quantity"),
        ("correction", 1, "shipped", "packed", "", "ck_finished_goods_moves_correction_reason"),
        ("return", 1, "shipped", "production", "", "ck_finished_goods_moves_kind"),
    )
    for kind, quantity, source, target, reason, check in cases:
        with engine.begin() as connection, pytest.raises(IntegrityError, match=check):
            connection.execute(insert, {"id": uuid4().hex, "good": good_id, "kind": kind, "quantity": quantity,
                                        "source": source, "target": target, "reason": reason})


def test_a_good_with_a_journal_outlives_its_project_card(register_api):
    client, sessions, _ = register_api
    good = client.post("/api/shipping/goods", json={
        "project": "708К.4", "item_index": "КБ1", "name": "Жгут", "ordered": "5",
    }).json()["good"]
    client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "2"})
    dropped = client.post("/api/shipping/project-queue/drop", json={"project": "708К.4"})
    assert dropped.json() == {"deleted": 0, "kept": 1}
