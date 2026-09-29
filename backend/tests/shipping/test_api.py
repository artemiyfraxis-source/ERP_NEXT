from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor


@pytest.fixture()
def shipping_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="admin@example.test", roles=frozenset({"admin"}), permissions={"shipping": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def test_finished_good_partial_shipment_and_close_gates(shipping_api):
    client = shipping_api
    created = client.post("/api/shipping/goods", json={
        "project": "591К.3", "item_index": "КБ1", "customer": "Заказчик",
        "name": "Жгут", "ordered": "40", "unit": "шт",
    })
    good = created.json()["good"]
    accepted = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "6"})
    good = accepted.json()["good"]
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "terms": "ДОСТАВКА ТК",
        "upd_type": "ЭДО", "lines": [{"good_id": good["id"], "quantity": "10"}],
    })
    shipment = made.json()["shipment"]
    assert shipment["status"] == "Упакована"
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    shipment = shipped.json()["shipment"]
    rejected = client.post(f"/api/shipping/shipments/{shipment['id']}/close", json={"version": shipment["version"]})
    documented = client.put(f"/api/shipping/shipments/{shipment['id']}/documents", json={
        "version": shipment["version"], "upd_type": "ЭДО", "upd_number": "123",
        "upd_url": "https://files.example/upd.pdf", "track_number": "TRACK-1",
    })
    shipment = documented.json()["shipment"]
    closed = client.post(f"/api/shipping/shipments/{shipment['id']}/close", json={"version": shipment["version"]})

    assert created.status_code == 200
    assert good["grn"] == "ГП.26.0000001"
    assert shipment["lines"][0]["requested"] == "10"
    assert shipment["lines"][0]["picked"] == "6"
    assert shipment["lines"][0]["shortage"] == "4"
    assert rejected.status_code == 422
    assert closed.status_code == 200
    assert closed.json()["shipment"]["status"] == "Закрыта"


def test_manager_cannot_execute_warehouse_step(shipping_api):
    client = shipping_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Менеджер", department="Продажи"),
        email="pm@example.test", roles=frozenset({"pm"}), permissions={"shipping": "edit"},
    )
    response = client.post("/api/shipping/shipments/10000000-0000-0000-0000-000000000099/pick", json={"version": 1})
    assert response.status_code == 403


def _stocked_good(client, item_index: str, quantity: str) -> dict:
    good = client.post("/api/shipping/goods", json={
        "project": "591К.3", "item_index": item_index, "customer": "Заказчик",
        "name": "Жгут " + item_index, "ordered": "40", "unit": "шт",
    }).json()["good"]
    return client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": quantity}).json()["good"]


def _picked_shipment(client, lines: list[tuple[dict, str]]) -> dict:
    # SH-1: creation picks at once; no_docs stops at «Собрана», the state these tests start from.
    shipment = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО",
        "no_docs": True, "lines": [{"good_id": good["id"], "quantity": quantity} for good, quantity in lines],
    }).json()["shipment"]
    assert shipment["status"] == "Собрана"
    return shipment


def _goods(client) -> dict[str, dict]:
    return {good["id"]: good for good in client.get("/api/shipping/goods").json()["goods"]}


def test_unpick_returns_picked_quantity_to_finished_goods_register(shipping_api):
    client = shipping_api
    first, second = _stocked_good(client, "КБ1", "6"), _stocked_good(client, "КБ2", "3")
    shipment = _picked_shipment(client, [(first, "10"), (second, "3")])
    assert _goods(client)[first["id"]]["shipped"] == "6"

    line = next(line for line in shipment["lines"] if line["good_id"] == first["id"])
    unpicked = client.post(f"/api/shipping/shipments/{shipment['id']}/unpick", json={"version": shipment["version"], "line_id": line["id"]})
    goods = _goods(client)

    assert unpicked.status_code == 200
    assert unpicked.json()["shipment"]["status"] == "Собрана"
    assert goods[first["id"]]["shipped"] == "0"
    assert goods[first["id"]]["remaining"] == "6"
    assert goods[second["id"]]["shipped"] == "3"

    stale = client.post(f"/api/shipping/shipments/{shipment['id']}/unpick", json={"version": shipment["version"], "line_id": line["id"]})
    assert stale.status_code == 409
    assert _goods(client)[first["id"]]["shipped"] == "0"


