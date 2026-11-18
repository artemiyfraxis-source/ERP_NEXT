"""0195_equipment_setup_model: the tables refuse bad states themselves (constraints, not rollbacks)."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0195_equipment_setup_model"
TABLES = ("equipment_setup_models", "equipment_setup_model_parameters", "equipment_setup_unit_overrides",
          "equipment_setup_unit_parameters", "equipment_setup_references", "equipment_setup_configurations",
          "equipment_setup_machine_corrections")
NOW = "2026-11-19 09:00:00"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _unit(connection) -> tuple[str, str]:
    unit, model = uuid.uuid4().hex, uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_models (id, name, article, maker, role, role_confirmed) "
                       "VALUES (?, 'Станок', '', '', 'operational', 0)", (model,))
    connection.execute("INSERT INTO equipment_units (id, model_id, catalog, ob_id) VALUES (?, ?, 'ob', ?)",
                       (unit, model, unit))
    connection.execute("INSERT INTO equipment_machines (id, tab, equipment_model_id, display_text, normalized_search, "
                       "source_sheet, sort_key, fields, mirror_key, edited_in_erp) "
                       "VALUES (?, 'ob', ?, 'Станок', '', '', '', ?, '', 0)",
                       (unit, model, json.dumps({"Инв. номер": "ОБ.900"})))
    return unit, model


def _reference(connection, unit, *, wire="ПВАМ 0,35", contact="", version=1) -> str:
    identifier = uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_setup_references (id, unit_id, wire, wire_key, contact, contact_key, "
                       "version, setup_values, note, created_by, created_by_name, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, '{}', '', ?, 'Слава', ?)",
                       (identifier, unit, wire, wire.casefold(), contact, contact.casefold(), version,
                        uuid.uuid4().hex, NOW))
    return identifier


def test_setup_model_tables_refuse_bad_rows_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "setup.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    unit, model = _unit(connection)
    other, _ = _unit(connection)
    connection.execute("COMMIT")

    def refused(sql, args):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, args)

    insert_model = ("INSERT INTO equipment_setup_models (model_id, requires_setup, reason, updated_at, version) "
                    "VALUES (?, ?, '', ?, 1)")
    refused(insert_model, (model, "maybe", NOW))
    connection.execute(insert_model, (model, "full", NOW))
    insert_parameter = ("INSERT INTO equipment_setup_model_parameters (model_id, param_key, position, label, unit, source) "
                        "VALUES (?, ?, 0, ?, 'мм', ?)")
    connection.execute(insert_parameter, (model, "strip_front_mm", "Зачистка начала", "etk"))
    refused(insert_parameter, (model, "diameter_mm", "Диаметр", "excel"))  # source not in the list
    refused(insert_parameter, (model, "diameter_mm", " ", "wire"))  # no label
    refused(insert_parameter, (model, "strip_front_mm", "Ещё раз", "etk"))  # one row per key

    connection.execute("INSERT INTO equipment_setup_unit_overrides (unit_id, requires_setup, reason, updated_at, version) "
                       "VALUES (?, NULL, 'свой', ?, 1)", (unit, NOW))
    connection.execute("INSERT INTO equipment_setup_unit_parameters (unit_id, param_key, removed, position, label, source) "
                       "VALUES (?, 'strip_front_mm', 1, 0, '', 'manual')", (unit,))
    refused("INSERT INTO equipment_setup_unit_parameters (unit_id, param_key, removed, position, label, source) "
            "VALUES (?, 'blades', 0, 0, '', 'tooling')", (unit,))  # a replacement needs its label

    first = _reference(connection, unit)
    _reference(connection, unit, version=2)
    with pytest.raises(sqlite3.IntegrityError):
        _reference(connection, unit, version=2)  # one row per version
    with pytest.raises(sqlite3.IntegrityError):
        _reference(connection, unit, wire="")  # neither wire nor contact
    foreign = _reference(connection, other)

    insert_configuration = ("INSERT INTO equipment_setup_configurations (unit_id, reference_id, setup_values, set_by, "
                            "set_by_name, set_at, version) VALUES (?, ?, '{}', ?, 'Слава', ?, 1)")
    refused(insert_configuration, (unit, foreign, uuid.uuid4().hex, NOW))  # another unit's эталон
    connection.execute(insert_configuration, (unit, first, uuid.uuid4().hex, NOW))

    insert_correction = ("INSERT INTO equipment_setup_machine_corrections (unit_id, parameter_key, wire_key, offset_value, "
                         "set_by, set_by_name, set_at, version) VALUES (?, 'strip_front_mm', ?, ?, ?, 'Слава', ?, 1)")
    connection.execute(insert_correction, (unit, "", -0.3, uuid.uuid4().hex, NOW))
    connection.execute(insert_correction, (unit, "пвам 0,35", 0.2, uuid.uuid4().hex, NOW))
    refused(insert_correction, (unit, "", 0.1, uuid.uuid4().hex, NOW))  # one general correction per parameter
    refused(insert_correction, (unit, "мгтф", 1500, uuid.uuid4().hex, NOW))
    connection.close()

    command.downgrade(config, "-1")
    with sqlite3.connect(path) as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables.intersection(TABLES)
