"""0204_equipment_show_flag: «Показывать в «Оборудовании»» — NOT NULL, default false; the backfill switches it on for every
unit an ERP agent reaches (a machine bridge or a marking printer's print agent) and leaves the rest off; the downgrade
drops the column only."""

import json
import sqlite3
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config

REVISION = "0204_equipment_show_flag"
PREVIOUS = "0203_marking_printer_control"
NOW = "2026-11-19 10:00:00.000000"


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


def _flags(connection) -> dict[str, int]:
    return {json.loads(fields)["Инв. номер"]: shown for fields, shown in connection.execute(
        "SELECT fields, show_in_equipment FROM equipment_machines")}


def test_agent_linked_units_are_switched_on_the_rest_stay_off(tmp_path):
    path = tmp_path / "flag.db"
    config = _config(path)
    command.upgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        bridged, marking, plain = _unit(connection, "ОБ.019"), _unit(connection, "ОБ.101"), _unit(connection, "ОБ.005")
        connection.execute("INSERT INTO production_machine_links (equipment_id, inventory_number, kind, token_id, machine_id, "
                           "bound_at, version) VALUES (?, 'ОБ.019', 'ob019_modbus', ?, 'guid-108', ?, 1)",
                           (bridged, uuid.uuid4().hex, NOW))
        connection.execute("INSERT INTO warehouse_marking_bindings (equipment_id, version, machine_id, printer) "
                           "VALUES (?, 1, 'godex-pc', 'Godex EZ2350i')", (marking,))
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert _flags(connection) == {"ОБ.019": 1, "ОБ.101": 1, "ОБ.005": 0}
        new = _unit(connection, "ОБ.017")
        assert connection.execute("SELECT show_in_equipment FROM equipment_machines WHERE id = ?", (new,)).fetchone() == (0,)
        try:
            connection.execute("UPDATE equipment_machines SET show_in_equipment = NULL WHERE id = ?", (plain,))
            raise AssertionError("NULL accepted")
        except sqlite3.IntegrityError:
            pass
    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(equipment_machines)")}
        assert "show_in_equipment" not in columns and connection.execute("SELECT count(*) FROM equipment_machines").fetchone() == (4,)
