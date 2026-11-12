"""Equipment linked by id (owner 05.11.2026: «связка должна быть не через название, а через идентификатор»): unit and
operation ids survive mirror loads, snapshots, imports and the xlsx round trip; БД.ОП cells link to the model, a
rename rewrites their text; a linked unit is never deleted; ambiguous text is not guessed and is in the report;
supporting equipment (compressor, chiller) is no machine of an operation."""

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.references.application import delete_record
from app.modules.references.equipment_identity import LinkedEquipmentError
from app.modules.references.equipment_links_report import format_report
from app.modules.references.infrastructure import (
    EquipmentModelRow, EquipmentOperationLinkRow, EquipmentUnitLinkRow, SqlAlchemyReferencesUnitOfWork,
    TechOperationRecordRow,
)
from app.modules.references.load_mirror import ACTOR, default_source, load_mirror
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from app.shared.persistence import create_schema
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_roundtrip import _download, _edited, _upload

BASE = "/api/references/tech-operations"
NOW = datetime(2026, 11, 5, 12, 0)


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


def _unit(inv, name, article, *, kind="Оборудование", maker="", tool=""):
    fields = {"Тип": kind, "Инв. номер": inv, "Наменование": name, "Артикул": article, "Производитель": maker,
              "Для базы": f"{article}/{name}", "Для инструмента": tool, "Расположение": ""}
    return {"tab": "ob", "display_text": f"{article}/{name}", "source_sheet": "БД.ОБ", "sort_key": inv, "fields": fields}


def _operation(code, machine="", tool=""):
    fields = {"Номер": code, "Название": f"Операция {code}", "opNumber": code, "opName": f"Операция {code}",
              "Машина": machine, "Инструмент": tool}
    return {"tab": "op", "display_text": f"Операция {code} | {code}", "source_sheet": "БД.ОП", "sort_key": code,
            "fields": fields}


UNITS = [
    _unit("ОБ.070.1", "Станция паяльная", "8620DX"),
    _unit("ОБ.070.2", "Станция паяльная", "8620DX"),
    _unit("ОБ.044", "Блок питания", "APS"),
    _unit("ОБ.050", "Сушилка", "", maker="Creality"),
    _unit("ОБ.051", "Сушилка", "", maker="Элбэт"),
    _unit("ОБ.060", "Компрессор поршневой", "K1"),
    _unit("ОСН.001", "Матрица", "M-1", kind="Матрицы", tool="ОБ.044"),
]
OPERATIONS = [
    _operation("SOLDER", "8620DX/Станция паяльная"),
    _operation("POWER", "APS / блок питания; ОБ.070.2"),
    _operation("DRY", "/Сушилка"),  # two models (two makers): ambiguous
    _operation("AIR", "Пневмостанок"),  # nothing
]


def _snapshot(client, units=UNITS, operations=OPERATIONS):
    response = client.put(f"{BASE}/snapshot", json={"schema_version": 14, "records": [*units, *operations]})
    assert response.status_code == 200, response.text


def _rows(client, tab):
    return {item["fields"].get("Инв. номер") or item["fields"]["opNumber"]: item
            for item in client.get(BASE, params={"tab": tab, "limit": 5000}).json()["records"]}


def _links(client):
    ops = _rows(client, "op")
    return {code: [(link["column"], link["position"], link["model_id"]) for link in item["equipment_links"]]
            for code, item in ops.items()}


def _sessions():
    return app.state.references_uow_factory().session_factory


def test_snapshot_twice_keeps_ids_models_and_links(references_api):
    _snapshot(references_api)
    units, ops, links = _rows(references_api, "ob"), _rows(references_api, "op"), _links(references_api)
    _snapshot(references_api)

    assert {key: item["id"] for key, item in _rows(references_api, "ob").items()} == {key: item["id"] for key, item in units.items()}
    assert {key: item["id"] for key, item in _rows(references_api, "op").items()} == {key: item["id"] for key, item in ops.items()}
    assert _links(references_api) == links
    station = units["ОБ.070.1"]["equipment_model_id"]
    assert units["ОБ.070.2"]["equipment_model_id"] == station != units["ОБ.044"]["equipment_model_id"]
    assert units["ОБ.050"]["equipment_model_id"] != units["ОБ.051"]["equipment_model_id"]  # makers differ
    assert links["SOLDER"] == [("Машина", 0, station)]
    # «APS / блок питания» — the text of the model (case, spaces around «/»); «ОБ.070.2» — a unit's inventory number
    assert links["POWER"] == [("Машина", 0, units["ОБ.044"]["equipment_model_id"]), ("Машина", 1, station)]
    assert links["DRY"] == [] and links["AIR"] == []
    assert units["ОСН.001"]["tool_unit_id"] == units["ОБ.044"]["id"]
    assert units["ОБ.060"]["equipment_role"] == "supporting" and units["ОБ.044"]["equipment_role"] == "operational"


