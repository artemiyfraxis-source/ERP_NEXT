"""Physical catalog ownership, write routing and identity/link preservation."""

from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.references.application import ReplaceSnapshotCommand, TechRecordInput, replace_snapshot_detailed
from app.modules.references.domain import MATERIAL_TABS, TechOperationTab
from app.modules.references.infrastructure import (
    CoveringRecordRow, HeatShrinkRecordRow, ReferenceStockLinkRow, TechOperationRecordRow,
)
from app.modules.references.material_catalogs import route
from app.modules.references.load_mirror import ACTOR
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from tests.references.test_api import references_api  # noqa: F401
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401
from tests.references.test_roundtrip import _download, _edited, _rows, _upload
from tests.references.test_stock_links import stock_refs  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


def create(client, tab, name, **fields):
    response = client.post("/api/references/tech-operations", json={
        "tab": tab, "fields": {"Наименование": name, **fields}})
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, tab, **params):
    response = client.get("/api/references/tech-operations", params={"tab": tab, **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("fields", "current", "expected"), [
    ({"Наименование": "Трубка термоусадочная 4/2", "Группа": "Новая серия"}, "mat", ("heat_shrink", "tut", "classified")),
    ({"Наименование": "СТТК 4/2", "Клеевой слой": "да"}, "mat", ("heat_shrink", "ttk", "classified")),
    ({"Наименование": "Клеевой состав", "Группа": "Клей"}, "mat", ("mat", "other", "unknown")),
    ({"Наименование": "Новая деталь"}, "heat_shrink", ("heat_shrink", "other", "unknown")),
    ({"Наименование": "ТУТ 4/2", "Раздел оболочек": "Гофра"}, "heat_shrink", ("cov", "corrugated", "classified")),
])
def test_material_routing_is_explicit_and_does_not_guess_adhesive(fields, current, expected):
    assert route(fields, current) == expected


def test_catalogs_own_physical_rows_and_family_read_preserves_ids(references_api):
    client = references_api
    shrink = create(client, "mat", "ТУТ 4/2", **{"Артикул": "SKU-1", "Группа": "ТУТ", "Неизвестное поле": "сохранить"})
    cover = create(client, "mat", "Оплётка 10", **{"Группа": "оплётка"})
    other = create(client, "mat", "Клеевой состав")
    assert (shrink["tab"], cover["tab"], other["tab"]) == ("heat_shrink", "cov", "mat")
    assert [row["id"] for row in listed(client, "heat_shrink")["records"]] == [shrink["id"]]
    assert {row["id"] for row in listed(client, "mat", family="true")["records"]} == {shrink["id"], cover["id"], other["id"]}
    assert client.get("/api/references/coverings").json()["rows"][0]["id"] == cover["id"]
    assert client.get("/api/references/coverings?tab=heat_shrink").json()["rows"][0]["id"] == shrink["id"]
    with app.state.references_uow_factory() as work:
        assert work.session.get(TechOperationRecordRow, UUID(shrink["id"])) is None
        assert work.session.get(HeatShrinkRecordRow, UUID(shrink["id"])).fields == shrink["fields"]
        assert work.session.get(CoveringRecordRow, UUID(cover["id"])) is not None


def test_harness_and_warehouse_compatibility_read_both_material_catalogs(references_api):
    from app.modules.techcards.harness_catalog_api import reference_tables
    from app.workflows.item_names import ReferenceNames, reference_records
    from app.workflows.stock_receipt import known_articles

    shrink = create(references_api, "heat_shrink", "ТУТ 4/2", **{"Группа": "ТУТ"})
    cover = create(references_api, "cov", "Гофра 10", **{"Группа": "гофра"})
    factory = app.state.references_uow_factory
    records = reference_records(factory)
    assert {str(row.id) for row in records} == {shrink["id"], cover["id"]}
    names = ReferenceNames(records)
    assert names.name("Гофра 10") == "Гофра 10"
    assert {fields["Наименование"] for fields in reference_tables(factory)[1]["mat"]} == {"ТУТ 4/2", "Гофра 10"}
    assert {article for article, _ in known_articles(None, None, factory)} == {"ТУТ 4/2", "Гофра 10"}


def test_unknown_after_edit_stays_visible_in_current_catalog(references_api):
    row = create(references_api, "heat_shrink", "ТУТ 4/2", **{"Группа": "ТУТ"})
    response = references_api.patch(f"/api/references/tech-operations/{row['id']}", json={"edits": [
        {"field": "Наименование", "old": "ТУТ 4/2", "value": "Новая деталь"},
        {"field": "Группа", "old": "ТУТ", "value": "Неизвестная"}]})
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["tab"] == "heat_shrink" and changed["classification_status"] == "unknown"
    [shown] = references_api.get("/api/references/coverings?tab=heat_shrink").json()["rows"]
    assert shown["id"] == row["id"] and shown["group"] == "other" and shown["classification_status"] == "unknown"


def test_schema_removal_retains_payload_and_empty_tabs_have_own_columns(references_api):
    client = references_api
    endpoint = "/api/references/tech-operations/columns"
    assert client.post(endpoint, json={"tab": "heat_shrink", "name": "Полка"}).status_code == 201
    assert "Полка" in _rows(_download(client, "heat_shrink"))[0]
    assert "Полка" in client.get("/api/references/import/heat_shrink/schema").json()["fields"]
    assert "Полка" not in client.get("/api/references/import/cov/schema").json()["fields"]
    imported = client.post("/api/references/import/heat_shrink?dry_run=0", json={"rows": [{"fields": {
        "Наименование": "ТУТ 8/4", "Полка": "B2"}}]})
    assert imported.status_code == 200 and len(imported.json()["created"]) == 1, imported.text
    shrink = create(client, "heat_shrink", "ТУТ 4/2", **{"Производитель": "КВТ", "Полка": "A1"})
    create(client, "cov", "Гофра 10", **{"Производитель": "КВТ"})
    for name in ("Производитель", "Полка"):
        response = client.post(endpoint + "/remove", json={"tab": "heat_shrink", "name": name})
        assert response.status_code == 200, response.text
    table = listed(client, "heat_shrink")
    assert "Производитель" not in table["columns"] and "Полка" not in table["custom_columns"]
    assert next(row for row in table["records"] if row["id"] == shrink["id"])["fields"]["Полка"] == "A1"
    assert "Полка" not in client.get("/api/references/import/heat_shrink/schema").json()["fields"]
    assert client.post(endpoint + "/remove", json={"tab": "heat_shrink", "name": "Стенка, мм"}).status_code == 200
    assert "Стенка, мм" in listed(client, "heat_shrink")["removed_columns"]
    assert "Стенка, мм" not in listed(client, "cov")["removed_columns"]
    assert client.post(endpoint, json={"tab": "heat_shrink", "name": "Стенка, мм"}).status_code == 201
    restored = listed(client, "heat_shrink")
    assert "Стенка, мм" not in restored["removed_columns"] and "Стенка, мм" not in restored["custom_columns"]
    assert all("Стенка, мм" not in row["fields"] for row in restored["records"])
    assert client.post(endpoint, json={"tab": "heat_shrink", "name": "Стенка, мм"}).status_code == 422
    assert "Производитель" in listed(client, "cov")["columns"]
    assert client.post(endpoint + "/remove", json={"tab": "heat_shrink", "name": "Наименование"}).status_code == 422
    assert client.post(endpoint, json={"tab": "heat_shrink", "name": "Полка"}).status_code == 201
    assert next(row for row in listed(client, "heat_shrink")["records"] if row["id"] == shrink["id"])["fields"]["Полка"] == shrink["fields"]["Полка"]


def test_stock_link_follows_two_moves_and_deletion_keeps_journal(stock_refs):
    client, lot, record, sessions = stock_refs
    linked = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": record["id"]})
    assert linked.status_code == 200
    for label, tab, model, target in (("ТУТ", "heat_shrink", HeatShrinkRecordRow, "heat_shrink_id"),
                                      ("Гофра", "cov", CoveringRecordRow, "covering_id")):
        moved = client.put(f"/api/references/coverings/{record['id']}/group", json={"group": label})
        assert moved.status_code == 200, moved.text
        assert moved.json()["id"] == record["id"] and moved.json()["tab"] == tab
        with sessions() as session:
            [journal] = session.scalars(select(ReferenceStockLinkRow))
            assert getattr(journal, target) == UUID(record["id"]) and journal.record_tab == tab
            assert journal.record_id is None and session.get(model, UUID(record["id"])) is not None
            archive = session.get(TechOperationRecordRow, UUID(record["id"]))
            assert archive.material_archived and archive.fields == record["fields"]
    assert client.delete(f"/api/references/tech-operations/{record['id']}").status_code == 200
    with sessions() as session:
        [journal] = session.scalars(select(ReferenceStockLinkRow))
        assert journal.covering_id is None and journal.record_text == record["display_text"]
    assert not listed(client, "mat", family="true")["records"]


def test_mirror_reload_preserves_uuid_and_deleted_source_stays_deleted(uow):  # noqa: F811
    command = ReplaceSnapshotCommand(schema_version=14, source_updated_at=None, actor=ACTOR, now=NOW,
        preserve_erp_edits=True, declared_tabs=(TechOperationTab.MATERIAL,), keep_other_tabs=True,
        records=(TechRecordInput(TechOperationTab.MATERIAL, "ТУТ 4/2", "", "БД.МАТ", "1", {"Наименование": "ТУТ 4/2"}),))
    replace_snapshot_detailed(command, uow)
    with uow:
        [first] = uow.tech_operations.list(tab=MATERIAL_TABS, query="", limit=100)
    replace_snapshot_detailed(command, uow)
    with uow:
        [second] = uow.tech_operations.list(tab=MATERIAL_TABS, query="", limit=100)
    assert second.id == first.id and second.fields == first.fields and second.mirror_key == first.mirror_key
    from app.modules.references.application import delete_record
    delete_record(first.id, ACTOR, NOW, uow)
    replace_snapshot_detailed(command, uow)
    with uow:
        assert uow.tech_operations.list(tab=MATERIAL_TABS, query="", limit=100) == []


def test_import_matches_moved_rows_across_family_and_roundtrips_each_tab(references_api):
    client = references_api
    shrink = create(client, "heat_shrink", "ТУТ 4/2", **{"Группа": "ТУТ"})
    cover = create(client, "cov", "Гофра 10", **{"Группа": "гофра"})
    for table in ("mat", "heat_shrink"):
        response = client.post(f"/api/references/import/{table}?dry_run=0", json={"rows": [{
            "fields": {"Наименование": "ТУТ 4/2", "Производитель": "КВТ"}}]})
        assert response.status_code == 200, response.text
        assert response.json()["created"] == []
    assert [row["id"] for row in listed(client, "heat_shrink")["records"]] == [shrink["id"]]
    for table, row in (("heat_shrink", shrink), ("cov", cover)):
        content = _download(client, table)
        assert _rows(content)[1][0] == row["id"]
        result = _upload(client, content, dry_run=0, table=table)
        assert result.status_code == 200, result.text
        edited = _edited(content, lambda rows: rows[1].__setitem__(rows[0].index("Производитель"), "Новый завод"))
        changed = _upload(client, edited, dry_run=0, table=table)
        assert changed.status_code == 200 and len(changed.json()["updated"]) == 1, changed.text
        assert listed(client, table)["records"][0]["fields"]["Производитель"] == "Новый завод"
