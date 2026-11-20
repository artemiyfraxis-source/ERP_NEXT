"""Generic machine operation queue (T4): open ETK steps of running tasks per physical unit, cuts excluded."""

from uuid import UUID, uuid4

import pytest

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionTaskRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import batch, document
from tests.production.test_cutting_queue import POOL, _no_stale_references, issue, seed_units, standalone  # noqa: F401

URL = "/api/production/machine-queue"
JOBS = "/api/production/manual-jobs"


def tooled(doc, **tools):
    """Tooling per ETK position (1-based): {"op1": "ОБ.100.2", ...}; a value of (inventory, program) sets the program."""
    for key, value in tools.items():
        inventory, program = value if isinstance(value, tuple) else (value, "P1")
        doc["operations"][int(key[2:]) - 1]["card"]["tooling"] = [
            {"section": "Оборудование", "inventory_number": inventory, "name": "BZW-D2.0S(R) | автомат",
             "program": program, "parameters": "confirmed" if program else ""}]
    return doc


def mixed():
    """cut -> ОБ.100.2 (excluded as a cable cut), labels -> pool of ОБ.100, tubes -> ОБ.101.1, assembly -> ОБ.100.2."""
    return standalone(tooled(document(), op1="ОБ.100.2", op2=POOL, op3="ОБ.101.1", op4="ОБ.100.2"))


def queue(client, unit):
    response = client.get(URL, params={"equipment_id": str(unit)})
    assert response.status_code == 200, response.text
    return response.json()


def positions(client, unit):
    return sorted(row["etk_position"] for row in queue(client, unit)["rows"])