def test_saving_legacy_applicator_uses_code_without_changing_unit_model_or_links(references_api):
    applicator = _unit("ОБ.106", "Аппликатор", "A", kind="Аппликаторы")
    applicator["fields"]["Код в базе терминалов"] = "M10"
    matrix = _unit("ОСН.001", "Матрица", "M", kind="Матрицы", tool="ОБ.106")
    _snapshot(references_api, [applicator, matrix], [_operation("PRESS", "ОБ.106")])
    before = _rows(references_api, "ob")
    links = _links(references_api)
    edited = references_api.patch(f"{BASE}/{before['ОБ.106']['id']}", json={"edits": [
        {"field": "Код в базе терминалов", "old": "M10", "value": "M11"}]})
    assert edited.status_code == 200, edited.text
    after = _rows(references_api, "ob")
    assert after["M11"]["id"] == before["ОБ.106"]["id"]
    assert after["M11"]["equipment_model_id"] == before["ОБ.106"]["equipment_model_id"]
    assert after["ОСН.001"]["tool_unit_id"] == after["M11"]["id"]
    assert after["ОСН.001"]["fields"]["Для инструмента"] == "M11"
    assert _links(references_api) == links


def test_ambiguous_and_unmatched_cells_are_not_guessed_and_are_in_the_report(references_api):
    _snapshot(references_api)
    report = references_api.get("/api/references/equipment-links/report").json()

    unresolved = {(item["number"], item["text"]): item for item in report["operations"]}
    assert set(unresolved) == {("DRY", "/Сушилка"), ("AIR", "Пневмостанок")}
    assert unresolved[("DRY", "/Сушилка")]["reason"] == "ambiguous"
    assert len(unresolved[("DRY", "/Сушилка")]["candidates"]) == 2
    assert unresolved[("AIR", "Пневмостанок")]["reason"] == "unmatched"
    assert report["counts"]["operation_links"] == 3 and report["counts"]["tools_unresolved"] == 0
    text = format_report(report)
    assert "DRY · Машина: «/Сушилка» — несколько моделей" in text and "AIR · Машина" in text


def test_a_renamed_unit_keeps_its_links_and_the_operation_text_follows_once_every_unit_is_renamed(references_api):
    _snapshot(references_api)
    units = _rows(references_api, "ob")
    station = units["ОБ.070.1"]["equipment_model_id"]

    def rename(inv):
        unit = _rows(references_api, "ob")[inv]
        response = references_api.patch(f"{BASE}/{unit['id']}", json={"edits": [
            {"field": "Наменование", "old": "Станция паяльная", "value": "Паяльная станция"}]})
        assert response.status_code == 200, response.text

    rename("ОБ.070.1")  # the screen renames a model unit by unit: the old text still names the other unit
    assert _rows(references_api, "op")["SOLDER"]["fields"]["Машина"] == "8620DX/Станция паяльная"
    rename("ОБ.070.2")

    after = _rows(references_api, "ob")
    assert after["ОБ.070.1"]["id"] == units["ОБ.070.1"]["id"] and after["ОБ.070.1"]["equipment_model_id"] == station
    operation = _rows(references_api, "op")["SOLDER"]
    assert operation["fields"]["Машина"] == "8620DX/Паяльная станция"
    assert _links(references_api)["SOLDER"] == [("Машина", 0, station)]
    model = next(item for item in references_api.get("/api/references/equipment-models").json()["models"]
                 if item["id"] == station)
    assert model["name"] == "Паяльная станция" and model["operations"] == 2  # SOLDER, POWER
    # the inventory-number piece of POWER still names the unit: it stays as written
    assert _rows(references_api, "op")["POWER"]["fields"]["Машина"] == "APS / блок питания; ОБ.070.2"


def test_an_edited_cell_relinks_or_loses_its_link(references_api):
    _snapshot(references_api)
    units, operation = _rows(references_api, "ob"), _rows(references_api, "op")["SOLDER"]

    def edit(old, value):
        return references_api.patch(f"{BASE}/{operation['id']}", json={"edits": [
            {"field": "Машина", "old": old, "value": value}]})

    assert edit("8620DX/Станция паяльная", "APS/Блок питания").status_code == 200
    assert _links(references_api)["SOLDER"] == [("Машина", 0, units["ОБ.044"]["equipment_model_id"])]
    assert edit("APS/Блок питания", "Неизвестный станок").status_code == 200
    assert _links(references_api)["SOLDER"] == []
    refused = edit("Неизвестный станок", "K1/Компрессор поршневой")
    assert refused.status_code == 422 and "обеспечивающее" in refused.json()["detail"]


