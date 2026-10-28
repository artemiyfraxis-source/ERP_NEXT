"""«Отгрузить» одной операцией: POST /api/shipping/project-ship — всё или ничего, повтор по ключу."""

import base64
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.shipping import application
from app.modules.shipping.infrastructure import (
    FinishedGoodRow, ProjectAddresseeRow, ShipmentLineRow, ShipmentRow, ShippingFileRow, SqlAlchemyShippingUnitOfWork,
)
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow, create_schema
from tests.factories import Actor

PROJECT = "591К.3"
PDF = base64.b64encode(b"%PDF-1.4 upd").decode()
ADMIN = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP"),
    email="admin@example.test", roles=frozenset({"admin"}), permissions={"shipping": "edit"},
)


MANAGER = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Менеджер", department="Продажи"),
    email="pm@example.test", roles=frozenset({"pm"}), permissions={"shipping": "edit"},
)
STOREKEEPER = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000003"), name="Кладовщик", department="Склад"),
    email="store@example.test", roles=frozenset({"pm"}), permissions={"shipping_goods": "edit"},
)


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: ADMIN
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0)
    yield TestClient(app, raise_server_exceptions=False), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def _as(principal: Principal) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: principal


def _stock(client, item_index="КБ1", quantity="6") -> dict:
    good = client.post("/api/shipping/goods", json={
        "project": PROJECT, "item_index": item_index, "customer": "Заказчик", "name": "Жгут " + item_index,
        "ordered": "40", "unit": "шт",
    }).json()["good"]
    return client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": quantity}).json()["good"]


def _card(client, **fields) -> dict:
    # Карточка проекта со всем, что нужно для отгрузки (владелец 28.10.2026): телефон и условия доставки.
    fields = {"phone": "+7 900", "terms": "САМОВЫВОЗ", **fields}
    return client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": 0, **fields}).json()["addressee"]


def _body(goods: list[tuple[dict, str]], *, addressee_version=0, **extra) -> dict:
    return {
        "project": PROJECT, "addressee_version": addressee_version,
        "lines": [{"good_id": good["id"], "version": good["version"], "quantity": quantity} for good, quantity in goods],
        **extra,
    }


def _ship(client, body: dict, key="key-1"):
    return client.post("/api/shipping/project-ship", json=body, headers={"Idempotency-Key": key})


def _snapshot(sessions) -> dict:
    with sessions() as session:
        count = lambda row: session.scalar(select(func.count()).select_from(row))  # noqa: E731
        return {
            "shipments": count(ShipmentRow), "lines": count(ShipmentLineRow), "files": count(ShippingFileRow),
            "audit": count(AuditEventRow), "outbox": count(OutboxMessageRow),
            "goods": sorted((row.item_index, row.version, str(row.shipped), str(row.sent)) for row in session.scalars(select(FinishedGoodRow))),
            "cards": sorted((row.project, row.version, row.shipping_point, row.contact) for row in session.scalars(select(ProjectAddresseeRow))),
        }


def test_manager_with_warehouse_ships_card_change_documents_and_file_in_one_request(env):
    client, sessions = env
    good = _stock(client)
    card = _card(client, shipping_point="Москва", terms="САМОВЫВОЗ", upd_type="БУМАГА")
    response = _ship(client, _body([(good, "6")], addressee_version=card["version"],
        addressee={"contact": "Иванов", "phone": "+7 900"},
        documents={"upd_number": "77", "upd_date": "2026-09-22", "deadline": "2026-09-30"},
        upd_file={"file_name": "upd.pdf", "content_type": "application/pdf", "content_base64": PDF}, ship=True))

    assert response.status_code == 200, response.text
    data = response.json()
    shipment = data["shipment"]
    assert (data["shipped"], data["closed"], data["why"], data["replayed"]) == (True, True, "", False)
    assert shipment["status"] == "Закрыта"
    assert shipment["id"] == str(application.project_ship_id("key-1"))
    assert (shipment["contact"], shipment["upd_number"], shipment["deadline"]) == ("Иванов", "77", "2026-09-30")
    assert shipment["upd_url"].startswith("/api/shipping/files/")
    goods = {row["id"]: row for row in client.get("/api/shipping/goods").json()["goods"]}
    assert (goods[good["id"]]["sent"], goods[good["id"]]["remaining"]) == ("6", "0")
    stored = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    assert (stored["version"], stored["contact"], stored["phone"]) == (card["version"] + 1, "Иванов", "+7 900")
    with sessions() as session:
        events = list(session.scalars(select(AuditEventRow.event_type).order_by(AuditEventRow.occurred_at)))
    assert {"shipping.addressee_set", "shipping.created", "shipping.picked", "shipping.packed", "shipping.upd_attached",
            "shipping.shipped", "shipping.closed"} <= set(events)


def test_shipping_without_upd_number_is_refused_and_says_why(env):
    client, _ = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="ЭДО")
    response = _ship(client, _body([(good, "4")], addressee_version=1, ship=True))
    # Без № УПД отправка не уезжает вовсе (владелец 28.10.2026), а не уезжает незакрытой.
    assert response.status_code == 422
    assert "№ УПД" in response.json()["detail"]


def test_pack_only_manager_gets_packed_storekeeper_gets_assembled(env):
    client, _ = env
    first, second = _stock(client, "КБ1"), _stock(client, "КБ2")
    _card(client, shipping_point="Москва")
    _as(MANAGER)
    by_manager = _ship(client, _body([(first, "2")], addressee_version=1), key="m").json()
    _as(STOREKEEPER)
    by_storekeeper = _ship(client, _body([(second, "3")], addressee_version=1), key="s").json()

    assert (by_manager["shipment"]["status"], by_manager["shipped"]) == ("Упакована", False)
    assert by_manager["shipment"]["pl_number"]
    assert by_storekeeper["shipment"]["status"] == "Собрана"
    assert by_storekeeper["shipment"]["lines"][0]["picked"] == "3"


