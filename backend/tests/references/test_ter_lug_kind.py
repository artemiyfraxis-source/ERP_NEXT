"""«Наконечники» (owner 26.10.2026: «тип наконечник, а не терминал… и в базе их бы тоже отдельно сделать, в другую БД —
наконечники»): the lugs of БД.ТЕР are their own list (`ter_kind = 'lug'`) with their own columns, «Терминалы» shows only
the crimp contacts. The rows stay in tab `ter`, so every reader still finds a lug; the warehouse stocks it as
«Наконечник». Migration 0155 classifies the rows by the rule, keeps the counts and its downgrade drops the list."""

import csv
import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.references.domain import ReferenceValidationError, TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import TER_KIND_CHECK
from app.modules.references.lugs import LUG_COLUMN_SLOTS, LUG_FAMILY, LUG_WORDS, is_lug, lug_columns, ter_kind_of
from app.workflows.item_names import ReferenceNames
from app.workflows.warehouse_article import infer_article_identity
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BEFORE, AFTER = "0154_user_ui_settings", "0155_ter_lug_kind"
MIRROR = Path(__file__).parents[2] / "data" / "reference-mirror" / "БД.ТЕР.tsv"
MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0155_ter_lug_kind.py"

# БД.ТЕР rows as the mirror keeps them (sheet columns), plus the schema keys a GAS snapshot carries
LUG_ROW = {"Тип разъема": "Обжимная клемма", "Производитель": "КВТ", "Product Name": "НШВИ", "Series": "НШВИ 0.5",
           "Артикул контакта (REEL)": "НШВИ 0.5-8", "От AWG": "20", "До AWG": "20", "От мм2": "0,5", "До мм2": "0,5",
           "Длина зачистки, мм": "8", "Материал контакта": "Медь / гальваническое лужение", "L+": "", "L-": "",
           "Высота обжима проводника , мм": "", "Аппликатор": "", "Пневма Автомат": "FALSE",
           "Ссылка на DATASHEET на сайте производителя": "https://kvt.su/x",
           "Источник высоты обжима": "нет данных производителя"}
CONTACT_ROW = {"Тип разъема": "Обжимной контакт", "Производитель": "JST", "Product Name": "XH", "Series": "XHP",
               "Тип контакта": "Female (Socket)", "Артикул контакта (REEL)": "SXH-001T-P0.6", "От AWG": "28",
               "До AWG": "22", "От мм2": "0,08", "До мм2": "0,35", "L+": "2,1", "L-": "1,0",
               "Высота обжима проводника , мм": "28 AWG: 0,60", "Аппликатор": "AP-XH",
               "Ссылка на DATASHEET на сайте производителя": "https://www.jst-mfg.com/x"}


@pytest.mark.parametrize("fields", [
    {"Product Name": "НШВИ", "Артикул контакта (REEL)": "НШВИ 0.5-8"},
    {"terComponent": "НКИ", "terArticle": "НКИ 1.5-4"},  # schema keys only (a GAS snapshot)
    {"Product Name": "РПИ-М(н)", "Series": "РПИ-М(н) (6.3)"},
    {"Product Name": "РППИ-М", "Series": "РППи-м 2.5-(6.3) / РпИмп 2-250"},  # REXANT, any case
    {"Product Name": "FDD", "Артикул контакта (BAG)": "FDD2-110(5)"},
    {"Product Name": "Fully Insulated Female Disconnect", "Series": "FDFN1-250"},
    {"Product Name": "InsulKrimp Quick Disconnect", "Series": "19003"},
    {"Product Name": "TAB, FASTIN-FASTON", "Series": "6,35 [.250]"},
    {"Product Name": "ПМ", "Артикул контакта (BAG)": "ПМ 1.5-4"},
    {"Series": "ТМЛ 16-8"},
    {"Артикул контакта (REEL)": "НВ 1,5-4"},  # the article alone
])
def test_the_rule_sends_a_lug_to_lugs(fields):
    assert is_lug(fields) and ter_kind_of(fields) == "lug"


