"""Recovery temperature stays distinct from operating limits through every write path."""

import pytest
from uuid import UUID

from app.main import app
from app.modules.references.domain import ReferenceEntry, ReferenceKind
from app.modules.references.infrastructure import HeatShrinkRecordRow

from app.modules.references.shrink_temperature import (
    CHARACTERISTIC, COLUMNS, DOCUMENT, KIND, LOWER, PAGE, QUOTE, SOURCE_URL, UPPER, read,
)
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from tests.references.test_api import references_api  # noqa: F401
from tests.references.test_material_catalogs import create, listed
from tests.references.test_roundtrip import _download, _edited, _rows, _upload


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


EVIDENCE = {LOWER: "85", UPPER: "125", KIND: "оптимальная", CHARACTERISTIC: "110",
            SOURCE_URL: "https://example.org/passport.pdf", DOCUMENT: "Паспорт ТУТ 2:1",
            PAGE: "1–2", QUOTE: "Температура усадки: +110; оптимальная: 85–125 °C"}


@pytest.mark.parametrize(("fields", "bounds"), [
    ({}, (None, None)),
    ({LOWER: "105", KIND: "минимальная"}, (105, None)),
    ({LOWER: "105"}, (105, None)),
    ({UPPER: "120", KIND: "диапазон"}, (None, 120)),
    ({LOWER: "120", UPPER: "120", KIND: "диапазон"}, (120, 120)),
    ({LOWER: "90,5", UPPER: "120", KIND: "рекомендуемая"}, (90.5, 120)),
    ({LOWER: "0", KIND: "минимальная"}, (0, None)),
])
def test_typed_bounds_preserve_open_single_and_range_semantics(fields, bounds):
    value = read(fields)
    assert (value.lower_c, value.upper_c) == bounds


@pytest.mark.parametrize("fields", [
    {LOWER: "nan", KIND: "диапазон"}, {LOWER: "Infinity", KIND: "диапазон"},
    {LOWER: "90 °C", KIND: "диапазон"}, {LOWER: "-274", KIND: "диапазон"},
    {LOWER: "130", UPPER: "90", KIND: "диапазон"},
    {KIND: "оптимальная"}, {LOWER: "90", KIND: "примерная"},
    {LOWER: "105", UPPER: "125", KIND: "минимальная"},
    {SOURCE_URL: "javascript:alert(1)"}, {SOURCE_URL: "https://user:password@example.org"},
])
def test_invalid_payload_is_rejected(fields):
    with pytest.raises(ValueError):
        read(fields)


def test_schema_only_belongs_to_heat_shrink_even_when_empty(references_api):
    client = references_api
    for tab in ("heat_shrink", "cov", "mat"):
        schema = client.get(f"/api/references/import/{tab}/schema").json()
        assert (set(COLUMNS) <= set(schema["fields"])) == (tab == "heat_shrink")
    assert set(COLUMNS) <= set(_rows(_download(client, "heat_shrink"))[0])


def test_card_edit_is_guarded_and_operating_limits_are_unchanged(references_api):
    client = references_api
    original = create(client, "heat_shrink", "ТУТ 4/2", **{"Темп. от, °C": "-55", "Темп. до, °C": "125"})
    endpoint = f"/api/references/tech-operations/{original['id']}"
    edits = [{"field": name, "old": "", "value": value} for name, value in EVIDENCE.items()]
    response = client.patch(endpoint, json={"edits": edits})
    assert response.status_code == 200, response.text
    assert client.patch(endpoint, json={"edits": edits}).status_code == 409
    [row] = client.get("/api/references/coverings?tab=heat_shrink").json()["rows"]
    assert (row["temp_min_c"], row["temp_max_c"]) == (-55, 125)
    assert row["shrink_temperature"] == {
        "lower_c": 85, "upper_c": 125, "kind": "оптимальная", "characteristic_c": 110,
        "source_url": EVIDENCE[SOURCE_URL], "document": EVIDENCE[DOCUMENT], "page": "1–2", "quote": EVIDENCE[QUOTE],
    }
    bad = client.patch(endpoint, json={"edits": [{"field": LOWER, "old": "85", "value": "140"}]})
    assert bad.status_code == 422, bad.text
    assert listed(client, "heat_shrink")["records"][0]["fields"][LOWER] == "85"