def test_cancel_returns_every_picked_line_and_repeated_cancel_changes_nothing(shipping_api):
    client = shipping_api
    first, second = _stocked_good(client, "КБ1", "6"), _stocked_good(client, "КБ2", "3")
    shipment = _picked_shipment(client, [(first, "4"), (second, "3")])
    packed = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]}).json()["shipment"]

    cancelled = client.post(f"/api/shipping/shipments/{shipment['id']}/cancel", json={"version": packed["version"]})
    goods = _goods(client)

    assert cancelled.status_code == 200
    assert cancelled.json()["shipment"]["status"] == "Отменена"
    assert all(line["picked"] == "0" for line in cancelled.json()["shipment"]["lines"])
    assert (goods[first["id"]]["shipped"], goods[first["id"]]["remaining"]) == ("0", "6")
    assert (goods[second["id"]]["shipped"], goods[second["id"]]["remaining"]) == ("0", "3")

    again = client.post(f"/api/shipping/shipments/{shipment['id']}/cancel", json={"version": cancelled.json()["shipment"]["version"]})
    assert again.status_code == 422
    assert _goods(client)[first["id"]]["shipped"] == "0"


def test_shipped_goods_stay_shipped_when_cancel_is_refused(shipping_api):
    client = shipping_api
    good = _stocked_good(client, "КБ1", "5")
    shipment = _picked_shipment(client, [(good, "5")])
    packed = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]}).json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": packed["version"]}).json()["shipment"]

    refused = client.post(f"/api/shipping/shipments/{shipment['id']}/cancel", json={"version": shipped["version"]})
    unpick = client.post(f"/api/shipping/shipments/{shipment['id']}/unpick", json={"version": shipped["version"], "line_id": shipped["lines"][0]["id"]})

    assert refused.status_code == 422
    assert unpick.status_code == 422
    assert _goods(client)[good["id"]]["shipped"] == "5"
    assert _goods(client)[good["id"]]["sent"] == "5"


def test_packed_goods_are_not_sent_until_the_shipment_leaves(shipping_api):
    client = shipping_api
    good = _stocked_good(client, "КБ1", "5")
    shipment = _picked_shipment(client, [(good, "3")])
    packed = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]}).json()["shipment"]
    before = _goods(client)[good["id"]]
    client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": packed["version"]})
    after = _goods(client)[good["id"]]

    assert (before["shipped"], before["sent"]) == ("3", "0")
    assert (after["shipped"], after["sent"], after["remaining"]) == ("3", "3", "2")


def _good_with_stock(client, *, project="591К.3", item_index="КБ1", accepted="6"):
    good = client.post("/api/shipping/goods", json={
        "project": project, "item_index": item_index, "name": "Жгут", "ordered": "40", "unit": "шт",
    }).json()["good"]
    if accepted:
        good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": accepted}).json()["good"]
    return good


def _create(client, lines, **extra):
    return client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "terms": "САМОВЫВОЗ",
        "upd_type": "ЭДО", "lines": lines, **extra,
    })


def test_creating_shipment_picks_available_stock_and_packs_documents_at_once(shipping_api):
    client = shipping_api
    good = _good_with_stock(client)

    made = _create(client, [{"good_id": good["id"], "quantity": "4"}])
    shipment = made.json()["shipment"]
    stock = client.get("/api/shipping/goods").json()["goods"][0]

    assert made.status_code == 200
    assert shipment["status"] == "Упакована"
    assert shipment["lines"][0]["picked"] == "4"
    assert shipment["lines"][0]["grn"] == "ГП.26.0000001"
    assert shipment["pl_number"] == "PL591К.3-1"
    assert shipment["dn_number"] == "DN591К.3-1"
    assert shipment["packed_at"] == "2026-09-22"
    assert stock["shipped"] == "4"
    assert stock["remaining"] == "2"


def test_no_docs_shipment_stops_assembled_and_warehouse_packs_later(shipping_api):
    client = shipping_api
    good = _good_with_stock(client)

    shipment = _create(client, [{"good_id": good["id"], "quantity": "10"}], no_docs=True).json()["shipment"]
    packed = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]})

    assert shipment["status"] == "Собрана"
    assert shipment["pl_number"] == ""
    assert shipment["lines"][0]["picked"] == "6"
    assert shipment["lines"][0]["shortage"] == "4"
    assert packed.status_code == 200
    assert packed.json()["shipment"]["status"] == "Упакована"


