"""0202_manual_jobs: every CHECK / UNIQUE / FK of «Ручная работа» refuses its own bad row on the migrated schema, good rows
of each status pass, the downgrade drops both tables."""

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0202_manual_jobs"
PREVIOUS = "0201_instrument_reference_curve"
STARTED = "2026-11-19 10:00:00.000000"
LATER = "2026-11-19 11:00:00.000000"
EARLIER = "2026-11-19 09:00:00.000000"


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


def _reference(connection, unit: str) -> str:
    reference = uuid.uuid4().hex
    connection.execute("INSERT INTO equipment_setup_references (id, unit_id, wire, wire_key, version, setup_values, "
                       "created_by, created_by_name, created_at) VALUES (?, ?, 'ПВАМ 0,35', 'пвам 0,35', 1, '{}', ?, "
                       "'Слава', ?)", (reference, unit, uuid.uuid4().hex, STARTED))
    return reference


class Jobs:
    def __init__(self, connection, unit: str):
        self.connection, self.unit, self.number = connection, unit, 0

    def insert(self, **values):
        self.number += 1
        row = {"id": uuid.uuid4().hex, "number": self.number, "equipment_id": self.unit, "status": "running",
               "purpose": "Проба на образцах", "params": "{}", "operator_id": uuid.uuid4().hex,
               "operator_name": "Слава", "started_at": STARTED, "version": 1, **values}
        self.connection.execute(f"INSERT INTO production_manual_jobs ({', '.join(row)}) VALUES "
                                f"({', '.join('?' for _ in row)})", tuple(row.values()))
        return row


DONE = {"status": "done", "good_quantity": 10, "defect_quantity": 0, "finished_at": LATER,
        "material": "not_issued", "material_note": "обрезки"}
# constraint name → a row breaking only that rule (on a machine with no running job unless the rule is about it).
BAD = {
    "ck_production_manual_jobs_number": {"number": 0},
    "ck_production_manual_jobs_status": {"status": "paused"},
    "ck_production_manual_jobs_purpose": {"purpose": "   "},
    "ck_production_manual_jobs_wire_key": {"wire": "ПВАМ 0,35", "wire_key": ""},
    "ck_production_manual_jobs_contact_key": {"contact": "", "contact_key": "x"},
    "ck_production_manual_jobs_length": {"length_mm": 0},
    "ck_production_manual_jobs_planned": {"quantity_planned": 0},
    "ck_production_manual_jobs_count_source": {"count_source": "eye"},
    "ck_production_manual_jobs_counter": {"count_source": "plc"},
    "ck_production_manual_jobs_counter_start": {"counter_start": -1},
    "ck_production_manual_jobs_counts": {**DONE, "good_quantity": -1},
    "ck_production_manual_jobs_defect_reason": {**DONE, "defect_quantity": 2, "defect_reason": " "},
    "ck_production_manual_jobs_material": {**DONE, "material": "lost"},
    "ck_production_manual_jobs_not_issued": {**DONE, "material_note": ""},
    "ck_production_manual_jobs_running_material": {"material": "not_issued", "material_note": "обрезки"},
    "ck_production_manual_jobs_running_totals": {"good_quantity": 1},
    "ck_production_manual_jobs_done": {**DONE, "good_quantity": None},
    "ck_production_manual_jobs_finished": {"finished_at": LATER},
    "ck_production_manual_jobs_order": {**DONE, "finished_at": EARLIER},
    "ck_production_manual_jobs_cancel": {"status": "cancelled", "finished_at": LATER, "cancel_reason": " "},
    "ck_production_manual_jobs_operator": {"operator_name": " "},
    "ck_production_manual_jobs_version": {"version": 0},
}


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """One upgrade for the whole file; each test works on its own copy."""
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        path = tmp_path_factory.mktemp("manual-jobs") / "template.db"
        command.upgrade(_config(path), REVISION)
    return path


