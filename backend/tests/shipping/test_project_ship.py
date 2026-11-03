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
from tests.shipping.test_api import decide_pack

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


def _decide(client) -> dict:
    return decide_pack(client, PROJECT)


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


@pytest.mark.parametrize("endpoint", ["project-ship", "project-pack"])
@pytest.mark.parametrize("stored_project,requested_project", [("780К", "780К.1"), ("780К.1", "780K")])
def test_packing_accepts_canonical_project_alias_only_with_explicit_cap(env, endpoint, stored_project, requested_project):
    client, sessions = env
    good = client.post("/api/shipping/goods", json={
        "project": stored_project, "item_index": "КБ1", "customer": "Заказчик", "name": "Жгут",
        "ordered": "15", "unit": "шт",
    }).json()["good"]
    good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "15"}).json()["good"]
    body = {"project": requested_project, "lines": [{"good_id": good["id"], "quantity": "15"}]}
    if endpoint == "project-ship":
        body.update(addressee_version=0, ship=False)
        body["lines"][0]["version"] = good["version"]

    def pack():
        return client.post(f"/api/shipping/{endpoint}", json=body, headers={"Idempotency-Key": "alias-pack"})

    _as(STOREKEEPER)
    before = _snapshot(sessions)
    assert pack().status_code == 422  # Canonical equality never grants an implicit allowance.
    assert _snapshot(sessions) == before
    _as(MANAGER)
    granted = client.post("/api/shipping/project-pack-permissions", json={
        "project": requested_project, "lines": [{"item_index": "КБ1", "quantity": "15", "version": 0}],
    })
    assert granted.status_code == 200, granted.text
    assert granted.json()["permissions"][0]["remaining"] == "15"
    _as(STOREKEEPER)
    result = pack()
    assert result.status_code == 200, result.text
    assert result.json()["shipment"]["lines"][0]["project"] == stored_project
    permission = client.get("/api/shipping/project-pack-permissions", params={"project": requested_project}).json()["permissions"][0]
    assert (permission["quantity"], permission["consumed"], permission["remaining"]) == ("15", "15", "0")


@pytest.mark.parametrize("endpoint", ["project-ship", "project-pack"])
def test_packing_does_not_match_a_neighbor_project(env, endpoint):
    client, sessions = env
    good = _stock(client)
    _decide(client)
    body = {"project": "591К.30", "lines": [{"good_id": good["id"], "quantity": "1"}]}
    if endpoint == "project-ship":
        body.update(addressee_version=0, ship=False)
        body["lines"][0]["version"] = good["version"]
    before = _snapshot(sessions)
    result = client.post(f"/api/shipping/{endpoint}", json=body, headers={"Idempotency-Key": "wrong-project"})
    assert result.status_code == 422
    assert _snapshot(sessions) == before


def test_manager_alias_uses_one_card_for_cas_copy_and_later_follow(env):
    client, sessions = env
    good = _stock(client)
    _decide(client)
    alias = "591K.3"
    card = client.put("/api/shipping/project-addressee", json={
        "project": alias, "version": 0, "shipping_point": "Верный адрес", "phone": "+7 900",
        "terms": "САМОВЫВОЗ",
    }).json()["addressee"]
    loaded = client.get("/api/shipping/project-addressee", params={"project": PROJECT})
    assert loaded.status_code == 200 and loaded.json()["addressee"]["version"] == card["version"]
    bulk = client.get("/api/shipping/project-addressees", params={"project": PROJECT})
    assert bulk.status_code == 200 and bulk.json()["addressees"][0]["version"] == card["version"]
    assert bulk.json()["addressees"][0]["project"] == PROJECT
    _as(MANAGER)
    before = _snapshot(sessions)
    stale = _ship(client, {**_body([(good, "2")]), "project": alias,
        "addressee": {"contact": "Не сохранять"}}, key="stale-alias")
    assert stale.status_code == 409
    assert _snapshot(sessions) == before
    response = _ship(client, {**_body([(good, "2")], addressee_version=card["version"]),
        "project": alias, "addressee": {"contact": "Иванов"}}, key="manager-alias")
    assert response.status_code == 200, response.text
    shipment = response.json()["shipment"]
    assert shipment["lines"][0]["project"] == PROJECT
    assert (shipment["shipping_point"], shipment["phone"], shipment["contact"]) == ("Верный адрес", "+7 900", "Иванов")
    updated = client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "version": card["version"] + 1, "shipping_point": "Новый адрес",
    })
    assert updated.status_code == 200, updated.text
    with sessions() as session:
        cards = list(session.scalars(select(ProjectAddresseeRow)))
        assert len(cards) == 1 and cards[0].project == alias
        stored = session.get(ShipmentRow, UUID(shipment["id"]))
        assert stored.shipping_point == "Новый адрес"


def test_card_alias_creation_locks_before_read_and_rejects_stale_creation(env, monkeypatch):
    client, sessions = env
    events = []
    lock = SqlAlchemyShippingUnitOfWork.lock_projects
    edit = application._set_project_addressee

    def locked(uow, projects):
        events.append("lock")
        return lock(uow, projects)

    def edited(*args, **kwargs):
        assert events[-1] == "lock"
        events.append("read")
        return edit(*args, **kwargs)

    monkeypatch.setattr(SqlAlchemyShippingUnitOfWork, "lock_projects", locked)
    monkeypatch.setattr(application, "_set_project_addressee", edited)
    first = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": 0, "phone": "+7"})
    assert first.status_code == 200
    before = _snapshot(sessions)
    second = client.put("/api/shipping/project-addressee", json={"project": "591K.3", "version": 0, "phone": "Другой"})
    assert second.status_code == 409
    assert events == ["lock", "read", "lock", "read"]
    assert _snapshot(sessions) == before


