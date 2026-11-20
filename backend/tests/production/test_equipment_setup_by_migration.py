"""0213_equipment_setup_by: «Кто налаживает» — every existing machine becomes «Сам оператор» ('operator'), a new one too
by default; NOT NULL and CHECK refuse anything but 'operator' / 'technician'; the downgrade drops the column and keeps
the rows."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0213_equipment_setup_by"
PREVIOUS = "0212_machine_speed_history"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _unit(connection, inventory: str) -> str:
    unit, model = uuid.uuid4().hex, uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_models (id) VALUES (?)", (model,))
    connection.execute("INSERT INTO equipment_units (id, model_id, catalog, ob_id) VALUES (?, ?, 'ob', ?)", (unit, model, unit))
    connection.execute("INSERT INTO equipment_machines (id, tab, equipment_model_id, display_text, normalized_search, "
                       "source_sheet, sort_key, fields) VALUES (?, 'ob', ?, ?, '', '', '', ?)",
                       (unit, model, inventory, json.dumps({"Инв. номер": inventory})))
    return unit


def _setup(connection) -> dict[str, str]:
    return {json.loads(fields)["Инв. номер"]: setup_by for fields, setup_by in connection.execute(
        "SELECT fields, setup_by FROM equipment_machines")}


def test_every_machine_is_set_up_by_its_operator_and_only_two_values_are_allowed(tmp_path):
    path = tmp_path / "setup.db"
    config = _config(path)
    command.upgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        cutter, press, shown = _unit(connection, "ОБ.019"), _unit(connection, "ОБ.099"), _unit(connection, "ОБ.005")
        connection.execute("UPDATE equipment_machines SET show_in_equipment = TRUE WHERE id = ?", (shown,))
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert _setup(connection) == {"ОБ.019": "operator", "ОБ.099": "operator", "ОБ.005": "operator"}
        new = _unit(connection, "ОБ.017")
        assert connection.execute("SELECT setup_by FROM equipment_machines WHERE id = ?", (new,)).fetchone() == ("operator",)
        connection.execute("UPDATE equipment_machines SET setup_by = 'technician' WHERE id = ?", (press,))
        assert _setup(connection)["ОБ.099"] == "technician"
        for bad in ("Наладчик", "", None):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE equipment_machines SET setup_by = ? WHERE id = ?", (bad, cutter))
        # The other catalogs have no such setting.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(equipment_measuring_units)")}
        assert "setup_by" not in columns
    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(equipment_machines)")}
        assert "setup_by" not in columns and "show_in_equipment" in columns
        assert connection.execute("SELECT count(*) FROM equipment_machines").fetchone() == (4,)
        assert connection.execute("SELECT show_in_equipment FROM equipment_machines WHERE id = ?", (shown,)).fetchone() == (1,)
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert set(_setup(connection).values()) == {"operator"}