@pytest.fixture
def migrated(template, tmp_path, monkeypatch):
    """Foreign keys are switched on by the test after its machines are in (their catalog FKs are mutual)."""
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "manual.db"
    shutil.copyfile(template, path)
    connection = sqlite3.connect(path, isolation_level=None)
    yield connection, _config(path), path
    connection.close()


@pytest.mark.parametrize("name", sorted(BAD))
def test_each_check_refuses_its_own_bad_row(migrated, name):
    connection, _, _ = migrated
    jobs = Jobs(connection, _unit(connection, "ОБ.027"))
    with pytest.raises(sqlite3.IntegrityError, match=f"CHECK constraint failed: {name}$"):
        jobs.insert(**BAD[name])


def test_good_rows_unique_number_one_running_per_machine_and_foreign_keys(migrated):
    connection, config, path = migrated
    first, second = _unit(connection, "ОБ.019"), _unit(connection, "ОБ.099")
    reference = _reference(connection, first)
    connection.execute("PRAGMA foreign_keys = ON")
    jobs = Jobs(connection, first)
    jobs.insert(wire="ПВАМ 0,35", wire_key="пвам 0,35", length_mm="325.50", quantity_planned=20, reference_id=reference)
    jobs.insert(**DONE)
    jobs.insert(**{**DONE, "material": "pending", "material_note": "", "defect_quantity": 3, "defect_reason": "надрез"})
    jobs.insert(status="cancelled", finished_at=LATER, cancel_reason="не тот провод")
    jobs.insert(count_source="plc", counter_start=0, **DONE)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: production_manual_jobs.equipment_id"):
        jobs.insert()  # a second running job on ОБ.019
    other = Jobs(connection, second)
    other.number = 1
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: production_manual_jobs.number"):
        other.insert()  # Р-000002 is taken
    other.number = 100
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        other.insert(reference_id=reference)  # the эталон of ОБ.019 on ОБ.099
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        Jobs(connection, uuid.uuid4().hex).insert(number=500)  # no such machine
    other.insert()
    keys = {row[2]: (row[3], row[4], row[6]) for row in connection.execute("PRAGMA foreign_key_list(production_manual_jobs)")}
    assert keys["equipment_units"][2] == "RESTRICT" and keys["production_machine_writes"][2] == "SET NULL"
    assert keys["equipment_setup_references"][2] == "RESTRICT"
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        connection.execute("DELETE FROM equipment_setup_references WHERE id = ?", (reference,))
    connection.close()

    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as check:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"production_manual_jobs", "production_manual_job_issues"} & tables
    command.upgrade(config, REVISION)


def test_issue_rows_one_per_lot_one_writeoff_positive_quantity(migrated):
    connection, _, _ = migrated
    keys = {row[2]: row[6] for row in connection.execute("PRAGMA foreign_key_list(production_manual_job_issues)")}
    assert keys == {"production_manual_jobs": "RESTRICT", "warehouse_lots": "RESTRICT", "warehouse_writeoffs": "RESTRICT"}
    # Foreign keys stay off: lots and write-offs are not needed to prove CHECK and UNIQUE.
    job, lot = uuid.uuid4().hex, uuid.uuid4().hex

    def issue(lot_id, writeoff, quantity="1.5"):
        connection.execute("INSERT INTO production_manual_job_issues (id, job_id, lot_id, quantity, writeoff_id, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, job, lot_id, quantity, writeoff, STARTED))

    writeoff = uuid.uuid4().hex
    issue(lot, writeoff)
    with pytest.raises(sqlite3.IntegrityError, match="production_manual_job_issues.job_id, production_manual_job_issues.lot_id"):
        issue(lot, uuid.uuid4().hex)
    with pytest.raises(sqlite3.IntegrityError, match="production_manual_job_issues.writeoff_id"):
        issue(uuid.uuid4().hex, writeoff)
    with pytest.raises(sqlite3.IntegrityError, match="ck_production_manual_job_issues_quantity"):
        issue(uuid.uuid4().hex, uuid.uuid4().hex, "0")