@pytest.mark.parametrize("endpoint", ["project-ship", "project-pack"])
def test_alias_with_conflicting_address_cards_refuses_without_writes(env, endpoint):
    client, sessions = env
    good = _stock(client)
    _decide(client)
    _card(client, shipping_point="Первый адрес")
    alias = "591K.3"
    with sessions() as session:
        original = session.get(ProjectAddresseeRow, PROJECT)
        values = {column.name: getattr(original, column.name) for column in ProjectAddresseeRow.__table__.columns}
        values.update(project=alias, shipping_point="Другой адрес")
        session.add(ProjectAddresseeRow(**values))
        session.commit()
    before = _snapshot(sessions)
    body = {**_body([(good, "2")], addressee_version=1), "project": alias}
    if endpoint == "project-ship":
        body["addressee"] = {"contact": "Не сохранять"}
    response = client.post(f"/api/shipping/{endpoint}", json=body, headers={"Idempotency-Key": "conflicting-card"})
    assert response.status_code == 422, response.text
    assert "несколько адресных карточек" in response.json()["detail"]
    assert client.get("/api/shipping/project-addressee", params={"project": alias}).status_code == 422
    assert client.get("/api/shipping/project-addressees", params={"project": alias}).status_code == 422
    assert _snapshot(sessions) == before


def test_manager_with_warehouse_ships_card_change_documents_and_file_in_one_request(env):
    client, sessions = env
    good = _stock(client)
    card = _card(client, shipping_point="Москва", terms="САМОВЫВОЗ", upd_type="БУМАГА")
    _decide(client)
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
    # Only the explicit card edit changes its version.
    assert (stored["version"], stored["contact"], stored["phone"]) == (card["version"] + 1, "Иванов", "+7 900")
    assert stored["pack_decided"] is False
    with sessions() as session:
        events = list(session.scalars(select(AuditEventRow.event_type).order_by(AuditEventRow.occurred_at)))
    assert {"shipping.addressee_set", "shipping.created", "shipping.picked", "shipping.packed", "shipping.upd_attached",
            "shipping.shipped", "shipping.closed"} <= set(events)


def test_shipping_without_upd_number_is_refused_and_says_why(env):
    client, _ = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="ЭДО")
    _decide(client)
    response = _ship(client, _body([(good, "4")], addressee_version=1, ship=True))
    # Без № УПД отправка не уезжает вовсе (владелец 28.10.2026), а не уезжает незакрытой.
    assert response.status_code == 422
    assert "№ УПД" in response.json()["detail"]


def test_pack_only_manager_gets_packed_storekeeper_gets_assembled(env):
    client, _ = env
    first, second = _stock(client, "КБ1"), _stock(client, "КБ2")
    _card(client, shipping_point="Москва")
    _decide(client)
    _as(MANAGER)
    by_manager = _ship(client, _body([(first, "2")], addressee_version=1), key="m").json()
    _as(STOREKEEPER)
    # Both KB caps were explicitly granted; shipment creation does not change the card.
    by_storekeeper = _ship(client, _body([(second, "3")], addressee_version=1), key="s").json()

    assert (by_manager["shipment"]["status"], by_manager["shipped"]) == ("Упакована", False)
    assert by_manager["shipment"]["pl_number"]
    assert by_storekeeper["shipment"]["status"] == "Собрана"
    assert by_storekeeper["shipment"]["lines"][0]["picked"] == "3"


def test_replay_with_same_key_and_body_returns_stored_result_and_writes_nothing(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="ЭДО")
    _decide(client)
    body = _body([(good, "5")], addressee_version=1, ship=True, documents={"upd_number": "5"})
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
    _decide(client)
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
    _decide(client)
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
    _decide(client)
    before = _snapshot(sessions)
    response = _ship(client, _body([(good, "7")], addressee_version=1))

    assert response.status_code == 409
    assert "только 6" in response.json()["detail"]
    assert _snapshot(sessions) == before


def test_role_matrix(env):
    client, sessions = env
    good = _stock(client)
    # № УПД в карточке проекта (0162): отправка склада берёт его сама.
    _card(client, shipping_point="Москва", upd_type="ЭДО", upd_number="5")
    _as(STOREKEEPER)
    undecided = _ship(client, _body([(good, "1")], addressee_version=1, ship=True), key="r0")
    _as(ADMIN)
    _decide(client)
    before = _snapshot(sessions)

    _as(STOREKEEPER)
    card_by_storekeeper = _ship(client, _body([(good, "1")], addressee_version=1, addressee={"contact": "Петров"}, ship=True), key="r1")
    docs_by_storekeeper = _ship(client, _body([(good, "1")], addressee_version=1, documents={"upd_number": "5"}), key="r2")
    _as(MANAGER)
    ship_by_manager = _ship(client, _body([(good, "1")], addressee_version=1, ship=True), key="r3")
    assert undecided.status_code == 422 and "разреш" in undecided.json()["detail"]
    assert (card_by_storekeeper.status_code, docs_by_storekeeper.status_code, ship_by_manager.status_code) == (403, 403, 403)
    assert _snapshot(sessions) == before

    _as(STOREKEEPER)
    shipped = _ship(client, _body([(good, "1")], addressee_version=1, ship=True), key="r4")
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["shipment"]["upd_number"] == "5"
    assert shipped.json()["shipment"]["status"] == "Отгружена"  # закроется, когда приложат файл УПД


def test_paper_upd_without_file_refuses_shipping_and_keeps_the_card_unchanged(env):
    client, sessions = env
    good = _stock(client)
    _card(client, shipping_point="Москва", upd_type="БУМАГА")
    _decide(client)
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
    _decide(client)
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
