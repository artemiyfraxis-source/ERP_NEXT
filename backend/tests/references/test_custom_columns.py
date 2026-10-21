"""«+ Столбец» (owner 14.10.2026): the owner's own column of a reference table; БД.ПРОВ — of one cable list (20.10.2026)."""

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.references.application import ColumnCommand, FieldEdit, _edit_in, add_column, custom_columns
from app.modules.references.cables import CableKind
from app.modules.references.domain import TechOperationTab
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_cables import PROV, PROV_ROWS, _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures/helpers)
from tests.references.test_roundtrip import _download, _rows

COLUMNS = "/api/references/tech-operations/columns"


def _seed(client: TestClient, tab="prov", fields=None) -> list[dict]:
    rows = fields or [{"Марка": "МГТФ 0,2", "Цвета": "белый"}, {"Марка": "МГШВ 0,5", "Цвета": "красный"}]
    assert client.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "records": [{"tab": tab, "display_text": next(iter(row.values())), "source_sheet": "БД", "fields": row} for row in rows],
    }).status_code == 200
    return client.get("/api/references/tech-operations", params={"tab": tab}).json()["records"]


def _columns(client, tab="prov") -> list[str]:
    return client.get("/api/references/tech-operations", params={"tab": tab}).json()["columns"]


def _events(event_type: str):
    sessions = app.state.references_uow_factory().session_factory
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)).all()


def test_a_column_is_added_to_every_record_listed_edited_inline_and_audited_once(references_api):
    records = _seed(references_api)

    response = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "  Склад   полка "})
    after = references_api.get("/api/references/tech-operations", params={"tab": "prov"}).json()

    assert response.status_code == 201
    assert response.json() == {"tab": "prov", "name": "Склад полка", "records": 2, "cable_kind": "wire"}
    assert after["columns"][-1] == "Склад полка"
    assert all(record["fields"]["Склад полка"] == "" for record in after["records"])
    [event] = _events("references.column_added")
    assert event.details == {"tab": "prov", "column": "Склад полка", "records": 2, "cable_kind": "wire"}
    edit = references_api.patch(f"/api/references/tech-operations/{records[0]['id']}",
                                json={"edits": [{"field": "Склад полка", "old": "", "value": "А-12"}]})
    assert edit.status_code == 200 and edit.json()["fields"]["Склад полка"] == "А-12"


def test_the_cables_screen_columns_and_the_coax_tab_get_the_new_column(references_api):
    _seed(references_api)
    _seed(references_api, tab="coax", fields=[{"Артикул": "SMA-1", "Тип": "SMA"}])

    assert references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Полка"}).status_code == 201
    assert references_api.post(COLUMNS, json={"tab": "coax", "name": "Полка"}).status_code == 201

    cables = references_api.get("/api/references/cables").json()
    assert cables["columns"][-1] == "Полка" and cables["columns_by_kind"]["wire"][-1] == "Полка"
    assert "Полка" not in cables["columns_by_kind"]["coax"] and cables["custom_columns_by_kind"]["wire"] == ["Полка"]
    assert _columns(references_api, "coax")[-1] == "Полка"
    assert references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "полка"}).status_code == 422
    no_list = references_api.post(COLUMNS, json={"tab": "prov", "name": "Ячейка"})
    assert no_list.status_code == 422 and "список кабелей" in no_list.json()["detail"]
    # another list has no such column yet: a coax cable gets its own «Полка» (here the list is empty — no place for it)
    assert "нет записей" in references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "coax", "name": "Полка"}).json()["detail"]


