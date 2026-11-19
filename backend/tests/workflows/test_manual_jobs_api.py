"""«Ручная работа» (S1): start on any machine of БД.ОБ, one running job per machine, numbers Р-000001…, finish with good /
defect and a material decision, cancel with a reason, compare-and-swap on `version`, the period register."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

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
    assert "складом" in _post(admin, finish, {**body, "material": "issued"}).json()["detail"]
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
