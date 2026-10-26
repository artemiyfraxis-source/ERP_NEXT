"""БД.ТЕР contact card by protocol v5.2 (owner 12.10.2026, «карточка контакта»): Male / Female, the BAG article of
the same record, insulation crimp and OD, «Металл / Покрытие», max current, datasheet — derived from the sheet
columns; a terminal is found by REEL or BAG article; a load never writes the technologist's fields and brings units
to the base's; the mirror gives the load's payload, and its second apply changes nothing."""

import json
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import TECHNOLOGIST_REASON, import_rows, technologist_field
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork, TechOperationRecordRow
from app.modules.references.load_mirror import ACTOR, default_source, load_mirror, main, ter_import_rows
from app.modules.references.technology import (
    TER_CARD_KEYS, TerminalRecord, contact_gender, matching_terminal_records, normalize_material, pull_force_newtons,
    resolve_terminal, sync_schema_fields, wire_section,
)
from app.shared.bulk_import import ImportRowIn
from app.shared.persistence import create_schema
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)
from tests.references.test_import_api import TER_SHEET, _record, fresh_limiter, loader  # noqa: F401  (fixtures)

NOW = datetime(2026, 10, 12, 12, 0)


@pytest.mark.parametrize("raw, gender", [
    ("Male (Pin)", "Male"), ("Male (tab)", "Male"), ("Male (Pin) — см. notes", "Male"), ("male", "Male"),
    ("Female", "Female"), ("Female (Socket)", "Female"), ("Гнездо", "Female"), ("Штырь", "Male"),
    ("Обжимной контакт", ""), ("Кольцевой наконечник", ""), ("", ""),
])
def test_contact_type_becomes_male_or_female_and_a_type_without_a_gender_gives_none(raw, gender):
    assert contact_gender(raw) == gender


def test_material_pull_force_units_follow_the_protocol():
    assert normalize_material("Brass/Tin") == "Brass / Tin"
    assert normalize_material("Phosphor  Bronze /Gold 0,76µm") == "Phosphor Bronze / Gold 0,76µm"
    # kgf ×9.8, lbf ×4.448, rounded to 0,1 N; AWG labels and wire marks are not forces
    assert pull_force_newtons("28 AWG: 2 kgf\n26-24 AWG: 3,5 кгс (UL1007)") == "28 AWG: 19,6\n26-24 AWG: 34,3 (UL1007)"
    assert pull_force_newtons("10 lbf") == "44,5"
    assert pull_force_newtons("2kgf") == "19,6"
    assert pull_force_newtons("28 AWG: 9,8; 26 AWG: 19,6") == "28 AWG: 9,8; 26 AWG: 19,6"  # already N: untouched


def test_card_keys_come_from_the_sheet_columns_and_nothing_is_guessed():
    fields = sync_schema_fields("ter", {
        "Артикул контакта (REEL)": "39000038", "Артикул контакта (BAG)": "39000039", "Тип контакта": "Female (Socket)",
        "Высота обжима изоляции, мм": "", "Материал контакта": "Brass/Tin", "Максисмальная сила тока, А": "9",
        "Ссылка на DATASHEET на сайте производителя": "https://www.molex.com/x", "От AWG": "24", "До AWG": "18",
    })
    assert {key: fields[key] for key in TER_CARD_KEYS if key in fields} == {
        "terGender": "Female", "terArticleBag": "39000039", "terInsCrimpHeight": "", "terMaterial": "Brass / Tin",
        "terMaxCurrent": "9", "terDatasheet": "https://www.molex.com/x",
    }
    assert fields["Артикул контакта (BAG)"] == "39000039" and fields["terArticle"] == "39000038"
    assert "mm2From" not in fields  # mm² is not computed from AWG


def test_a_bag_article_finds_the_same_terminal_record():
    card = TerminalRecord.from_fields({"terArticle": "39000038", "terArticleBag": "39000039", "terApplicator": "M5",
                                       "terCrimpHeight": "1,8", "awgFrom": "24", "awgTo": "18"})
    # a row stored before terArticleBag existed: its sheet column gives the BAG article
    legacy = TerminalRecord.from_fields({"terArticle": "5556-T", "Артикул контакта (BAG)": "5556-TL",
                                         "terApplicator": "M6"})
    other = TerminalRecord.from_fields({"terArticle": "39000040"})
    records = (card, legacy, other)
    assert matching_terminal_records(records, "39000039") == (card,)
    assert matching_terminal_records(records, "Molex 39000039") == (card,)
    assert matching_terminal_records(records, "5556 tl") == (legacy,)
    assert legacy.article_bag == "5556-TL"  # stored spelling kept
    resolved = resolve_terminal(records, "39000039", (wire_section("UL1007 22 AWG", {}),))
    assert (resolved.applicator, resolved.crimp_height) == ("M5", "1,8")