@pytest.mark.parametrize("fields", [
    {"Product Name": "XH", "Артикул контакта (REEL)": "SXH-001T-P0.6"},
    {"Product Name": "PH", "Артикул контакта (REEL)": "SPH-002T-P0.5L"},
    {"Product Name": "Micro-Fit 3.0", "Артикул контакта (REEL)": "43030-0001"},
    {"Product Name": "ГС", "Series": "ГС 2,5", "Тип разъема": "Обжимная клемма"},  # a butt splice is no lug
    {"Product Name": "ГМЛ", "Series": "ГМЛ 4"},
    {"Product Name": "Контакт штыревой 6,3", "Series": "1/02506", "Тип разъема": "Обжимная клемма"},
    {"Product Name": "TS Sealed Series (0,64мм/025)"},  # «TS» — Latin, no «ТА»/«ТМ»
    {"Product Name": "AMP MCP 2.8K"},
    {"Product Name": "НКС", "Series": "НКС 1"},  # «НК» needs a non-letter after it
    {},
])
def test_the_rule_keeps_a_crimp_contact_with_the_terminals(fields):
    assert not is_lug(fields) and ter_kind_of(fields) == "contact"


def _mirror_rows() -> list[dict]:
    with MIRROR.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    header = [re.sub(r"\s+", " ", name).strip() for name in rows[0]]
    return [dict(zip(header, row)) for row in rows[1:]]


def test_the_mirror_splits_into_these_lug_families():
    lugs = {(row["Производитель"], row["Product Name"]) for row in _mirror_rows() if is_lug(row)}
    assert lugs == {
        ("JST", "FLVDDF"), ("KST", "Fully Insulated Female Disconnect"), ("REXANT", "РППИ-М"),
        ("Yueqing Chaodao", "FDD"), ("КВТ", "НВИ"), ("КВТ", "НК"), ("КВТ", "НКИ"), ("КВТ", "НШВ"), ("КВТ", "НШВИ"),
        ("КВТ", "ПМ"), ("КВТ", "РПИ-М"), ("КВТ", "РПИ-М(н)"), ("КВТ", "РПИ-П"), ("КВТ", "РППИ-М"), ("Копир", "НК"),
        ("Электромонтаж", "НКИ"), ("Электромонтаж", "НШВИ"), ("Электромонтаж", "РППИ-М"),
        ("Molex", "InsulKrimp Quick Disconnect"), ("TE", "TAB, FASTIN-FASTON"),
    }
    assert sum(1 for row in _mirror_rows() if is_lug(row)) == 115


def test_the_lug_columns_are_what_lugs_fill_in_the_group_order():
    sheet = list(LUG_ROW) + ["Тип контакта", "Артикул контакта (BAG)", "Диаметр изоляции до, мм", "Максисмальная сила тока, А"]
    assert lug_columns(sheet) == [
        "Производитель", "Product Name", "Series", "Артикул контакта (REEL)", "Артикул контакта (BAG)", "Тип контакта",
        "От мм2", "До мм2", "От AWG", "До AWG", "Диаметр изоляции до, мм", "Длина зачистки, мм", "L+", "L-", "Материал контакта",
        "Максисмальная сила тока, А", "Аппликатор", "Ссылка на DATASHEET на сайте производителя",
    ]
    # schema keys only (a GAS snapshot): the same slots
    assert lug_columns(["terManufacturer", "terComponent", "terArticle", "mm2From", "mm2To", "terCrimpHeight", "terLMinus",
                        "terStrip", "terLPlus"]) == [
        "terManufacturer", "terComponent", "terArticle", "mm2From", "mm2To", "terStrip", "terLPlus", "terLMinus"]
    assert len(LUG_COLUMN_SLOTS) == 18


def test_a_ter_record_takes_its_list_by_the_rule_and_only_ter_has_one():
    def record(tab, fields, **kind):
        return TechOperationRecord.create(tab=tab, display_text="x", search_text="", source_sheet="", sort_key="x",
                                          fields=fields, **kind)

    assert record(TechOperationTab.TERMINAL, LUG_ROW).ter_kind == "lug"
    assert record(TechOperationTab.TERMINAL, CONTACT_ROW).ter_kind == "contact"
    assert record(TechOperationTab.TERMINAL, LUG_ROW, ter_kind="contact").ter_kind == "contact"  # a move by hand wins
    assert record(TechOperationTab.MATERIAL, LUG_ROW).ter_kind is None
    with pytest.raises(ReferenceValidationError):
        record(TechOperationTab.MATERIAL, LUG_ROW, ter_kind="lug")
    with pytest.raises(ReferenceValidationError):
        record(TechOperationTab.TERMINAL, LUG_ROW, ter_kind="ring")
    assert "tab = 'ter'" in TER_KIND_CHECK and "('contact', 'lug')" in TER_KIND_CHECK


