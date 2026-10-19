"""Коаксиальные разъёмы gsm-repiteri.ru → БД.КОАКС через POST /api/references/import/coax (owner 13.10.2026): тело
загрузки собрано из файла сайта и закоммичено, проверка (dry_run=1) показывает +N новых / M обновлённых и ничего не
пишет, у строки, которая уже есть в базе, меняется только карточка, повторная загрузка ничего не меняет."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.references.application import mirror_keys, TechRecordInput
from app.modules.references.coax_connectors import (
    EXPORT_FILE, GSM_FILE, GsmFileError, build_export, export_body, gsm_import_rows, mirror_pairs, pair_key,
    pin_type, shield_type,
)
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.importers import COAX_CARD_COLUMNS
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork, TechOperationRecordRow
from app.modules.references.technology import sync_schema_fields
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)

SITE = json.loads(GSM_FILE.read_text(encoding="utf-8"))
BODY = json.loads(EXPORT_FILE.read_text(encoding="utf-8"))
# A БД.КОАКС row as the mirror holds it: the technologist's lengths, «Тип пина» / «Тип экрана».
MIRROR_SHEET = {"Артикул": "T-111L", "Тип/Серия": "TNC-male", "Провод": "RG-316", "Производитель": "", "Программа": "",
                "D1": "0,5", "D2": "1,48", "D3": "1,95", "L1": "4", "L2": "2", "L3": "6", "L+": "", "L-": "",
                "Тип пина": "пайка", "Тип экрана": "обжим"}


def _entry(**fields) -> dict:
    return {"article": None, "full_name": "Разъём", "url": "https://gsm-repiteri.ru/x", "series": "SMA",
            "gender": "male", "polarity": "normal", "angle": "прямой", "mount": "обжимной", "pin": "пайка",
            "shield": "обжимной", "cable": ["RG-316"], "impedance_ohm": 50, "max_freq_ghz": 6.0, "material_body": None,
            "plating": None, "dims_mm": None, "manufacturer": None, "type_name": "SMA-male", "note": None, **fields}


def _site(*entries) -> dict:
    return {"version": 1, "source": "gsm-repiteri.ru", "checked_on": "2026-10-12", "connectors": list(entries)}


# --- the body ---

def test_the_committed_body_is_what_the_builder_makes_from_the_site_file():
    export = build_export()
    assert EXPORT_FILE.read_text(encoding="utf-8") == export_body(export), \
        "пересоберите: python -m app.modules.references.coax_connectors"
    assert export.connectors == len(SITE["connectors"]) == 311


def test_the_body_has_one_row_per_connector_and_cable_with_known_fields():
    rows = BODY["rows"]
    assert 311 < len(rows) <= bulk_import.MAX_IMPORT_ROWS  # one call
    keys = [pair_key(row["fields"]["coaxArticle"], row["fields"]["coaxWire"]) for row in rows]
    assert len(set(keys)) == len(keys)
    allowed = {"coaxArticle", "coaxWire", "coaxType", "coaxMfr", *COAX_CARD_COLUMNS}
    for row in rows:
        assert set(row["fields"]) <= allowed, row
        assert all(isinstance(value, str) and value.strip() for value in row["fields"].values()), row
        assert row["fields"].get("Тип пина", "пайка") == "пайка"  # the base's words only
        assert row["fields"].get("Тип экрана", "обжим") in ("обжим", "пайка", "прижим")
    sites = {row["fields"]["Ссылка"] for row in rows}
    assert len(sites) == 311 - 2  # S-231L and N-112C/5D are on the site twice: one row, the second page in the note


def test_a_pair_the_base_has_gets_only_its_card():
    existing = mirror_pairs()
    rows = [row["fields"] for row in BODY["rows"]]
    known = [fields for fields in rows if pair_key(fields["coaxArticle"], fields["coaxWire"]) in existing]
    assert len(known) == 21
    for fields in known:
        assert not {"coaxType", "coaxMfr", "Тип пина", "Тип экрана"} & set(fields), fields


# --- the rules ---

def test_an_entry_without_article_is_named_by_what_tells_it_apart_not_by_the_site_number():
    export = gsm_import_rows(_site(_entry(note="номер на сайте: 3148", cable=["RG-178", "RG-316"])))
    assert [row["fields"]["coaxArticle"] for row in export.rows] == ["SMA-male для RG-178/RG-316"] * 2
    assert [row["fields"]["coaxWire"] for row in export.rows] == ["RG-178", "RG-316"]
    named = gsm_import_rows(_site(
        _entry(type_name="F-male", mount="резьбовой", cable=["RG-11"], url="https://gsm-repiteri.ru/f"),
        _entry(type_name="U.FL-female", angle="угловой", cable=["RG-178"], url="https://gsm-repiteri.ru/u"),
        _entry(type_name="FAKRA-female (тип Z)", full_name="Разъём Fakra Z для кабеля RG-174 (с интегрированной иглой)",
               cable=["RG-174"], url="https://gsm-repiteri.ru/z"),
        _entry(type_name="SMA-female", full_name="Разъём SMA female (на корпус, резьба M16)", mount=None,
               cable=["RG-178"], url="https://gsm-repiteri.ru/s"),
    ))
    assert [row["fields"]["coaxArticle"] for row in named.rows] == [
        "F-male резьбовой для RG-11", "U.FL-female угловой для RG-178", "FAKRA-female (тип Z) с иглой для RG-174",
        "SMA-female на корпус для RG-178"]


def test_two_different_site_entries_with_one_name_keep_the_site_number_on_the_second():
    export = gsm_import_rows(_site(
        _entry(note="номер на сайте: 1", url="https://gsm-repiteri.ru/a"),
        _entry(note="номер на сайте: 2", url="https://gsm-repiteri.ru/b"),
    ))
    assert [row["fields"]["coaxArticle"] for row in export.rows] == ["SMA-male для RG-316", "SMA-male для RG-316 №2"]


def test_the_angled_twin_of_an_article_is_named_r_a_and_the_same_connector_twice_is_one_row():
    export = gsm_import_rows(_site(
        _entry(article="TS9", cable=["RG-316"]),
        _entry(article="TS9", angle="угловой", cable=["RG-174", "RG-316"]),
        _entry(article="S-231L", url="https://gsm-repiteri.ru/a"),
        _entry(article="S-231L", url="https://gsm-repiteri.ru/b"),
    ))
    names = [(row["fields"]["coaxArticle"], row["fields"]["coaxWire"]) for row in export.rows]
    assert names == [("TS9", "RG-316"), ("TS9 R/A", "RG-174"), ("TS9 R/A", "RG-316"), ("S-231L", "RG-316")]
    assert export.rows[-1]["fields"]["Примечание"] == "также на сайте: https://gsm-repiteri.ru/b"
    assert export.merged == ["S-231L / RG-316"]


def test_pin_and_shield_go_to_the_base_words_or_stay_empty():
    assert [pin_type(value) for value in ("пайка", "цанга", "не требуется", None)] == ["пайка", "", "", ""]
    assert [shield_type(value) for value in ("обжимной", "обжим (под клещи 0,475″)", "пайка", "RG-178: пайка",
                                            "прижимной", "накручивание на кабель", None)] == \
        ["обжим", "обжим", "пайка", "пайка", "прижим", "", ""]
    [row] = gsm_import_rows(_site(_entry(pin="цанга", shield="прижимной", max_freq_ghz=2.5))).rows
    assert "Тип пина" not in row["fields"] and row["fields"]["Тип экрана"] == "прижим"
    assert (row["fields"]["Центральный контакт"], row["fields"]["Крепление экрана"]) == ("цанга", "прижимной")
    assert row["fields"]["Частота до, ГГц"] == "2,5"
    assert row["fields"]["Источник данных"] == "gsm-repiteri.ru, 12.10.2026"


def test_a_file_of_another_shape_is_refused():
    with pytest.raises(GsmFileError):
        gsm_import_rows({"version": 2, "connectors": []})
    with pytest.raises(GsmFileError):
        gsm_import_rows([])


# --- through the API ---

@pytest.fixture()
def coax_api(refs_api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())
    sessions, tokens = refs_api
    fields = sync_schema_fields("coax", MIRROR_SHEET)
    [key] = mirror_keys([TechRecordInput(tab=TechOperationTab.COAX, display_text="TNC-male | RG-316", search_text="",
                                         source_sheet="БД.КОАКС", sort_key="", fields=fields)])
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    with uow:
        uow.tech_operations.save(TechOperationRecord.create(
            tab=TechOperationTab.COAX, display_text="TNC-male | RG-316", search_text="", source_sheet="БД.КОАКС",
            sort_key="", fields=fields, mirror_key=key))
        uow.commit()
    client = TestClient(app)

    def load(dry_run: int):
        return client.post("/api/references/import/coax", params={"dry_run": dry_run}, json=BODY,
                           headers=tokens["refs:write"])

    return sessions, load, tokens


def _coax(sessions) -> dict[tuple[str, str], dict]:
    with sessions() as session:
        rows = session.scalars(select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "coax")).all()
        return {pair_key(row.fields.get("coaxArticle"), row.fields.get("coaxWire")): dict(row.fields) for row in rows}


def test_the_check_reports_new_and_updated_rows_and_writes_nothing_then_the_apply_writes_them(coax_api):
    sessions, load, _tokens = coax_api
    before = _coax(sessions)

    checked = load(1)
    assert checked.status_code == 200, checked.text
    report = checked.json()
    total = len(BODY["rows"])
    assert (len(report["created"]), len(report["updated"]), report["unchanged"], report["refused"]) == \
        (total - 1, 1, 0, [])
    [update] = report["updated"]
    assert update["key"] == "T-111L / RG-316"
    assert set(update["changes"]) <= set(COAX_CARD_COLUMNS) - {"Тип пина", "Тип экрана"}
    assert update["changes"]["Серия"] == [None, "TNC"]
    assert _coax(sessions) == before

    applied = load(0)
    assert {**applied.json(), "dry_run": True} == report
    after = _coax(sessions)
    assert len(after) == total
    t111 = after[pair_key("T-111L", "RG-316")]
    assert {name: t111[name] for name in ("Тип пина", "Тип экрана", "L1", "L2", "L3", "coaxType")} == \
        {"Тип пина": "пайка", "Тип экрана": "обжим", "L1": "4", "L2": "2", "L3": "6", "coaxType": "TNC-male"}
    assert t111["Ссылка"].startswith("https://gsm-repiteri.ru/")
    created = after[pair_key("F-111A", "RG-6")]
    assert (created["coaxType"], created["Тип/Серия"], created["Провод"], created["Серия"]) == \
        ("F-male", "F-male", "RG-6", "F")  # the schema value reaches the sheet column the screen shows

    again = load(0).json()
    assert (again["created"], again["updated"], again["unchanged"]) == ([], [], total)
    assert _coax(sessions) == after


def test_the_schema_offers_the_card_columns_before_any_row_has_them(coax_api):
    _sessions, _load, tokens = coax_api
    schema = TestClient(app).get("/api/references/import/coax/schema", headers=tokens["refs:read"]).json()
    assert schema["key"] == ["coaxArticle", "coaxWire"]
    assert set(COAX_CARD_COLUMNS) <= set(schema["sheet_columns"])
    assert {"L1", "Тип пина"} <= set(schema["sheet_columns"])
