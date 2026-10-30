"""0131_prov_cable_kind (owner 20.10.2026: «у каждого своя структура»): every БД.ПРОВ row stores its cable list and each
list has its own columns. The migration keeps the rows, their ids, every filled value and the stock links; it drops only
empty cells of another list's columns and splits the owner's columns by list. The database refuses a БД.ПРОВ row without
a list and a row of another tab with one."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.references.application import cable_catalog
from tests.references.test_cables import move_cable
from app.modules.references.cables import CableKind
from app.modules.references.domain import CABLE_COLUMNS, CABLE_WRITABLE, cable_fields
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.workflows import etk_refresh  # noqa: F401  (its before_flush hook is live while the migration runs)
from tests.references.test_cables import _mirror
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)

BEFORE, AFTER = "0130_td_issue_place", "0131_prov_cable_kind"
SHEET = (
    "Марка", "Тип провода", "Стандарт", "Производители", "Сечение, мм2", "AWG", "Жила: проволок × диаметр, мм",
    "Число жил", "Цвета", "Наружный диаметр, мм", "Изоляция", "Экран", "Волновое сопротивление, Ом", "Темп. от, °C",
    "Темп. до, °C", "Напряжение, В", "Ток, А", "Масса, г/м", "Закупок", "Цена посл., ₽/м без НДС", "Поставщик",
    "Ссылка на datasheet", "Источник данных", "Категория",
)
OWN = ("Материал диэлектрика", "Материал изоляции жилы", "Полка")


def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


def _prov(**cells: str) -> dict:
    """A БД.ПРОВ row as the shared column set left it: every sheet column and every own column, empty unless given."""
    return {**{name: "" for name in (*SHEET, *OWN)}, **cells}


ROWS = {
    "rg316": _prov(**{"Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1", "Волновое сопротивление, Ом": "50",
                      "Материал диэлектрика": "PTFE"}),
    "ul1061": _prov(**{"Марка": "UL1061 28AWG", "Тип провода": "монтажный", "Число жил": "1", "AWG": "28",
                       "Ток, А": "1,5"}),
    "kspvg": _prov(**{"Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4",
                      "Материал изоляции жилы": "ПВХ", "Категория": "Сигнальные"}),
    # a multi-core cable with a current filled: «Ток, А» is no multi column, the value stays
    "liyy": _prov(**{"Марка": "LiYY 3x0,25", "Тип провода": "многожильный", "Число жил": "3", "Ток, А": "2"}),
    # moved to «Многожильные» by hand
    "pvam": _prov(**{"Марка": "ПВАМ 0,50", "Тип провода": "силовой", "Число жил": "1"}),
    # moved to the closed «Прочие» by hand, one core → монтажный
    "flry": _prov(**{"Марка": "FLRY-B 0,35", "Тип провода": "автомобильный", "Число жил": "1"}),
    # no type, no cores, no series: used to be «прочие» by the rule → монтажный
    "wire_x": {"Марка": "Проволока", "Тип провода": "", "Число жил": "", "Стандарт / производитель": "",
               "Старое поле": "было"},
}
KINDS = {"rg316": "coax", "ul1061": "wire", "kspvg": "multi", "liyy": "multi", "pvam": "multi", "flry": "wire",
         "wire_x": "wire"}


def _seed(connection) -> dict:
    ids = {name: uuid4().hex for name in ROWS}
    for name, fields in ROWS.items():
        connection.execute(
            "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
            " mirror_key, edited_in_erp) VALUES (?, 'prov', ?, '', 'БД.ПРОВ', ?, ?, '', 0)",
            (ids[name], fields["Марка"], fields["Марка"], json.dumps(fields, ensure_ascii=False)))
    for tab, display, fields in (("mat", "ТУТ 3/1", {"Наименование": "ТУТ 3/1", "Цвет": ""}),
                                 ("kab_coax", "RG-316", {"Кабель": "RG-316", "D1": "0,5", "D2": "1,48", "D3": "1,95"})):
        connection.execute(
            "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
            " mirror_key, edited_in_erp) VALUES (?, ?, ?, '', '', ?, ?, '', 0)",
            (uuid4().hex, tab, display, display, json.dumps(fields, ensure_ascii=False)))
    entries = [("cable_kind", "ПВАМ 0,50", "пвам0,50", "multi", 0), ("cable_kind", "FLRY-B 0,35", "flryb0,35", "other", 1),
               ("custom_column", "mat:Ячейка", "mat:ячейка", "Ячейка", 0)]
    entries += [("custom_column", f"prov:{name}", f"prov:{name.casefold()}", name, position + 1)
                for position, name in enumerate(OWN)]
    for kind, key, normalized, value, position in entries:
        connection.execute("INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)"
                           " VALUES (?, ?, ?, ?, ?, ?)", (uuid4().hex, kind, key, normalized, value, position))
    lot = uuid4().hex
    connection.execute(
        "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
        " unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
        " VALUES (?, 1, 'ЦАР', 'FRX.26.0000042', '', 'RG316', '', 'Кабель', '', '', 'м', '', 5, 0, 0, '[]')", (lot,))
    for record in ("rg316", "ul1061"):
        connection.execute(
            "INSERT INTO reference_stock_links (id, occurred_at, event, lot_id, warehouse, grn, package, article, name,"
            " record_id, record_tab, record_text, changes, actor_id, actor_name)"
            " VALUES (?, '2026-10-13 11:30:00', 'linked', ?, 'ЦАР', 'FRX.26.0000042', '', 'RG316', 'Кабель', ?, 'prov',"
            " ?, '[]', ?, 'Кладовщик')", (uuid4().hex, lot, ids[record], ROWS[record]["Марка"], uuid4().hex))
    return ids


def _state(connection) -> dict:
    prov = {row[0]: json.loads(row[1]) for row in connection.execute(
        "SELECT id, fields FROM tech_operation_records WHERE tab = 'prov'")}
    return {
        "tabs": dict(connection.execute("SELECT tab, COUNT(*) FROM tech_operation_records GROUP BY tab").fetchall()),
        "prov_ids": set(prov),
        "filled": {(row_id, key, value) for row_id, fields in prov.items() for key, value in fields.items() if str(value).strip()},
        "links": sorted(connection.execute("SELECT id, record_id FROM reference_stock_links").fetchall()),
        "others": sorted(connection.execute(
            "SELECT id, tab, fields FROM tech_operation_records WHERE tab <> 'prov'").fetchall()),
        "refresh": connection.execute("SELECT COUNT(*) FROM techcard_refresh_requests").fetchone()[0],
    }


def _insert(connection, tab: str, kind):
    connection.execute(
        "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
        " mirror_key, edited_in_erp, cable_kind) VALUES (?, ?, 'X', '', '', 'X', '{}', '', 0, ?)", (uuid4().hex, tab, kind))


def test_the_migration_stores_each_list_keeps_every_value_and_splits_the_own_columns(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        ids = _seed(connection)
        before = _state(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database) as connection:
        after = _state(connection)
        kinds = dict(connection.execute("SELECT id, cable_kind FROM tech_operation_records WHERE tab = 'prov'").fetchall())
        fields = {row[0]: json.loads(row[1]) for row in connection.execute(
            "SELECT id, fields FROM tech_operation_records WHERE tab = 'prov'")}
        others = connection.execute("SELECT COUNT(*) FROM tech_operation_records WHERE tab <> 'prov' AND cable_kind IS NOT NULL").fetchone()[0]
        own = sorted(connection.execute(
            "SELECT key, value, position FROM reference_entries WHERE kind = 'custom_column'").fetchall())

    # invariants: rows per tab, the БД.ПРОВ ids, every filled value, the stock links, the other tabs untouched,
    # no ETK refresh queued
    assert after == before
    assert None not in kinds.values() and others == 0
    assert {name: kinds[ids[name]] for name in ROWS} == KINDS
    # only empty cells of another list's columns left the rows
    assert "AWG" not in fields[ids["rg316"]] and "Ток, А" not in fields[ids["rg316"]] and "Категория" not in fields[ids["rg316"]]
    assert fields[ids["rg316"]]["Материал диэлектрика"] == "PTFE" and fields[ids["rg316"]]["Полка"] == ""
    assert "Волновое сопротивление, Ом" not in fields[ids["ul1061"]] and "Материал диэлектрика" not in fields[ids["ul1061"]]
    assert fields[ids["ul1061"]]["AWG"] == "28" and fields[ids["ul1061"]]["Ток, А"] == "1,5"
    assert fields[ids["liyy"]]["Ток, А"] == "2" and "Ток, А" not in fields[ids["kspvg"]]  # filled stays, empty goes
    assert fields[ids["kspvg"]]["Категория"] == "Сигнальные" and fields[ids["kspvg"]]["Материал изоляции жилы"] == "ПВХ"
    assert fields[ids["wire_x"]] == {"Марка": "Проволока", "Тип провода": "", "Число жил": "", "Старое поле": "было"}
    # the list's columns as 0129 froze them (0130 turned the монтажные «Цвета» into one «Цвет»)
    # (and the columns removed from the монтажные 23.10 and 25.10.2026 were still its columns then)
    assert set(fields[ids["ul1061"]]) == (set(CABLE_WRITABLE["wire"]) - {"Цвет", "Дата цены"}) | {"Цвета", "Полка", "Изоляция", "Экран", "Число жил", "Ток, А"}
    # the owner's columns: by the lists that fill them, none filled → all three
    assert own == sorted([
        ("mat:Ячейка", "Ячейка", 0), ("prov:coax:Материал диэлектрика", "Материал диэлектрика", 1),
        ("prov:multi:Материал изоляции жилы", "Материал изоляции жилы", 2),
        ("prov:coax:Полка", "Полка", 3), ("prov:wire:Полка", "Полка", 3), ("prov:multi:Полка", "Полка", 3),
    ])

    # the database itself keeps every БД.ПРОВ row in one list and every other row in none
    connection = sqlite3.connect(database)
    try:
        for tab, kind in (("prov", None), ("prov", "other"), ("prov", ""), ("mat", "wire"), ("ter", "coax")):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(connection, tab, kind)
        _insert(connection, "prov", "multi")
        _insert(connection, "mat", None)
        connection.rollback()
    finally:
        connection.close()

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tech_operation_records)")}
        merged = sorted(connection.execute("SELECT key, value, position FROM reference_entries WHERE kind = 'custom_column'").fetchall())
        assert "cable_kind" not in columns
        assert merged == sorted([("mat:Ячейка", "Ячейка", 0), ("prov:Материал диэлектрика", "Материал диэлектрика", 1),
                                 ("prov:Материал изоляции жилы", "Материал изоляции жилы", 2), ("prov:Полка", "Полка", 3)])
        assert _state(connection) == after  # downgrade loses nothing


def test_a_row_is_shaped_to_its_list_without_losing_a_filled_value():
    fields = {"Марка": "RG316", "AWG": "", "Ток, А": "3", "Чужое": "", "Своё": "x"}
    assert cable_fields(CABLE_COLUMNS["coax"], fields) == {"Марка": "RG316", "Ток, А": "3", "Своё": "x"}
    padded = cable_fields(CABLE_COLUMNS["coax"], {"Марка": "RG316"}, pad=CABLE_COLUMNS["coax"])
    assert list(padded) == list(CABLE_COLUMNS["coax"]) and padded["Марка"] == "RG316"


def test_moving_a_row_takes_the_new_list_columns_and_keeps_its_values(tmp_path, uow):  # noqa: F811
    load_mirror(_mirror(tmp_path), uow, dry_run=False, now=NOW)
    rows = {row.cable: row for row in cable_catalog(uow).rows}
    ul = rows["UL1061 28AWG"]
    assert ul.record.cable_kind == "wire" and ul.record.fields["AWG"] == "28"
    assert "Волновое сопротивление, Ом" not in ul.record.fields  # the mirror row came with the list's columns only
    rg = rows["RG316"]
    assert rg.record.cable_kind == "coax" and "AWG" not in rg.record.fields  # an empty AWG is no coax cell

    moved = move_cable(ul.record.id, CableKind.COAX, uow)

    assert (moved.kind, moved.record.cable_kind, moved.manual) == (CableKind.COAX, "coax", True)
    assert moved.record.fields["AWG"] == "28"  # a filled value is never dropped, even in another list's column
    assert cable_catalog(uow).rows and {row.cable: row.kind for row in cable_catalog(uow).rows}["UL1061 28AWG"] == CableKind.COAX