# --- the screen and its API ---

def _load(api, *rows) -> None:
    response = api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "ter", "display_text": row["Series"], "source_sheet": "БД.ТЕР", "fields": row} for row in rows
    ] + [{"tab": "mat", "display_text": "ТУТ 3/1", "source_sheet": "БД.МАТ", "fields": {"Наименование": "ТУТ 3/1"}}]})
    assert response.status_code == 200, response.text


def test_lugs_has_only_lugs_with_its_columns_and_terminals_only_contacts(references_api):
    _load(references_api, LUG_ROW, CONTACT_ROW)
    get = lambda **params: references_api.get("/api/references/tech-operations", params={"tab": "ter", **params}).json()

    lugs, contacts, every = get(ter_kind="lug"), get(ter_kind="contact"), get()

    assert [row["fields"]["Product Name"] for row in lugs["records"]] == ["НШВИ"]
    assert lugs["records"][0]["ter_kind"] == "lug" and lugs["records"][0]["ter_rule"] == "lug"
    assert lugs["columns"] == ["Производитель", "Product Name", "Series", "Артикул контакта (REEL)", "От мм2", "До мм2",
                               "От AWG", "До AWG", "Длина зачистки, мм", "L+", "L-", "Материал контакта", "Аппликатор",
                               "Ссылка на DATASHEET на сайте производителя"]
    assert lugs["custom_columns"] == []
    assert [row["fields"]["Product Name"] for row in contacts["records"]] == ["XH"]
    assert "L+" in contacts["columns"] and "Высота обжима проводника , мм" in contacts["columns"]
    # without a list — every БД.ТЕР row (the readers that look a lug or a contact up by its article)
    assert sorted(row["fields"]["Product Name"] for row in every["records"]) == ["XH", "НШВИ"]
    assert references_api.get("/api/references/bases").json()["ter_kinds"] == {"contact": 1, "lug": 1}
    assert references_api.get("/api/references/tech-operations",
                              params={"tab": "mat", "ter_kind": "lug"}).status_code == 422


def test_each_list_has_its_own_columns_a_column_of_one_is_never_a_column_of_the_other(references_api):
    # owner 28.10.2026: «каждая вкладка в БД — это отдельная таблица БД»
    _load(references_api, LUG_ROW, CONTACT_ROW)
    get = lambda kind: references_api.get("/api/references/tech-operations", params={"tab": "ter", "ter_kind": kind}).json()
    column = lambda path, name, kind: references_api.post(f"/api/references/tech-operations/columns{path}",
                                                          json={"tab": "ter", "name": name, "ter_kind": kind})

    assert column("", "Цвет изоляции", "lug").status_code == 201
    assert column("", "Полка", "contact").status_code == 201
    lugs, contacts = get("lug"), get("contact")
    assert lugs["custom_columns"] == ["Цвет изоляции"] and lugs["columns"][-1] == "Цвет изоляции"
    assert "Полка" not in lugs["columns"] and "Полка" not in lugs["records"][0]["fields"]
    assert contacts["custom_columns"] == ["Полка"] and "Цвет изоляции" not in contacts["columns"]
    assert "Цвет изоляции" not in contacts["records"][0]["fields"]
    # the same name in the other list is its own column; removing it from one list leaves the other
    assert column("", "Полка", "lug").status_code == 201
    assert column("/remove", "Полка", "lug").status_code == 200
    assert get("contact")["custom_columns"] == ["Полка"] and "Полка" in get("contact")["records"][0]["fields"]
    assert get("lug")["custom_columns"] == ["Цвет изоляции"]
    # a new lug takes the lugs' own columns, not the terminals'
    created = references_api.post("/api/references/tech-operations", json={
        "tab": "ter", "ter_kind": "lug", "fields": {"Product Name": "НКИ", "Артикул контакта (REEL)": "НКИ 1.5-4"}}).json()
    assert "Цвет изоляции" in created["fields"] and "Полка" not in created["fields"]


