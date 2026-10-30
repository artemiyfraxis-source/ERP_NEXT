"""Migration 0165: «Наконечники» and each cable kind are bases of their own (owner 28–29.10.2026: «каждая вкладка в БД —
это отдельная таблица БД»). The rows keep their ids and fields, the kind columns go, the owner's columns and the stock
journal follow; upgrade → downgrade → upgrade lands on the same data."""

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.modules.references.domain import (
    CABLE_KIND_BY_TAB, CABLE_TABS, TERMINAL_TABS, TechOperationRecord, TechOperationTab, cable_tab, tab_family, ter_tab,
)
from app.modules.references.infrastructure import STOCK_LINK_TAB_CHECK, TAB_CHECK, terminal_card_checks
from tests.migrations.seed_any_revision import seed
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BEFORE, AFTER = "0164_stage_ready_qc_passed", "0165_split_lug_cable_tabs"
MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / f"{AFTER}.py"

LUG = {"Product Name": "НШВИ", "Series": "НШВИ 0.5", "Артикул контакта (REEL)": "НШВИ 0.5-8", "terArticle": "НШВИ 0.5-8"}
CONTACT = {"Product Name": "XH", "Артикул контакта (REEL)": "SXH-001T-P0.6", "terGender": "Female"}
# (display, tab, ter_kind, cable_kind, fields) → the base after 0165
ROWS = [
    ("contact", "ter", "contact", None, CONTACT, "ter"),
    ("lug", "ter", "lug", None, LUG, "lug"),
    ("lug-bad-card", "ter", "lug", None, {**LUG, "terDatasheet": "kvt.su/x"}, "lug"),  # outside the 0115 checks
    ("wire", "prov", None, "wire", {"Марка": "МГТФ 0,12", "Цвет": "красный"}, "prov"),
    ("coax", "prov", None, "coax", {"Марка": "RG-316"}, "cab_coax"),
    ("multi", "prov", None, "multi", {"Марка": "КГ 3х1,5"}, "cab_multi"),
    ("lan", "prov", None, "lan", {"Марка": "UTP 4x2 Cat5e"}, "cab_lan"),
    ("mat", "mat", None, None, {"Наименование": "ТУТ 3/1"}, "mat"),
]
CUSTOM = {"prov:coax:Затухание": "cab_coax:Затухание", "prov:wire:Полка": "prov:Полка", "prov:multi:Экран 2": "cab_multi:Экран 2",
          "prov:lan:PoE": "cab_lan:PoE", "ter:lug:Цвет изоляции": "lug:Цвет изоляции", "ter:Полка": "ter:Полка",
          "mat:Партия": "mat:Партия"}
VIEWS = {"prov:coax:2510|Марка": "prov:coax:2510|Марка", "ter|prov:coax:Затухание": "ter|cab_coax:Затухание"}


def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


def _seed(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")  # the «outside» row is one 0115 left as it was
        for display, tab, ter_kind, cable_kind, fields, _base in ROWS:
            connection.execute(
                "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                " fields, mirror_key, edited_in_erp, ter_kind, cable_kind) VALUES (?, ?, ?, ?, '', ?, ?, ?, 0, ?, ?)",
                (uuid4().hex, tab, display, display, display, json.dumps(fields, ensure_ascii=False), f"m-{display}",
                 ter_kind, cable_kind))
        for position, key in enumerate([*CUSTOM, *VIEWS]):
            kind = "column_view" if "|" in key else "custom_column"
            connection.execute("INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)"
                               " VALUES (?, ?, ?, ?, ?, ?)", (uuid4().hex, kind, key, " ".join(key.casefold().split()),
                                                              key.rsplit(":", 1)[-1] if kind == "custom_column" else "1",
                                                              position))
    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        seed(engine, rows=len(ROWS))  # every other table, the stock journal pointing at the records above
    finally:
        engine.dispose()
    with sqlite3.connect(database) as connection:  # a journal row says the tab of its record, as the app writes it
        connection.execute("UPDATE reference_stock_links SET record_tab = (SELECT tab FROM tech_operation_records"
                           " WHERE tech_operation_records.id = reference_stock_links.record_id)")


