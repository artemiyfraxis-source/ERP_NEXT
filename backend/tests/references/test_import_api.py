"""POST /api/references/import/<table> (owner 12.10.2026: «парсить контакты, разъёмы вручную гемор»): per table a dry run
writes nothing and reports what the apply does, the second apply changes nothing, a bad row is refused with its reason
while the others apply, a stale `old` is refused, the audit names the token and the source; a token without
`refs:write` cannot load and no token deletes."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.references.application import (
    ReplaceSnapshotCommand, SaveGaugeCommand, TechRecordInput, mirror_keys, replace_snapshot_detailed, save_gauge,
)
from app.modules.references.cable_structure import HookupWire
from app.modules.references.technology import TECH_SNAPSHOT_SCHEMA_VERSION
from app.modules.references.domain import (
    BASE_COLUMNS, SUPPLIER_BY_LINK, ReferenceEntry, ReferenceKind, TechOperationRecord, TechOperationTab,
)
from app.modules.references.importers import IMPORT_TABLES
from app.modules.references.infrastructure import (
    ReferenceEntryRow, SqlAlchemyReferencesUnitOfWork, TechOperationRecordRow, WireGaugeRow,
)
from app.modules.references.technology import sync_schema_fields
from app.shared import bulk_import
from app.shared.bulk_import import MAX_IMPORT_ROWS, STALE_REASON, ImportRateLimiter
from app.shared.clock import moscow_now
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, _login, tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)

SEEDER = Actor(id=UUID(int=99), name="Сидер", department="ОГТ")
HOOKUPS = Path(__file__).with_name("hookup-wires.fixture.json")
TER_SHEET = {
    "Тип разъема": "Разъём", "Производитель": "JST", "Product Name": "ZH", "Series": "ZH", "Шаг разьема": "1,5",
    "Артикул контакта (REEL)": "ZHR-5", "Аппликатор": "AP-1", "От AWG": "28", "До AWG": "26",
}


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())


def _mirror_row(tab: TechOperationTab, display: str, fields: dict, sort: str = "") -> TechRecordInput:
    return TechRecordInput(tab=tab, display_text=display, search_text="", source_sheet="", sort_key=sort, fields=fields)


def _record(tab: TechOperationTab, display: str, fields: dict, sort: str = "") -> TechOperationRecord:
    """A row as the mirror load stores it (with its mirror key)."""
    [key] = mirror_keys([_mirror_row(tab, display, fields, sort)])
    return TechOperationRecord.create(tab=tab, display_text=display, search_text="", source_sheet="", sort_key=sort,
                                      fields=fields, mirror_key=key,
                                      cable_kind="wire" if tab == TechOperationTab.WIRE else None)


PROV_ROW = {**{name: "" for name in BASE_COLUMNS[TechOperationTab.WIRE]}, "Марка": "UL1061 28AWG",
            "Тип провода": "монтажный", "Цвета": "чёрный", "Сечение, мм2": "0,5", "AWG": "20"}


def _seed(sessions) -> None:
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    with uow:
        for record in (
            _record(TechOperationTab.TERMINAL, "JST | ZH | ZH", sync_schema_fields("ter", TER_SHEET)),
            _record(TechOperationTab.TERMINAL, "КВТ | НВИ 1.5 | НВИ", {
                "terManufacturer": "КВТ", "terSeries": "НВИ 1.5", "terComponent": "НВИ", "terArticle": "НВИ 1.5-3",
                "terApplicator": "M31"}),
            _record(TechOperationTab.COAX, "BNC-male | RG58", {
                "coaxArticle": "BNC-C58P", "coaxType": "BNC-male", "coaxMfr": "RUICHI", "coaxWire": "RG58"}),
            _record(TechOperationTab.WIRE, "UL1061 28AWG", PROV_ROW),
            _record(TechOperationTab.MATERIAL, "ТУТнг 4/2 черная", {
                **{name: "" for name in BASE_COLUMNS[TechOperationTab.MATERIAL]}, "Наименование": "ТУТнг 4/2 черная",
                "Цвет": "черный"}),
            _record(TechOperationTab.EQUIPMENT, "T21/Станция паяльная", {"obType": "Инструмент"}),
            _record(TechOperationTab.EQUIPMENT, "8620DX/Фен", {"obType": "Инструмент"}, "1"),
            _record(TechOperationTab.EQUIPMENT, "8620DX/Фен", {"obType": "Инструмент"}, "2"),
        ):
            uow.tech_operations.save(record)
        uow.references.replace((ReferenceEntry.create(ReferenceKind.GOST_AWG, "0,75", "18", 0),))
        uow.commit()
    save_gauge(SaveGaugeCommand(gauge_id=None, awg="20", section_mm2="0,5", conductor_d_mm="",
                                actor=SEEDER, now=moscow_now()), SqlAlchemyReferencesUnitOfWork(sessions))


def _dump(sessions) -> dict:
    with sessions() as session:
        return {
            "records": sorted((row.tab, row.display_text, json.dumps(row.fields, ensure_ascii=False, sort_keys=True),
                               row.edited_in_erp) for row in session.scalars(select(TechOperationRecordRow))),
            "entries": sorted((row.kind, row.key, row.value, row.position) for row in session.scalars(select(ReferenceEntryRow))),
            "gauges": sorted((row.awg or "", str(row.section_mm2), str(row.conductor_d_mm))
                             for row in session.scalars(select(WireGaugeRow))),
            "audit": len(session.scalars(select(AuditEventRow)).all()),
        }


def _imports(sessions) -> list[AuditEventRow]:
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type.like("references.%imported"))).all()


@pytest.fixture()
def loader(refs_api):  # noqa: F811
    sessions, tokens = refs_api
    _seed(sessions)
    client = TestClient(app)

    def load(table: str, rows: list[dict], *, dry_run: int | None = 0, scope: str = "refs:write", **params):
        query = {**({} if dry_run is None else {"dry_run": dry_run}), **params}
        return client.post(f"/api/references/import/{table}", params=query, json={"rows": rows}, headers=tokens[scope])

    return sessions, tokens, client, load


# (table, new row, update of a seeded row, its key in the report, the field it changes [old, new], a bad row, reason)
CASES = [
    ("ter", {"terArticle": "PHR-2", "terManufacturer": "JST", "terStep": "2"},
     {"terArticle": "ZHR 5", "terStep": "1,6"}, "ZHR 5", ("terStep", "1,5", "1,6"),
     {"terArticle": "X-1", "Цена": "5"}, "неизвестное поле: Цена"),
    ("coax", {"coaxArticle": "BNC-C58P", "coaxWire": "RG-174", "coaxType": "BNC-male"},
     {"coaxArticle": "bnc-c58p", "coaxWire": "RG 58", "coaxMfr": "Ruichi Co"}, "bnc-c58p / RG 58",
     ("coaxMfr", "RUICHI", "Ruichi Co"), {"coaxArticle": "SMA-1"}, "не заполнен ключ: coaxWire"),
    ("prov", {"Марка": "ПВАМ 0,75", "Тип провода": "силовой", "Сечение, мм2": 0.75},
     {"Марка": "UL1061-28AWG", "Цвета": "красный"}, "UL1061-28AWG", ("Цвета", "чёрный", "красный"),
     {"Марка": "МГТФ 0,2", "Цвета": "=A1"}, "формулы вводить нельзя"),
    ("mat", {"Наименование": "ТУТнг 6/3 черная", "Цвет": "черный"},
     {"Наименование": "тутнг 4/2  черная", "Цвет": "чёрный"}, "тутнг 4/2  черная", ("Цвет", "черный", "чёрный"),
     {"Наименование": "Стяжка", "Цвет": ["белый"]}, "ожидается текст или число"),
    ("ob", {"baseValue": "EW-50A/Автомат", "obType": "Станок"},
     {"baseValue": "t21/станция паяльная", "obType": "Станция"}, "t21/станция паяльная",
     ("obType", "Инструмент", "Станция"), {"baseValue": "8620DX/Фен", "obType": "Фен"}, "несколько строк"),
    ("suppliers", {"key": "https://www.terraelectronica.ru/catalog", "value": "ООО «Терраэлектроника»"},
     {"key": "chipdip.ru", "value": "АО «ЧИП и ДИП»"}, "chipdip.ru", ("value", 'АО "ЧИП И ДИП"', "АО «ЧИП и ДИП»"),
     {"key": "kroks.ru", "value": ""}, "Значение справочника обязательно"),
    ("countries", {"key": "Акме Коннект", "value": "Россия"},
     {"key": "Molex", "value": "Япония"}, "Molex", ("value", "США", "Япония"),
     {"key": "   ", "value": "Китай"}, "не заполнен ключ: key"),
    ("gost_awg", {"key": "0,35", "value": 22},
     {"key": "0.75", "value": "17"}, "0.75", ("value", "18", "17"),
     {"key": "0,5", "value": "21"}, "следует таблице сечений"),
    ("wire-gauges", {"awg": "22", "section_mm2": "0,35", "conductor_d_mm": "0,64"},
     {"awg": 20, "conductor_d_mm": "0.81"}, "20 AWG", ("conductor_d_mm", "", "0,81"),
     {"awg": "21", "section_mm2": "0.5"}, "Такое сечение уже есть"),
]
IDS = [case[0] for case in CASES]


def _rows(case) -> list[dict]:
    _table, new, update, _key, _change, bad, _reason = case
    return [{"fields": new}, {"fields": update}, {"fields": bad}]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_dry_run_writes_nothing_and_the_apply_does_what_it_reported(loader, case):
    sessions, _tokens, _client, load = loader
    table, new, _update, key, (field, old, value), _bad, reason = case
    before = _dump(sessions)

    checked = load(table, _rows(case), dry_run=None)  # no dry_run in the address: a check
    assert checked.status_code == 200, checked.text
    report = checked.json()
    assert report["dry_run"] is True
    assert len(report["created"]) == 1
    assert report["updated"] == [{"key": key, "changes": {field: [old, value]}}]
    assert report["unchanged"] == 0
    [refused] = report["refused"]
    assert reason in refused["reason"]
    assert _dump(sessions) == before

    applied = load(table, _rows(case), dry_run=0)
    assert applied.status_code == 200, applied.text
    assert {**applied.json(), "dry_run": True} == report
    after = _dump(sessions)
    assert after != before and after["audit"] == before["audit"] + 1

    again = load(table, _rows(case), dry_run=0).json()
    assert (again["created"], again["updated"], again["unchanged"]) == ([], [], 2)
    assert len(again["refused"]) == 1
    assert _dump(sessions) == after  # nothing written, no audit event


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_stale_old_value_is_refused_and_the_other_rows_apply(loader, case):
    sessions, _tokens, _client, load = loader
    table, new, update, key, (field, _old, _value), _bad, _reason = case
    report = load(table, [{"fields": new}, {"fields": update, "old": {field: "1"}}]).json()
    assert len(report["created"]) == 1 and report["updated"] == []
    assert report["refused"] == [{"key": key, "reason": STALE_REASON}]


def test_rows_written_to_a_base_are_marked_edited_in_erp_and_the_schema_value_reaches_its_column(loader):
    sessions, _tokens, _client, load = loader
    report = load("ter", [{"fields": {"terArticle": "ZHR 5", "terStep": "1,6"}},
                          {"fields": {"terArticle": "PHR-2", "terManufacturer": "JST", "terStep": "2"}}]).json()
    assert report["refused"] == [], report
    with sessions() as session:
        rows = {row.fields.get("terArticle"): row for row in session.scalars(
            select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "ter"))}
    zhr, phr = rows["ZHR-5"], rows["PHR-2"]
    assert zhr.edited_in_erp and phr.edited_in_erp and not rows["НВИ 1.5-3"].edited_in_erp
    # the generator reads terStep, «База данных» shows the sheet column: both moved, the stored article kept its spelling
    assert (zhr.fields["terStep"], zhr.fields["Шаг разьема"]) == ("1,6", "1,6")
    assert phr.fields["Артикул контакта (REEL)"] == "PHR-2" and phr.fields["Шаг разьема"] == "2"
    assert set(TER_SHEET) <= set(phr.fields) and phr.display_text == "JST"


def test_a_mirror_reload_keeps_the_loaded_row(loader):
    sessions, _tokens, _client, load = loader
    assert load("prov", [{"fields": {"Марка": "UL1061 28AWG", "Цвета": "красный"}}]).json()["updated"]
    _count, skipped = replace_snapshot_detailed(ReplaceSnapshotCommand(
        schema_version=TECH_SNAPSHOT_SCHEMA_VERSION, source_updated_at=None,
        records=(_mirror_row(TechOperationTab.WIRE, "UL1061 28AWG", PROV_ROW),), actor=SEEDER, now=moscow_now(),
        keep_other_tabs=True, preserve_erp_edits=True,
    ), SqlAlchemyReferencesUnitOfWork(sessions))
    assert skipped == {"prov": 1}
    with sessions() as session:
        [row] = session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "prov")).all()
    assert row.fields["Цвета"] == "красный" and row.edited_in_erp


def test_a_structured_hookup_wire_follows_its_loaded_cells(loader):
    sessions, _tokens, _client, load = loader
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    with uow:
        [gauge] = uow.cable_structure.gauges()
        uow.cable_structure.save_hookup(HookupWire.create(mark="UL1061 28AWG", series="UL1061", gauge_id=gauge.id,
                                                          outer_d_mm="0,89"), moscow_now())
        uow.commit()
    report = load("prov", [{"fields": {"Марка": "UL1061 28AWG", "Наружный диаметр, мм": "0,9"}}]).json()
    assert [item["key"] for item in report["updated"]] == ["UL1061 28AWG"]
    with uow:
        wire = uow.cable_structure.hookup("ul106128awg")
        record = next(item for item in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10))
    assert wire.outer_d_mm == Decimal("0.9") and record.fields["Наружный диаметр, мм"] == "0,9"


def test_a_schema_value_and_its_column_with_different_values_refuse_the_row(loader):
    _sessions, _tokens, _client, load = loader
    report = load("ter", [{"fields": {"terArticle": "ZHR-5", "terStep": "1,6", "Шаг разьема": "1,7"}}]).json()
    assert report["updated"] == [] and "одно и то же поле" in report["refused"][0]["reason"]


def test_two_spellings_of_one_article_in_a_load_are_one_row(loader):
    _sessions, _tokens, _client, load = loader
    report = load("ter", [{"fields": {"terArticle": "ZHR-5", "terStep": "1,6"}},
                          {"fields": {"terArticle": "zhr 5", "terStep": "1,7"}},
                          {"fields": {"terArticle": "PHR-2"}}, {"fields": {"terArticle": "PHR 2"}}]).json()
    assert [item["key"] for item in report["updated"]] == ["ZHR-5"] and report["created"] == ["PHR-2"]
    assert sorted(item["key"] for item in report["refused"]) == ["PHR 2", "zhr 5"]


def test_the_audit_names_the_token_and_the_source(loader):
    sessions, _tokens, _client, load = loader
    assert load("coax", [{"fields": {"coaxArticle": "N-1", "coaxWire": "RG58"}}]).status_code == 200
    assert load("coax", [{"fields": {"coaxArticle": "N-2", "coaxWire": "RG58"}}], source="warehouse").status_code == 200
    events = _imports(sessions)
    assert [(event.actor_name, event.details["source"], event.details["table"]) for event in events] == [
        ("API: Загрузчик refs:write", "api", "coax"), ("API: Загрузчик refs:write", "warehouse", "coax"),
    ]
    assert load("coax", [{"fields": {"coaxArticle": "N-3", "coaxWire": "RG58"}}], source="google").status_code == 422


def test_a_token_without_refs_write_cannot_load_and_a_read_token_reads_the_schema(loader):
    sessions, tokens, client, load = loader
    before = _dump(sessions)
    for scope in ("refs:read", "tcgen"):
        for table in IMPORT_TABLES:
            assert load(table, [{"fields": {"key": "x"}}], scope=scope).status_code == 403, (scope, table)
    assert _dump(sessions) == before
    schema = client.get("/api/references/import/ter/schema", headers=tokens["refs:read"])
    assert schema.status_code == 200
    body = schema.json()
    assert body["key"] == ["terArticle"] and "terStep" in body["fields"] and "Шаг разьема" in body["sheet_columns"]
    assert "connType" not in body["fields"]  # not stored: only through its sheet column
    assert client.get("/api/references/import/ter/schema", headers=tokens["tcgen"]).status_code == 403
    assert client.get("/api/references/import/nope/schema", headers=tokens["refs:read"]).status_code == 404


@pytest.mark.parametrize("table", IMPORT_TABLES)
def test_every_table_has_a_schema(loader, table):
    _sessions, tokens, client, _load = loader
    body = client.get(f"/api/references/import/{table}/schema", headers=tokens["refs:read"]).json()
    assert body["table"] == table and body["key"] and body["label"]


def test_no_token_deletes_a_reference_row(loader):
    sessions, tokens, _client, _load = loader
    with sessions() as session:
        gauge_id = session.scalars(select(WireGaugeRow.id)).one()
    client = TestClient(app)
    for scope in ("refs:write", "refs:read"):
        assert client.delete(f"/api/references/wire-gauges/{gauge_id}", headers=tokens[scope]).status_code == 403
    admin = _login("admin")
    assert admin.delete(f"/api/references/wire-gauges/{gauge_id}", headers=_csrf(admin)).status_code == 204


def test_a_person_with_reference_edit_rights_loads_through_the_same_endpoint(loader):
    sessions, _tokens, _client, _load = loader
    lead = _login("techlead")
    response = lead.post("/api/references/import/mat?dry_run=0", headers=_csrf(lead),
                         json={"rows": [{"fields": {"Наименование": "Стяжка 100", "Цвет": "белый"}}]})
    assert response.status_code == 200 and response.json()["created"] == ["Стяжка 100"]
    assert [event.actor_name for event in _imports(sessions)] == ["Технолог"]
    operator = _login("operator")
    refused = operator.post("/api/references/import/mat?dry_run=0", headers=_csrf(operator),
                            json={"rows": [{"fields": {"Наименование": "Стяжка 200"}}]})
    assert refused.status_code == 403


def test_the_first_supplier_load_stores_the_seed_the_screen_showed(loader):
    _sessions, tokens, _client, load = loader
    assert load("suppliers", [{"fields": {"key": "terraelectronica.ru", "value": "Терра"}}]).status_code == 200
    admin = _login("admin")
    suppliers = {item["key"]: item["value"] for item in admin.get("/api/references/maps").json()["suppliers"]}
    assert suppliers == {**dict(SUPPLIER_BY_LINK), "terraelectronica.ru": "Терра"}


def test_a_size_writes_its_gost_awg_and_is_matched_by_awg_else_by_section(loader):
    sessions, _tokens, _client, load = loader
    report = load("wire-gauges", [{"fields": {"awg": "22", "section_mm2": "0,35"}},
                                  {"fields": {"section_mm2": "0,07"}},
                                  {"fields": {"section_mm2": "0.50", "conductor_d_mm": "0,8"}},
                                  {"fields": {"conductor_d_mm": "1"}},
                                  {"fields": {"awg": "24", "strands": "7×0,2"}}]).json()
    assert report["created"] == ["22 AWG", "0,07 мм²"]
    assert report["updated"] == [{"key": "0,5 мм²", "changes": {"conductor_d_mm": ["", "0,8"]}}]
    assert report["refused"] == [{"key": "#4", "reason": "не заполнен ключ: AWG или сечение"},
                                 {"key": "24 AWG", "reason": "неизвестное поле: strands"}]  # dropped in 0114
    with sessions() as session:
        gost = {row.key: row.value for row in session.scalars(
            select(ReferenceEntryRow).where(ReferenceEntryRow.kind == "gost_awg"))}
    assert gost == {"0,75": "18", "0,5": "20", "0,35": "22"}


def test_the_call_is_refused_whole_when_too_large_or_malformed(loader):
    _sessions, _tokens, _client, load = loader
    assert load("mat", [{"fields": {"Наименование": f"М{i}"}} for i in range(MAX_IMPORT_ROWS + 1)]).status_code == 413
    assert load("mat", []).status_code == 422
    assert load("mat", [{"fields": {}}]).status_code == 422
    assert load("nope", [{"fields": {"key": "x"}}]).status_code == 404


def test_the_hookup_file_loads_through_the_same_endpoint_with_its_own_report(loader):
    sessions, tokens, client, _load = loader
    payload = json.loads(HOOKUPS.read_text(encoding="utf-8"))
    before = _dump(sessions)

    def post(dry_run: int):
        response = client.post(f"/api/references/import/hookups?dry_run={dry_run}", json=payload,
                               headers=tokens["refs:write"])
        assert response.status_code == 200, response.text
        return response.json()

    checked = post(1)
    assert checked["dry_run"] is True and checked["created"] and "gauges_added" in checked and "counts" in checked
    assert _dump(sessions) == before
    applied = post(0)
    assert {**applied, "dry_run": True} == checked
    [event] = _imports(sessions)
    assert (event.actor_name, event.details["source"]) == ("API: Загрузчик refs:write", "api")
    again = post(0)
    assert (again["created"], again["updated"], again["gauges_added"]) == ([], [], [])
    assert len(_imports(sessions)) == 1
    bad = client.post("/api/references/import/hookups?dry_run=0", json={"version": 2},
                      headers=tokens["refs:write"])
    assert bad.status_code == 422
    assert client.post("/api/references/import/hookups", json=payload, headers=tokens["refs:read"]).status_code == 403