# --- the load: POST /api/references/import/ter ---

def _ter(sessions) -> dict[str, TechOperationRecordRow]:
    with sessions() as session:
        return {row.fields.get("terArticle"): row for row in session.scalars(
            select(TechOperationRecordRow).where(TechOperationRecordRow.tab == "ter"))}


def test_a_load_by_the_bag_article_updates_the_reel_row(loader):  # noqa: F811
    sessions, _tokens, _client, load = loader
    assert load("ter", [{"fields": {"terArticle": "ZHR-5", "terArticleBag": "ZHR-5-B"}}]).json()["updated"]
    report = load("ter", [{"fields": {"terArticle": "zhr 5 b", "terStep": "1,7"}}]).json()
    assert report["created"] == [] and [item["key"] for item in report["updated"]] == ["zhr 5 b"]
    rows = _ter(sessions)
    assert (rows["ZHR-5"].fields["terStep"], rows["ZHR-5"].fields["terArticleBag"]) == ("1,7", "ZHR-5-B")
    assert "zhr 5 b" not in rows


@pytest.mark.parametrize("field", ["terApplicator", "Аппликатор", "mm2From", "mm2To"])
def test_the_load_never_writes_the_technologists_fields(loader, field):  # noqa: F811
    sessions, _tokens, _client, load = loader
    before = _ter(sessions)["ZHR-5"].fields
    report = load("ter", [{"fields": {"terArticle": "ZHR-5", field: "S5"}},
                          {"fields": {"terArticle": "PHR-2", "terManufacturer": "JST"}}]).json()
    assert report["created"] == ["PHR-2"]
    assert report["refused"] == [{"key": "ZHR-5", "reason": f"{field}: {TECHNOLOGIST_REASON}"}]
    assert _ter(sessions)["ZHR-5"].fields == before


@pytest.mark.parametrize("name", ["Аппликатор", "Пневма Автомат", "ОТР", "С зачисткой", "От мм2", "До мм2",
                                  "terApplicator", "mm2From"])
def test_technologist_columns_are_recognised_by_their_header(name):
    assert technologist_field(name)


@pytest.mark.parametrize("name", ["terArticle", "Артикул контакта (REEL)", "От AWG", "Длина зачистки, мм",
                                  "Высота обжима изоляции, мм", "terStrip"])
def test_other_columns_are_not_the_technologists(name):
    assert not technologist_field(name)


def test_the_load_brings_units_to_the_base_and_a_second_apply_changes_nothing(loader):  # noqa: F811
    sessions, _tokens, _client, load = loader
    rows = [{"fields": {"terArticle": "ZHR-5", "terPullForceMin": "28 AWG: 2 kgf", "awgFrom": "#30",
                        "terMaterial": "Brass/Tin", "terGender": "Female (Socket)", "terInsCrimpHeight": ""}}]
    report = load("ter", rows).json()
    assert report["refused"] == [], report
    zhr = _ter(sessions)["ZHR-5"].fields
    assert (zhr["terPullForceMin"], zhr["awgFrom"], zhr["terMaterial"]) == ("28 AWG: 19,6", "30", "Brass / Tin")
    # the base had no «Тип контакта»: the gender is the type now, and read back from it
    assert (zhr["terType"], zhr["terGender"]) == ("Female", "Female")
    assert zhr["От AWG"] == "30" and not zhr.get("mm2From") and not zhr.get("terInsCrimpHeight")  # nothing computed
    again = load("ter", rows).json()
    assert (again["created"], again["updated"], again["unchanged"], again["refused"]) == ([], [], 1, [])


