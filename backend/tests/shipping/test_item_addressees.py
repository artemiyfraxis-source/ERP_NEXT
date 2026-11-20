"""Destination resolution, atomic KB edits and compatibility with legacy shipments."""

from datetime import datetime
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.shipping import application
from app.modules.shipping.domain import ADDRESSEE_CARD_FIELDS
from app.modules.shipping.infrastructure import (
    FinishedGoodMoveRow, ItemAddresseeRow, PackPermissionRow, ShipmentRow, ShippingSequenceRow,
)
from app.modules.techcards.kitset_infrastructure import KitsetRow
from tests.shipping.test_project_ship import (
    env, PROJECT, STOREKEEPER, _as, _stock, _card, _body, _ship, _snapshot,
)


def save(client, index="КБ1", version=0, fields=None, inherit=()):
    return client.put("/api/shipping/item-addressee", json={
        "project": PROJECT, "item_index": index, "version": version, "fields": fields or {}, "inherit": list(inherit),
    })


def view(client):
    response = client.get("/api/shipping/item-addressees", params={"project": PROJECT})
    assert response.status_code == 200, response.text
    return {item["item_index"]: item for item in response.json()["items"]}


def test_catalog_before_goods_and_clear_is_different_from_inherit(env):
    client, sessions = env
    client._transport.raise_server_exceptions = True
    with sessions() as session:
        session.add(KitsetRow(project=PROJECT, project_key=PROJECT, version=1, customer="Заказчик", calc_source="",
                              assemblies=[{"index": "КБ1", "name": "Жгут"}], created_at=datetime.now(),
                              updated_at=datetime.now(), updated_by="Тест"))
        session.commit()
    _card(client, shipping_point="Адрес проекта", phone="123")
    initial = view(client)["КБ1"]
    assert initial["version"] == 0 and initial["effective"]["receiver"] == "Заказчик"
    assert initial["provenance"]["receiver"] == "customer"
    _as(STOREKEEPER)
    response = save(client, fields={"phone": "", "shipping_point": "Адрес КБ"})
    assert response.status_code == 200, response.text
    item = view(client)["КБ1"]
    assert item["effective"]["phone"] == "" and item["provenance"]["phone"] == "item"
    assert item["overrides"] == {"phone": "", "shipping_point": "Адрес КБ"}
    assert save(client, version=1, inherit=ADDRESSEE_CARD_FIELDS).status_code == 200
    item = view(client)["КБ1"]
    assert item["version"] == 2 and item["overrides"] == {}
    assert item["effective"]["phone"] == "123" and item["provenance"]["phone"] == "project"
    assert save(client, version=1, fields={"phone": "stale"}).status_code == 409


@pytest.mark.parametrize("bad", [{"item_index": "КБ2", "version": 8}, {"item_index": "КБ999", "version": 0}])
def test_bulk_rejects_whole_batch_and_does_not_write_audit(env, bad):
    client, sessions = env
    _stock(client); _stock(client, "КБ2")
    before = _snapshot(sessions)
    result = client.put("/api/shipping/item-addressees/bulk", json={"project": PROJECT, "items": [
        {"item_index": "КБ1", "version": 0, "fields": {"phone": "123"}}, {**bad, "fields": {"phone": "456"}},
    ]})
    assert result.status_code in {409, 422}, result.text
    assert _snapshot(sessions) == before
    assert all(item["version"] == 0 for item in view(client).values())


@pytest.mark.parametrize("endpoint", ["shipments", "project-pack", "project-ship"])
def test_mixed_destinations_rejected_before_stock_or_permissions(env, endpoint):
    client, sessions = env
    first, second = _stock(client), _stock(client, "КБ2")
    assert save(client, fields={"shipping_point": "Другой адрес"}).status_code == 200
    before = _snapshot(sessions)
    lines = [{"good_id": good["id"], "version": good["version"], "quantity": "1"} for good in (first, second)]
    body = {"project": PROJECT, "lines": lines, "addressee_version": 0, "ship": False}
    if endpoint == "project-pack":
        from tests.shipping.test_api import decide_pack
        decide_pack(client, PROJECT)
        before = _snapshot(sessions)
    result = client.post(f"/api/shipping/{endpoint}", json=body, headers={"Idempotency-Key": "mixed"})
    assert result.status_code == 422, result.text
    assert "КБ1" in result.text and "КБ2" in result.text and "доставки" in result.text
    assert _snapshot(sessions) == before


