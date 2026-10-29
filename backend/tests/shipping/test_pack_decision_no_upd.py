"""Шлюзы отгрузки (владелец 29.10.2026): менеджер решает «упаковывать» — только тогда склад упаковывает; трек вносится
у «Упакованной» отправки с УПД и ТК; «На отгрузку» — когда есть всё; № и дата УПД в карточке проекта; «Без УПД» —
только по разрешению с PIN (сама проверка PIN — tests/workflows/test_ship_no_upd.py)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.main import app
from app.modules.shipping.application import PACK_DECISION_LOCKED
from app.modules.shipping.domain import (
    PACK_NOT_DECIDED, DeliveryTerms, ProjectAddressee, ShipmentStatus, ShippingValidationError, UpdType,
)
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import OutboxMessageRow
from tests.factories import Actor
from tests.shipping.test_api import decide_pack, shipping_api  # noqa: F401 — fixture
from tests.shipping.test_domain import good, shipment

PROJECT = "591К.3"
DAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 22, 10, 0)
ADMIN = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP"),
    email="admin@example.test", roles=frozenset({"admin"}), permissions={"shipping": "edit"},
)
STOREKEEPER = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000005"), name="Алексей", department="Склад"),
    email="sklad@example.test", roles=frozenset({"storekeeper"}), permissions={"shipping_goods": "edit", "shipping": "view"},
)


def _as(principal: Principal) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: principal


# ── Домен: трек, «готово к треку», «на отгрузку», разрешение «без УПД» ─────────────────────────────────────────


def _assembled(upd_type=UpdType.EDO, terms=DeliveryTerms.CARRIER, **own):
    item = good().accept(Decimal("10"), DAY, "ГП.26.0000001")
    picked, _ = shipment(item, terms=terms, upd_type=upd_type, **own).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    return picked


def _packed(upd_type=UpdType.EDO, terms=DeliveryTerms.CARRIER, **own):
    return _assembled(upd_type, terms, **own).pack("PL591К.3-1", "DN591К.3-1", DAY)


def _documents(item, **changes):
    fields = dict(upd_type=item.upd_type, upd_number=item.upd_number, upd_date=item.upd_date, upd_url=item.upd_url,
                  track_number=item.track_number)
    fields.update(changes)
    return item.update_documents(**fields)


def test_a_track_needs_a_packed_shipment():
    with pytest.raises(ShippingValidationError, match="когда отправка упакована"):
        _documents(_assembled(), track_number="T-1")


def test_a_track_needs_the_upd_and_the_carrier_first():
    with pytest.raises(ShippingValidationError, match="не хватает — № УПД$"):
        _documents(_packed(upd_number=""), track_number="T-1")
    with pytest.raises(ShippingValidationError, match="не хватает — перевозчик$"):
        _documents(_packed(carrier=""), track_number="T-1")
    # № УПД в том же запросе, что и трек, — годится.
    tracked = _documents(_packed(upd_number=""), upd_number="77", track_number="T-1")
    assert (tracked.upd_number, tracked.track_number) == ("77", "T-1")


def test_a_stored_track_is_left_alone():
    """Трек, записанный до шлюза, не перепроверяется: правка других документов его не трогает."""
    legacy = replace(_assembled(upd_number=""), track_number="OLD-1")
    edited = _documents(legacy, upd_date=DAY)

    assert edited.track_number == "OLD-1"
    with pytest.raises(ShippingValidationError, match="упакована"):
        _documents(legacy, track_number="NEW-2")


@pytest.mark.parametrize(("upd_type", "terms", "own", "track_ready", "ship_ready"), [
    # ЭДО: нужен №; ТК — ещё перевозчик и адрес, и трек для отгрузки.
    (UpdType.EDO, DeliveryTerms.CARRIER, {}, True, False),
    (UpdType.EDO, DeliveryTerms.CARRIER, {"upd_number": ""}, False, False),
    (UpdType.EDO, DeliveryTerms.CARRIER, {"carrier": ""}, False, False),
    # Бумага: № и файл.
    (UpdType.PAPER, DeliveryTerms.PICKUP, {}, False, False),
    (UpdType.PAPER, DeliveryTerms.PICKUP, {"upd_url": "/api/shipping/files/x"}, True, True),
    # «Без УПД» без разрешения — не готово ни к треку, ни к отгрузке.
    (UpdType.NONE, DeliveryTerms.CARRIER, {}, False, False),
    (UpdType.NONE, DeliveryTerms.PICKUP, {}, False, False),
    # Самовывоз: ни ТК, ни трек не нужны.
    (UpdType.EDO, DeliveryTerms.PICKUP, {}, True, True),
])
def test_ready_flags(upd_type, terms, own, track_ready, ship_ready):
    url = own.pop("upd_url", "")
    packed = replace(_packed(upd_type, terms, **own), upd_url=url)

    assert (packed.ready_for_track, packed.ready_to_ship) == (track_ready, ship_ready)


def test_ready_flags_follow_the_track_and_the_permit():
    carrier = _packed()
    tracked = _documents(carrier, track_number="T-1")
    without = _packed(UpdType.NONE).approve_no_upd("Зингер", NOW)
    without_tracked = _documents(without, track_number="T-2")
    assembled_pickup = _assembled(terms=DeliveryTerms.PICKUP)

    assert (tracked.ready_for_track, tracked.ready_to_ship) == (True, True)
    assert (without.ready_for_track, without.ready_to_ship) == (True, False)
    assert without_tracked.ready_to_ship is True
    # Трек — только у «Упакованной»; самовывоз, набранный и не упакованный, уехать может.
    assert (assembled_pickup.ready_for_track, assembled_pickup.ready_to_ship) == (False, True)
    assert tracked.mark_shipped(DAY).ready_to_ship is False


def test_the_permit_is_for_one_shipment_and_a_rollback_clears_it():
    approved = _packed(UpdType.NONE, terms=DeliveryTerms.PICKUP).approve_no_upd("Касаточкин", NOW)
    with pytest.raises(ShippingValidationError, match="уже разрешил Касаточкин"):
        approved.approve_no_upd("Зингер", NOW)
    shipped = approved.mark_shipped(DAY)
    with pytest.raises(ShippingValidationError, match="до отгрузки"):
        shipped.approve_no_upd("Зингер", NOW)
    with pytest.raises(ShippingValidationError, match="№ УПД, файл УПД"):
        shipped.close()
    back = shipped.unship()

    assert (shipped.status, shipped.no_upd_approved_by) == (ShipmentStatus.SHIPPED, "Касаточкин")
    assert (back.no_upd_approved_by, back.no_upd_approved_at, back.ready_to_ship) == ("", None, False)


def test_card_pack_decision_and_upd_fields():
    card = ProjectAddressee.empty(PROJECT)
    decided = card.decide_pack(True, "Ульяна", NOW)
    numbered = decided.edit({"upd_number": " 125 ", "upd_date": "2026-10-29"})

    assert (decided.pack_decided, decided.pack_decided_by, decided.pack_decided_at) == (True, "Ульяна", NOW)
    assert decided.decide_pack(True, "Другой", NOW) is decided
    assert card.decide_pack(False, "Ульяна", NOW) is card
    assert decided.decide_pack(False, "Ульяна", NOW).pack_decided_by == ""
    assert (numbered.upd_number, numbered.upd_date) == ("125", date(2026, 10, 29)) and numbered.differs(decided)
    with pytest.raises(ShippingValidationError, match="Дата УПД"):
        card.edit({"upd_date": "29.10.2026"})


# ── API: решение «упаковывать» ─────────────────────────────────────────────────────────────────────────────────


def _good(client, item_index="КБ1", accepted="6") -> dict:
    created = client.post("/api/shipping/goods", json={
        "project": PROJECT, "item_index": item_index, "customer": "Заказчик", "name": "Жгут " + item_index,
        "ordered": "40", "unit": "шт",
    }).json()["good"]
    if not accepted:
        return created
    return client.post(f"/api/shipping/goods/{created['id']}/accept", json={
        "version": created["version"], "quantity": accepted,
    }).json()["good"]


def _decision(client, decided: bool, version: int):
    return client.post("/api/shipping/project-pack-decision", json={"project": PROJECT, "version": version, "decided": decided})


def _titles() -> list[str]:
    with app.state.shipping_uow_factory() as uow:
        return [row.payload.get("title") for row in uow.session.query(OutboxMessageRow)]


def test_storekeeper_packs_only_after_the_managers_decision(shipping_api):
    client = shipping_api
    stocked = _good(client)
    lines = [{"good_id": stocked["id"], "quantity": "2"}]
    _as(STOREKEEPER)
    refused = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": lines})
    not_his = _decision(client, True, 0)
    intake_before = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    _as(ADMIN)
    decided = _decision(client, True, 0)
    stale = _decision(client, False, 0)
    _as(STOREKEEPER)
    packed = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": lines})
    _as(ADMIN)
    locked = _decision(client, False, decided.json()["addressee"]["version"])

    assert refused.status_code == 422 and refused.json()["detail"] == PACK_NOT_DECIDED
    assert not_his.status_code == 403
    assert (intake_before["pack_decided"], intake_before["pack_decided_by"], intake_before["pack_decided_at"]) == (False, "", None)
    assert decided.status_code == 200
    card = decided.json()["addressee"]
    assert (card["pack_decided"], card["pack_decided_by"], card["version"]) == (True, "Администратор", 1)
    assert card["pack_decided_at"].startswith("2026-09-22")
    assert f"Можно упаковывать: проект {PROJECT}" in _titles()
    assert stale.status_code == 409
    assert packed.status_code == 200 and packed.json()["shipment"]["lines"][0]["picked"] == "2"
    assert locked.status_code == 422 and locked.json()["detail"] == PACK_DECISION_LOCKED


def test_decision_can_be_withdrawn_before_anything_is_picked_and_then_picking_stops(shipping_api):
    client = shipping_api
    queued = _good(client, accepted="")
    made = client.post("/api/shipping/shipments", json={
        "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО", "lines": [{"good_id": queued["id"], "quantity": "3"}],
    }).json()["shipment"]
    card = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    withdrawn = _decision(client, False, card["version"])
    fresh = client.get("/api/shipping/goods").json()["goods"][0]
    client.post(f"/api/shipping/goods/{fresh['id']}/accept", json={"version": fresh["version"], "quantity": "3"})
    _as(STOREKEEPER)
    pick = client.post(f"/api/shipping/shipments/{made['id']}/pick", json={"version": made["version"]})

    # Отправка менеджера сама поставила решение (ничего не набрано — заявка «Новая»).
    assert made["status"] == "Новая" and card["pack_decided"] is True
    assert withdrawn.status_code == 200 and withdrawn.json()["addressee"]["pack_decided"] is False
    assert pick.status_code == 422 and pick.json()["detail"] == PACK_NOT_DECIDED


def test_card_upd_number_goes_to_one_new_shipment(shipping_api):
    client = shipping_api
    stocked = _good(client, accepted="6")
    card = client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО",
        "upd_number": "125", "upd_date": "2026-09-21",
    }).json()["addressee"]
    decide_pack(client)
    _as(STOREKEEPER)
    first = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": [{"good_id": stocked["id"], "quantity": "2"}]}).json()["shipment"]
    second = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": [{"good_id": stocked["id"], "quantity": "2"}]}).json()["shipment"]

    assert (card["upd_number"], card["upd_date"]) == ("125", "2026-09-21")
    assert (first["upd_number"], first["upd_date"]) == ("125", "2026-09-21")
    assert (second["upd_number"], second["upd_date"]) == ("", None)
    # Набрано, но не упаковано (PL/DN нет): к треку не готово; самовывоз с № УПД уехать может.
    assert (first["ready_for_track"], first["ready_to_ship"]) == (False, True)
    assert (first["no_upd_approved_by"], first["no_upd_approved_at"]) == ("", None)


def test_track_through_the_api_is_refused_until_the_upd_is_there(shipping_api):
    client = shipping_api
    stocked = _good(client, accepted="6")
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "phone": "+7 900", "terms": "ДОСТАВКА ТК", "carrier": "ПЭК",
        "upd_type": "ЭДО", "lines": [{"good_id": stocked["id"], "quantity": "6"}],
    }).json()["shipment"]
    _as(STOREKEEPER)
    early = client.put(f"/api/shipping/shipments/{made['id']}/documents", json={
        "version": made["version"], "upd_type": "ЭДО", "track_number": "T-1",
    })
    _as(ADMIN)
    numbered = client.put(f"/api/shipping/shipments/{made['id']}/documents", json={
        "version": made["version"], "upd_type": "ЭДО", "upd_number": "9",
    }).json()["shipment"]
    _as(STOREKEEPER)
    tracked = client.put(f"/api/shipping/shipments/{made['id']}/documents", json={
        "version": numbered["version"], "upd_type": "ЭДО", "upd_number": "9", "track_number": "T-1",
    })

    assert (made["status"], made["ready_for_track"], made["ready_to_ship"]) == ("Упакована", False, False)
    assert early.status_code == 422 and "№ УПД" in early.json()["detail"]
    assert (numbered["ready_for_track"], numbered["ready_to_ship"]) == (True, False)
    assert tracked.status_code == 200, tracked.text
    assert (tracked.json()["shipment"]["ready_to_ship"], tracked.json()["shipment"]["missing_ship"]) == (True, [])