def test_shipment_without_finished_stock_stays_new_and_is_picked_after_intake(shipping_api):
    client = shipping_api
    good = _good_with_stock(client, accepted="")

    shipment = _create(client, [{"good_id": good["id"], "quantity": "3"}]).json()["shipment"]
    assert shipment["status"] == "Новая"
    assert shipment["lines"][0]["picked"] == "0"
    assert shipment["pl_number"] == ""

    good = client.get("/api/shipping/goods").json()["goods"][0]
    client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "5"})
    picked = client.post(f"/api/shipping/shipments/{shipment['id']}/pick", json={"version": shipment["version"]})

    assert picked.status_code == 200
    assert picked.json()["shipment"]["status"] == "Собрана"
    assert picked.json()["shipment"]["lines"][0]["picked"] == "3"


def test_auto_pick_on_creation_is_one_transaction_with_audit(shipping_api):
    client = shipping_api
    first = _good_with_stock(client, item_index="КБ1")
    second = _good_with_stock(client, project="592К.1", item_index="КБ2", accepted="")

    shipment = _create(client, [
        {"good_id": first["id"], "quantity": "2"}, {"good_id": second["id"], "quantity": "1"},
    ]).json()["shipment"]
    uow = app.state.shipping_uow_factory()
    with uow:
        events = [row.event_type for row in uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == UUID(shipment["id"])))]

    assert shipment["status"] == "Упакована"
    assert shipment["pl_number"] == "PL591К.3-1"
    assert [line["picked"] for line in shipment["lines"]] == ["2", "0"]
    assert sorted(events) == ["shipping.created", "shipping.packed", "shipping.picked"]


def test_project_queue_is_idempotent_and_drop_keeps_goods_with_movement(shipping_api):
    client = shipping_api
    card = {"project": "735К.1", "customer": "Заказчик", "assemblies": [
        {"item_index": "КБ1", "name": "Жгут", "run": "10", "article": "A-1"},
        {"item_index": "КБ2", "name": "", "run": "4", "article": "A-2"},
        {"item_index": "КБ3", "name": "Кабель", "run": "2"},
        {"item_index": "КБ4", "name": "Без тиража", "run": "0"},
    ]}
    first = client.post("/api/shipping/project-queue", json=card)
    assert first.status_code == 200, first.text
    assert first.json() == {"added": 3, "updated": 0, "skipped": ["КБ4"]}
    assert client.post("/api/shipping/project-queue", json=card).json() == {"added": 0, "updated": 0, "skipped": ["КБ4"]}

    goods = {item["item_index"]: item for item in client.get("/api/shipping/goods").json()["goods"]}
    assert set(goods) == {"КБ1", "КБ2", "КБ3"}
    assert goods["КБ1"]["ordered"] == "10" and goods["КБ1"]["customer"] == "Заказчик" and goods["КБ1"]["accepted"] == "0"
    assert goods["КБ2"]["name"] == "A-2"

    card["assemblies"][0]["run"] = "12"
    assert client.post("/api/shipping/project-queue", json=card).json()["updated"] == 1
    good = client.get("/api/shipping/goods").json()["goods"][0]
    assert good["ordered"] == "12"

    kb2 = goods["КБ2"]
    accepted = client.post(f"/api/shipping/goods/{kb2['id']}/accept", json={"version": kb2["version"], "quantity": "1"})
    assert accepted.status_code == 200, accepted.text
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "terms": "САМОВЫВОЗ",
        "upd_type": "ЭДО", "lines": [{"good_id": goods["КБ3"]["id"], "quantity": "1"}],
    })
    assert made.status_code == 200, made.text

    dropped = client.post("/api/shipping/project-queue/drop", json={"project": "735К.1"})
    assert dropped.json() == {"deleted": 1, "kept": 2}
    assert {item["item_index"] for item in client.get("/api/shipping/goods").json()["goods"]} == {"КБ2", "КБ3"}


def _queued_good(client, project="591К.3", item_index="КБ1"):
    good = client.post("/api/shipping/goods", json={
        "project": project, "item_index": item_index, "customer": "Заказчик",
        "name": "Жгут", "ordered": "40", "unit": "шт",
    }).json()["good"]
    return good