def test_replay_with_same_key_and_body_returns_stored_result_and_writes_nothing(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="БЕЗ УПД")
    body = _body([(good, "5")], addressee_version=1, ship=True)
    first = _ship(client, body).json()
    before = _snapshot(sessions)
    again = _ship(client, body)

    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert again.json()["shipment"]["id"] == first["shipment"]["id"]
    assert again.json()["shipment"]["number"] == first["shipment"]["number"]
    assert (again.json()["shipped"], again.json()["closed"], again.json()["why"]) == (first["shipped"], first["closed"], first["why"])
    assert _snapshot(sessions) == before


def test_same_key_with_other_body_is_refused_and_writes_nothing(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва")
    _ship(client, _body([(good, "2")], addressee_version=1))
    before = _snapshot(sessions)
    fresh = client.get("/api/shipping/goods").json()["goods"][0]
    other = _ship(client, _body([(fresh, "3")], addressee_version=1))

    assert other.status_code == 409
    assert "ключ повтора" in other.json()["detail"]
    assert _snapshot(sessions) == before


def test_stale_good_version_or_addressee_version_is_409_and_nothing_written(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва")
    before = _snapshot(sessions)

    stale_good = _ship(client, _body([({**good, "version": good["version"] - 1}, "2")], addressee_version=1,
        addressee={"contact": "Петров"}), key="a")
    stale_card = _ship(client, _body([(good, "2")], addressee_version=0, addressee={"contact": "Петров"}), key="b")

    assert (stale_good.status_code, stale_card.status_code) == (409, 409)
    assert _snapshot(sessions) == before


def test_quantity_above_stock_is_409_not_clamped(env):
    client, sessions = env
    good = _stock(client, quantity="6")
    _card(client, shipping_point="Москва")
    before = _snapshot(sessions)
    response = _ship(client, _body([(good, "7")], addressee_version=1))

    assert response.status_code == 409
    assert "только 6" in response.json()["detail"]
    assert _snapshot(sessions) == before


def test_role_matrix(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="БЕЗ УПД")
    before = _snapshot(sessions)

    _as(STOREKEEPER)
    card_by_storekeeper = _ship(client, _body([(good, "1")], addressee_version=1, addressee={"contact": "Петров"}, ship=True), key="r1")
    docs_by_storekeeper = _ship(client, _body([(good, "1")], addressee_version=1, documents={"upd_number": "5"}), key="r2")
    _as(MANAGER)
    ship_by_manager = _ship(client, _body([(good, "1")], addressee_version=1, ship=True), key="r3")
    assert (card_by_storekeeper.status_code, docs_by_storekeeper.status_code, ship_by_manager.status_code) == (403, 403, 403)
    assert _snapshot(sessions) == before

    _as(STOREKEEPER)
    shipped = _ship(client, _body([(good, "1")], addressee_version=1, ship=True), key="r4")
    assert shipped.status_code == 200
    assert shipped.json()["shipment"]["status"] == "Закрыта"  # «Без УПД»: закрывается сразу


def test_paper_upd_without_file_refuses_shipping_and_keeps_the_card_unchanged(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="БУМАГА")
    before = _snapshot(sessions)
    response = _ship(client, _body([(good, "2")], addressee_version=1, addressee={"contact": "Петров"}, ship=True))

    assert response.status_code == 422
    assert "файл УПД" in response.json()["detail"]
    assert _snapshot(sessions) == before


@pytest.mark.parametrize("step", ["_set_project_addressee", "_create_shipment", "_attach_upd", "_mark_shipped"])
def test_failure_at_any_step_writes_nothing(env, monkeypatch, step):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="БУМАГА")
    before = _snapshot(sessions)
    original = getattr(application, step)

    def broken(*args, **kwargs):
        original(*args, **kwargs)  # шаг успел записать в сессию — и всё равно ничего не должно остаться
        raise RuntimeError(f"сбой на шаге {step}")

    monkeypatch.setattr(application, step, broken)
    response = _ship(client, _body([(good, "6")], addressee_version=1, addressee={"contact": "Петров"},
        documents={"upd_number": "9"},
        upd_file={"file_name": "upd.pdf", "content_type": "application/pdf", "content_base64": PDF}, ship=True))

    assert response.status_code == 500
    assert _snapshot(sessions) == before
    monkeypatch.undo()
    retried = _ship(client, _body([(good, "6")], addressee_version=1, addressee={"contact": "Петров"},
        documents={"upd_number": "9"},
        upd_file={"file_name": "upd.pdf", "content_type": "application/pdf", "content_base64": PDF}, ship=True))
    assert retried.status_code == 200
    assert retried.json()["shipment"]["status"] == "Закрыта"


def test_missing_key_or_lines_are_rejected(env):
    client, _ = env
    good = _stock(client)
    no_key = client.post("/api/shipping/project-ship", json=_body([(good, "1")]))
    no_lines = _ship(client, {"project": PROJECT, "addressee_version": 0, "lines": []})
    other_project = client.post("/api/shipping/goods", json={
        "project": "600К.1", "item_index": "КБ7", "customer": "", "name": "Жгут", "ordered": "1", "unit": "шт",
    }).json()["good"]
    foreign = _ship(client, _body([(other_project, "1")]), key="f")
    assert (no_key.status_code, no_lines.status_code, foreign.status_code) == (422, 422, 422)