def test_import_dry_run_guards_invalid_rows_and_preserves_both_statements(references_api):
    client = references_api
    original = create(client, "heat_shrink", "ТУТ 4/2")
    body = {"rows": [{"fields": {"Наименование": "ТУТ 4/2", **EVIDENCE},
                      "old": {name: "" for name in EVIDENCE}}]}
    url = "/api/references/import/heat_shrink"
    dry = client.post(url + "?dry_run=1", json=body)
    assert dry.status_code == 200 and len(dry.json()["updated"]) == 1, dry.text
    assert listed(client, "heat_shrink")["records"][0]["fields"] == original["fields"]
    applied = client.post(url + "?dry_run=0", json=body)
    assert applied.status_code == 200 and len(applied.json()["updated"]) == 1, applied.text
    stale = client.post(url + "?dry_run=0", json=body).json()
    assert len(stale["refused"]) == 1 and not stale["updated"]
    bad = client.post(url + "?dry_run=0", json={"rows": [
        {"fields": {"Наименование": "ТУТ 4/2", LOWER: "200"}, "old": {LOWER: "85"}},
        {"fields": {"Наименование": "ТУТ новая", LOWER: "105", UPPER: "90", KIND: "диапазон"}},
    ]})
    assert bad.status_code == 200 and len(bad.json()["refused"]) == 2, bad.text
    [saved] = listed(client, "heat_shrink")["records"]
    assert saved["id"] == original["id"] and all(saved["fields"][name] == value for name, value in EVIDENCE.items())


def test_excel_roundtrip_guards_and_validates_the_complete_temperature(references_api):
    client = references_api
    create(client, "heat_shrink", "ТУТ 4/2", **EVIDENCE)
    content = _download(client, "heat_shrink")
    same = _upload(client, content, table="heat_shrink", dry_run=0)
    assert same.status_code == 200 and not same.json()["updated"], same.text
    invalid = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index(UPPER), "80"))
    rejected = _upload(client, invalid, table="heat_shrink", dry_run=0)
    assert rejected.status_code == 200 and rejected.json()["errors"], rejected.text
    changed = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index(LOWER), "90"))
    applied = _upload(client, changed, table="heat_shrink", dry_run=0)
    assert applied.status_code == 200 and len(applied.json()["updated"]) == 1, applied.text
    stale = _upload(client, content, table="heat_shrink", dry_run=0)
    assert stale.status_code == 200 and stale.json()["conflicts"], stale.text
    saved = listed(client, "heat_shrink")["records"][0]["fields"]
    assert saved[LOWER] == "90" and saved[CHARACTERISTIC] == "110" and saved[QUOTE] == EVIDENCE[QUOTE]


def test_invalid_create_does_not_insert_a_row(references_api):
    response = references_api.post("/api/references/tech-operations", json={
        "tab": "heat_shrink", "fields": {"Наименование": "ТУТ 4/2", LOWER: "120", UPPER: "90", KIND: "диапазон"}})
    assert response.status_code == 422, response.text
    assert not listed(references_api, "heat_shrink")["records"]


