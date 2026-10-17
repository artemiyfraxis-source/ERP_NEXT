"""Файл УПД отправки: хранилище, ссылка в отправке, уведомление кладовщикам (portalShipUpdFile/shipUpdAttach_)."""

import base64
from urllib.parse import unquote

from sqlalchemy import select

from app.main import app
from app.modules.shipping import application
from app.shared.persistence import OutboxMessageRow
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_documents_api import _shipment, _stocked


def _upload(client, shipment, content=b"%PDF-1.4 upd", name="upd.pdf"):
    return client.post("/api/shipping/upd-files", json={
        "shipment_id": shipment["id"], "version": shipment["version"], "file_name": name,
        "content_type": "application/pdf", "content_base64": base64.b64encode(content).decode(),
    })


def test_upload_stores_file_links_shipment_notifies_storekeepers_and_opens_the_paper_gate(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], upd_type="БУМАГА", upd_number="17")
    refused = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})

    uploaded = _upload(client, shipment)
    updated = uploaded.json()["shipment"]
    downloaded = client.get(updated["upd_url"])
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": updated["version"]}).json()
    with app.state.shipping_uow_factory() as uow:
        messages = [row.payload for row in uow.session.scalars(select(OutboxMessageRow))]

    assert refused.status_code == 422 and "файл УПД" in refused.json()["detail"]
    assert uploaded.status_code == 200
    assert updated["upd_url"].startswith("/api/shipping/files/")
    assert downloaded.status_code == 200 and downloaded.content == b"%PDF-1.4 upd"
    assert downloaded.headers["content-type"] == "application/pdf"
    assert unquote(downloaded.headers["content-disposition"]) == f"inline; filename*=UTF-8''УПД № 17 591К.3 {shipment['number']}.pdf"
    assert any("УПД к отправке" in str(message) for message in messages)
    # Файл открыл гейт «Отгружена»; закрыть не даёт только трек — доставка ТК (shipGateMiss_('Закрыта')).
    assert shipped["shipment"]["status"] == "Отгружена" and shipped["why"].endswith("трек-номер")


def test_upload_is_refused_for_cancelled_shipment_empty_or_too_large_file(shipping_api, monkeypatch):
    client = shipping_api
    good = _stocked(client, accepted="5")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "1"}])
    cancelled = client.post(f"/api/shipping/shipments/{shipment['id']}/cancel", json={"version": shipment["version"]}).json()["shipment"]
    live = _shipment(client, [{"good_id": good["id"], "quantity": "1"}])
    monkeypatch.setattr(application, "UPD_FILE_LIMIT", 4)

    assert _upload(client, cancelled, content=b"x").json()["detail"] == "Отправка отменена."
    assert _upload(client, live, content=b"12345").json()["detail"] == "Файл УПД больше 20 МБ."
    assert client.get("/api/shipping/files/00000000-0000-0000-0000-000000000000").status_code == 404
