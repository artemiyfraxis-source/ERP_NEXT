import base64
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.main import app
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instruments_api import _as, create


@pytest.mark.parametrize("content,mime,disposition", [
    (b"%PDF-1.7\n test", "application/pdf", "inline"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "image/png", "inline"),
    (b"<html><script>alert(1)</script></html>", "application/octet-stream", "attachment"),
])
def test_file_upload_listing_sniffed_serve_delete_and_audit(production_api, content, mime, disposition):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    url = f"/api/production/instruments/{item['id']}/files"
    response = client.post(url, json={"kind": "Паспорт", "file_name": "паспорт.pdf", "content_base64": base64.b64encode(content).decode()})
    assert response.status_code == 200, response.text
    file = response.json()["file"]
    assert file["size"] == len(content) and "content" not in file and "content_base64" not in file
    statements = []
    engine = app.state.production_uow_factory().session_factory.kw["bind"]
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        listed = client.get("/api/production/instruments").json()["instruments"][0]["files"]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(listed) == 1 and listed[0]["id"] == file["id"] and listed[0]["uploaded_by_name"] == "Мастер"
    assert listed[0]["uploaded_at"] == file["uploaded_at"] == "2026-09-22T06:00:00+00:00"
    assert not any("production_instrument_files.content AS" in sql for sql in statements)
    download = client.get(f"{url}/{file['id']}")
    assert download.status_code == 200 and download.content == content
    assert download.headers["content-type"] == mime
    assert download.headers["content-disposition"].startswith(disposition)
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "Content-Security-Policy" in download.headers
    wrong = f"/api/production/instruments/{uuid4()}/files/{file['id']}"
    assert client.get(wrong).status_code == 404
    assert client.delete(wrong).status_code == 404
    assert client.delete(f"{url}/{file['id']}").status_code == 204
    assert client.delete(f"{url}/{file['id']}").status_code == 404
    assert client.get(f"{url}/{file['id']}").status_code == 404
    assert client.get("/api/production/instruments").json()["instruments"][0]["files"] == []
    with app.state.production_uow_factory() as uow:
        events = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type.like("production.instrument_file_%"))).all()
        assert sorted(row.event_type for row in events) == ["production.instrument_file_deleted", "production.instrument_file_uploaded"]


@pytest.mark.parametrize("extra", [
    {"content_base64": "%%%"}, {"content_base64": ""}, {"file_name": " "}, {"kind": "Неизвестный"},
    {"content_base64": base64.b64encode(b"x" * (20 * 1024 * 1024 + 1)).decode()},
])
def test_invalid_upload_is_rejected(production_api, extra):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    response = client.post(f"/api/production/instruments/{item['id']}/files", json={
        "kind": "Прочее", "file_name": "файл.bin", "content_base64": "eA==", **extra,
    })
    assert response.status_code in ({413, 422} if len(extra.get("content_base64", "")) > 20 * 1024 * 1024 else {422}), response.text
    assert client.get("/api/production/instruments").json()["instruments"][0]["files"] == []


def test_file_permissions_and_missing_instrument(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    body = {"kind": "Руководство", "file_name": "файл.bin", "content_base64": "eA=="}
    assert client.post(f"/api/production/instruments/{uuid4()}/files", json=body).status_code == 404
    url = f"/api/production/instruments/{item['id']}/files"
    file = client.post(url, json=body).json()["file"]
    _as({"production": "view"})
    assert client.post(url, json=body).status_code == 403
    assert client.delete(f"{url}/{file['id']}").status_code == 403
    assert client.get(f"{url}/{file['id']}").status_code == 200
    _as({"myops": "edit"})
    assert client.get(f"{url}/{file['id']}").status_code == 403


def test_twenty_mib_file_is_accepted_at_documented_limit(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    size = 20 * 1024 * 1024
    response = client.post(f"/api/production/instruments/{item['id']}/files", json={
        "kind": "Свидетельство о поверке", "file_name": "свидетельство.pdf",
        "content_base64": base64.b64encode(b"x" * size).decode(),
    })
    assert response.status_code == 200, response.text
    assert response.json()["file"]["size"] == size
