"""Migration 0171: БД.ОБ units get models by the screen's grouping, БД.ОП cells, «Для инструмента» and the card templates'
«Оборудование» rows link by id only on an unambiguous match, roles are confirmed only where certain; the texts stay;
downgrade → upgrade lands on the same counts."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

BEFORE, AFTER = "0170_equipment_setup_resources", "0171_equipment_identity"


def _config(database: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    return config


UNITS = [  # (inv, name, article, maker, type, tool)
    ("ОБ.070.1", "Станция паяльная", "8620DX", "", "Оборудование", ""),
    ("ОБ.070.2", "станция  паяльная", "8620dx", "-", "Оборудование", ""),  # same model: case, spaces, «-» maker
    ("ОБ.044", "Блок питания", "APS", "", "Оборудование", ""),
    ("ОБ.050", "Сушилка", "", "Creality", "Оборудование", ""),
    ("ОБ.051", "Сушилка", "", "Элбэт", "Оборудование", ""),
    ("ОБ.060", "Компрессор поршневой", "K1", "", "Оборудование", ""),
    ("ОБ.061", "Охладитель воды", "CW", "", "Оборудование", ""),
    ("ОСН.001", "Матрица", "M-1", "", "Матрицы", "ОБ.044"),
    ("ОСН.002", "Матрица 2", "M-2", "", "Матрицы", "ОБ.999"),
    ("ОБ.080", "", "", "", "Оборудование", ""),  # nameless: a model of its own
]
OPERATIONS = [  # (code, «Машина»)
    ("SOLDER", "8620DX/Станция паяльная"),
    ("POWER", "APS / блок питания\nОБ.070.2"),
    ("DRY", "/Сушилка"),
    ("AIR", "Пневмостанок"),
    ("COOL", "CW/Охладитель воды"),  # a worded model operations name: operational, not confirmed
]
TEMPLATES = [  # (id, tooling)
    ("T1", [{"section": "Оборудование", "name": "8620DX(R) | Станция паяльная", "inventory_number": "ОБ.070.X"},
            {"section": "Инструмент", "name": "Пинцет", "inventory_number": ""}]),
    ("T2", [{"section": "Оборудование", "name": "Сушилка", "inventory_number": ""}]),
]


def _seed(database: Path) -> dict:
    ids = {}
    with sqlite3.connect(database) as connection:
        for inv, name, article, maker, kind, tool in UNITS:
            ids[inv] = uuid4()
            fields = {"Инв. номер": inv, "Наменование": name, "Артикул": article, "Производитель": maker, "Тип": kind,
                      "Для базы": f"{article}/{name}", "Для инструмента": tool}
            connection.execute(
                "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                " fields, mirror_key, edited_in_erp) VALUES (?, 'ob', ?, '', 'БД.ОБ', ?, ?, '', 0)",
                (ids[inv].hex, f"{article}/{name}", inv, json.dumps(fields, ensure_ascii=False)))
        for code, machine in OPERATIONS:
            ids[code] = uuid4()
            fields = {"opNumber": code, "opName": code, "Машина": machine}
            connection.execute(
                "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key,"
                " fields, mirror_key, edited_in_erp) VALUES (?, 'op', ?, '', 'БД.ОП', ?, ?, '', 0)",
                (ids[code].hex, code, code, json.dumps(fields, ensure_ascii=False)))
        for template_id, tooling in TEMPLATES:
            connection.execute(
                "INSERT INTO techcard_operation_templates (id, code, side, title, name, category, document, raw_rows,"
                " images, source_commit, imported_at) VALUES (?, ?, '', ?, ?, 'c', ?, '[]', '[]', 'x', '2026-10-01')",
                (template_id, template_id, template_id, template_id, json.dumps({"tooling": tooling}, ensure_ascii=False)))
    return ids


def _state(database: Path) -> dict:
    with sqlite3.connect(database) as connection:
        units = dict(connection.execute(
            "SELECT json_extract(fields, '$.\"Инв. номер\"'), equipment_model_id FROM tech_operation_records WHERE tab = 'ob'"))
        models = {row[0]: row[1:] for row in connection.execute(
            "SELECT id, name, role, role_confirmed FROM equipment_models")}
        links = connection.execute(
            "SELECT json_extract(r.fields, '$.opNumber'), l.position, l.model_id FROM equipment_operation_links l"
            " JOIN tech_operation_records r ON r.id = l.operation_id ORDER BY 1, 2").fetchall()
        tools = connection.execute("SELECT unit_id, target_id FROM equipment_unit_links").fetchall()
        templates = connection.execute(
            "SELECT template_id, position, candidates, model_id FROM equipment_template_tools ORDER BY 1, 2").fetchall()
        texts = dict(connection.execute(
            "SELECT json_extract(fields, '$.opNumber'), json_extract(fields, '$.\"Машина\"') FROM tech_operation_records"
            " WHERE tab = 'op'"))
    return {"units": units, "models": models, "links": links, "tools": tools, "templates": templates, "texts": texts}


def test_the_migration_links_by_id_only_on_a_single_match_and_the_round_trip_keeps_it(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    ids = _seed(database)
    command.upgrade(config, AFTER)
    state = _state(database)
    printed = capsys.readouterr().out

    units, models = state["units"], state["models"]
    assert len(models) == 9  # 10 units, the two stations one model
    assert units["ОБ.070.1"] == units["ОБ.070.2"] and units["ОБ.050"] != units["ОБ.051"]
    station, power = units["ОБ.070.1"], units["ОБ.044"]
    assert state["links"] == [("COOL", 0, units["ОБ.061"]), ("POWER", 0, power), ("POWER", 1, station),
                              ("SOLDER", 0, station)]  # DRY ambiguous, AIR nothing
    assert state["tools"] == [(ids["ОСН.001"].hex, ids["ОБ.044"].hex)]  # ОБ.999 is no unit
    assert state["templates"] == [("T1", 0, 1, station), ("T2", 0, 2, None)]
    roles = {name: (role, confirmed) for name, role, confirmed in models.values()}
    assert roles["Компрессор поршневой"] == ("supporting", 1)
    assert roles["Охладитель воды"] == ("operational", 0)  # worded, but an operation runs on it
    assert roles["Станция паяльная"] == ("operational", 1) and roles["Блок питания"] == ("operational", 1)
    assert roles["Матрица"] == ("operational", 0)
    assert state["texts"]["POWER"] == "APS / блок питания\nОБ.070.2"  # texts are not touched
    assert "связано 4, неоднозначно 1, не найдено 1" in printed

    with sqlite3.connect(database) as connection:  # the CHECK: a БД.ОБ unit has a model
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE tech_operation_records SET equipment_model_id = NULL WHERE tab = 'ob'")

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tech_operation_records)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert connection.execute("SELECT count(*) FROM tech_operation_records").fetchone()[0] == len(UNITS) + len(OPERATIONS)
    assert "equipment_model_id" not in columns and not tables & {"equipment_models", "equipment_operation_links"}
    command.upgrade(config, AFTER)
    again = _state(database)
    assert len(again["models"]) == 9 and len(again["links"]) == 4 and len(again["templates"]) == 2
