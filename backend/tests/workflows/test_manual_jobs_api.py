"""«Ручная работа» (S1): start on any machine of БД.ОБ, one running job per machine, numbers Р-000001…, finish with good /
defect and a material decision, cancel with a reason, compare-and-swap on `version`, the period register."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.equipment_setup.setup_model_infrastructure import SetupReferenceRow
from app.modules.production import manual_jobs as jobs
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import machines_api  # noqa: F401

BASE = "/api/production/manual-jobs"


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _start(client, equipment_id, **values):
    return _post(client, BASE, {"equipment_id": str(equipment_id), "purpose": "Проба на образцах", **values})


def test_start_numbers_one_running_per_machine_and_options(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    options = admin.get(BASE + "/options").json()
    assert [(item["inventory"], item["running"]) for item in options["machines"]] == [("ОБ.019", None), ("ОБ.101", None)]
    first = _start(admin, ob019, wire="ПВАМ  0,35", length_mm="325,5", quantity_planned=20)
    assert first.status_code == 200, first.text
    job = first.json()
    assert (job["number"], job["plc_number"], job["status_text"], job["inventory"]) == \
        ("Р-000001", "R-000001", "Идёт", "ОБ.019")
    assert (job["wire"], job["length_mm"], job["quantity_planned"], job["count_text"]) == ("ПВАМ 0,35", "325,5", 20, "Вручную")
    assert job["started_at"].endswith("+03:00") and job["finished_at"] is None and job["material_text"] == ""
    busy = _start(admin, ob019)
    assert busy.status_code == 409 and "Р-000001" in busy.json()["detail"]
    assert _start(admin, ob101).json()["number"] == "Р-000002"
    options = admin.get(BASE + "/options").json()
    assert [item["running"] for item in options["machines"]] == ["Р-000001", "Р-000002"]
    assert _start(admin, ob019, purpose="   ").status_code == 422
    assert _start(admin, uuid4()).status_code == 404
    assert _start(admin, ob019, length_mm="0").status_code == 422
    with sessions() as session:
        events = session.scalars(select(AuditEventRow.details).where(
            AuditEventRow.event_type == "production.manual_job_started")).all()
    assert events[0]["text"] == "Начата ручная работа Р-000001 на ОБ.019: Проба на образцах (ПВАМ 0,35 · 325,5 мм)"


def test_finish_rules_version_and_material(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    job = _start(admin, ob019).json()
    finish = f"{BASE}/{job['id']}/finish"
    body = {"version": 1, "good": 18, "defect": 2, "defect_reason": "надрез жилы", "material": "not_issued",
            "material_note": "обрезки из отходов"}
    assert _post(admin, finish, {**body, "defect_reason": ""}).status_code == 422
    assert "после завершения" in _post(admin, finish, {**body, "material": "issued"}).json()["detail"]
    assert _post(admin, finish, {**body, "material_note": " "}).status_code == 422
    stale = _post(admin, finish, {**body, "version": 2})
    assert stale.status_code == 409 and "обновите" in stale.json()["detail"]
    clock.now += timedelta(hours=1)
    done = _post(admin, finish, body)
    assert done.status_code == 200, done.text
    done = done.json()
    assert (done["status_text"], done["good"], done["defect"], done["material_text"], done["version"]) == \
        ("Завершена", 18, 2, "Не списывать", 2)
    again = _post(admin, finish, {**body, "version": 2})
    assert again.status_code == 409 and "уже завершена" in again.json()["detail"]
    pending = _start(admin, ob019).json()  # the machine is free again
    answer = _post(admin, f"{BASE}/{pending['id']}/finish", {"version": 1, "good": 5, "material": "pending"}).json()
    assert (answer["material_text"], answer["defect_reason"]) == ("Ждёт решения склада", "")
    assert _post(admin, f"{BASE}/{uuid4()}/finish", body).status_code == 404


def test_cancel_and_the_period_register(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    job = _start(admin, ob019).json()
    cancel = f"{BASE}/{job['id']}/cancel"
    assert _post(admin, cancel, {"version": 1, "reason": ""}).status_code == 422
    assert _post(admin, cancel, {"version": 1, "reason": "  "}).status_code == 422
    cancelled = _post(admin, cancel, {"version": 1, "reason": "не тот провод"}).json()
    assert (cancelled["status_text"], cancelled["cancel_reason"]) == ("Отменена", "не тот провод")
    _start(admin, ob101)
    today = clock.now.date()
    listed = admin.get(BASE, params={"date_from": today.isoformat(), "date_to": today.isoformat()}).json()
    assert [row["number"] for row in listed["rows"]] == ["Р-000002", "Р-000001"] and listed["can_edit"] is True
    by_machine = admin.get(BASE, params={"date_from": today.isoformat(), "equipment_id": str(ob101)}).json()
    assert [row["number"] for row in by_machine["rows"]] == ["Р-000002"]
    tomorrow = (today + timedelta(days=1)).isoformat()
    # A running job started earlier is still in a later period; a cancelled one is not.
    assert [row["number"] for row in admin.get(BASE, params={"date_from": tomorrow, "date_to": tomorrow}).json()["rows"]] \
        == ["Р-000002"]
    assert admin.get(BASE, params={"date_from": tomorrow, "date_to": today.isoformat()}).status_code == 422
    assert admin.get(BASE).json()["date_from"] == today.isoformat()


def test_reference_must_belong_to_the_machine_and_fills_the_wire(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    reference = uuid4()
    with sessions.begin() as session:
        session.add(SetupReferenceRow(id=reference, unit_id=ob019, wire="ПВАМ 0,5", wire_key="пвам 0,5", version=1,
                                      setup_values={}, created_by=uuid4(), created_by_name="Слава", created_at=clock.now))
    assert _start(admin, ob101, reference_id=str(reference)).status_code == 422
    job = _start(admin, ob019, reference_id=str(reference)).json()
    assert (job["wire"], job["reference_id"]) == ("ПВАМ 0,5", str(reference))
    options = admin.get(BASE + "/options").json()
    assert options["can_edit"] is True and options["references"] == [{"id": str(reference), "equipment_id": str(ob019), "wire": "ПВАМ 0,5",
                                      "contact": "", "version": 1, "label": "ПВАМ 0,5 · версия 1"}]


def test_a_number_taken_meanwhile_is_retried_not_duplicated(machines_api, monkeypatch):
    sessions, clock, admin, ob019, ob101 = machines_api
    _start(admin, ob019)
    stale = iter([1])
    real = jobs.next_number
    monkeypatch.setattr(jobs, "next_number", lambda session: next(stale, None) or real(session))
    answer = _start(admin, ob101)
    assert answer.status_code == 200, answer.text
    assert answer.json()["number"] == "Р-000002"


def test_only_the_number_is_retried_other_refusals_say_what_happened(machines_api, monkeypatch):
    sessions, clock, admin, ob019, ob101 = machines_api
    first = _start(admin, ob019).json()
    real = jobs.running
    calls = iter([None])  # the pre-check misses the parallel start; the index refuses the insert
    monkeypatch.setattr(jobs, "running", lambda session, equipment_id: next(calls, "real") and real(session, equipment_id))
    busy = _start(admin, ob019)
    assert busy.status_code == 409 and first["number"] in busy.json()["detail"]
    monkeypatch.setattr(jobs, "running", real)

    def refused(text):
        return IntegrityError("INSERT", {}, Exception(text))

    assert jobs.integrity_kind(refused("UNIQUE constraint failed: production_manual_jobs.number")) == "number"
    assert jobs.integrity_kind(refused('duplicate key value violates unique constraint "uq_production_manual_jobs_number"')) == "number"
    assert jobs.integrity_kind(refused('duplicate key value violates unique constraint "ux_production_manual_jobs_running"')) == "running"
    assert jobs.integrity_kind(refused('insert or update violates foreign key constraint "fk_production_manual_jobs_reference"')) == "foreign"
    assert jobs.integrity_kind(refused("CHECK constraint failed: ck_production_manual_jobs_purpose")) == "other"
    attempts = []

    def check_failure(session):
        attempts.append(1)
        raise refused("CHECK constraint failed: ck_production_manual_jobs_purpose")

    with sessions() as session:
        monkeypatch.setattr(session, "flush", lambda *args, **kwargs: check_failure(session))
        try:
            jobs.start(session, jobs.NewJob(ob101, "Проба"), actor_id=uuid4(), actor_name="Слава", now=clock.now)
        except IntegrityError:
            pass
        else:
            raise AssertionError("a CHECK refusal must not be retried or hidden")
    assert attempts == [1]


# --- «Сделано» and the machine's downtime (machine without control, migration 0208) ---

def test_progress_is_typed_while_running_with_version_and_kept_after_the_finish(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    job = _start(admin, ob019, quantity_planned=20).json()
    assert (job["progress_quantity"], job["progress_at"], job["progress_over_plan"]) == (None, None, False)
    progress = f"{BASE}/{job['id']}/progress"
    assert _post(admin, progress, {"version": 1, "quantity": -1}).status_code == 422
    clock.now += timedelta(minutes=10)
    first = _post(admin, progress, {"version": 1, "quantity": 12})
    assert first.status_code == 200, first.text
    first = first.json()
    assert (first["progress_quantity"], first["version"], first["progress_over_plan"]) == (12, 2, False)
    assert first["progress_at"].endswith("+03:00")
    stale = _post(admin, progress, {"version": 1, "quantity": 13})
    assert stale.status_code == 409 and "обновите" in stale.json()["detail"]
    over = _post(admin, progress, {"version": 2, "quantity": 25}).json()  # more than planned: allowed, flagged
    assert (over["progress_quantity"], over["progress_over_plan"], over["version"]) == (25, True, 3)
    listed = admin.get(BASE, params={"equipment_id": str(ob019)}).json()
    assert [(row["progress_quantity"], row["progress_over_plan"]) for row in listed["rows"]] == [(25, True)]
    done = _post(admin, f"{BASE}/{job['id']}/finish", {"version": 3, "good": 24, "defect": 1, "defect_reason": "надрез",
                                                       "material": "pending"}).json()
    assert (done["status"], done["progress_quantity"]) == ("done", 25)  # history
    late = _post(admin, progress, {"version": 4, "quantity": 26})
    assert late.status_code == 409 and "уже завершена" in late.json()["detail"]
    assert _post(admin, f"{BASE}/{uuid4()}/progress", {"version": 1, "quantity": 1}).status_code == 404
    with sessions() as session:
        texts = session.scalars(select(AuditEventRow.details["text"].as_string()).where(
            AuditEventRow.event_type == "production.manual_job_progress").order_by(AuditEventRow.occurred_at)).all()
    assert texts == ["Сделано по Р-000001: 12 из 20", "Сделано по Р-000001: 25 из 20 — больше плана"]


def test_machine_downtime_one_open_per_machine_shown_and_stopped(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    down = f"{BASE}/downtime"
    options = admin.get(BASE + "/options").json()
    assert [item["downtime"] for item in options["machines"]] == [None, None]
    assert {"key": "equipment", "name": "Оборудование"} in options["reasons"]
    assert _post(admin, down, {"equipment_id": str(ob019), "reason": "other", "comment": " "}).status_code == 422
    assert _post(admin, down, {"equipment_id": str(ob019), "reason": "lunch"}).status_code == 422
    assert _post(admin, down, {"equipment_id": str(uuid4()), "reason": "equipment"}).status_code == 404
    job = _start(admin, ob019).json()  # a running job does not stop the machine's downtime, nor the other way round
    started = _post(admin, down, {"equipment_id": str(ob019), "reason": "equipment", "comment": "заело ролик"})
    assert started.status_code == 200, started.text
    item = started.json()["downtime"]
    assert (item["equipment_id"], item["reason_name"], item["comment"], item["open"]) == \
        (str(ob019), "Оборудование", "заело ролик", True)
    assert item["machine"].startswith("ОБ.019")
    again = _post(admin, down, {"equipment_id": str(ob019), "reason": "no_material"})
    assert again.status_code == 409 and "уже открыт простой" in again.json()["detail"]
    assert _post(admin, down, {"equipment_id": str(ob101), "reason": "no_material"}).status_code == 200
    options = admin.get(BASE + "/options").json()
    assert [(item["downtime"] or {}).get("reason") for item in options["machines"]] == ["equipment", "no_material"]
    listed = admin.get(BASE, params={"equipment_id": str(ob019)}).json()
    assert [row["id"] for row in listed["downtimes"]] == [item["id"]]
    assert len(admin.get(BASE).json()["downtimes"]) == 2
    # The task screen's «Закончить простой» does not end a machine's downtime.
    task_stop = _post(admin, f"/api/production/downtime/{item['id']}/stop", {})
    assert task_stop.status_code == 422 and "карточке станка" in task_stop.json()["detail"]
    clock.now += timedelta(minutes=30)
    stopped = _post(admin, f"{down}/{item['id']}/stop", {})
    assert stopped.status_code == 200, stopped.text
    stopped = stopped.json()["downtime"]
    assert (stopped["open"], stopped["seconds"], stopped["ended_by"] != "") == (False, 1800, True)
    twice = _post(admin, f"{down}/{item['id']}/stop", {})
    assert twice.status_code == 409 and "уже закончен" in twice.json()["detail"]
    assert _post(admin, f"{down}/{uuid4()}/stop", {}).status_code == 404
    assert admin.get(BASE, params={"equipment_id": str(ob019)}).json()["downtimes"] == []
    assert _post(admin, down, {"equipment_id": str(ob019), "reason": "waiting_qc"}).status_code == 200  # a new one
    assert admin.get(f"{BASE}", params={"equipment_id": str(ob019)}).json()["rows"][0]["id"] == job["id"]
    with sessions() as session:
        events = session.execute(select(AuditEventRow.event_type, AuditEventRow.aggregate_id).where(
            AuditEventRow.event_type.like("production.machine_downtime_%"))).all()
    assert sorted(event for event, _ in events) == ["production.machine_downtime_ended"] + \
        ["production.machine_downtime_started"] * 3
    assert {aggregate for _, aggregate in events} == {ob019, ob101}


def test_a_parallel_downtime_start_is_refused_by_the_index(machines_api, monkeypatch):
    sessions, clock, admin, ob019, _ = machines_api
    from app.modules.production.downtime_infrastructure import SqlAlchemyDowntimeRepository
    assert _post(admin, f"{BASE}/downtime", {"equipment_id": str(ob019), "reason": "equipment"}).status_code == 200
    monkeypatch.setattr(SqlAlchemyDowntimeRepository, "open_for_equipment", lambda self, equipment_id: None)
    raced = _post(admin, f"{BASE}/downtime", {"equipment_id": str(ob019), "reason": "no_material"})
    assert raced.status_code == 409 and "уже открыт простой" in raced.json()["detail"]