def test_a_bad_name_is_refused_and_nothing_is_written(references_api):
    _seed(references_api)
    cases = {"": 422, "   ": 422, "Я" * 61: 422, "марка": 422, "Цвет": 422, "=A1": 422}

    codes = {name: references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": name}).status_code for name in cases}
    ok_long = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Я" * 60})

    assert codes == cases
    assert ok_long.status_code == 201
    assert len(_events("references.column_added")) == 1


def test_a_schema_key_and_a_tab_without_own_columns_are_refused(references_api):
    _seed(references_api, tab="coax", fields=[{"coaxArticle": "SMA-1", "Артикул": "SMA-1"}])
    _seed(references_api, tab="ob", fields=[{"Тип": "Инструмент", "Инв. номер": "ОБ.001", "Наменование": "Кримпер"}])

    schema_key = references_api.post(COLUMNS, json={"tab": "coax", "name": "coaxArticle"})
    equipment = references_api.post(COLUMNS, json={"tab": "ob", "name": "Полка"})
    unknown_tab = references_api.post(COLUMNS, json={"tab": "zzz", "name": "Полка"})

    assert schema_key.status_code == 422 and "уже есть" in schema_key.json()["detail"]
    assert equipment.status_code == 422 and "не добавляются" in equipment.json()["detail"]
    assert unknown_tab.status_code == 422


def test_an_empty_table_has_no_place_for_a_column(references_api):
    response = references_api.post(COLUMNS, json={"tab": "mat", "name": "Полка"})

    assert response.status_code == 422 and "нет записей" in response.json()["detail"]


def test_the_column_survives_a_mirror_reload_and_a_row_that_lacks_it_still_edits(tmp_path, uow):
    load_mirror(_mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    command = ColumnCommand(TechOperationTab.WIRE, "Полка", ACTOR, NOW, CableKind.WIRE)
    written = add_column(command, uow)

    prov = PROV + "Новый 1\tмонтажный\t0,5\t\t1\t1\t\n"  # a row the mirror brings after the column was added
    load_mirror(_mirror(tmp_path / "b", prov=prov), uow, dry_run=False, now=NOW)

    with uow:
        records = {item.fields["Марка"]: item for item in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100)}
        wires = {mark for mark, record in records.items() if record.cable_kind == "wire"}
        assert written == len(wires) - 1 == 3 and len(records) == len(PROV_ROWS) + 1
        assert all(record.fields.get("Полка") == "" for mark, record in records.items() if mark in wires - {"Новый 1"})
        assert not any("Полка" in record.fields for mark, record in records.items() if mark not in wires)  # own list only
        assert "Полка" not in records["Новый 1"].fields and records["Новый 1"].cable_kind == "wire"
        assert custom_columns(TechOperationTab.WIRE, uow) == ["Полка"] == custom_columns(TechOperationTab.WIRE, uow, "wire")
        assert custom_columns(TechOperationTab.WIRE, uow, "coax") == []
        _record_in, updated = _edit_in(records["Новый 1"].id, (FieldEdit("Полка", "", "Б-3"),), NOW, uow)
    assert updated.fields["Полка"] == "Б-3"


def test_the_download_for_editing_carries_the_column_and_reads_it_back(references_api):
    records = _seed(references_api)
    references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Полка"})
    references_api.patch(f"/api/references/tech-operations/{records[0]['id']}",
                         json={"edits": [{"field": "Полка", "old": "", "value": "А-1"}]})

    fields = references_api.get("/api/references/import/prov/file/fields").json()
    rows = _rows(_download(references_api))

    assert "Полка" in fields["fields"]
    assert "Полка" in rows[0] and "А-1" in {row[rows[0].index("Полка")] for row in rows[1:]}


def test_a_column_is_removed_only_when_every_value_is_empty(references_api):
    records = _seed(references_api)
    references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Полка"})
    references_api.patch(f"/api/references/tech-operations/{records[0]['id']}",
                         json={"edits": [{"field": "Полка", "old": "", "value": "А-1"}]})

    refused = references_api.post(f"{COLUMNS}/remove", json={"tab": "prov", "cable_kind": "wire", "name": "Полка"})
    still = "Полка" in _columns(references_api)
    references_api.patch(f"/api/references/tech-operations/{records[0]['id']}",
                         json={"edits": [{"field": "Полка", "old": "А-1", "value": ""}]})
    removed = references_api.post(f"{COLUMNS}/remove", json={"tab": "prov", "cable_kind": "wire", "name": "полка"})
    base = references_api.post(f"{COLUMNS}/remove", json={"tab": "prov", "cable_kind": "wire", "name": "Цвета"})

    assert refused.status_code == 422 and "есть данные" in refused.json()["detail"] and still
    assert removed.status_code == 200 and removed.json()["records"] == 2
    assert "Полка" not in _columns(references_api)
    assert all("Полка" not in record["fields"] for record in
               references_api.get("/api/references/tech-operations", params={"tab": "prov"}).json()["records"])
    assert base.status_code == 422 and "только свой" in base.json()["detail"]
    assert len(_events("references.column_removed")) == 1
    assert references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Полка"}).status_code == 201


def test_adding_a_column_needs_erpref_edit(references_api):
    _seed(references_api)
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Кладовщик", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="viewer@example.test", roles=frozenset({"receiver"}), permissions={"erpref": "view"},
    )

    add = references_api.post(COLUMNS, json={"tab": "prov", "cable_kind": "wire", "name": "Полка"})
    remove = references_api.post(f"{COLUMNS}/remove", json={"tab": "prov", "cable_kind": "wire", "name": "Полка"})

    assert add.status_code == 403 and remove.status_code == 403
    assert "Полка" not in _columns(references_api)


