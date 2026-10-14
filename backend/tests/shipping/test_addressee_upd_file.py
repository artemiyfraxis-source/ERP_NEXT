"""Бумажная УПД в данных отправки проекта (владелец 14.10.2026): файл кладётся в то же хранилище УПД, ссылка — в
карточку проекта; новая отправка с бумажной УПД берёт его себе (один файл — одной отправке), гейт «нужен файл УПД»
проходит."""

from __future__ import annotations

import base64

from sqlalchemy import select

from app.main import app
from app.modules.shipping.domain import ProjectAddressee, UpdType
from app.modules.shipping.infrastructure import ShippingFileRow
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_dispatch_plan import _as
from tests.shipping.test_documents_api import _stocked

PROJECT = "591К.3"


def _upload(client, content=b"%PDF-1.4 upd", name="УПД 591К.3.pdf", project=PROJECT):
    return client.post("/api/shipping/upd-files", json={
        "project": project, "file_name": name, "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
    })


def _card(client, **fields) -> dict:
    current = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    response = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "version": current["version"], **fields})
    assert response.status_code == 200, response.text
    return response.json()["addressee"]


def _paper_card(client) -> dict:
    url = _upload(client).json()["file"]["url"]
    return _card(client, receiver="ООО Заказчик", shipping_point="Москва", phone="+7 900", upd_type="БУМАГА", upd_url=url)


def test_card_upd_file_round_trips_and_an_empty_value_removes_it(shipping_api):
    client = shipping_api
    uploaded = _upload(client)
    file = uploaded.json()["file"]
    card = _card(client, upd_type="БУМАГА", upd_url=file["url"])
    read = client.get("/api/shipping/project-addressee", params={"project": PROJECT}).json()["addressee"]
    every = client.get("/api/shipping/project-addressees").json()["addressees"]
    downloaded = client.get(file["url"])
    # Неприсланное поле не меняется: правка телефона файл не трогает.
    phoned = _card(client, phone="+7 900")
    removed = _card(client, upd_url="")
    with app.state.shipping_uow_factory() as uow:
        stored = uow.session.execute(select(ShippingFileRow.shipment_id, ShippingFileRow.project)).one()

    assert uploaded.status_code == 200 and file["url"].startswith("/api/shipping/files/") and file["name"] == "УПД 591К.3.pdf"
    assert (card["upd_url"], card["upd_file_name"]) == (file["url"], "УПД 591К.3.pdf")
    assert (read["upd_url"], read["upd_file_name"]) == (file["url"], "УПД 591К.3.pdf")
    assert every[0]["upd_file_name"] == "УПД 591К.3.pdf"
    assert downloaded.status_code == 200 and downloaded.content == b"%PDF-1.4 upd"
    assert phoned["upd_url"] == file["url"]
    assert (removed["upd_url"], removed["upd_file_name"]) == ("", "")
    assert tuple(stored) == (None, PROJECT)


def test_card_upload_needs_a_project_or_shipment_and_editor_rights(shipping_api):
    client = shipping_api
    nobody = client.post("/api/shipping/upd-files", json={
        "file_name": "a.pdf", "content_base64": base64.b64encode(b"%PDF-1.4").decode(),
    })
    empty = _upload(client, content=b"")
    _as({"shipping_goods": "edit"})
    storekeeper = _upload(client)
    _as({"shipping": "view", "shipping_goods": "view"})
    viewer = _upload(client)
    viewer_put = client.put("/api/shipping/project-addressee", json={"project": PROJECT, "upd_url": "/api/shipping/files/x"})

    assert nobody.status_code == 422
    assert empty.status_code == 422
    assert storekeeper.status_code == 200
    assert viewer.status_code == 403 and viewer_put.status_code == 403


def test_project_pack_takes_the_paper_upd_of_the_card_and_passes_the_file_gate(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="6")
    card = _paper_card(client)
    packed = client.post("/api/shipping/project-pack", json={"project": PROJECT, "lines": [{"good_id": good["id"], "quantity": "2"}]})
    shipment = packed.json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})

    assert packed.status_code == 200
    assert shipment["upd_type"] == "БУМАГА" and shipment["upd_url"] == card["upd_url"]
    assert shipped.status_code == 200 and shipped.json()["shipment"]["status"] == "Отгружена"


def test_one_card_file_goes_to_one_shipment_and_back_after_cancel(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="6")
    card = _paper_card(client)
    body = {"no_docs": True, "lines": [{"good_id": good["id"], "quantity": "1"}]}
    first = client.post("/api/shipping/shipments", json=body).json()["shipment"]
    second = client.post("/api/shipping/shipments", json=body).json()["shipment"]
    client.post(f"/api/shipping/shipments/{first['id']}/cancel", json={"version": first["version"]})
    third = client.post("/api/shipping/shipments", json=body).json()["shipment"]

    assert first["upd_url"] == card["upd_url"]
    assert second["upd_url"] == ""
    assert third["upd_url"] == card["upd_url"]


def test_own_file_or_electronic_upd_of_a_shipment_is_not_replaced_by_the_card(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="6")
    _paper_card(client)
    lines = [{"good_id": good["id"], "quantity": "1"}]
    own = client.post("/api/shipping/shipments", json={"no_docs": True, "upd_url": "/api/shipping/files/своя", "lines": lines}).json()["shipment"]
    electronic = client.post("/api/shipping/shipments", json={"no_docs": True, "upd_type": "ЭДО", "lines": lines}).json()["shipment"]

    assert own["upd_url"] == "/api/shipping/files/своя"
    assert electronic["upd_url"] == ""


def test_dispatch_gaps_name_what_is_missing():
    empty = ProjectAddressee.empty(PROJECT)
    paper = ProjectAddressee(project=PROJECT, receiver="П", shipping_point="М", phone="1", upd_type=UpdType.PAPER)

    assert empty.dispatch_gaps() == ("получатель", "адрес доставки", "телефон", "вариант УПД")
    assert paper.dispatch_gaps() == ("файл бумажной УПД",)
    assert ProjectAddressee(project=PROJECT, receiver="П", shipping_point="М", phone="1", upd_type=UpdType.EDO).dispatch_gaps() == ()
    assert paper.edit({"upd_url": " /api/shipping/files/1 "}).dispatch_gaps() == ()