def test_snapshot_freezes_and_never_remembers_own_address_in_project(env):
    client, _ = env
    first, second = _stock(client), _stock(client, "КБ2")
    card = _card(client, shipping_point="Проект", receiver="Получатель")
    save(client, fields={"shipping_point": "КБ"})
    response = client.post("/api/shipping/shipments", json={"shipping_point": "Отдельная партия",
        "lines": [{"good_id": good["id"], "quantity": "1"} for good in (first, second)]})
    assert response.status_code == 200, response.text
    shipment = response.json()["shipment"]
    assert shipment["delivery_snapshot_at"] and not shipment["inherited"]
    project = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    assert project["shipping_point"] == "Проект" and project["version"] == card["version"]
    client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": card["version"], "phone": "Новый"})
    stored = client.get("/api/shipping/shipments").json()["shipments"][0]
    assert stored["phone"] == card["phone"] and stored["version"] == shipment["version"]
    refresh = client.put(f"/api/shipping/shipments/{shipment['id']}/head", json={"version": shipment["version"], "refresh_delivery": True})
    assert refresh.status_code == 422
    save(client, version=1, inherit=ADDRESSEE_CARD_FIELDS)
    refresh = client.put(f"/api/shipping/shipments/{shipment['id']}/head", json={"version": shipment["version"], "refresh_delivery": True})
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["shipment"]["shipping_point"] == "Проект"


@pytest.mark.parametrize("action", ["pick", "pack", "boxes", "ship"])
def test_legacy_follows_project_but_rejects_later_mixed_kb_mutations(env, action, monkeypatch):
    client, sessions = env
    first, second = _stock(client), _stock(client, "КБ2")
    card = _card(client, receiver="Получатель", shipping_point="Проект", upd_type="ЭДО", upd_number="УПД-1")
    response = client.post("/api/shipping/shipments", json={"no_docs": True,
        "lines": [{"good_id": good["id"], "quantity": "10"} for good in (first, second)]})
    shipment = response.json()["shipment"]
    with sessions() as session:
        row = session.get(ShipmentRow, UUID(shipment["id"]))
        row.delivery_snapshot_at = None
        row.inherited_fields = ",".join(ADDRESSEE_CARD_FIELDS)
        session.commit()
    client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": card["version"], "phone": "Изменён"})
    current = client.get("/api/shipping/shipments").json()["shipments"][0]
    assert current["phone"] == "Изменён" and current["delivery_snapshot_at"] is None
    if action == "ship":
        response = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": current["version"]})
        assert response.status_code == 200, response.text
        current = response.json()["shipment"]
    assert save(client, fields={"shipping_point": "Разные маршруты"}).status_code == 200
    before = _snapshot(sessions)

    def packing_state():
        with sessions() as session:
            return {
                row_type.__tablename__: list(session.execute(select(row_type.__table__)).tuples())
                for row_type in (PackPermissionRow, FinishedGoodMoveRow, ShippingSequenceRow)
            }

    validations = []
    resolve = application._resolve_destinations

    def validate_locked(lines, uow, *args, **kwargs):
        assert PROJECT in uow._locked_projects
        assert not uow.session.new and not uow.session.dirty
        validations.append(True)
        return resolve(lines, uow, *args, **kwargs)

    monkeypatch.setattr(application, "_resolve_destinations", validate_locked)
    before_packing = packing_state()
    body = {"version": current["version"]}
    if action == "boxes":
        body["boxes"] = [{"kind": "Короб", "items": [
            {"item_index": line["item_index"], "quantity": line["picked"]} for line in current["lines"]
        ]}]
    result = client.request("PUT" if action == "boxes" else "POST",
                            f"/api/shipping/shipments/{shipment['id']}/{action}", json=body)
    assert result.status_code == 422, result.text
    assert "КБ1" in result.text and "КБ2" in result.text and "доставки" in result.text
    assert validations == [True]
    assert _snapshot(sessions) == before
    assert packing_state() == before_packing
    assert client.get("/api/shipping/shipments").json()["shipments"][0] == current


