"""The shared column set of a table (owner 22.10.2026): shown / hidden columns and their order, one for everyone."""

from uuid import UUID

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_custom_columns import _events, _seed

VIEWS = "/api/references/column-views"
COLUMNS = [{"key": "Марка", "visible": True}, {"key": "Цвета", "visible": False}, {"key": "Полка", "visible": True}]


def test_a_saved_set_comes_back_in_order_and_replaces_the_previous_one(references_api):
    assert references_api.get(VIEWS).json() == {"views": {}, "can_edit": True}

    saved = references_api.put(f"{VIEWS}/prov:coax", json={"columns": COLUMNS})
    references_api.put(f"{VIEWS}/mat", json={"columns": [{"key": "Артикул", "visible": True}]})
    again = references_api.put(f"{VIEWS}/prov:coax", json={"columns": list(reversed(COLUMNS))})
    fetched = references_api.get(VIEWS).json()

    assert saved.status_code == 200 and saved.json()["columns"] == COLUMNS
    assert again.status_code == 200
    assert fetched["views"]["prov:coax"] == list(reversed(COLUMNS))
    assert fetched["views"]["mat"] == [{"key": "Артикул", "visible": True}]
    assert len(_events("references.column_view_saved")) == 3


def test_an_empty_list_drops_the_shared_set(references_api):
    references_api.put(f"{VIEWS}/prov:coax", json={"columns": COLUMNS})

    dropped = references_api.put(f"{VIEWS}/prov:coax", json={"columns": []})

    assert dropped.status_code == 200
    assert references_api.get(VIEWS).json()["views"] == {}


def test_a_set_needs_a_shown_column_no_repeats_and_a_plain_view_name(references_api):
    none_shown = references_api.put(f"{VIEWS}/mat", json={"columns": [{"key": "Марка", "visible": False}]})
    repeated = references_api.put(f"{VIEWS}/mat", json={"columns": [{"key": "Марка", "visible": True}, {"key": " марка ", "visible": True}]})
    bad_name = references_api.put(f"{VIEWS}/Ма рка", json={"columns": COLUMNS})

    assert none_shown.status_code == 422 and "хотя бы один" in none_shown.json()["detail"]
    assert repeated.status_code == 422 and "дважды" in repeated.json()["detail"]
    assert bad_name.status_code in (404, 422)
    assert references_api.get(VIEWS).json()["views"] == {}


def test_only_those_who_edit_the_section_change_the_set_everyone_may_read_it(references_api):
    references_api.put(f"{VIEWS}/mat", json={"columns": COLUMNS})
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Кладовщик", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="viewer@example.test", roles=frozenset({"receiver"}), permissions={"erpref": "view"})

    read = references_api.get(VIEWS)
    write = references_api.put(f"{VIEWS}/mat", json={"columns": []})

    assert read.status_code == 200 and read.json()["can_edit"] is False and "mat" in read.json()["views"]
    assert write.status_code == 403
    assert "mat" in references_api.get(VIEWS).json()["views"]


def test_hiding_a_column_leaves_the_data_and_the_maps_save_keeps_the_set(references_api):
    records = _seed(references_api)

    references_api.put(f"{VIEWS}/prov:wire", json={"columns": [{"key": "Марка", "visible": True}, {"key": "Цвета", "visible": False}]})
    references_api.put("/api/references/maps", json={"suppliers": [], "countries": [], "gost_awg": []})
    after = references_api.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"]

    assert [r["fields"] for r in after] == [r["fields"] for r in records]
    assert "prov:wire" in references_api.get(VIEWS).json()["views"]
