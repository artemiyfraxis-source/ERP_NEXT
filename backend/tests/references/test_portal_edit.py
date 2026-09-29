"""Portal «Справочник» (SUPPLYMAP c61ce9d): per-row edit of technology bases, view-only СПР.КАБ/ТК."""

from uuid import UUID

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)


def _seed_record(client, tab="prov", fields=None):
    fields = fields or {"Марка": "МГТФ", "Сечение": "0,35"}
    assert client.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "records": [{"tab": tab, "display_text": "МГТФ", "source_sheet": "БД.ПРОВ", "fields": fields}],
    }).status_code == 200
    return client.get("/api/references/tech-operations", params={"tab": tab}).json()["records"][0]


def test_portal_bases_list_marks_view_only(references_api):
    bases = {item["id"]: item["editable"] for item in references_api.get("/api/references/bases").json()["bases"]}

    assert bases["prov"] and bases["mat"] and bases["op"] and bases["ob"] and bases["ter"] and bases["coax"]
    assert bases["spr_kab"] is False and bases["tk_templates"] is False


def test_edits_row_and_search_finds_new_value(references_api):
    record = _seed_record(references_api)
    response = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={
        "edits": [{"field": "Марка", "old": "МГТФ", "value": "МГШВ"}, {"field": "Сечение", "old": "0,35", "value": "0,5"}],
    })
    found = references_api.get("/api/references/tech-operations", params={"tab": "prov", "q": "мгшв"}).json()["records"]
    stale = references_api.get("/api/references/tech-operations", params={"tab": "prov", "q": "мгтф"}).json()["records"]

    assert response.status_code == 200
    assert response.json()["display_text"] == "МГШВ" and response.json()["fields"]["Сечение"] == "0,5"
    assert [item["id"] for item in found] == [record["id"]]
    assert stale == []


def test_edit_is_atomic_and_rejects_stale_and_formulas(references_api):
    record = _seed_record(references_api)
    url = f"/api/references/tech-operations/{record['id']}"

    stale = references_api.patch(url, json={"edits": [
        {"field": "Сечение", "old": "0,35", "value": "0,75"},
        {"field": "Марка", "old": "не то", "value": "X"},
    ]})
    formula = references_api.patch(url, json={"edits": [{"field": "Марка", "old": "МГТФ", "value": "=A1"}]})
    unknown = references_api.patch(url, json={"edits": [{"field": "Нет", "old": "", "value": "1"}]})
    missing = references_api.patch(
        "/api/references/tech-operations/00000000-0000-0000-0000-0000000000ff",
        json={"edits": [{"field": "Марка", "old": "", "value": "1"}]},
    )
    current = references_api.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"][0]

    assert stale.status_code == 409 and "уже поменяли" in stale.json()["detail"]
    assert formula.status_code == 409 and "Формулы" in formula.json()["detail"]
    assert unknown.status_code == 409
    assert missing.status_code == 404
    assert current["fields"] == {"Марка": "МГТФ", "Сечение": "0,35"}


def test_creates_row_needs_a_filled_field_and_rejects_view_only_bases(references_api):
    created = references_api.post("/api/references/tech-operations", json={"tab": "mat", "fields": {"Наименование": " Трубка ", "Группа": ""}})
    empty = references_api.post("/api/references/tech-operations", json={"tab": "mat", "fields": {"Наименование": "  "}})
    formula = references_api.post("/api/references/tech-operations", json={"tab": "mat", "fields": {"Наименование": "=1+1"}})
    kab = references_api.post("/api/references/tech-operations", json={"tab": "spr_kab", "fields": {"a": "1"}})
    tk = references_api.post("/api/references/tech-operations", json={"tab": "tk_templates", "fields": {"a": "1"}})
    unknown = references_api.post("/api/references/tech-operations", json={"tab": "zzz", "fields": {"a": "1"}})

    assert created.status_code == 201 and created.json()["display_text"] == "Трубка"
    assert empty.status_code == 422 and formula.status_code == 422
    assert kab.status_code == 422 and tk.status_code == 422 and unknown.status_code == 422
    assert len(references_api.get("/api/references/tech-operations", params={"tab": "mat"}).json()["records"]) == 1


def test_erpref_edit_opens_editing_to_any_role(references_api):
    """SUPPLYMAP PortalRef.gs portalSectionEditGuard_(token, 'erpref'): the section decides, not the role (ID-3)."""
    record = _seed_record(references_api)
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000003"), name="Технолог", department="КБ")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="tech@example.test", roles=frozenset({"engineer"}), permissions={"erpref": "edit"},
    )

    patch = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={"edits": [{"field": "Марка", "old": "МГТФ", "value": "X"}]})
    snapshot = references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": []})

    assert patch.status_code == 200
    assert snapshot.status_code == 403  # whole-base loads stay the administrator's tool


def test_bases_tiles_carry_record_counts(references_api):
    _seed_record(references_api)
    tiles = {item["id"]: item for item in references_api.get("/api/references/bases").json()["bases"]}

    assert tiles["prov"]["count"] == 1 and tiles["op"]["count"] == 0
    assert tiles["prov"]["icon"] and tiles["prov"]["sub"]
    assert tiles["tk_templates"]["count"] is None


def test_editing_needs_erpref_edit(references_api):
    record = _seed_record(references_api)
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Кладовщик", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="viewer@example.test", roles=frozenset({"receiver"}), permissions={"erpref": "view"},
    )

    patch = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={"edits": [{"field": "Марка", "old": "МГТФ", "value": "X"}]})
    post = references_api.post("/api/references/tech-operations", json={"tab": "mat", "fields": {"a": "1"}})
    read = references_api.get("/api/references/tech-operations", params={"tab": "prov"})

    assert patch.status_code == 403 and post.status_code == 403
    assert read.status_code == 200