def test_unit_and_pool_match_and_cuts_and_other_models_are_left_out(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", mixed())])
    answer = queue(client, ids["ОБ.100.2"])
    assert (answer["equipment_id"], answer["inventory"]) == (str(ids["ОБ.100.2"]), "ОБ.100.2")
    assert positions(client, ids["ОБ.100.2"]) == [2, 4]  # the pool step and its own step; the cut (1) is cutting-queue work
    assert positions(client, ids["ОБ.100.1"]) == [2]  # only the pool step
    assert positions(client, ids["ОБ.101.1"]) == [3]  # another model: its own step only
    row = next(item for item in answer["rows"] if item["etk_position"] == 4)
    assert (row["project"], row["item"], row["planned"], row["done"], row["left"], row["job"]) == \
        ("708К.", "КБ1", "10", "0", "10", None)
    assert row["key"] == f"{row['task_id']}:{row['operation_id']}:4" and row["step"] and row["task_number"]
    assert row["etk"] == [{"label": "BZW-D2.0S(R) | автомат", "value": "P1 · confirmed", "source": "etk"}]
    assert row["warnings"] == []


def test_the_version_follows_the_rows(production_api):
    client, _ = production_api
    ids = seed_units()
    empty = queue(client, ids["ОБ.100.2"])
    assert empty["rows"] == []
    batch(client, [issue("708К.", "КБ1", mixed())])
    assert queue(client, ids["ОБ.100.2"])["version"] != empty["version"]
    assert queue(client, ids["ОБ.100.2"])["version"] == queue(client, ids["ОБ.100.2"])["version"]


def test_a_missing_etk_value_is_a_warning_and_keeps_the_row(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", standalone(tooled(document(), op1="ОБ.100.2", op4=("ОБ.100.2", ""))))])
    [row] = [item for item in queue(client, ids["ОБ.100.2"])["rows"] if item["etk_position"] == 4]
    assert row["warnings"] == ["нет в ЭТК: режим станка"]
    assert row["etk"] == [{"label": "Режим станка", "value": "", "source": "missing"}]


def test_a_step_whose_pf_is_done_is_dropped(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", mixed())])
    [row] = queue(client, ids["ОБ.100.1"])["rows"]
    posted = client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/manual-output",
                         json={"good_quantity": 10})
    assert posted.status_code == 200, posted.text
    assert queue(client, ids["ОБ.100.1"])["rows"] == []
    refused = _start(client, ids["ОБ.100.1"], {"task_id": row["task_id"], "operation_id": row["operation_id"],
                                              "etk_position": 2})
    assert refused.status_code == 422 and "сдан" in refused.json()["detail"]


def test_the_site_of_the_unit_filters_tasks(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", mixed())])
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, UUID("10000000-0000-0000-0000-000000000001")).site = ""  # the viewer sees all sites
        for row in uow.session.query(ProductionTaskRow).all():
            row.handed_to = "ЖУК"
        uow.commit()
    assert positions(client, ids["ОБ.100.1"]) == []  # ЦАР unit, ЖУК task
    assert positions(client, ids["ОБ.100.3"]) == [2]  # ЖУК unit takes it (pool step)
    with app.state.production_uow_factory() as uow:
        for row in uow.session.query(ProductionTaskRow).all():
            row.handed_to = ""  # no site known - every unit
        uow.commit()
    assert positions(client, ids["ОБ.100.1"]) == [2] and positions(client, ids["ОБ.100.3"]) == [2]


def test_refusals_not_a_machine_unknown_record_and_no_references(production_api):
    client, _ = production_api
    assert client.get(URL, params={"equipment_id": str(uuid4())}).status_code == 503  # no БД.ОБ reference at all
    ids = seed_units()
    assert client.get(URL, params={"equipment_id": str(ids[POOL])}).status_code == 422  # calculation row
    assert client.get(URL, params={"equipment_id": str(uuid4())}).status_code == 422  # not in БД.ОБ
    assert client.get(URL).status_code == 422  # equipment_id is required


def _start(client, unit, source=None, **values):
    body = {"equipment_id": str(unit), "purpose": "Пайка", "quantity_planned": 10, **values}
    if source is not None:
        body["source"] = source
    return client.post(JOBS, json=body)


def test_a_job_started_from_a_row_is_its_progress_and_round_trips_the_source(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", mixed())])
    unit = ids["ОБ.100.2"]
    row = next(item for item in queue(client, unit)["rows"] if item["etk_position"] == 4)
    source = {"task_id": row["task_id"], "operation_id": row["operation_id"], "etk_position": 4}
    started = _start(client, unit, source)
    assert started.status_code == 200, started.text
    job = started.json()
    assert job["source"]["label"] == f"708К. · КБ1 · {row['step']}"
    assert {key: job["source"][key] for key in source} == source
    shown = next(item for item in queue(client, unit)["rows"] if item["etk_position"] == 4)
    assert shown["job"] == {"id": job["id"], "number": job["number"], "progress_quantity": None}
    progress = client.post(f"{JOBS}/{job['id']}/progress", json={"version": job["version"], "quantity": 4})
    assert progress.status_code == 200, progress.text
    shown = next(item for item in queue(client, unit)["rows"] if item["etk_position"] == 4)
    assert (shown["done"], shown["left"], shown["job"]["progress_quantity"]) == ("4", "6", 4)
    listed = client.get(JOBS).json()["rows"]
    assert listed[0]["source"] == job["source"]


def test_the_source_is_refused_when_unknown_or_not_workable(production_api):
    client, _ = production_api
    ids = seed_units()
    batch(client, [issue("708К.", "КБ1", mixed())])
    unit = ids["ОБ.100.2"]
    row = next(item for item in queue(client, unit)["rows"] if item["etk_position"] == 4)
    good = {"task_id": row["task_id"], "operation_id": row["operation_id"], "etk_position": 4}
    assert _start(client, unit, {**good, "task_id": str(uuid4())}).status_code == 404
    assert _start(client, unit, {**good, "operation_id": str(uuid4())}).status_code == 404
    assert _start(client, unit, {**good, "etk_position": 9}).status_code == 404
    with app.state.production_uow_factory() as uow:
        row_ = uow.session.get(ProductionTaskRow, UUID(row["task_id"]))
        row_.status = "Отменено"
        uow.commit()
    cancelled = _start(client, unit, good)
    assert cancelled.status_code == 422 and "не запущено" in cancelled.json()["detail"]
    with app.state.production_uow_factory() as uow:
        uow.session.get(ProductionTaskRow, UUID(row["task_id"])).status = "Запущено"
        uow.commit()
    assert _start(client, unit, good).status_code == 200
    assert client.get(JOBS).json()["rows"][0]["source"]["etk_position"] == 4
