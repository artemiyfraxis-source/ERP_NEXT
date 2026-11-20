"""0209_machine_work_counter: «Сделано» of a manual job (NULL or >= 0, with its moment, not before the start) and the
downtime of a machine (FK RESTRICT onto equipment_units, no task, one open per machine) are held by the schema; the
downgrade refuses while such rows exist and restores the previous schema once they are gone."""

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0209_machine_work_counter"
PREVIOUS = "0208_marking_outside_prints"
STARTED = "2026-11-20 10:00:00.000000"
LATER = "2026-11-20 11:00:00.000000"
EARLIER = "2026-11-20 09:00:00.000000"


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


def _job(connection, unit: str, number: int, **values) -> dict:
    row = {"id": uuid.uuid4().hex, "number": number, "equipment_id": unit, "status": "running",
           "purpose": "Проба", "params": "{}", "operator_id": uuid.uuid4().hex, "operator_name": "Слава",
           "started_at": STARTED, "version": 1, **values}
    connection.execute(f"INSERT INTO production_manual_jobs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                       tuple(row.values()))
    return row


def _downtime(connection, **values) -> dict:
    row = {"id": uuid.uuid4().hex, "operator_name": "", "reason": "equipment", "comment": "", "started_at": STARTED,
           "started_by_id": uuid.uuid4().hex, "started_by_name": "Мастер", "project": "", "task_number": "",
           "item": "ОБ.027.1", **values}
    connection.execute(f"INSERT INTO production_downtimes ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                       tuple(row.values()))
    return row


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        path = tmp_path_factory.mktemp("machine-counter") / "template.db"
        command.upgrade(_config(path), REVISION)
    return path


@pytest.fixture
def migrated(template, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "counter.db"
    shutil.copyfile(template, path)
    connection = sqlite3.connect(path, isolation_level=None)
    yield connection, _config(path), path
    connection.close()


BAD_JOBS = {
    "ck_production_manual_jobs_progress": {"progress_quantity": -1, "progress_at": LATER},
    "ck_production_manual_jobs_progress_at": {"progress_quantity": 3},
    "ck_production_manual_jobs_progress_order": {"progress_quantity": 3, "progress_at": EARLIER},
}


@pytest.mark.parametrize("name", sorted(BAD_JOBS))
def test_each_progress_check_refuses_its_own_bad_row(migrated, name):
    connection, _, _ = migrated
    with pytest.raises(sqlite3.IntegrityError, match=f"CHECK constraint failed: {name}$"):
        _job(connection, _unit(connection, "ОБ.027.1"), 1, **BAD_JOBS[name])


def test_progress_while_running_stays_after_the_finish_and_the_old_checks_hold(migrated):
    connection, _, _ = migrated
    unit = _unit(connection, "ОБ.027.1")
    _job(connection, unit, 1, progress_quantity=0, progress_at=STARTED)
    _job(connection, unit, 2, status="done", good_quantity=12, defect_quantity=0, finished_at=LATER,
         material="pending", progress_quantity=25, progress_at=LATER)  # more than planned, kept as history
    with pytest.raises(sqlite3.IntegrityError, match="production_manual_jobs.equipment_id"):
        _job(connection, unit, 3, progress_quantity=1, progress_at=LATER)  # still one running job per machine
    with pytest.raises(sqlite3.IntegrityError, match="ck_production_manual_jobs_running_totals"):
        _job(connection, _unit(connection, "ОБ.027.2"), 4, good_quantity=1)


def test_machine_downtime_one_open_per_machine_no_task_restrict(migrated):
    connection, _, _ = migrated
    first, second = _unit(connection, "ОБ.027.1"), _unit(connection, "ОБ.027.2")
    _downtime(connection, equipment_id=first)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: production_downtimes.equipment_id"):
        _downtime(connection, equipment_id=first, reason="no_material")
    _downtime(connection, equipment_id=first, ended_at=LATER, ended_by_id=uuid.uuid4().hex)  # history of the machine
    _downtime(connection, equipment_id=second)
    task = uuid.uuid4().hex
    _downtime(connection, task_id=task, project="П3-0007", task_number="ПЗ-0001")  # a task downtime as before
    with pytest.raises(sqlite3.IntegrityError, match="production_downtimes.task_id"):
        _downtime(connection, task_id=task)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: ck_production_downtimes_equipment$"):
        _downtime(connection, equipment_id=uuid.uuid4().hex, task_id=uuid.uuid4().hex)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: ck_production_downtimes_equipment$"):
        _downtime(connection, equipment_id=uuid.uuid4().hex, operation_id=uuid.uuid4().hex)
    keys = {row[2]: row[6] for row in connection.execute("PRAGMA foreign_key_list(production_downtimes)")}
    assert keys["equipment_units"] == "RESTRICT"
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN")
    connection.execute("DELETE FROM equipment_machines WHERE id = ?", (second,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        connection.execute("DELETE FROM equipment_units WHERE id = ?", (second,))  # RESTRICT: refused at once
    connection.execute("ROLLBACK")


def test_downgrade_refuses_while_counters_or_machine_downtimes_exist(migrated):
    connection, config, path = migrated
    unit = _unit(connection, "ОБ.027.1")
    job = _job(connection, unit, 1, progress_quantity=5, progress_at=LATER)
    downtime = _downtime(connection, equipment_id=unit)
    with pytest.raises(RuntimeError, match="откат стёр бы"):
        command.downgrade(config, PREVIOUS)
    connection.execute("UPDATE production_manual_jobs SET progress_quantity = NULL, progress_at = NULL WHERE id = ?",
                       (job["id"],))
    with pytest.raises(RuntimeError, match="откат стёр бы"):
        command.downgrade(config, PREVIOUS)
    connection.execute("DELETE FROM production_downtimes WHERE id = ?", (downtime["id"],))
    connection.close()
    command.downgrade(config, PREVIOUS)
    with sqlite3.connect(path) as check:
        downtime_columns = {row[1] for row in check.execute("PRAGMA table_info(production_downtimes)")}
        job_columns = {row[1] for row in check.execute("PRAGMA table_info(production_manual_jobs)")}
        indexes = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "equipment_id" not in downtime_columns and not {"progress_quantity", "progress_at"} & job_columns
    assert {"uq_production_downtimes_open_task", "ux_production_manual_jobs_running"} <= indexes
    assert "uq_production_downtimes_open_equipment" not in indexes
    command.upgrade(config, REVISION)
