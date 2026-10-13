"""БД.ТЕР contact card held by the database (migration 0115, owner 13.10.2026): gender Male / Female or empty,
insulation Ø a positive number with from ≤ to, datasheet a http(s) link — the load and a screen edit refuse such a value
in words, the database CHECKs refuse it from anywhere; the screen gets the card with every record."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.references.application import (
    CreateRecordCommand, EditRecordCommand, FieldEdit, create_record, edit_record,
)
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR
from app.modules.references.technology import terminal_card, terminal_card_problems
from app.shared.persistence import create_schema
from tests.identity.test_api_tokens import tokens_api  # noqa: F401  (fixture)
from tests.identity.test_refs_tokens import refs_api  # noqa: F401  (fixture)
from tests.references.test_contact_card import _ter
from tests.references.test_import_api import TER_SHEET, fresh_limiter, loader  # noqa: F401  (fixtures)

NOW = datetime(2026, 10, 13, 12, 0)
OD_FROM, OD_TO, SHEET = "Диаметр изоляции от, мм", "Диаметр изоляции до, мм", "Ссылка на DATASHEET на сайте производителя"


@pytest.mark.parametrize("fields, problem", [
    ({"terGender": "Pin"}, "Пол контакта"),
    ({"terInsOdFrom": "abc"}, "Диаметр изоляции от"),
    ({"terInsOdTo": "0"}, "Диаметр изоляции до"),
    ({"terInsOdFrom": "1,9", "terInsOdTo": "1.2"}, "«от» 1,9 больше «до» 1.2"),
    ({OD_FROM: "3", OD_TO: "2"}, "больше «до»"),  # a row stored before the card keys: its sheet columns
    ({"terDatasheet": "www.molex.com/x"}, "Даташит"),
])
def test_a_card_value_the_database_would_refuse_is_named(fields, problem):
    [found] = terminal_card_problems(fields)
    assert problem in found


@pytest.mark.parametrize("fields", [
    {}, {"terGender": "Female", "terInsOdFrom": "1,3", "terInsOdTo": "2.5", "terDatasheet": "https://www.molex.com/x"},
    {"terInsOdTo": "1,7"},  # only «до» (КВТ НКИ) — null-spec: the other side stays empty
    {"terInsOdFrom": "9", "terInsOdTo": "10"},  # numbers, not text: 9 ≤ 10
    {"Тип контакта": "Кольцевой наконечник"},  # a lug has no gender
])
def test_a_card_within_the_protocol_passes(fields):
    assert terminal_card_problems(fields) == []


# --- the database ---

@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _insert(session: Session, tab: str, fields: dict) -> None:
    session.execute(text(
        "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields)"
        " VALUES (:id, :tab, 'x', 'x', '', 'x', :fields)"), {"id": uuid4().hex, "tab": tab, "fields": json.dumps(fields)})


@pytest.mark.parametrize("fields", [
    {"terGender": "Pin"}, {"terInsOdFrom": "3", "terInsOdTo": "2"}, {"terInsOdFrom": "abc"}, {"terInsOdTo": "0"},
    {"terInsOdTo": "1,2,3"}, {"terDatasheet": "www.molex.com"},
])
def test_the_database_refuses_a_bad_card_whoever_writes_it(sessions, fields):
    with sessions() as session, pytest.raises(IntegrityError):
        _insert(session, "ter", fields)
        session.commit()


def test_the_database_keeps_good_cards_and_other_bases_untouched(sessions):
    with sessions() as session:
        for fields in ({}, {"terGender": "Male", "terInsOdFrom": "0,8", "terInsOdTo": "1,5", "terDatasheet": "https://x"},
                       {"terGender": ""}, {"terInsOdTo": "10"}):
            _insert(session, "ter", fields)
        _insert(session, "op", {"terGender": "Pin", "terDatasheet": "нет"})  # the card CHECKs are БД.ТЕР's only
        session.commit()
        assert session.execute(text("SELECT count(*) FROM tech_operation_records")).scalar() == 5


def test_a_screen_edit_or_new_record_with_a_bad_card_is_refused_in_words(sessions):
    uow = SqlAlchemyReferencesUnitOfWork(sessions)
    record = create_record(CreateRecordCommand(TechOperationTab.TERMINAL, {**TER_SHEET, OD_FROM: "1,2", OD_TO: "2"},
                                               ACTOR, NOW), uow)
    with pytest.raises(ReferenceValidationError, match="больше «до»"):
        edit_record(EditRecordCommand(record.id, (FieldEdit(OD_FROM, "1,2", "2,5"),), ACTOR, NOW),
                    SqlAlchemyReferencesUnitOfWork(sessions))
    with pytest.raises(ReferenceValidationError, match="Даташит"):
        create_record(CreateRecordCommand(TechOperationTab.TERMINAL, {**TER_SHEET, SHEET: "molex.com"}, ACTOR, NOW),
                      SqlAlchemyReferencesUnitOfWork(sessions))
    edited = edit_record(EditRecordCommand(record.id, (FieldEdit(OD_TO, "2", "2,5"),), ACTOR, NOW),
                         SqlAlchemyReferencesUnitOfWork(sessions))
    assert (edited.fields["terInsOdFrom"], edited.fields["terInsOdTo"]) == ("1,2", "2,5")


# --- the load: POST /api/references/import/ter ---

def test_the_load_refuses_a_bad_card_row_and_writes_the_rest(loader):  # noqa: F811
    sessions, _tokens, _client, load = loader
    report = load("ter", [
        {"fields": {"terArticle": "ZHR-5", "terGender": "Ring"}},
        {"fields": {"terArticle": "PHR-2", "terInsOdFrom": "1,9", "terInsOdTo": "1,2"}},
        {"fields": {"terArticle": "SHR-1", "terDatasheet": "jst-mfg.com/x"}},
        {"fields": {"terArticle": "39000038", "terArticleBag": "39000039", "terGender": "Female (Socket)",
                    "terInsOdFrom": "1,3", "terInsOdTo": "3,1", "terMaterial": "Brass/Tin", "terMaxCurrent": "9",
                    "terDatasheet": "https://www.molex.com/x", "terInsCrimpHeight": "24 AWG: 2,21"}},
    ]).json()
    refused = {item["key"]: item["reason"] for item in report["refused"]}
    assert set(refused) == {"ZHR-5", "PHR-2", "SHR-1"}, report
    assert "Male или Female" in refused["ZHR-5"] and "больше «до»" in refused["PHR-2"] and "Даташит" in refused["SHR-1"]
    assert report["created"] == ["39000038"]
    rows = _ter(sessions)
    assert "PHR-2" not in rows and "SHR-1" not in rows
    card = rows["39000038"].fields
    assert (card["terGender"], card["terInsOdFrom"], card["terInsOdTo"]) == ("Female", "1,3", "3,1")


def test_the_load_checks_the_given_value_against_the_stored_one(loader):  # noqa: F811
    sessions, _tokens, _client, load = loader
    assert load("ter", [{"fields": {"terArticle": "ZHR-5", "terInsOdTo": "1,1"}}]).json()["refused"] == []
    report = load("ter", [{"fields": {"terArticle": "ZHR-5", "terInsOdFrom": "1,4"}}]).json()
    assert [item["key"] for item in report["refused"]] == ["ZHR-5"]
    assert not _ter(sessions)["ZHR-5"].fields.get("terInsOdFrom")


def test_the_screen_gets_the_card_with_every_terminal(loader):  # noqa: F811
    _sessions, tokens, client, load = loader
    load("ter", [{"fields": {"terArticle": "ZHR-5", "terArticleBag": "ZHR-5-B", "terGender": "Female",
                             "terInsOdFrom": "0,8", "terInsOdTo": "1,1", "terDatasheet": "https://www.jst-mfg.com/x"}}])
    response = client.get("/api/references/tech-operations", params={"tab": "ter"}, headers=tokens["refs:read"])
    [card] = [item["card"] for item in response.json()["records"] if item["fields"].get("terArticle") == "ZHR-5"]
    assert {key: card[key] for key in ("article", "article_bag", "gender", "ins_od_from", "ins_od_to", "datasheet",
                                       "maker", "series", "awg_from", "awg_to", "applicator")} == {
        "article": "ZHR-5", "article_bag": "ZHR-5-B", "gender": "Female", "ins_od_from": "0,8", "ins_od_to": "1,1",
        "datasheet": "https://www.jst-mfg.com/x", "maker": "JST", "series": "ZH", "awg_from": "28", "awg_to": "26",
        "applicator": "AP-1",
    }
    others = client.get("/api/references/tech-operations", params={"tab": "op"}, headers=tokens["refs:read"]).json()
    assert all("card" not in item for item in others["records"])


def test_the_card_of_a_row_stored_before_the_card_keys_comes_from_its_sheet_columns():
    card = terminal_card({"terArticle": "5556-T", "Артикул контакта (BAG)": "5556-TL", "terType": "Male (Pin)",
                          OD_FROM: "1,3", OD_TO: "3,1", "terManufacturer": "Molex"})
    assert (card["article_bag"], card["gender"], card["ins_od_from"], card["ins_od_to"], card["maker"]) == (
        "5556-TL", "Male", "1,3", "3,1", "Molex")


# --- migration 0115 over existing rows ---

def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_migration_brings_the_gender_over_keeps_every_row_and_checks_new_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, "0112_harness_route_v3")
    rows = {
        "t1": ("ter", {"terArticle": "A", "terType": "Обжимной контакт", "terGender": "Обжимной контакт"}),
        "t2": ("ter", {"terArticle": "B", "terGender": "Socket"}),
        "t3": ("ter", {"terArticle": "C", "terGender": "Male", "terInsOdFrom": "3", "terInsOdTo": "2"}),  # kept as is
        "t4": ("ter", {"terArticle": "D"}),
        "o1": ("op", {"terGender": "что угодно"}),
    }
    with sqlite3.connect(database) as connection:
        for name, (tab, fields) in rows.items():
            connection.execute(
                "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                " fields, mirror_key, edited_in_erp) VALUES (?, ?, ?, '', '', '', ?, '', 0)",
                (uuid4().hex, tab, name, json.dumps(fields, ensure_ascii=False)))

    command.upgrade(config, "0115_terminal_contact_card")
    with sqlite3.connect(database) as connection:
        stored = {row[0]: json.loads(row[1]) for row in connection.execute(
            "SELECT display_text, fields FROM tech_operation_records")}
        assert set(stored) == set(rows)
        assert stored["t1"] == {**rows["t1"][1], "terGender": ""}  # «Тип контакта» stays as it was
        assert stored["t2"]["terGender"] == "Female"
        assert stored["t3"] == rows["t3"][1] and stored["t4"] == rows["t4"][1] and stored["o1"] == rows["o1"][1]
        try:
            connection.execute("UPDATE tech_operation_records SET fields = ? WHERE display_text = 't4'",
                               (json.dumps({"terArticle": "D", "terGender": "Pin"}),))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a bad gender was accepted")

    command.downgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE tech_operation_records SET fields = ? WHERE display_text = 't4'",
                           (json.dumps({"terArticle": "D", "terGender": "Pin"}),))
        assert connection.execute("SELECT count(*) FROM tech_operation_records").fetchone()[0] == len(rows)
