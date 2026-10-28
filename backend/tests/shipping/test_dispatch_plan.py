"""План отгрузки в карточке проекта, «без УПД» и место хранения ГП (владелец 14.10.2026): склад и менеджер заносят
дату и комментарий заранее, новая отправка берёт их, если свои пусты; отправка «БЕЗ УПД» уезжает и закрывается без
файла и номера; принятое кладётся на место хранения (одно на КБ, последняя приёмка с местом его заменяет)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.main import app
from app.modules.shipping.documents import build_delivery_note
from app.modules.shipping.domain import DeliveryTerms, ProjectAddressee, ShippingValidationError, UpdType
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_domain import good, shipment
from tests.workflows.test_finished_goods_register import _accept, flow_api  # noqa: F401 — fixture

PROJECT = "591К.3"
DAY = date(2026, 9, 22)


def _packed(upd_type, terms=DeliveryTerms.PICKUP):
    item = good().accept(Decimal("10"), DAY, "ГП.26.0000001")
    picked, _ = shipment(item, terms=terms, upd_type=upd_type).pick({item.id: item.remaining}, {item.id: item.grn or ""})
    return picked.pack("PL591К.3-1", "DN591К.3-1", DAY)


# ── «Без УПД» ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_without_upd_ships_and_closes_with_neither_file_nor_number():
    shipped = _packed(UpdType.NONE).mark_shipped(DAY)
    closed = shipped.close()

    assert (shipped.upd_url, shipped.upd_number) == ("", "")
    assert closed.status.value == "Закрыта"


def test_without_upd_by_carrier_still_needs_the_track():
    packed = _packed(UpdType.NONE, terms=DeliveryTerms.CARRIER)

    with pytest.raises(ShippingValidationError, match="не хватает — трек-номер$"):
        packed.mark_shipped(DAY)
    shipped = packed.update_documents(upd_type=UpdType.NONE, upd_number="", upd_date=None, upd_url="", track_number="T-1").mark_shipped(DAY)
    assert shipped.close().status.value == "Закрыта"


def test_paper_and_empty_type_still_need_the_file():
    for kind, missing in ((UpdType.PAPER, "файл УПД"), (None, "тип УПД")):
        with pytest.raises(ShippingValidationError, match=missing):
            _packed(kind).mark_shipped(DAY)


def test_delivery_note_says_without_upd():
    form = build_delivery_note(_packed(UpdType.NONE), "Заказчик")

    assert (form.upd, form.upd_edo, form.upd_paper) == ("без УПД", False, False)


def _stocked(client, item_index="КБ1", quantity="6", **accept) -> dict:
    created = client.post("/api/shipping/goods", json={
        "project": PROJECT, "item_index": item_index, "customer": "Заказчик", "name": "Жгут " + item_index,
        "ordered": "40", "unit": "шт",
    }).json()["good"]
    return client.post(f"/api/shipping/goods/{created['id']}/accept", json={
        "version": created["version"], "quantity": quantity, **accept,
    }).json()["good"]


def test_without_upd_shipment_closes_at_once_through_the_api(shipping_api):
    client = shipping_api
    stocked = _stocked(client)
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "БЕЗ УПД",
        "lines": [{"good_id": stocked["id"], "quantity": "6"}],
    }).json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{made['id']}/ship", json={"version": made["version"]})

    assert made["upd_type"] == "БЕЗ УПД"
    assert shipped.status_code == 200
    assert shipped.json()["closed"] is True and shipped.json()["why"] == ""
    assert shipped.json()["shipment"]["status"] == "Закрыта"


def test_card_upd_type_without_upd_is_inherited_by_the_shipment(shipping_api):
    client = shipping_api
    stocked = _stocked(client)
    card = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "shipping_point": "Москва", "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "БЕЗ УПД"})
    made = client.post("/api/shipping/shipments", json={"lines": [{"good_id": stocked["id"], "quantity": "6"}]}).json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{made['id']}/ship", json={"version": made["version"]}).json()

    assert card.json()["addressee"]["upd_type"] == "БЕЗ УПД"
    assert made["upd_type"] == "БЕЗ УПД" and "upd_type" in made["inherited"]
    assert shipped["shipment"]["status"] == "Закрыта"


# ── План отгрузки в карточке проекта ────────────────────────────────────────────────────────────────────────────


def test_card_edit_sets_and_erases_plan_date_and_note():
    card = ProjectAddressee.empty(PROJECT)
    planned = card.edit({"plan_date": "2026-10-20", "note": "  частями, сначала КБ1 "})
    erased = planned.edit({"plan_date": None, "note": ""})

    assert (planned.plan_date, planned.note) == (date(2026, 10, 20), "частями, сначала КБ1")
    assert planned.differs(card) and erased.plan_date is None and erased.note == ""
    with pytest.raises(ShippingValidationError, match="ГГГГ-ММ-ДД"):
        card.edit({"plan_date": "20.10.2026"})


def test_plan_date_and_note_round_trip_through_the_card(shipping_api):
    client = shipping_api
    saved = client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "version": 0, "plan_date": "2026-10-20", "note": "Отгрузить двумя партиями",
    })
    one = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    every = client.get("/api/shipping/project-addressees").json()["addressees"]
    # Неприсланное поле не меняется; null стирает дату.
    noted = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": 1, "note": "Одной партией"})
    erased = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": 2, "plan_date": None})
    stale = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": 1, "note": "x"})

    assert saved.status_code == 200
    assert (one["plan_date"], one["note"], one["version"]) == ("2026-10-20", "Отгрузить двумя партиями", 1)
    assert (every[0]["plan_date"], every[0]["note"]) == ("2026-10-20", "Отгрузить двумя партиями")
    assert (noted.json()["addressee"]["plan_date"], noted.json()["addressee"]["note"]) == ("2026-10-20", "Одной партией")
    assert (erased.json()["addressee"]["plan_date"], erased.json()["addressee"]["note"]) == (None, "Одной партией")
    assert stale.status_code == 409


def test_empty_card_reads_without_plan(shipping_api):
    card = shipping_api.get("/api/shipping/project-addressee", params={"project": "600"}).json()["addressee"]

    assert (card["plan_date"], card["note"]) == (None, "")


def _as(permissions: dict[str, str], roles=frozenset({"foreman"})) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("10000000-0000-0000-0000-000000000005"), name="Алексей", department="Склад"),
        email="sklad@example.test", roles=roles, permissions=permissions,
    )


def test_storekeeper_edits_the_card_and_a_viewer_cannot(shipping_api):
    client = shipping_api
    _as({"shipping_goods": "edit"})
    storekeeper = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "plan_date": "2026-10-21", "note": "Склад"})
    _as({"shipping": "view", "shipping_goods": "view"})
    viewer = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "note": "Нельзя"})
    read = client.get("/api/shipping/project-addressee", params={"project": PROJECT})

    assert storekeeper.status_code == 200
    assert storekeeper.json()["addressee"]["updated_by"] == "Алексей"
    assert viewer.status_code == 403
    assert read.status_code == 200 and read.json()["addressee"]["note"] == "Склад"


def test_project_pack_takes_deadline_and_note_from_the_card(shipping_api):
    client = shipping_api
    stocked = _stocked(client)
    client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "shipping_point": "Склад заказчика", "plan_date": "2026-10-20", "note": "Хрупкое",
    })
    packed = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": [{"good_id": stocked["id"], "quantity": "2"}]})

    assert packed.status_code == 200
    assert (packed.json()["shipment"]["deadline"], packed.json()["shipment"]["note"]) == ("2026-10-20", "Хрупкое")


def test_own_deadline_and_note_of_a_shipment_win_over_the_card(shipping_api):
    client = shipping_api
    stocked = _stocked(client)
    client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "shipping_point": "Москва", "plan_date": "2026-10-20", "note": "Из карточки",
    })
    own = client.post("/api/shipping/shipments", json={
        "deadline": "2026-10-25", "note": "Своё", "no_docs": True, "lines": [{"good_id": stocked["id"], "quantity": "1"}],
    }).json()["shipment"]
    inherited = client.post("/api/shipping/shipments", json={
        "no_docs": True, "lines": [{"good_id": stocked["id"], "quantity": "1"}],
    }).json()["shipment"]

    assert (own["deadline"], own["note"]) == ("2026-10-25", "Своё")
    assert (inherited["deadline"], inherited["note"]) == ("2026-10-20", "Из карточки")


def test_project_ship_by_storekeeper_takes_the_card_deadline(shipping_api):
    client = shipping_api
    stocked = _stocked(client)
    card = client.put("/api/shipping/project-addressee", json={
        "project": PROJECT, "shipping_point": "Москва", "plan_date": "2026-10-22", "note": "Позвонить заранее",
    }).json()["addressee"]
    _as({"shipping_goods": "edit"})
    made = client.post("/api/shipping/project-ship", headers={"Idempotency-Key": "plan-1"}, json={
        "project": PROJECT, "addressee_version": card["version"],
        "lines": [{"good_id": stocked["id"], "version": stocked["version"], "quantity": "3"}],
    })

    assert made.status_code == 200
    assert (made.json()["shipment"]["deadline"], made.json()["shipment"]["note"]) == ("2026-10-22", "Позвонить заранее")


# ── Место хранения ГП ───────────────────────────────────────────────────────────────────────────────────────────


def test_accept_sets_location_and_an_empty_one_keeps_it():
    item = good()
    placed = item.accept(Decimal("2"), DAY, "ГП.26.0000001", " Стеллаж 3, полка 2 ")
    kept = placed.accept(Decimal("1"), DAY, "ГП.26.0000001")
    moved = kept.accept(Decimal("1"), DAY, "ГП.26.0000001", "Стеллаж 5")

    assert item.location == ""
    assert (placed.location, kept.location, moved.location) == ("Стеллаж 3, полка 2", "Стеллаж 3, полка 2", "Стеллаж 5")
    with pytest.raises(ShippingValidationError, match="Место хранения"):
        item.accept(Decimal("1"), DAY, "ГП.26.0000001", "x" * 256)


def test_single_accept_stores_location(shipping_api):
    client = shipping_api
    placed = _stocked(client, quantity="2", location="Стеллаж 3")
    again = client.post(f"/api/shipping/goods/{placed['id']}/accept", json={"version": placed["version"], "quantity": "1"})
    listed = client.get("/api/shipping/goods").json()["goods"]

    assert placed["location"] == "Стеллаж 3"
    assert again.json()["good"]["location"] == "Стеллаж 3" and again.json()["good"]["accepted"] == "3"
    assert listed[0]["location"] == "Стеллаж 3"


def test_batch_accept_stores_location_per_kb_and_keeps_the_shop_limit(flow_api):
    client, _ = flow_api
    over = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "loc-0"}, json={
        "lines": [{"project": "708К.4", "item_index": "КБ1", "quantity": "51", "location": "Стеллаж 1"}],
    })
    accepted = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "loc-1"}, json={
        "lines": [
            {"project": "708К.4", "item_index": "КБ1", "quantity": "10", "location": "Стеллаж 1"},
            {"project": "708К.4", "item_index": "КБ2", "quantity": "4"},
        ],
    })
    replay = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "loc-1"}, json={
        "lines": [
            {"project": "708К.4", "item_index": "КБ1", "quantity": "10", "location": "Стеллаж 1"},
            {"project": "708К.4", "item_index": "КБ2", "quantity": "4"},
        ],
    })
    other_place = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "loc-1"}, json={
        "lines": [
            {"project": "708К.4", "item_index": "КБ1", "quantity": "10", "location": "Стеллаж 2"},
            {"project": "708К.4", "item_index": "КБ2", "quantity": "4"},
        ],
    })
    plain = _accept(client, [("708К.4", "КБ1", "5")], key="loc-2")
    # Место на весь пакет — поле рядом с `lines`; место строки его заменяет.
    batch_place = client.post("/api/shipping/finished-goods/accept", headers={"Idempotency-Key": "loc-3"}, json={
        "location": "Паллет 7",
        "lines": [
            {"project": "708К.4", "item_index": "КБ1", "quantity": "1"},
            {"project": "708К.4", "item_index": "КБ2", "quantity": "1", "location": "Стеллаж 9"},
        ],
    })

    assert batch_place.status_code == 200
    assert [row["location"] for row in batch_place.json()["goods"]] == ["Паллет 7", "Стеллаж 9"]
    assert over.status_code == 422 and "тираж 50" in over.json()["detail"]  # in work: up to the order (28.10)
    goods = {row["item_index"]: row for row in accepted.json()["goods"]}
    assert (goods["КБ1"]["location"], goods["КБ2"]["location"]) == ("Стеллаж 1", "")
    assert replay.json()["replayed"] is True
    assert other_place.status_code == 409
    assert plain.json()["goods"][0]["location"] == "Стеллаж 1" and plain.json()["goods"][0]["accepted"] == "15"
