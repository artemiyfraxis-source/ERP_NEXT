"""КД и эскизы: тип файла — по байтам; страница или SVG не открываются в origin ERP (audit F04)."""

import base64

from sqlalchemy import select, update

from app.main import app
from app.modules.techcards.kitset_infrastructure import KitDocumentRow
from app.modules.techcards.project_cards import operation_key
from tests.techcards.test_kitset_api import _load, kitset_api  # noqa: F401 — fixture
from tests.techcards.test_operation_sketches import PNG, _built
from tests.techcards.test_project_cards import project_techcards_api  # noqa: F401 — fixture

PAGE = b"<html><body><script>fetch('/api/identity/me')</script></body></html>"


def test_kd_page_declared_as_pdf_or_harness_is_kept_as_a_plain_download(kitset_api):
    client, _lot = kitset_api
    _load(client)
    attached = client.post("/api/techcards/kitsets/708К.4/documents", json={
        "index": "КБ1", "file_name": "Чертёж.pdf", "content_type": "application/vnd.fraxis.harness+json",
        "content_b64": base64.b64encode(PAGE).decode(),
    })
    assert attached.status_code == 201, attached.text
    document = attached.json()["res"]
    url = f"/api/techcards/kitsets/708К.4/documents/{document['id']}"

    fresh = client.get(url)
    with app.state.techcards_uow_factory() as uow:
        kept = uow.session.scalar(select(KitDocumentRow.content_type).where(KitDocumentRow.file_name.like("%Чертёж.pdf")))
        uow.session.execute(update(KitDocumentRow).values(content_type="text/html"))  # a row from before the fix
        uow.commit()
    old = client.get(url)

    # a declared type no longer passes a file off as the harness drawing
    assert (document["kind"], kept) == ("file", "application/octet-stream")
    for response in (fresh, old):
        assert response.content == PAGE
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"


def test_sketch_must_be_a_picture_by_its_bytes_and_opens_inline(project_techcards_api):
    client = project_techcards_api
    card = _built(client)
    base = f"/api/techcards/projects/{card['id']}/sketches/{operation_key(card['document']['operations'][0])}"

    svg = client.put(base, json={
        "content_type": "image/png", "content_base64": base64.b64encode(b"<svg onload='alert(1)'/>").decode(),
    })
    jpeg_named_png = client.put(base, json={
        "content_type": "image/png", "content_base64": base64.b64encode(b"\xff\xd8\xff\xe0 jpeg").decode(),
    })
    shown = client.get(base)

    assert svg.status_code == 422 and "не похож на картинку" in svg.json()["detail"]
    assert jpeg_named_png.status_code == 200 and jpeg_named_png.json()["file_name"] == "эскиз.jpg"
    assert (shown.headers["content-type"], shown.headers["content-disposition"][:6]) == ("image/jpeg", "inline")
    assert "sandbox" in shown.headers["content-security-policy"]
    assert client.put(base, json={"content_type": "image/png", "content_base64": base64.b64encode(PNG).decode()}).status_code == 200
