"""Отправка: файл УПД с подменённым типом скачивается, а не открывается страницей в origin ERP (audit F04)."""

from sqlalchemy import update

from app.main import app
from app.modules.shipping.infrastructure import ShippingFileRow
from tests.shipping.test_api import shipping_api  # noqa: F401 — fixture
from tests.shipping.test_documents_api import _shipment, _stocked
from tests.shipping.test_upd_files import _upload

SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


def test_svg_named_pdf_is_served_as_a_download_old_rows_too(shipping_api):
    client = shipping_api
    good = _stocked(client, accepted="5")
    shipment = _shipment(client, [{"good_id": good["id"], "quantity": "5"}], upd_type="БУМАГА", upd_number="17")
    uploaded = _upload(client, shipment, content=SVG, name="upd.pdf")
    assert uploaded.status_code == 200, uploaded.text
    url = uploaded.json()["shipment"]["upd_url"]

    fresh = client.get(url)
    with app.state.shipping_uow_factory() as uow:  # a row from before the fix keeps the uploader's MIME
        uow.session.execute(update(ShippingFileRow).values(content_type="image/svg+xml"))
        uow.commit()
    old = client.get(url)

    for response in (fresh, old):
        assert response.content == SVG
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