def test_a_row_moved_by_hand_keeps_its_fields_and_a_new_row_goes_to_the_open_list(references_api):
    _load(references_api, LUG_ROW, CONTACT_ROW)
    [lug] = references_api.get("/api/references/tech-operations", params={"tab": "ter", "ter_kind": "lug"}).json()["records"]

    moved = references_api.put(f"/api/references/tech-operations/{lug['id']}/ter-kind", json={"kind": "contact"})

    assert moved.status_code == 200, moved.text
    assert (moved.json()["ter_kind"], moved.json()["ter_rule"]) == ("contact", "lug")
    contacts = references_api.get("/api/references/tech-operations", params={"tab": "ter", "ter_kind": "contact"}).json()
    again = next(row for row in contacts["records"] if row["id"] == lug["id"])
    assert again["fields"] == lug["fields"]
    assert references_api.put(f"/api/references/tech-operations/{lug['id']}/ter-kind", json={"kind": "ring"}).status_code == 422
    mat = references_api.get("/api/references/tech-operations", params={"tab": "mat"}).json()["records"][0]
    assert references_api.put(f"/api/references/tech-operations/{mat['id']}/ter-kind", json={"kind": "lug"}).status_code == 422

    created = references_api.post("/api/references/tech-operations", json={
        "tab": "ter", "fields": {"Производитель": "Атлант", "Product Name": "Контакт штыревой 6,3", "Series": "1/02506",
                                 "Артикул контакта (REEL)": "1/02506-01"}, "ter_kind": "lug"})
    assert created.status_code == 201, created.text
    assert created.json()["ter_kind"] == "lug" and created.json()["ter_rule"] == "contact"
    by_rule = references_api.post("/api/references/tech-operations", json={
        "tab": "ter", "fields": {"Производитель": "КВТ", "Product Name": "НКИ", "Series": "НКИ 2.5",
                                 "Артикул контакта (REEL)": "НКИ 2.5-5"}})
    assert by_rule.json()["ter_kind"] == "lug"
    assert references_api.post("/api/references/tech-operations", json={
        "tab": "mat", "fields": {"Наименование": "ТУТ"}, "ter_kind": "lug"}).status_code == 422


def test_the_file_of_lugs_holds_the_lugs_with_their_columns(references_api):
    _load(references_api, LUG_ROW, CONTACT_ROW)
    fields = lambda kind: references_api.get("/api/references/import/ter/file/fields", params={"kind": kind}).json()

    lugs, contacts = fields("lug"), fields("contact")

    assert (lugs["label"], lugs["rows"], contacts["label"], contacts["rows"]) == ("Наконечники", 1, "Терминалы", 1)
    # L+ / L− — припуски наконечника, как у контакта (владелец 29.10); служебная «Источник высоты обжима» — нет.
    assert "L+" in lugs["fields"] and "Источник высоты обжима" not in lugs["fields"]
    assert lugs["fields"][:3] == ["Производитель", "Product Name", "Series"]
    assert "L+" in contacts["fields"]
    assert references_api.get("/api/references/import/ter/file/fields", params={"kind": "ring"}).status_code >= 400


# --- the readers find a lug as before; the warehouse stocks it as «Наконечник» ---

def _records():
    return [TechOperationRecord.create(tab=TechOperationTab.TERMINAL, display_text=row["Series"], search_text="",
                                       source_sheet="БД.ТЕР", sort_key="", fields=row)
            for row in ({**LUG_ROW, "terManufacturer": "КВТ", "terComponent": "НШВИ", "terArticle": "НШВИ 0.5-8"},
                        {**CONTACT_ROW, "terManufacturer": "JST", "terComponent": "XH", "terArticle": "SXH-001T-P0.6",
                         "terType": "Female (Socket)"})]


def test_the_warehouse_names_a_lug_a_lug_and_a_contact_a_terminal():
    lug = infer_article_identity("НШВИ 0.5-8", _records())
    contact = infer_article_identity("SXH-001T-P0.6", _records())

    assert (lug.item_type, lug.name, lug.source) == ("Наконечник", "Наконечник НШВИ", "БД.ТЕР")
    assert contact.item_type == "Терминал"
    names = ReferenceNames(_records())
    assert names.name("НШВИ 0.5-8") == "Наконечник КВТ НШВИ"
    assert names.name("SXH-001T-P0.6").startswith("Контакт обжимной")


