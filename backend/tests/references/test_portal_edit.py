"""Portal «Справочник» (SUPPLYMAP c61ce9d): per-row edit of technology bases, view-only СПР.КАБ/ТК."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.workflows.new_operation import SqlAlchemyReferencesOperationsUnitOfWork
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


def test_sheet_columns_edit_moves_the_schema_value_and_columns_hide_schema_keys(references_api):
    # БД.ОП keeps every sheet column (owner 29.09.2026); editing «Название» also moves opName the generator reads.
    record = _seed_record(references_api, tab="op", fields={
        "opNumber": "CUT", "opName": "Резка", "Номер": "CUT", "Название": "Резка", "Машина": "EW-05F",
    })
    columns = references_api.get("/api/references/tech-operations", params={"tab": "op"}).json()["columns"]
    response = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={
        "edits": [{"field": "Название", "old": "Резка", "value": "Резка провода"}],
    })

    assert columns == ["Номер", "Название", "Машина"]
    assert response.status_code == 200
    assert response.json()["fields"]["opName"] == "Резка провода"
    assert response.json()["display_text"] == "Резка провода | CUT"


def test_created_row_gets_schema_values_from_sheet_columns(references_api):
    _seed_record(references_api, tab="ob", fields={"obType": "Инструмент", "Тип": "Инструмент", "Для базы": "A/Б"})
    response = references_api.post("/api/references/tech-operations", json={
        "tab": "ob", "fields": {"Тип": "Станок", "Наменование": "Автомат", "Для базы": "EW-05F/Автомат"},
    })

    assert response.status_code in (200, 201)
    assert response.json()["fields"]["obType"] == "Станок"
    assert response.json()["display_text"] == "EW-05F/Автомат"


def test_generator_edit_of_a_terminal_schema_value_is_kept_and_reaches_its_sheet_column(references_api):
    # «Записать в БД» of the generator writes terLPlus / terLMinus (owner 13.10.2026: «после записывания введённое число
    # удаляется»): the record keeps the sheet columns L+ / L-, and the sync from them must not take the new value back.
    record = _seed_record(references_api, tab="ter", fields={
        "Артикул контакта (REEL)": "500798000", "Аппликатор": "S19", "L+": "", "L-": "", "Шаг разьема": "1,25",
        "terArticle": "500798000", "terApplicator": "S19", "terLPlus": "", "terLMinus": "", "terStep": "1,25",
    })
    url = f"/api/references/tech-operations/{record['id']}"

    response = references_api.patch(url, json={"edits": [
        {"field": "terLPlus", "old": "", "value": "5"}, {"field": "terLMinus", "old": "", "value": "3,5"},
    ]})
    again = references_api.get("/api/references/tech-operations", params={"tab": "ter"}).json()["records"][0]["fields"]
    # the next edit of another field (the sheet columns sync again) keeps them too
    later = references_api.patch(url, json={"edits": [{"field": "terApplicator", "old": "S19", "value": "S19/A2"}]})

    assert response.status_code == 200, response.text
    fields = response.json()["fields"]
    assert (fields["terLPlus"], fields["L+"], fields["terLMinus"], fields["L-"]) == ("5", "5", "3,5", "3,5")
    assert (again["terLPlus"], again["terLMinus"]) == ("5", "3,5")
    assert later.status_code == 200
    assert (later.json()["fields"]["terLMinus"], later.json()["fields"]["Аппликатор"]) == ("3,5", "S19/A2")


def test_generator_can_write_a_terminal_schema_value_the_record_never_had(references_api):
    # A БД.ТЕР row added by hand before L− was asked for has no terLMinus at all: «Записать в БД» still writes it.
    record = _seed_record(references_api, tab="ter", fields={"terArticle": "SSHL-002T-P0.2", "terLPlus": "5"})

    response = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={
        "edits": [{"field": "terLMinus", "old": "", "value": "10"}],
    })
    unknown = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={
        "edits": [{"field": "нет такого", "old": "", "value": "1"}],
    })

    assert response.status_code == 200, response.text
    assert response.json()["fields"]["terLMinus"] == "10"
    assert unknown.status_code == 409


# --- БД.ОП «Номер» is the operation code — the key that joins the row to its card template (owner 14.10.2026) ---

def _op(client, number, name="Резка"):
    return client.post("/api/references/tech-operations", json={
        "tab": "op", "fields": {"Номер": number, "Название": name, "Время операции": "3,5"},
    })


def test_operation_number_is_unique_on_create_and_rename(references_api):
    first = _op(references_api, "CUT").json()
    second = _op(references_api, "STRIP", "Зачистка").json()

    duplicate = _op(references_api, "CUT", "Ещё резка")
    other_case = _op(references_api, "cut", "Ещё резка")
    rename = references_api.patch(f"/api/references/tech-operations/{second['id']}", json={
        "edits": [{"field": "Номер", "old": "STRIP", "value": "CUT"}],
    })
    rename_key = references_api.patch(f"/api/references/tech-operations/{second['id']}", json={
        "edits": [{"field": "opNumber", "old": "STRIP", "value": "Cut"}],
    })
    own = references_api.patch(f"/api/references/tech-operations/{first['id']}", json={
        "edits": [{"field": "Номер", "old": "CUT", "value": "CUT_WIRE"}],
    })
    name_only = references_api.patch(f"/api/references/tech-operations/{second['id']}", json={
        "edits": [{"field": "Название", "old": "Зачистка", "value": "Зачистка провода"}],
    })

    assert duplicate.status_code == 422
    assert duplicate.json()["detail"] == "Операция с номером «CUT» уже есть — номер операции должен быть уникальным"
    assert other_case.status_code == 422
    assert rename.status_code == 422 and rename.json()["detail"] == duplicate.json()["detail"]
    assert rename_key.status_code == 422
    # no card template for CUT: the number may change; a name edit does not touch the number check
    assert own.status_code == 200 and own.json()["fields"]["opNumber"] == "CUT_WIRE"
    assert name_only.status_code == 200
    numbers = sorted(item["fields"]["opNumber"] for item in references_api.get(
        "/api/references/tech-operations", params={"tab": "op"}).json()["records"])
    assert numbers == ["CUT_WIRE", "STRIP"]


@pytest.fixture()
def references_with_templates_api():
    """«Справочник» on the unit of work that also sees the card template codes (app/workflows/new_operation.py)."""
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesOperationsUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP"),
        email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit", "erptctpl": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 14, 10, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory
    del app.state.techcards_uow_factory
    engine.dispose()


def test_renaming_the_number_of_an_operation_with_a_card_is_refused(references_with_templates_api):
    client = references_with_templates_api
    created = client.post("/api/techcards/operations", json={"code": "NEW_OP", "name": "Новая"})
    assert created.status_code == 201, created.text
    record = created.json()["record"]
    plain = _op(client, "PLAIN").json()
    url = f"/api/references/tech-operations/{record['id']}"

    orphan = client.patch(url, json={"edits": [{"field": "opNumber", "old": "NEW_OP", "value": "NEW_OP_2"}]})
    cleared = client.patch(url, json={"edits": [{"field": "opNumber", "old": "NEW_OP", "value": ""}]})
    taken = client.patch(f"/api/references/tech-operations/{plain['id']}", json={
        "edits": [{"field": "Номер", "old": "PLAIN", "value": "NEW_OP"}],
    })
    free = client.patch(f"/api/references/tech-operations/{plain['id']}", json={
        "edits": [{"field": "Номер", "old": "PLAIN", "value": "PLAIN_2"}],
    })
    renamed = client.patch(url, json={"edits": [{"field": "opName", "old": "Новая", "value": "Новая операция"}]})

    assert orphan.status_code == 422
    assert orphan.json()["detail"] == (
        "Номер «NEW_OP» не меняется: по нему к строке привязана карточка операции, она осталась бы без строки. "
        "Нужна другая операция — добавьте новую"
    )
    assert cleared.status_code == 422
    assert taken.status_code == 422 and "уже есть" in taken.json()["detail"]
    assert free.status_code == 200
    assert renamed.status_code == 200 and renamed.json()["fields"]["opNumber"] == "NEW_OP"