def test_a_gender_against_the_contact_type_given_with_it_is_refused(loader):  # noqa: F811
    sessions, _tokens, _client, load = loader
    report = load("ter", [{"fields": {"terArticle": "ZHR-5", "terType": "Male (Pin)", "terGender": "Female"}}]).json()
    assert report["updated"] == [] and "расходятся" in report["refused"][0]["reason"]
    # a row with the «Тип контакта» column: the type given in the load, not the stored one, is what the gender meets
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    with uow:
        uow.tech_operations.save(_record(TechOperationTab.TERMINAL, "JST | PH | PH", sync_schema_fields("ter", {
            **TER_SHEET, "Артикул контакта (REEL)": "PHR-2", "Тип контакта": "Female (Socket)"})))
        uow.commit()
    report = load("ter", [{"fields": {"terArticle": "PHR-2", "terType": "Male (Pin)", "terGender": "Male"}}]).json()
    assert report["refused"] == [], report
    assert (_ter(sessions)["PHR-2"].fields["Тип контакта"], _ter(sessions)["PHR-2"].fields["terGender"]) == (
        "Male (Pin)", "Male")


# --- the mirror → the load's payload ---

@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def test_the_mirror_export_carries_the_card_and_never_the_technologists_fields():
    export = ter_import_rows(default_source())
    assert len(export.rows) > 400 and export.skipped  # rows without a REEL article have no import key
    for row in export.rows:
        fields = row["fields"]
        assert fields["terArticle"] and all(fields.values())  # empty cells are left out, never sent as ""
        assert not any(technologist_field(name) for name in fields)
        assert not set(fields) & {"terCrimpHeight", "terPullForceMin", "terStrip", "awgFrom"}  # ERP keeps these
    [mini_fit] = [row["fields"] for row in export.rows if row["fields"]["terArticle"] == "39000038"]
    assert mini_fit["terArticleBag"] == "39000039" and mini_fit["terGender"] == "Female"
    assert mini_fit["terMaterial"] == "Brass / Tin" and mini_fit["terMaxCurrent"] == "9"


def test_the_export_flag_writes_the_load_body_without_a_database(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    target = tmp_path / "ter.json"
    assert main(["--export-ter", str(target)]) == 0
    body = json.loads(target.read_text(encoding="utf-8"))
    assert body["rows"] == ter_import_rows(default_source()).rows
    assert "БД.ТЕР" in capsys.readouterr().out


def test_the_mirror_export_fills_rows_stored_before_the_card_and_its_second_apply_changes_nothing(sessions):
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    load_mirror(default_source(), uow, dry_run=False, now=NOW)
    with uow:  # the base as production holds it: loaded before the card keys existed
        for record in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000):
            uow.tech_operations.save(replace(record, fields={
                key: value for key, value in record.fields.items() if key not in TER_CARD_KEYS}))
        uow.commit()
    rows = [ImportRowIn(**row) for row in ter_import_rows(default_source()).rows]

    def apply():
        return import_rows("ter", SqlAlchemyReferencesUnitOfWork(sessions), rows, ACTOR, NOW, dry_run=False,
                           source="api", limiter=None)

    first = apply()
    assert first.refused == [] and first.created == [] and len(first.updated) > 300
    assert {name for item in first.updated for name in item.changes} <= set(TER_CARD_KEYS)
    second = apply()
    assert (second.created, second.updated, second.refused, second.unchanged) == ([], [], [], len(rows))
    with uow:
        records = [TerminalRecord.from_fields(record.fields)
                   for record in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)]
    [found] = matching_terminal_records(records, "39000039")  # the BAG article of the Mini-Fit Jr. contact
    assert found.article == "39000038" and found.gender == "Female"


def test_jst_reel_suffix_n_finds_the_same_contact():
    """JST «(N)» — пометка катушки, контакт тот же (394К.10: SYM-001T-P0.6(N) → SYM-001T-P0.6)."""
    card = TerminalRecord.from_fields({"terArticle": "SYM-001T-P0.6", "awgFrom": "28", "awgTo": "22"})
    other = TerminalRecord.from_fields({"terArticle": "SYM-001T-P0.6A", "awgFrom": "28", "awgTo": "22"})
    assert matching_terminal_records((other, card), "SYM-001T-P0.6(N)") == (card,)
    assert matching_terminal_records((other, card), "SYM-001T-P0.6 (n)") == (card,)
    assert matching_terminal_records((other, card), "SYM-001T-P0.6N") == ()