def test_a_lug_moved_to_terminals_is_stocked_as_a_terminal():
    lug, contact = _records()
    moved = TechOperationRecord(**{**lug.__dict__, "ter_kind": "contact"})
    assert infer_article_identity("НШВИ 0.5-8", [moved, contact]).item_type == "Терминал"


def test_the_crimp_lookup_still_reads_lugs_from_the_terminals_tab(references_api):
    # the schema keys the generator reads (a mirror load derives them from the sheet columns)
    _load(references_api, {**LUG_ROW, "Аппликатор": "M31", "terArticle": "НШВИ 0.5-8", "terApplicator": "M31",
                           "awgFrom": "20", "awgTo": "20", "mm2From": "0,5", "mm2To": "0,5"}, CONTACT_ROW)
    response = references_api.post("/api/references/technology/resolve-terminal",
                                   json={"article": "НШВИ 0.5-8", "wires": ["0,5"]})
    assert response.status_code == 200, response.text
    assert response.json()["applicator"] == "M31"


# --- migration 0155 ---

def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


ROWS = {
    "lug": ("ter", LUG_ROW),
    "gas_lug": ("ter", {"terManufacturer": "КВТ", "terComponent": "РПИ-П", "terArticle": "РПИ-П 1.5 (4.8)"}),
    "splice": ("ter", {"Product Name": "ГС", "Series": "ГС 2,5", "Тип разъема": "Обжимная клемма"}),
    "contact": ("ter", CONTACT_ROW),
    "empty": ("ter", {}),
    "mat": ("mat", {"Наименование": "НШВИ 0.5-8 в пакете"}),  # another tab is never looked at
}


def test_the_migration_classifies_the_rows_keeps_the_counts_and_the_downgrade_drops_the_list(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        for name, (tab, fields) in ROWS.items():
            connection.execute(
                "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                " fields, mirror_key, edited_in_erp) VALUES (?, ?, ?, '', '', '', ?, '', 0)",
                (uuid4().hex, tab, name, json.dumps(fields, ensure_ascii=False)))
        before = {row[0]: row[1] for row in connection.execute("SELECT display_text, fields FROM tech_operation_records")}

    command.upgrade(config, AFTER)

    printed = capsys.readouterr().out
    assert "БД.ТЕР 5: «Терминалы» 3, «Наконечники» 2" in printed and "НШВИ 1" in printed and "РПИ-П 1" in printed
    with sqlite3.connect(database) as connection:
        kinds = dict(connection.execute("SELECT display_text, ter_kind FROM tech_operation_records"))
        assert kinds == {"lug": "lug", "gas_lug": "lug", "splice": "contact", "contact": "contact", "empty": "contact",
                         "mat": None}
        assert {row[0]: row[1] for row in connection.execute("SELECT display_text, fields FROM tech_operation_records")} == before
        for sql in ("INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                    " fields, mirror_key, edited_in_erp) VALUES ('a', 'ter', 'x', '', '', '', '{}', '', 0)",
                    "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                    " fields, mirror_key, edited_in_erp, ter_kind) VALUES ('b', 'mat', 'x', '', '', '', '{}', '', 0, 'lug')",
                    "UPDATE tech_operation_records SET ter_kind = 'ring' WHERE display_text = 'lug'"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)

    command.downgrade(config, BEFORE)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tech_operation_records)")}
        assert "ter_kind" not in columns
        assert {row[0]: row[1] for row in connection.execute("SELECT display_text, fields FROM tech_operation_records")} == before


def test_the_migration_holds_a_literal_copy_of_the_rule_and_the_check():
    text = MIGRATION.read_text(encoding="utf-8")
    flat = re.sub(r'"\s*r"', "", text)  # the pattern is split over two raw-string lines

    assert LUG_FAMILY.pattern in flat and LUG_WORDS.pattern in text
    assert all(line.strip(" ()") in text for line in TER_KIND_CHECK.split(" OR "))
