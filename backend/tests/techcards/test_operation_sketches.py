import base64

from app.modules.techcards.project_cards import operation_key

from .test_project_cards import _document, project_techcards_api  # noqa: F401 — the shared API fixture

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _built(client) -> dict:
    card = client.post("/api/techcards/projects", json={
        "project": "P-100", "item_index": "01", "product_name": "Жгут", "source_calculation": "Расчёт 7",
    }).json()
    return client.put(
        f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": _document()},
    ).json()


def test_operation_sketch_is_stored_replaced_and_deleted(project_techcards_api):
    client = project_techcards_api
    card = _built(client)
    key = operation_key(card["document"]["operations"][0])
    base = f"/api/techcards/projects/{card['id']}/sketches"

    first = client.put(f"{base}/{key}", json={
        "file_name": "распиновка.png", "content_type": "image/png",
        "content_base64": "data:image/png;base64," + base64.b64encode(PNG).decode(),
    })
    again = client.put(f"{base}/{key}", json={"content_type": "image/png", "content_base64": base64.b64encode(PNG).decode()})
    listed = client.get(base).json()["items"]
    picture = client.get(f"{base}/{key}")

    assert first.status_code == 200 and first.json()["file_name"] == "распиновка.png"
    assert again.status_code == 200 and again.json()["file_name"] == "эскиз.png"
    assert [item["operation_key"] for item in listed] == [key]
    assert picture.status_code == 200 and picture.content == PNG
    assert picture.headers["content-type"] == "image/png"

    assert client.delete(f"{base}/{key}").json() == {"deleted": 1}
    assert client.get(base).json()["items"] == []
    assert client.get(f"{base}/{key}").status_code == 404


def test_operation_sketch_refuses_foreign_operation_and_non_image(project_techcards_api):
    client = project_techcards_api
    card = _built(client)
    key = operation_key(card["document"]["operations"][0])
    base = f"/api/techcards/projects/{card['id']}/sketches"
    png = base64.b64encode(PNG).decode()

    assert client.put(f"{base}/99::X::A", json={"content_type": "image/png", "content_base64": png}).status_code == 404
    svg = client.put(f"{base}/{key}", json={"content_type": "image/svg+xml", "content_base64": png})
    assert svg.status_code == 422 and "PNG" in svg.json()["detail"]


def test_connector_catalog_lists_every_family_with_a_drawing(project_techcards_api):
    items = project_techcards_api.get("/api/techcards/connector-families").json()["items"]
    families = {item["family"]: item for item in items}

    assert families["jst-gh"]["housing"]["n"] == 4
    assert families["dsub"]["housing"]["kind"] == "dsub"
    assert all(item["housing"]["family"] == item["family"] for item in items)


def test_connector_catalog_checks_an_article(project_techcards_api):
    found = project_techcards_api.get("/api/techcards/connector-families", params={"article": "Molex 39012080"}).json()
    unknown = project_techcards_api.get("/api/techcards/connector-families", params={"article": "XYZ-1"}).json()

    assert found["match"]["family"] == "molex-minifit-jr" and found["match"]["n"] == 8
    assert unknown["match"] is None