def test_a_linked_unit_is_never_deleted_and_a_reload_that_would_delete_it_is_refused(references_api):
    _snapshot(references_api)
    units = _rows(references_api, "ob")
    sessions = _sessions()

    with pytest.raises(LinkedEquipmentError, match="ОБ.044.*Для инструмента"):  # the matrix's tool
        delete_record(UUID(units["ОБ.044"]["id"]), ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions))
    with pytest.raises(LinkedEquipmentError, match="последняя единица модели"):  # SOLDER links to its model
        uow = SqlAlchemyReferencesUnitOfWork(sessions)
        with uow:
            uow.tech_operations.delete(UUID(units["ОБ.070.1"]["id"]))
            uow.tech_operations.delete(UUID(units["ОБ.070.2"]["id"]))
    delete_record(UUID(units["ОБ.070.1"]["id"]), ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions))  # one of two

    without_tool = [item for item in UNITS if item["fields"]["Инв. номер"] != "ОБ.044"]
    response = references_api.put(f"{BASE}/snapshot", json={"schema_version": 14, "records": [*without_tool, *OPERATIONS]})
    assert response.status_code == 422 and "ОБ.044" in response.json()["detail"]
    assert _rows(references_api, "ob")["ОБ.044"]["id"] == units["ОБ.044"]["id"]


def test_import_rows_and_the_xlsx_round_trip_keep_ids_and_links(references_api):
    _snapshot(references_api)
    units, links = _rows(references_api, "ob"), _links(references_api)

    imported = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [
        {"fields": {"baseValue": "APS/Блок питания", "Расположение": "ЦАР"}},
        {"fields": {"baseValue": "N9/Новый станок", "Тип": "Оборудование", "Наменование": "Новый станок",
                    "Артикул": "N9", "Инв. номер": "ОБ.099"}},
    ]})
    assert imported.status_code == 200, imported.text
    after = _rows(references_api, "ob")
    assert after["ОБ.044"]["id"] == units["ОБ.044"]["id"] and after["ОБ.044"]["fields"]["Расположение"] == "ЦАР"
    assert after["ОБ.099"]["equipment_model_id"] not in {item["equipment_model_id"] for item in units.values()}

    content = _download(references_api, "ob")

    def edit(rows):
        header = rows[0]
        place = header.index(next(title for title in header if title == "Расположение"))
        for row in rows[1:]:
            row[place] = "ЖУК"
    assert _upload(references_api, _edited(content, edit), dry_run=0, table="ob").status_code == 200
    final = _rows(references_api, "ob")
    assert {key: item["id"] for key, item in final.items()} == {key: item["id"] for key, item in after.items()}
    assert {item["fields"]["Расположение"] for item in final.values()} == {"ЖУК"}
    assert _links(references_api) == links


def test_bulk_import_numbering_dry_run_apply_and_refusals(references_api):
    ordinary = _unit("ОБ.009", "Станок", "P")
    ordinary["fields"]["Код в базе терминалов"] = ""  # the existing БД.ОБ import column
    _snapshot(references_api, [ordinary,
        _unit("СИ.004", "Микрометр", "MIC", kind="Средства измерений")], [])
    before = _rows(references_api, "ob")
    rows = [
        {"baseValue": "APP/Аппликатор", "Тип": "Аппликаторы", "Код в базе терминалов": " m 10 ", "Инв. номер": "ОБ.106"},
        {"baseValue": "SI/Измеритель", "Тип": "Средства измерений", "Инв. номер": "ОБ.107"},
        {"baseValue": "SI2/Измеритель", "obType": "Средства измерений"},
        {"baseValue": "NEW/Станок", "Тип": "Оборудование"},
        {"baseValue": "EMPTY/Аппликатор", "Тип": "Аппликаторы"},
        {"baseValue": "BAD/Аппликатор", "Тип": "Аппликаторы", "Код в базе терминалов": "X1"},
        {"baseValue": "DUP/Аппликатор", "Тип": "Аппликаторы", "Код в базе терминалов": "M10"},
        {"baseValue": "DUPINV/Инструмент", "Тип": "Инструмент", "Инв. номер": "ОБ.009"},
        {"baseValue": "LAST/Станок", "Тип": "Оборудование"},
    ]
    reports = []
    for dry_run in (1, 0):
        response = references_api.post(f"/api/references/import/ob?dry_run={dry_run}", json={"rows": [{"fields": row} for row in rows]})
        assert response.status_code == 200, response.text
        reports.append(response.json())
        assert len(reports[-1]["created"]) == 5 and len(reports[-1]["refused"]) == 4
        assert "Укажите код аппликатора" in str(reports[-1]["refused"])
        if dry_run:
            assert _rows(references_api, "ob") == before
    assert reports[0]["created"] == reports[1]["created"] and reports[0]["refused"] == reports[1]["refused"]
    after = _rows(references_api, "ob")
    assert set(after) == {"ОБ.009", "СИ.004", "M10", "ОБ.107", "СИ.005", "ОБ.108", "ОБ.109"}
    assert after["M10"]["fields"]["Код в базе терминалов"] == "M10"
    found = references_api.get(BASE, params={"tab": "ob", "query": "M10"}).json()["records"]
    assert any(item["id"] == after["M10"]["id"] for item in found)
    repeated = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": row} for row in rows]})
    assert repeated.status_code == 200, repeated.text
    assert _rows(references_api, "ob") == after


