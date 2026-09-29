"""PL/DN, короба, «Заказано», «Отгружено» с авто-закрытием и «Упаковать» из карточки проекта."""

from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture


def _stocked(client, *, item_index="КБ1", ordered="40", accepted="6", project="591К.3"):
    good = client.post("/api/shipping/goods", json={
        "project": project, "item_index": item_index, "customer": "Заказчик", "article": "A-" + item_index,
        "name": "Жгут " + item_index, "ordered": ordered, "unit": "шт",
    }).json()["good"]
    if accepted:
        good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": accepted}).json()["good"]
    return good


def _shipment(client, lines, **extra):
    body = {"receiver": "Получатель", "shipping_point": "Москва", "terms": "ДОСТАВКА ТК", "upd_type": "ЭДО", "lines": lines}
    body.update(extra)
    response = client.post("/api/shipping/shipments", json=body)
    assert response.status_code == 200, response.text
    return response.json()["shipment"]


def test_requested_quantity_changes_after_creation_but_never_below_picked(shipping_api):
    client = shipping_api
    good = _stocked(client)
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "10"}], no_docs=True)
    line = shipment["lines"][0]
    url = f"/api/shipping/shipments/{shipment['id']}/lines/{line['id']}"
    below = client.put(url, json={"version": shipment["version"], "quantity": "5"})
    changed = client.put(url, json={"version": shipment["version"], "quantity": "8"})

    assert below.status_code == 422
    assert "уже набрано 6" in below.json()["detail"]
    assert changed.status_code == 200
    assert changed.json()["shipment"]["lines"][0]["requested"] == "8"
    assert changed.json()["shipment"]["lines"][0]["shortage"] == "2"


def test_ship_closes_at_once_when_upd_is_filled_and_stays_shipped_otherwise(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="10")
    ready = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], terms="САМОВЫВОЗ",
                      upd_number="УПД-1", upd_url="https://files.example/upd.pdf")
    bare = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], no_docs=True)

    closed = client.post(f"/api/shipping/shipments/{ready['id']}/ship", json={"version": ready["version"]}).json()
    shipped = client.post(f"/api/shipping/shipments/{bare['id']}/ship", json={"version": bare["version"]}).json()

    assert closed["closed"] is True and closed["shipment"]["status"] == "Закрыта"
    assert closed["shipment"]["shipped_at"] == "2026-09-22"
    # «Собрана» без документов тоже уезжает одной кнопкой (shipShipped_), но закрыть нечем.
    assert shipped["closed"] is False and shipped["shipment"]["status"] == "Отгружена"
    assert "№ УПД" in shipped["why"]