def test_snapshot_dn_does_not_restore_cleared_contact(env):
    from app.modules.shipping.documents import build_delivery_note
    from app.modules.shipping.domain import ShipmentStatus, ProjectAddressee
    client, _ = env
    good = _stock(client)
    save(client, fields={"phone": "", "contact": "", "shipping_point": "", "carrier": ""})
    shipment = _ship(client, _body([(good, "1")], ship=False)).json()["shipment"]
    with app.state.shipping_uow_factory() as uow:
        stored = uow.shipments.get(UUID(shipment["id"]))
    frozen = replace(stored, status=ShipmentStatus.SHIPPED)
    note = build_delivery_note(frozen, "Заказчик", card=ProjectAddressee(PROJECT, phone="LIVE", contact="LIVE", shipping_point="LIVE", carrier="LIVE"))
    assert note.phone == "" and note.contact == "" and "LIVE" not in note.shipping_address and note.carrier == ""


def test_alias_uses_one_row_and_rejects_duplicate_indices(env):
    client, sessions = env
    _stock(client)
    body = {"project": "591k.3", "item_index": "КБ1", "version": 0, "fields": {"phone": "123"}}
    assert client.put("/api/shipping/item-addressee", json=body).status_code == 200
    assert save(client, fields={"phone": "456"}).status_code == 409
    with sessions() as session:
        assert len(list(session.scalars(select(ItemAddresseeRow)))) == 1
    line = {"item_index": "КБ1", "version": 1, "fields": {"phone": "456"}}
    response = client.put("/api/shipping/item-addressees/bulk", json={"project": PROJECT, "items": [line, line]})
    assert response.status_code == 422


def test_bulk_success_preserves_other_fields_and_cleared_receiver_is_not_fallback(env):
    client, _ = env
    first, second = _stock(client), _stock(client, "КБ2")
    _card(client, receiver="Проект", phone="123", shipping_point="Москва")
    response = client.put("/api/shipping/item-addressees/bulk", json={"project": PROJECT, "items": [
        {"item_index": index, "version": 0, "fields": {"phone": "456"}} for index in ("КБ1", "КБ2")
    ]})
    assert response.status_code == 200, response.text
    assert all(item["version"] == 1 and item["overrides"] == {"phone": "456"} for item in view(client).values())
    response = client.post("/api/shipping/shipments", json={"shipping_point": "   ",
        "lines": [{"good_id": good["id"], "quantity": "1"} for good in (first, second)]})
    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["shipping_point"] == "Москва"
    assert response.json()["shipment"]["phone"] == "456"
    assert save(client, version=1, fields={"receiver": ""}).status_code == 200
    assert view(client)["КБ1"]["effective"]["receiver"] == ""
    response = client.post("/api/shipping/shipments", json={"lines": [{"good_id": first["id"], "quantity": "1"}]})
    assert response.status_code == 422


def test_viewer_can_read_but_cannot_write_kb_destinations(env):
    client, _ = env
    _stock(client)
    _as(replace(STOREKEEPER, permissions={"shipping_goods": "view"}))
    assert view(client)["КБ1"]["version"] == 0
    assert save(client, fields={"phone": "123"}).status_code == 403


def test_different_project_document_defaults_are_not_silently_merged(env):
    client, sessions = env
    first = _stock(client)
    second = client.post("/api/shipping/goods", json={"project": "600К.1", "item_index": "КБ9", "name": "Жгут", "ordered": "10", "customer": "Заказчик"}).json()["good"]
    for project, number in ((PROJECT, "УПД1"), ("600К.1", "УПД2")):
        client.put("/api/shipping/project-addressee", json={"project": project, "upd_type": "ЭДО", "upd_number": number})
    before = _snapshot(sessions)
    response = client.post("/api/shipping/shipments", json={"lines": [{"good_id": good["id"], "quantity": "1"} for good in (first, second)]})
    assert response.status_code == 422 and "УПД" in response.text and "КБ9" in response.text
    assert _snapshot(sessions) == before


@pytest.mark.parametrize("endpoint", ["project-pack", "project-ship"])
def test_project_number_fallback_never_overrides_kb_receiver(env, endpoint):
    from tests.shipping.test_api import decide_pack
    client, _ = env
    good = client.post("/api/shipping/goods", json={"project": PROJECT, "item_index": "КБ1", "name": "Жгут", "ordered": "1"}).json()["good"]
    good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"confirm_beyond_tasks": True, "version": good["version"], "quantity": "1"}).json()["good"]
    assert save(client, fields={"receiver": "Получатель КБ"}).status_code == 200
    decide_pack(client, PROJECT)
    body = _body([(good, "1")], ship=False)
    response = client.post(f"/api/shipping/{endpoint}", json=body, headers={"Idempotency-Key": endpoint})
    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["receiver"] == "Получатель КБ"