@pytest.mark.parametrize("legacy", [
    {LOWER: "105"}, {LOWER: "около 105", KIND: "примерная"},
    {LOWER: "130", UPPER: "90", KIND: "диапазон"}, {SOURCE_URL: "неизвестен"},
])
def test_legacy_temperature_is_readable_and_unrelated_writes_succeed(references_api, legacy):
    client = references_api
    original = create(client, "heat_shrink", "ТУТ 4/2", **{"Цвет": "чёрный"})
    with app.state.references_uow_factory() as work:
        row = work.session.get(HeatShrinkRecordRow, UUID(original["id"]))
        row.fields = {**row.fields, **legacy}
        work.session.commit()
    response = client.get("/api/references/coverings?tab=heat_shrink")
    assert response.status_code == 200, response.text
    [row] = response.json()["rows"]
    temperature = row["shrink_temperature"]
    if legacy == {LOWER: "105"}:
        assert temperature["lower_c"] == 105 and temperature["kind"] == ""
    else:
        assert temperature["needs_review"] is True
        assert temperature["raw_fields"] == legacy
        assert temperature["validation_error"]
    endpoint = f"/api/references/tech-operations/{original['id']}"
    changed = client.patch(endpoint, json={"edits": [{"field": "Цвет", "old": "чёрный", "value": "белый"}]})
    assert changed.status_code == 200, changed.text
    assert all(changed.json()["fields"][key] == value for key, value in legacy.items())
    imported = client.post("/api/references/import/heat_shrink?dry_run=0", json={"rows": [{
        "fields": {"Наименование": "ТУТ 4/2", "Цвет": "красный"}, "old": {"Цвет": "белый"}}]})
    assert imported.status_code == 200 and len(imported.json()["updated"]) == 1, imported.text
    content = _download(client, "heat_shrink")
    content = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Цвет"), "синий"))
    applied = _upload(client, content, table="heat_shrink", dry_run=0)
    assert applied.status_code == 200 and len(applied.json()["updated"]) == 1, applied.text
    bad = client.patch(endpoint, json={"edits": [{"field": LOWER, "old": legacy.get(LOWER, ""), "value": "горячо"}]})
    assert bad.status_code == 422 and "укажите число в °C" in bad.text, bad.text
    saved = listed(client, "heat_shrink")["records"][0]["fields"]
    assert saved["Цвет"] == "синий"
    assert all(saved[key] == value for key, value in legacy.items())


@pytest.mark.parametrize(("removed", "restored"), [
    ("Температура, °C", "Рабочая температура, °C"),
    ("Рабочая температура, °C", "Температура, °C"),
    ("Рабочая температура, °C", "Рабочая температура, °C"),
])
def test_operating_temperature_column_aliases_share_removal_identity(references_api, removed, restored):
    client = references_api
    original = create(client, "heat_shrink", "ТУТ 4/2", **{"Темп. от, °C": "-55"})
    endpoint = "/api/references/tech-operations/columns"
    response = client.post(endpoint + "/remove", json={"tab": "heat_shrink", "name": removed})
    assert response.status_code == 200, response.text
    assert "Рабочая температура, °C" in listed(client, "heat_shrink")["removed_columns"]
    assert client.post(endpoint + "/remove", json={"tab": "heat_shrink", "name": restored}).status_code == 422
    response = client.post(endpoint, json={"tab": "heat_shrink", "name": restored})
    assert response.status_code == 201, response.text
    catalog = listed(client, "heat_shrink")
    assert not set((removed, restored)) & set(catalog["removed_columns"])
    assert not set((removed, restored)) & set(catalog["custom_columns"])
    assert catalog["records"][0]["fields"] == original["fields"]
    assert not listed(client, "cov")["removed_columns"]


def test_restoring_operating_column_clears_both_persisted_legacy_markers(references_api):
    client = references_api
    with app.state.references_uow_factory() as work:
        work.references.replace(tuple(ReferenceEntry.create(ReferenceKind.REMOVED_COLUMN,
            "heat_shrink:" + name, name, position) for position, name in enumerate(
                ("Температура, °C", "Рабочая температура, °C"))))
        work.commit()
    assert listed(client, "heat_shrink")["removed_columns"] == ["Рабочая температура, °C"]
    response = client.post("/api/references/tech-operations/columns", json={
        "tab": "heat_shrink", "name": "Рабочая температура, °C"})
    assert response.status_code == 201, response.text
    assert not listed(client, "heat_shrink")["removed_columns"]