def test_boxes_are_checked_pack_the_shipment_and_print_pl_per_box_and_one_dn(shipping_api):
    client = shipping_api
    first = _stocked(client, item_index="КБ1", accepted="10")
    second = _stocked(client, item_index="КБ2", accepted="5")
    client.put("/api/shipping/project-addressee", json={"project": "591К.3", "po": "PO-7", "hours": "9-18"})
    shipment = _shipment(client, [{"good_id": first["id"], "quantity": "10"}, {"good_id": second["id"], "quantity": "5"}], no_docs=True)
    url = f"/api/shipping/shipments/{shipment['id']}/boxes"
    short = client.put(url, json={"version": shipment["version"], "boxes": [{"items": [{"item_index": "КБ1", "quantity": "10"}]}]})
    bad_weight = client.put(url, json={"version": shipment["version"], "boxes": [
        {"gross": "тяжёлый", "items": [{"item_index": "КБ1", "quantity": "10"}, {"item_index": "КБ2", "quantity": "5"}]}]})
    laid = client.put(url, json={"version": shipment["version"], "tk_note": "Не кантовать", "boxes": [
        {"kind": "Короб", "dims": "30х30х30", "net": "12", "gross": "12.5", "items": [{"item_index": "КБ1", "quantity": "10"}]},
        {"kind": "Пакет", "gross": "1,5", "items": [{"item_index": "КБ2", "quantity": "5"}]},
    ]})
    packed = laid.json()["shipment"]
    pl = client.get(f"/api/shipping/shipments/{shipment['id']}/print", params={"document": "pl"}).json()["sheets"]
    dn = client.get(f"/api/shipping/shipments/{shipment['id']}/print", params={"document": "dn"}).json()["sheets"]

    assert "Разложено не всё упакованное: КБ2 — 0 из 5" in short.json()["detail"]
    assert "вес брутто" in bad_weight.json()["detail"]
    assert packed["status"] == "Упакована" and packed["pl_number"] == "PL591К.3-1"
    assert packed["places"] == 2 and packed["weight"] == "14" and packed["tk_note"] == "Не кантовать"
    assert [box["gross"] for box in packed["boxes"]] == ["12,5", "1,5"]
    assert len(pl) == 2
    assert pl[0]["rows"] == [["КБ1", "A-КБ1", "Жгут КБ1", "10", "шт", "ГП.26.0000001"]]
    assert "BXN 2 / 2" in pl[1]["subtitle"]
    assert dn[0]["rows"][1] == ["КБ2", "A-КБ2", "Жгут КБ2", "5", "шт", "PL591К.3-1", "BXN 2"]
    assert "Заказ №: PO-7" in dn[0]["subtitle"] and "Спецификация №: 591К.3" in dn[0]["subtitle"]
    assert "Примечания для ТК: Не кантовать" in dn[0]["subtitle"]
    assert "☑ УПД передана посредством ЭДО" in dn[0]["note"] and "Общий вес (брутто), кг: 14" in dn[0]["note"]

    # Перевыпуск: номера сохраняются, короба заменяются.
    again = client.put(url, json={"version": packed["version"], "boxes": [
        {"items": [{"item_index": "КБ1", "quantity": "10"}, {"item_index": "КБ2", "quantity": "5"}]}]}).json()["shipment"]
    assert again["pl_number"] == "PL591К.3-1" and again["places"] == 1 and again["weight"] == ""


def test_print_needs_documents_and_defaults_to_one_box(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="4")
    assembled = _shipment(client, [{"good_id": good["id"], "quantity": "3"}], no_docs=True)
    packed = _shipment(client, [{"good_id": good["id"], "quantity": "1"}])

    refused = client.get(f"/api/shipping/shipments/{assembled['id']}/print", params={"document": "pl"})
    default = client.get(f"/api/shipping/shipments/{packed['id']}/print", params={"document": "pl"})

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Сначала сформируйте отгрузочные документы"
    assert default.status_code == 200
    assert "BXN 1 / 1" in default.json()["sheets"][0]["subtitle"]


def test_project_pack_takes_marked_quantity_up_to_stock_with_project_addressee(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="6")
    empty = _stocked(client, item_index="КБ2", accepted="")
    no_point = client.post("/api/shipping/project-pack", json={"project": "591К.3", "lines": [{"good_id": good["id"], "quantity": "4"}]})
    client.put("/api/shipping/project-addressee", json={
        "project": "591К.3", "shipping_point": "Склад заказчика", "terms": "САМОВЫВОЗ", "upd_type": "ЭДО",
    })
    nothing = client.post("/api/shipping/project-pack", json={"project": "591К.3", "lines": [{"good_id": empty["id"], "quantity": "4"}]})
    packed = client.post("/api/shipping/project-pack", json={"project": "591К.3", "lines": [{"good_id": good["id"], "quantity": "9"}]})
    addressees = client.get("/api/shipping/project-addressees").json()["addressees"]

    assert no_point.status_code == 422 and "адрес отгрузки" in no_point.json()["detail"]
    assert nothing.status_code == 422 and "Упаковывать нечего" in nothing.json()["detail"]
    shipment = packed.json()["shipment"]
    assert shipment["status"] == "Собрана" and shipment["pl_number"] == ""
    assert shipment["receiver"] == "Заказчик" and shipment["shipping_point"] == "Склад заказчика"
    assert shipment["lines"][0]["requested"] == "6" and shipment["lines"][0]["picked"] == "6"
    assert [row["project"] for row in addressees] == ["591К.3"]
    assert addressees[0]["po"] == ""