def _state(database: Path) -> dict:
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tech_operation_records)")}
        kinds = ", ter_kind, cable_kind" if "ter_kind" in columns else ""
        rows = {}
        for row in connection.execute(f"SELECT id, tab, display_text, fields, sort_key, mirror_key, edited_in_erp{kinds}"
                                      " FROM tech_operation_records"):
            rows[row[0]] = {"tab": row[1], "kinds": tuple(row[7:]), "hash": hashlib.sha1(
                json.dumps([row[2], json.loads(row[3]), row[4], row[5], row[6]], ensure_ascii=False, sort_keys=True)
                .encode()).hexdigest()}
        links = sorted(connection.execute("SELECT id, record_id, record_tab FROM reference_stock_links"))
        entries = dict(connection.execute("SELECT key, kind FROM reference_entries WHERE kind IN ('custom_column', 'column_view')"))
        checks = " ".join(row[0] for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name IN ('tech_operation_records', 'reference_stock_links')"))
    return {"rows": rows, "links": links, "entries": entries, "columns": columns, "checks": checks}


def test_the_migration_moves_rows_to_their_bases_and_the_round_trip_keeps_everything(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    _seed(database)
    before = _state(database)
    assert len(before["links"]) == len(ROWS)

    command.upgrade(config, AFTER)

    after = _state(database)
    printed = capsys.readouterr().out
    assert "сверка по базам" in printed and "lug: 0 -> 2" in printed and "cab_coax: 0 -> 1" in printed
    assert "строк вне проверок карточки (оставлены как есть) — 1" in printed
    expected = {display: base for display, *_rest, base in ROWS}
    by_display = {}
    with sqlite3.connect(database) as connection:
        by_display = dict(connection.execute("SELECT display_text, tab FROM tech_operation_records"))
    assert by_display == expected
    # ids, fields and the rest of every row are the same; the kind columns are gone
    assert after["rows"].keys() == before["rows"].keys()
    assert {key: row["hash"] for key, row in after["rows"].items()} == {key: row["hash"] for key, row in before["rows"].items()}
    assert "ter_kind" not in after["columns"] and "cable_kind" not in after["columns"]
    # the journal follows its records; the owner's columns are the bases' own
    assert len(after["links"]) == len(before["links"])
    assert all(after["rows"][record_id]["tab"] == tab for _id, record_id, tab in after["links"])
    assert {key for key, kind in after["entries"].items() if kind == "custom_column"} == set(CUSTOM.values())
    assert {key for key, kind in after["entries"].items() if kind == "column_view"} == set(VIEWS.values())
    # the new checks are on: the tab, the stock journal, the card checks of «Наконечники»
    with sqlite3.connect(database) as connection:
        for sql in ("UPDATE tech_operation_records SET tab = 'lugs' WHERE display_text = 'lug'",
                    "UPDATE tech_operation_records SET fields = json_set(fields, '$.terGender', 'X')"
                    " WHERE display_text = 'lug'",
                    "UPDATE reference_stock_links SET record_tab = 'kab_coax'"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)

    command.downgrade(config, BEFORE)

    back = _state(database)
    assert back["rows"] == before["rows"] and back["links"] == before["links"] and back["entries"] == before["entries"]
    assert {"ter_kind", "cable_kind"} <= back["columns"]
    assert "ck_tech_operation_records_ter_kind" in back["checks"] and "ck_tech_operation_records_tab" not in back["checks"]
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE tech_operation_records SET cable_kind = NULL WHERE display_text = 'coax'")

    command.upgrade(config, AFTER)

    again = _state(database)
    assert again["rows"] == after["rows"] and again["links"] == after["links"] and again["entries"] == after["entries"]


def test_the_migration_holds_literal_copies_of_the_checks():
    text = MIGRATION.read_text(encoding="utf-8")
    flat = re.sub(r'"\s*"', "", text)  # a literal split over two lines

    assert TAB_CHECK in flat and STOCK_LINK_TAB_CHECK in flat
    module = {}
    exec(compile(text, str(MIGRATION), "exec"), module)
    for dialect in ("sqlite", "postgresql"):
        assert module["_card_checks"](dialect, module["CARD_OTHER_NEW"]) == terminal_card_checks(dialect)
    assert set(module["KNOWN_TABS"]) == {tab.value for tab in TechOperationTab}


def test_each_kind_is_its_own_base_and_a_family_groups_a_sheet():
    assert TERMINAL_TABS == (TechOperationTab.TERMINAL, TechOperationTab.LUG)
    assert {CABLE_KIND_BY_TAB[tab] for tab in CABLE_TABS} == {"coax", "wire", "multi", "lan"}
    assert cable_tab("wire") == TechOperationTab.WIRE and cable_tab("coax") == TechOperationTab.CAB_COAX
    assert ter_tab("lug") == TechOperationTab.LUG and tab_family(TechOperationTab.CAB_LAN) == CABLE_TABS
    record = TechOperationRecord.create(tab=TechOperationTab.WIRE, display_text="RG-316", search_text="", source_sheet="",
                                        sort_key="", fields={}, cable_kind="coax")
    assert (record.tab, record.cable_kind, record.ter_kind) == (TechOperationTab.CAB_COAX, "coax", None)
    lug = TechOperationRecord.create(tab=TechOperationTab.LUG, display_text="НШВИ", search_text="", source_sheet="",
                                     sort_key="", fields={})
    assert (lug.ter_kind, lug.cable_kind) == ("lug", None)


# --- the cable bases on the API ---

def test_each_cable_base_has_its_own_columns_own_columns_file_and_moves(references_api):
    from app.modules.references.domain import CABLE_COLUMNS

    rows = [{"Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1"},
            {"Марка": "МГТФ 0,35", "Тип провода": "монтажный", "Цвет": "белый"},
            {"Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4"},
            {"Марка": "UTP 4x2 Cat5e", "Тип провода": "витая пара U/UTP Cat5e (LAN)", "Число жил": "8 (4 пары)"}]
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row} for row in rows
    ]}).status_code == 200
    get = lambda tab: references_api.get("/api/references/tech-operations", params={"tab": tab}).json()

    bases = {tab: get(tab) for tab in ("cab_coax", "prov", "cab_multi", "cab_lan")}
    assert {tab: [row["fields"]["Марка"] for row in body["records"]] for tab, body in bases.items()} == {
        "cab_coax": ["RG316"], "prov": ["МГТФ 0,35"], "cab_multi": ["КСПВГ 4x0,20"], "cab_lan": ["UTP 4x2 Cat5e"]}
    for tab, body in bases.items():
        assert body["columns"] == list(CABLE_COLUMNS[CABLE_KIND_BY_TAB[TechOperationTab(tab)]]), tab
    counts = references_api.get("/api/references/bases").json()["tabs"]
    assert [counts[tab] for tab in ("cab_coax", "prov", "cab_multi", "cab_lan")] == [1, 1, 1, 1]

    # «+ Столбец» of one base is never a column of another
    assert references_api.post("/api/references/tech-operations/columns",
                               json={"tab": "cab_multi", "name": "Полка"}).status_code == 201
    assert get("cab_multi")["custom_columns"] == ["Полка"] and get("prov")["custom_columns"] == []
    cables = references_api.get("/api/references/cables").json()
    assert cables["custom_columns_by_kind"] == {"coax": [], "wire": [], "multi": ["Полка"], "lan": []}
    # the file of a base: its rows only, its own column in it
    fields = references_api.get("/api/references/import/cab_multi/file/fields").json()
    assert fields["rows"] == 1 and "Полка" in fields["fields"]
    # the load of a base creates there; another kind named in «Вид кабеля» is refused
    report = references_api.post("/api/references/import/cab_coax", params={"dry_run": 0}, json={"rows": [
        {"fields": {"Марка": "RG174"}}, {"fields": {"Марка": "МГШВ 0,5", "Вид кабеля": "wire"}}]}).json()
    assert report["created"] == ["RG174"] and "Коаксиальные кабели" in report["refused"][0]["reason"], report

    # a move between the cable bases: the same id, the new base's columns; a монтажный row's colours move with it
    [multi] = get("cab_multi")["records"]
    moved = references_api.put(f"/api/references/tech-operations/{multi['id']}/tab", json={"tab": "cab_lan"})
    assert moved.status_code == 200 and (moved.json()["id"], moved.json()["tab"], moved.json()["cable_kind"]) == (
        multi["id"], "cab_lan", "lan")
    assert set(CABLE_COLUMNS["lan"]) <= set(moved.json()["fields"])
    assert references_api.put(f"/api/references/tech-operations/{multi['id']}/tab",
                              json={"tab": "lug"}).status_code == 422