# --- БД.ТЕР and БД.ОП (14.10.2026: «+ Столбец» on every records table of «База данных») ---

def _mirror_api(tmp_path, sessions):
    from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
    from tests.references.test_load_mirror import mirror

    load_mirror(mirror(tmp_path), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit", "procurement": "edit"})
    return TestClient(app)


def _drop_api():
    app.dependency_overrides.pop(authenticated_principal, None)
    del app.state.references_uow_factory


def test_terminals_and_operations_take_an_own_column_edit_it_and_drop_it_when_empty(tmp_path, sessions):
    client = _mirror_api(tmp_path, sessions)
    try:
        added = {tab: client.post(COLUMNS, json={"tab": tab, "name": "Полка"}) for tab in ("ter", "op")}
        listed = {tab: client.get("/api/references/tech-operations", params={"tab": tab}).json() for tab in ("ter", "op")}
        ter = listed["ter"]["records"][0]
        edit = client.patch(f"/api/references/tech-operations/{ter['id']}", json={"edits": [{"field": "Полка", "old": "", "value": "Б-7"}]})
        refused = client.post(f"{COLUMNS}/remove", json={"tab": "ter", "name": "Полка"})
        client.patch(f"/api/references/tech-operations/{ter['id']}", json={"edits": [{"field": "Полка", "old": "Б-7", "value": ""}]})
        removed = client.post(f"{COLUMNS}/remove", json={"tab": "ter", "name": "Полка"})
        resolved = client.post("/api/references/technology/resolve-terminal", json={"article": "Molex 430310001", "wires": ["UL1007 22 AWG"]})
        after = client.get("/api/references/tech-operations", params={"tab": "ter"}).json()
    finally:
        _drop_api()

    assert added["ter"].status_code == 201 and added["op"].status_code == 201
    assert all(listed[tab]["columns"][-1] == "Полка" and listed[tab]["custom_columns"] == ["Полка"] for tab in ("ter", "op"))
    assert all(record["fields"]["Полка"] == "" for tab in ("ter", "op") for record in listed[tab]["records"])
    assert edit.status_code == 200 and edit.json()["fields"]["Полка"] == "Б-7"
    assert refused.status_code == 422 and "есть данные (1)" in refused.json()["detail"]
    assert removed.status_code == 200 and "Полка" not in after["columns"] and after["custom_columns"] == []
    assert resolved.status_code == 200 and resolved.json()["applicator"] == "S8/M8"  # the generator reads the row as before


def test_a_terminal_column_survives_a_mirror_reload(tmp_path, uow):
    load_mirror(_mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    add_column(ColumnCommand(TechOperationTab.TERMINAL, "Ячейка", ACTOR, NOW), uow)

    load_mirror(_mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)

    with uow:
        records = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100)
        assert records and all(record.fields.get("Ячейка") == "" and record.edited_in_erp for record in records)
        assert custom_columns(TechOperationTab.TERMINAL, uow) == ["Ячейка"]
