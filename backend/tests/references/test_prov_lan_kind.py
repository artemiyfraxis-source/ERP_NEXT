"""«ETHERNET» (owner 25.10.2026, at «Многожильные»: «интернетные вынести вообще в другую вкладку — новая вкладка, новая
БД»; «не витая пара»): the LAN cables are their own cable list `lan` with their own columns. Migration 0150 moves the
LAN rows out of the многожильные (counts kept, the moved marks' manual moves follow, the screen code in «Экран» with the
old text journaled) and its downgrade puts everything back; the rule sends a new LAN row to the list; a twisted pair of
no Ethernet (PROFIBUS, DIN 47100, a telephone pair, a control cable) stays многожильный."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.references.cables import (
    CABLE_KIND_LABELS, CableKind, auto_kind, lan_category, lan_derived, lan_fill, lan_laying, lan_pairs, lan_screen,
    resolve_kind,
)
from app.modules.references.domain import CABLE_COLUMNS, CABLE_DROPPED, CABLE_KIND_VALUES, CABLE_WRITABLE
from app.modules.references.domain import CABLE_TAB_BY_KIND, TechOperationTab
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BEFORE, AFTER = "0149_receiving_defect_quantity", "0150_prov_lan_kind"
MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0150_prov_lan_kind.py"
SPLIT = Path(__file__).parents[2] / "migrations" / "versions" / "0165_split_lug_cable_tabs.py"

LAN_TYPE = "витая пара U/UTP Cat5e (LAN, внутренняя прокладка)"
ROWS = {
    # LAN, as on prod: the type names the category, the screen code and the laying
    "parlan": {"Марка": "ParLan U/UTP Cat5e ZH нг(А)-HF 4х2х0,52", "Тип провода": LAN_TYPE, "Число жил": "8 (4 пары)",
               "Экран": "нет", "Материал жилы": "медь", "Источник данных": "Паритет 24.10"},
    # the type without «LAN», the mark gives nothing more; a filled «Категория» is never changed
    "ftp": {"Марка": "FTP-4P-Cat.5e-SOLID-GY", "Тип провода": "витая пара F/UTP Cat5e (экран — фольга)",
            "Число жил": "8 (4 пары)", "Экран": "общий — фольга", "Категория": "Cat5e"},
    # the category and the screen only in the mark, cores without pairs
    "sstp": {"Марка": "SSTP-4P-Cat.7-PATCH-IN-LSZH", "Тип провода": "витая пара (LAN, экранированная)", "Число жил": "8",
             "Экран": "S/FTP (фольга по парам + общий экран)"},
    # industrial Ethernet (PROFINET): no category, no code — LAN by the word
    "profinet": {"Марка": "6XV1840-2AH10", "Тип провода": "Industrial Ethernet (PROFINET), 4-жильный",
                 "Число жил": "4 (2 пары)", "Экран": "фольга + оплётка"},
    # twisted pairs of no Ethernet and control cables stay многожильные
    "profibus": {"Марка": "6XV1830-0EH10", "Тип провода": "сигнальный (промышленная шина PROFIBUS, экранированный)",
                 "Число жил": "2", "Экран": "фольга + оплётка"},
    "paar": {"Марка": "PAAR-TRONIC-CY 4x2x0,5", "Тип провода": "экранированный парный кабель для передачи данных (DIN 47100)",
             "Число жил": "8 (4 пары)", "Экран": "оплётка"},
    "phone": {"Марка": "ШТЛЭн-4", "Тип провода": "телефонный шнур экранированный", "Число жил": "4",
              "Категория": "Коммуникационные многожильные"},
    "kdvvg": {"Марка": "КДВВГ 4х0,50", "Тип провода": "многожильный кабель управления (аналог LiYY)", "Число жил": "4"},
    # other lists are not looked at
    "coax": {"Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1"},
    "wire": {"Марка": "МГТФ 0,35", "Цвет": "белый", "Тип провода": "монтажный", "Число жил": "1"},
}
KINDS = {"parlan": "multi", "ftp": "multi", "sstp": "multi", "profinet": "multi", "profibus": "multi", "paar": "multi",
         "phone": "multi", "kdvvg": "multi", "coax": "coax", "wire": "wire"}
MOVED = {"parlan", "ftp", "sstp", "profinet"}


def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


def _seed(connection) -> dict:
    ids = {name: uuid4().hex for name in ROWS}
    for name, fields in ROWS.items():
        connection.execute(
            "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
            " mirror_key, edited_in_erp, cable_kind) VALUES (?, 'prov', ?, '', 'БД.ПРОВ', ?, ?, '', 0, ?)",
            (ids[name], fields["Марка"], fields["Марка"], json.dumps(fields, ensure_ascii=False), KINDS[name]))
    for key, normalized, value in (("ParLan U/UTP Cat5e ZH нг(А)-HF 4х2х0,52", "parlanu/utpcat5ezhнг(а)hf4х2х0,52", "multi"),
                                   ("КДВВГ 4х0,50", "кдввг4х0,50", "multi")):
        connection.execute("INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)"
                           " VALUES (?, 'cable_kind', ?, ?, ?, 0)", (uuid4().hex, key, normalized, value))
    return ids


def _rows(connection) -> dict:
    return {row[0]: (row[1], json.loads(row[2])) for row in connection.execute(
        "SELECT id, cable_kind, fields FROM tech_operation_records WHERE tab = 'prov'")}


def _insert(connection, kind) -> None:
    connection.execute(
        "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
        " mirror_key, edited_in_erp, cable_kind) VALUES (?, 'prov', 'X', '', '', 'X', '{}', '', 0, ?)", (uuid4().hex, kind))


def test_the_migration_moves_the_lan_cables_keeps_the_counts_and_the_downgrade_puts_them_back(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        ids = _seed(connection)
        before = _rows(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "lan")  # no such list before 0150

    command.upgrade(config, AFTER)
    printed = capsys.readouterr().out
    with sqlite3.connect(database) as connection:
        after = _rows(connection)
        overrides = dict(connection.execute(
            "SELECT normalized_key, value FROM reference_entries WHERE kind = 'cable_kind'").fetchall())

    assert set(after) == set(before)  # the same rows, the same ids
    assert {name: after[ids[name]][0] for name in ROWS} == {**KINDS, **{name: "lan" for name in MOVED}}
    assert "в ETHERNET перенесено 4" in printed and "строк БД.ПРОВ 10 = 10" in printed
    # every filled value of a non-moved row is untouched
    for name in set(ROWS) - MOVED:
        assert after[ids[name]][1] == before[ids[name]][1], name
    parlan = after[ids["parlan"]][1]
    assert (parlan["Категория"], parlan["Экран"], parlan["Число пар"], parlan["Прокладка"]) == ("Cat5e", "U/UTP", "4", "внутренняя")
    assert parlan["Источник данных"].startswith("Паритет 24.10; 25.10.2026 (0150): в список ETHERNET")
    assert "Экран «U/UTP» (было «нет»)" in parlan["Источник данных"]
    assert set(CABLE_COLUMNS["lan"]) <= set(parlan)  # every column of the list, editable in each cell
    ftp = after[ids["ftp"]][1]
    assert ftp["Категория"] == "Cat5e" and ftp["Экран"] == "F/UTP" and ftp["Прокладка"] == ""
    sstp = after[ids["sstp"]][1]
    assert (sstp["Категория"], sstp["Экран"], sstp["Число пар"]) == ("Cat7", "S/FTP", "4")
    assert "было «S/FTP (фольга по парам + общий экран)»" in sstp["Источник данных"]
    profinet = after[ids["profinet"]][1]
    assert profinet["Экран"] == "фольга + оплётка" and profinet["Число пар"] == "2" and profinet["Категория"] == ""
    # the manual move of a moved mark follows it; another stays
    assert overrides == {"parlanu/utpcat5ezhнг(а)hf4х2х0,52": "lan", "кдввг4х0,50": "multi"}

    connection = sqlite3.connect(database)
    try:
        _insert(connection, "lan")
        for kind in ("other", None, "ethernet"):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(connection, kind)
        connection.rollback()
    finally:
        connection.close()

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        back = _rows(connection)
        overrides = dict(connection.execute(
            "SELECT normalized_key, value FROM reference_entries WHERE kind = 'cable_kind'").fetchall())
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "lan")
    assert {row_id: kind for row_id, (kind, _fields) in back.items()} == {row_id: kind for row_id, (kind, _fields) in before.items()}
    # every filled value as it was: the old «Экран», no journal entry, no derived cell (padded empties are no values)
    filled = lambda rows: {row_id: {key: value for key, value in fields.items() if str(value).strip()}  # noqa: E731
                           for row_id, (_kind, fields) in rows.items()}
    assert filled(back) == filled(before)
    assert set(overrides.values()) == {"multi"}


def test_the_downgrade_keeps_a_value_changed_after_the_move():
    namespace: dict = {}
    exec(compile(MIGRATION.read_text(encoding="utf-8"), str(MIGRATION), "exec"), namespace)  # the migration's helpers
    fields = {"Марка": "UTP-1P-Cat.5e-SOLID", "Тип провода": "витая пара U/UTP Cat5e (LAN, наружная прокладка)",
              "Число жил": "2 (1 пара)", "Экран": "нет"}
    moved, entry = namespace["lan_row"](fields, fields["Марка"])
    assert entry == ("25.10.2026 (0150): в список ETHERNET — из типа, марки и числа жил: Категория «Cat5e», "
                     "Экран «U/UTP» (было «нет»), Число пар «1», Прокладка «наружная»")
    assert moved["Источник данных"] == entry
    moved["Прокладка"] = "внутренняя и наружная"  # the owner corrected it on the ETHERNET list
    moved["Источник данных"] += "; 26.10: паспорт"
    back = namespace["multi_row"](moved)
    assert back["Прокладка"] == "внутренняя и наружная" and back["Экран"] == "нет"
    assert "Категория" not in back and "Число пар" not in back and back["Источник данных"] == "26.10: паспорт"
    # the migration's rule is the app's rule
    assert all(namespace["is_ethernet"](row, row["Марка"]) == (name in MOVED) for name, row in ROWS.items()
               if KINDS[name] == "multi")


def test_the_check_and_the_lists_know_the_lan_kind():
    # ETHERNET is a base of its own since migration 0165 (tab «cab_lan»); its downgrade restores the 0150 check
    assert "lan" in CABLE_KIND_VALUES and CABLE_TAB_BY_KIND["lan"] == TechOperationTab.CAB_LAN
    assert CABLE_KIND_LABELS[CableKind.LAN] == "Кабели Ethernet"
    namespace: dict = {}
    exec(compile(MIGRATION.read_text(encoding="utf-8"), str(MIGRATION), "exec"), namespace)
    split: dict = {}
    exec(compile(SPLIT.read_text(encoding="utf-8"), str(SPLIT), "exec"), split)
    assert namespace["CHECK_NEW"] == split["CABLE_KIND_CHECK"] and "'lan'" in split["CABLE_KIND_CHECK"]


@pytest.mark.parametrize("name", sorted(ROWS))
def test_the_rule_sends_a_lan_cable_to_ethernet_and_keeps_other_twisted_pairs_multicore(name):
    fields = ROWS[name]
    kind, reason = auto_kind(fields, fields["Марка"], frozenset())
    expected = "lan" if name in MOVED else KINDS[name]
    assert kind.value == expected, (name, reason)
    if kind == CableKind.LAN:
        assert reason.startswith("кабель Ethernet")


def test_the_rule_reads_the_mark_alone_and_a_manual_move_wins():
    assert auto_kind({"Число жил": "8"}, "UTP-4P-Cat.6-SOLID-IN", frozenset())[0] == CableKind.LAN
    assert auto_kind({"Тип провода": "EtherCAT кабель"}, "X", frozenset())[0] == CableKind.LAN
    assert auto_kind({"Тип провода": "кабель CAN-шины, витая пара", "Число жил": "2"}, "CAN 2x0,5", frozenset())[0] == CableKind.MULTI
    # «ParLan» is no word LAN, «Cat5» needs no «e»; a coax type wins over a category in the mark
    assert auto_kind({"Тип провода": "коаксиальный"}, "RG-6 Cat5", frozenset())[0] == CableKind.COAX
    overrides = {"utp4pcat.6solidin": CableKind.MULTI}
    assert resolve_kind({}, "UTP-4P-Cat.6-SOLID-IN", frozenset(), overrides) == CableKind.MULTI


def test_the_parsers_read_category_screen_pairs_and_laying():
    assert lan_category("витая пара S/FTP Cat6A (LAN)") == "Cat6A" and lan_category("Cat.5e") == "Cat5e"
    assert lan_category("категория 6") == "Cat6" and lan_category("КДВВГ 4х0,50") == ""
    assert lan_category("U/UTP 4х2х0,52мм Сat 5e") == "Cat5e"  # a Cyrillic «С» typed for the Latin one
    assert lan_screen("SFTP/SF-UTP Cat5e") == "SF/UTP" and lan_screen("SSTP-4P") == "S/FTP"
    assert lan_screen("FTP-4P-Cat.5e") == "F/UTP" and lan_screen("U/FTP Cat6A") == "U/FTP" and lan_screen("ТПП") == ""
    assert lan_pairs("8 (4 пары)") == "4" and lan_pairs("6 (2 пар + 2 силовые)") == "2" and lan_pairs("8") == "4"
    assert lan_pairs("200 (100 пар)") == "100" and lan_pairs("3") == "" and lan_pairs("") == ""
    assert lan_laying("LAN, внутренняя и наружная прокладка") == "внутренняя и наружная"
    assert lan_laying("для внутренней и внешней прокладки") == "внутренняя и наружная"
    assert lan_laying("LAN, наружная прокладка") == "наружная" and lan_laying("патч-корд") == ""
    fields = {"Тип провода": LAN_TYPE, "Число жил": "4 (2 пары)", "Экран": "нет", "Категория": "Cat6"}
    assert lan_derived(fields, "X") == {"Категория": "Cat5e", "Экран": "U/UTP", "Число пар": "2", "Прокладка": "внутренняя"}
    # the app fills only empty cells: a filled «Экран» or «Категория» stays as typed
    assert lan_fill(fields, "X") == {**fields, "Число пар": "2", "Прокладка": "внутренняя"}


def test_the_lan_list_has_its_own_columns_all_shown():
    assert CABLE_COLUMNS["lan"] == (
        "Марка", "Тип провода", "Категория", "Стандарт", "Производители",
        "AWG", "Сечение, мм2", "Число пар", "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы",
        "Экран", "Материал оболочки", "Прокладка", "Наружный диаметр, мм",
        "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка на datasheet",
    )
    # ≤ 5 % filled on prod (wave impedance 1,7 %, the seller's link 0 %) — no column; the многожильные cells the list does
    # not show stay in the row's card
    assert not {"Волновое сопротивление, Ом", "Ссылка", "Число жил", "Изоляция", "Ток, А"} & set(CABLE_COLUMNS["lan"])
    assert {"Число жил", "Масса, г/м", "Материал экрана", "Источник данных"} <= CABLE_DROPPED["lan"]
    assert set(CABLE_COLUMNS["lan"]) <= set(CABLE_WRITABLE["lan"])
    # the многожильные keep their columns
    assert CABLE_COLUMNS["multi"] == (
        "Марка", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число жил",
        "Жила: проволок × диаметр, мм", "Экран", "Наружный диаметр, мм", "Напряжение, В", "Масса, г/м", "Темп. от, °C",
        "Темп. до, °C", "Поставщик", "Ссылка на datasheet",
    )


def test_a_loaded_lan_cable_lands_in_ethernet_with_its_columns(references_api):
    rows = [ROWS["parlan"], ROWS["kdvvg"], {"Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1"}]
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": row["Марка"], "source_sheet": "БД.ПРОВ", "fields": row} for row in rows
    ]}).status_code == 200

    body = references_api.get("/api/references/cables").json()

    assert body["counts"] == {"coax": 1, "wire": 0, "multi": 1, "lan": 1}
    assert body["columns_by_kind"]["lan"] == list(CABLE_COLUMNS["lan"])
    assert body["custom_columns_by_kind"]["lan"] == []
    assert "Число жил" in body["removed_columns_by_kind"]["lan"]
    lan = next(row for row in body["rows"] if row["kind"] == "lan")
    assert lan["cable"] == ROWS["parlan"]["Марка"] and lan["manual"] is False
    fields = lan["record"]["fields"]
    # a new row: the empty derived cells filled, a filled «Экран» kept as typed, every column of the list given
    assert (fields["Категория"], fields["Экран"], fields["Число пар"], fields["Прокладка"]) == ("Cat5e", "нет", "4", "внутренняя")
    assert set(CABLE_COLUMNS["lan"]) <= set(fields)


def test_a_row_moved_by_hand_to_ethernet_takes_the_list_columns(references_api):
    assert references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": [
        {"tab": "prov", "display_text": "X", "source_sheet": "БД.ПРОВ",
         "fields": {"Марка": "Кабель X 4x2x0,5", "Тип провода": "многожильный", "Число жил": "8 (4 пары)"}}]}).status_code == 200
    row = references_api.get("/api/references/cables").json()["rows"][0]
    assert row["kind"] == "multi"

    response = references_api.put(f"/api/references/cables/{row['record']['id']}/kind", json={"kind": "lan"})

    assert response.status_code == 200, response.text
    moved = next(item for item in references_api.get("/api/references/cables").json()["rows"] if item["kind"] == "lan")
    assert moved["manual"] is True and moved["record"]["fields"]["Число пар"] == "4"
    assert set(CABLE_COLUMNS["lan"]) <= set(moved["record"]["fields"])