def test_bulk_applicator_edit_preserves_links_and_refuses_invalid_updates(references_api):
    applicator = _unit("M10", "Аппликатор", "APP", kind="Аппликаторы")
    applicator["fields"]["Код в базе терминалов"] = "M10"
    _snapshot(references_api, [applicator, _unit("ОСН.001", "Матрица", "MAT", kind="Матрицы", tool="M10")],
        [_operation("PRESS", "M10")])
    before, links = _rows(references_api, "ob"), _links(references_api)
    response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
        "baseValue": "APP/Аппликатор", "Код в базе терминалов": "S3"}}]})
    assert response.status_code == 200, response.text
    after = _rows(references_api, "ob")
    assert after["S3"]["id"] == before["M10"]["id"]
    assert after["S3"]["equipment_model_id"] == before["M10"]["equipment_model_id"]
    assert after["ОСН.001"]["tool_unit_id"] == after["S3"]["id"]
    assert after["ОСН.001"]["fields"]["Для инструмента"] == "S3"
    assert _links(references_api) == links
    for code in ("", "X1"):
        refused = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
            "baseValue": "APP/Аппликатор", "Код в базе терминалов": code}}]})
        assert refused.status_code == 200, refused.text
        assert len(refused.json()["refused"]) == 1
        assert _rows(references_api, "ob") == after


def test_another_unit_joins_the_model_and_supporting_equipment_serves_units(references_api):
    _snapshot(references_api)
    units = _rows(references_api, "ob")
    copy = references_api.post(f"{BASE}/{units['ОБ.044']['id']}/duplicate").json()
    assert copy["equipment_model_id"] == units["ОБ.044"]["equipment_model_id"]

    compressor, station = units["ОБ.060"], units["ОБ.070.1"]
    served = references_api.put(f"{BASE}/{compressor['id']}/serves", json={"unit_ids": [station["id"]]})
    assert served.status_code == 200 and served.json()["serves_unit_ids"] == [station["id"]]
    assert _rows(references_api, "ob")["ОБ.060"]["serves_unit_ids"] == [station["id"]]
    not_supporting = references_api.put(f"{BASE}/{station['id']}/serves", json={"unit_ids": [compressor["id"]]})
    assert not_supporting.status_code == 422
    sessions = _sessions()
    with pytest.raises(LinkedEquipmentError, match="Обслуживает"):
        delete_record(UUID(station["id"]), ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions))

    role = references_api.put(f"/api/references/equipment-models/{station['equipment_model_id']}/role",
                              json={"role": "supporting"})
    assert role.status_code == 422  # SOLDER runs on it
    confirmed = references_api.put(f"/api/references/equipment-models/{units['ОБ.044']['equipment_model_id']}/role",
                                   json={"role": "operational"}).json()
    assert confirmed["role_confirmed"] is True
    moved = references_api.put(f"{BASE}/{units['ОБ.070.2']['id']}/equipment-model", json={"model_id": None})
    assert moved.status_code == 200 and moved.json()["equipment_model_id"] != station["equipment_model_id"]


def _mirror_state(sessions):
    with sessions() as session:
        ids = {(row.tab, row.mirror_key): row.id for row in session.scalars(
            select(TechOperationRecordRow).where(TechOperationRecordRow.tab.in_(("ob", "op"))))}
        links = {(link.operation_id, link.op_column, link.position, link.model_id)
                 for link in session.scalars(select(EquipmentOperationLinkRow))}
        models = {row.id for row in session.scalars(select(EquipmentModelRow))}
        tools = {(link.unit_id, link.target_id) for link in session.scalars(select(EquipmentUnitLinkRow))}
    return ids, links, models, tools


def test_two_mirror_loads_in_a_row_keep_every_id_and_link():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        load_mirror(default_source(), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
        first = _mirror_state(sessions)
        load_mirror(default_source(), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
        second = _mirror_state(sessions)
    finally:
        engine.dispose()
    ids, links, models, _tools = first
    assert len([key for key in ids if key[0] == "ob"]) == 184 and len(models) == 140
    assert len(links) == 77  # every «Машина» of the mirror names exactly one model
    assert second == first