def test_shipment_inherits_project_card_and_remembers_typed_contact(shipping_api):
    client = shipping_api
    good = _queued_good(client)
    card = client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "receiver": "АО Получатель", "shipping_point": "Москва, склад 1",
        "contact": "Иванов", "phone": "+7 900 000-00-00", "terms": "ДОСТАВКА ТК", "carrier": "ПЭК",
        "upd_type": "БУМАГА",
    })
    made = client.post("/api/shipping/shipments", json={
        "contact": "Петров", "upd_type": "ЭДО", "lines": [{"good_id": good["id"], "quantity": "5"}],
    })
    stored = client.get("/api/shipping/project-addressee", params={"project": "591К.3"}).json()["addressee"]

    assert card.status_code == 200
    assert card.json()["addressee"]["version"] == 1
    assert made.status_code == 200
    shipment = made.json()["shipment"]
    assert shipment["receiver"] == "АО Получатель"
    assert shipment["shipping_point"] == "Москва, склад 1"
    assert shipment["contact"] == "Петров"
    assert shipment["phone"] == "+7 900 000-00-00"
    assert shipment["terms"] == "ДОСТАВКА ТК"
    assert shipment["carrier"] == "ПЭК"
    assert shipment["upd_type"] == "ЭДО"
    # The typed contact is remembered; nothing the manager left empty is erased.
    assert stored["contact"] == "Петров"
    assert stored["shipping_point"] == "Москва, склад 1"
    assert stored["upd_type"] == "БУМАГА"
    assert stored["version"] == 2


def test_first_shipment_without_card_requires_point_only_then_seeds_card(shipping_api):
    client = shipping_api
    good = _queued_good(client)
    lines = [{"good_id": good["id"], "quantity": "1"}]
    no_point = client.post("/api/shipping/shipments", json={"terms": "САМОВЫВОЗ", "upd_type": "ЭДО", "lines": lines})
    no_terms = client.post("/api/shipping/shipments", json={"shipping_point": "Москва", "upd_type": "ЭДО", "lines": lines})
    made = client.post("/api/shipping/shipments", json={
        "shipping_point": "Москва", "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО", "lines": lines,
    })
    card = client.get("/api/shipping/project-addressee", params={"project": "591К.3"}).json()["addressee"]

    assert no_point.status_code == 422
    assert "точку отгрузки" in no_point.json()["detail"]
    # shipRequestCreate_: условия доставки и тип УПД могут остаться пустыми.
    assert no_terms.status_code == 200
    assert (no_terms.json()["shipment"]["terms"], no_terms.json()["shipment"]["upd_type"]) == (None, "ЭДО")
    assert made.status_code == 200
    assert made.json()["shipment"]["receiver"] == "Заказчик"
    assert (card["shipping_point"], card["phone"], card["contact"], card["version"]) == ("Москва", "+7 900", "", 2)
    assert card["terms"] is None


def test_card_edit_erases_sent_empty_field_keeps_unsent_and_checks_version(shipping_api):
    client = shipping_api
    first = client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "shipping_point": "Москва", "contact": "Иванов", "terms": "ДОСТАВКА ТК",
    }).json()["addressee"]
    erased = client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "version": first["version"], "contact": "", "terms": None,
    })
    stale = client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "version": first["version"], "contact": "Сидоров",
    })
    unchanged = client.put("/api/shipping/project-addressee", json={"project": "591К.3", "shipping_point": "Москва"})

    assert erased.status_code == 200
    body = erased.json()["addressee"]
    assert (body["shipping_point"], body["contact"], body["terms"], body["version"]) == ("Москва", "", None, 2)
    assert stale.status_code == 409
    assert unchanged.json()["addressee"]["version"] == 2


def test_other_project_card_is_not_used_and_bad_terms_are_rejected(shipping_api):
    client = shipping_api
    good = _queued_good(client, project="600")
    client.put("/api/shipping/project-addressee", json={"project": "591К.3", "shipping_point": "Москва", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО"})
    made = client.post("/api/shipping/shipments", json={"lines": [{"good_id": good["id"], "quantity": "1"}]})
    bad = client.put("/api/shipping/project-addressee", json={"project": "600", "terms": "ПОЧТОЙ"})

    assert made.status_code == 422
    assert bad.status_code == 422
