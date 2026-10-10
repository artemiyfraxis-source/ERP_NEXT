"""Приёмка: файл УПД с подменённым типом скачивается, а не открывается страницей в origin ERP (audit F04)."""

import base64

from sqlalchemy import update

from app.main import app
from app.modules.receiving.infrastructure import ReceivingFileRow
from tests.receiving.test_api import receiving_api  # noqa: F401 — fixture

PAGE = b"<!doctype html><script>fetch('/api/identity/me')</script>"


def _upload(client, line, content, name="upd.pdf", declared="application/pdf"):
    return client.post("/api/receiving/files", json={
        "line_id": str(line.id), "kind": "upd", "file_name": name, "content_type": declared,
        "content_base64": base64.b64encode(content).decode(),
    })


def test_page_named_pdf_is_served_as_a_download_old_rows_too(receiving_api):
    client, line = receiving_api
    uploaded = _upload(client, line, PAGE)
    assert uploaded.status_code == 200, uploaded.text
    url = uploaded.json()["url"]

    fresh = client.get(url)
    with app.state.receiving_uow_factory() as uow:  # a row from before the fix keeps the uploader's MIME
        uow.session.execute(update(ReceivingFileRow).values(content_type="text/html"))
        uow.commit()
    old = client.get(url)

    for response in (fresh, old):
        assert response.content == PAGE
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"


def test_real_pdf_still_opens_in_the_tab(receiving_api):
    client, line = receiving_api
    opened = client.get(_upload(client, line, b"%PDF-1.4 upd", declared="").json()["url"])

    assert opened.headers["content-type"] == "application/pdf"
    assert opened.headers["content-disposition"].startswith("inline;")
