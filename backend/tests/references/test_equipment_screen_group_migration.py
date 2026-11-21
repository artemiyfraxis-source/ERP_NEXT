"""0216_equipment_screen_group: «Группа на экране» of an equipment model — backfilled from the keyword rule for models
with machines of the tab «Оборудование» (a model whose units disagree takes the earliest rule), NULL for the rest;
CHECK refuses an unknown code; the downgrade drops the column and keeps the rows."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0216_equipment_screen_group"
PREVIOUS = "0214_warehouse_document_devices"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _model(connection, *, article="", maker="") -> str:
    model = uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_models (id, article, maker) VALUES (?, ?, ?)", (model, article, maker))
    return model


def _machine(connection, model: str, inventory: str, name: str, operations: str = "", **extra) -> str:
    unit = uuid.uuid4().hex
    fields = {"Инв. номер": inventory, "Наменование": name, "Операции": operations, **extra}
    connection.execute("INSERT INTO equipment_units (id, model_id, catalog, ob_id) VALUES (?, ?, 'ob', ?)", (unit, model, unit))
    connection.execute("INSERT INTO equipment_machines (id, tab, equipment_model_id, display_text, normalized_search, "
                       "source_sheet, sort_key, fields) VALUES (?, 'ob', ?, ?, '', '', '', ?)",
                       (unit, model, name, json.dumps(fields, ensure_ascii=False)))
    return unit


def _measuring(connection, model: str, inventory: str, name: str) -> None:
    unit = uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_units (id, model_id, catalog, ob_measuring_id) VALUES (?, ?, 'ob_measuring', ?)",
                       (unit, model, unit))
    connection.execute("INSERT INTO equipment_measuring_units (id, tab, equipment_model_id, display_text, "
                       "normalized_search, source_sheet, sort_key, fields) VALUES (?, 'ob_measuring', ?, ?, '', '', '', ?)",
                       (unit, model, name, json.dumps({"Инв. номер": inventory, "Наменование": name}, ensure_ascii=False)))


def _group(connection, model: str):
    return connection.execute("SELECT screen_group FROM equipment_models WHERE id = ?", (model,)).fetchone()[0]


def test_models_get_their_group_from_the_keyword_rule_and_only_nine_codes_are_allowed(tmp_path, capsys):
    path = tmp_path / "groups.db"
    config = _config(path)
    command.upgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        models = {
            "ОБ.097": _model(connection), "ОБ.098": _model(connection), "ОБ.099": _model(connection),
            "ОБ.057": _model(connection, article="TP80E", maker="Supvan technology"), "ОБ.073": _model(connection),
            "ОБ.036": _model(connection), "ОБ.015": _model(connection), "ОБ.068": _model(connection),
            "ОБ.070": _model(connection), "ОБ.101": _model(connection), "СИ.110": _model(connection),
            "empty": _model(connection),
        }
        _machine(connection, models["ОБ.097"], "ОБ.097", "Станок резки/ обжим+обжим", "Автомат резка + опрессовка")
        _machine(connection, models["ОБ.098"], "ОБ.098", "Станок резки/ обжим+лужение",
                 "Автомат резка + опрессовка, Лужение")
        # The two presses of one model: crimp.
        _machine(connection, models["ОБ.099"], "ОБ.099.1", "Станок для обжима терминалов без зачистки", "Опрессовка терминалов")
        _machine(connection, models["ОБ.099"], "ОБ.099.2", "Станок для обжима терминалов без зачистки", "Опрессовка терминалов")
        # «Маркер» in the name would say marking — the printer maker wins.
        _machine(connection, models["ОБ.057"], "ОБ.057", "Принтер кабельных маркеров и термоусаживаемых трубок",
                 **{"Артикул": "TP80E", "Производитель": "Supvan technology"})
        _machine(connection, models["ОБ.073"], "ОБ.073", "Принтер 3D экструзионный", **{"Производитель": "Bambu Lab"})
        _machine(connection, models["ОБ.036"], "ОБ.036.1", "Станок для зачистки чего?")
        _machine(connection, models["ОБ.015"], "ОБ.015", "Автомат мерной резки проводов",
                 "Резка с разделкой, Резка линейных материалов, Резка")
        _machine(connection, models["ОБ.068"], "ОБ.068", "Измеритель коэффициента стоячей волны по напряжению", "ОТК")
        _machine(connection, models["ОБ.070"], "ОБ.070.1", "Станция паяльная термовоздушная", "Пайка, Усадка")
        # Units of one model that disagree: the earliest rule (crimp before marking).
        _machine(connection, models["ОБ.101"], "ОБ.101.1", "Маркиратор проводов флажковый", "Монтаж маркировки")
        _machine(connection, models["ОБ.101"], "ОБ.101.2", "Маркиратор проводов флажковый", "Опрессовка")
        _measuring(connection, models["СИ.110"], "СИ.110", "Пресс-микрометр для опрессовки")
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        got = {key: _group(connection, model) for key, model in models.items()}
        assert got == {
            "ОБ.097": "cut_crimp", "ОБ.098": "cut_crimp", "ОБ.099": "crimp", "ОБ.057": "printers", "ОБ.073": "other",
            "ОБ.036": "strip", "ОБ.015": "cut", "ОБ.068": "analyzers", "ОБ.070": "solder", "ОБ.101": "crimp",
            "СИ.110": None, "empty": None,
        }
        connection.execute("UPDATE equipment_models SET screen_group = 'marking' WHERE id = ?", (models["ОБ.101"],))
        connection.execute("UPDATE equipment_models SET screen_group = NULL WHERE id = ?", (models["ОБ.073"],))
        for bad in ("xxx", "", "Резка"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE equipment_models SET screen_group = ? WHERE id = ?", (bad, models["ОБ.015"]))
    assert "0216 screen_group: ОБ.099.1, ОБ.099.2 → crimp" in capsys.readouterr().out
    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(equipment_models)")}
        assert "screen_group" not in columns and "role" in columns
        assert connection.execute("SELECT count(*) FROM equipment_models").fetchone() == (len(models),)
        assert connection.execute("SELECT count(*) FROM equipment_machines").fetchone() == (12,)
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert _group(connection, models["ОБ.097"]) == "cut_crimp"


def test_the_codes_of_the_screen_match_the_check_of_the_migration():
    """`domain.SCREEN_GROUPS` and the CHECK of 0216 hold the same codes: a new code needs a new migration that widens
    `ck_equipment_models_screen_group` (the repository answers an unknown code with a Russian 422 before the CHECK)."""
    import importlib.util

    from app.modules.references.domain import SCREEN_GROUPS
    from app.modules.references.infrastructure import EquipmentModelRow

    path = Path(__file__).parents[2] / "migrations" / "versions" / "0216_equipment_screen_group.py"
    spec = importlib.util.spec_from_file_location("migration_0216", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert set(SCREEN_GROUPS) == set(migration.CODES)
    check = next(constraint for constraint in EquipmentModelRow.__table__.constraints
                 if constraint.name == "ck_equipment_models_screen_group")
    assert all(f"'{code}'" in str(check.sqltext) for code in migration.CODES)
