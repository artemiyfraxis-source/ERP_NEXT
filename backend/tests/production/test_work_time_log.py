"""Работа за сотрудника и журнал рабочего времени (владелец, 12.10.2026)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionBoxRow, ProductionOperationRow, ProductionOutputRow, ProductionWorkSessionRow, ProductionWorkTimeLogRow
from app.modules.production.traceability import WorkSession, WorkStatus
from app.modules.production.worklog import WorkEvent, WorkTimeEntry, earned_norm_seconds, percent, time_fact
from app.shared.clock import moscow_now
from app.shared.persistence import AuditEventRow
from tests.production.test_api import MASTER_ID, _as, launch_from_card, production_api  # noqa: F401 — фикстура

ZHUK_MASTER = UUID("10000000-0000-0000-0000-000000000005")


def _at(hour: int, minute: int = 0, day: int = 22) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, hour, minute)


def _assigned_task(client, operator_id, key="on-behalf-v1", code="CUT_WIRE+2crimp", unit=36, setup=60, quantity=100, project="П3-0007"):
    task = launch_from_card(client, key, project, "КБ1", quantity, [(code, "Автомат резка + опрессовка", unit, setup)]).json()["task"]
    operation = task["operations"][0]
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    assert assigned.status_code == 200, assigned.text
    return assigned.json()["task"]


def _rows(model):
    with app.state.production_uow_factory() as uow:
        rows = list(uow.session.scalars(select(model)))
        uow.commit()  # без отката: прочитанные строки остаются загруженными
        return rows


def _log_rows():
    with app.state.production_uow_factory() as uow:
        rows = list(uow.session.scalars(select(ProductionWorkTimeLogRow).order_by(ProductionWorkTimeLogRow.occurred_at, ProductionWorkTimeLogRow.sequence)))
        uow.commit()
        return rows


def test_admin_works_for_the_fitter_her_facts_and_time_his_name_in_the_audit(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    operation = task["operations"][0]
    _as(MASTER_ID, "Артемий", {"admin"}, {})
    behalf = {"on_behalf_of": str(operator_id)}

    _at(14, 5)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", params=behalf, json={})
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["id"]
    _at(14, 30)
    assert client.post(f"/api/production/work/{session_id}/pause", params=behalf).status_code == 200
    _at(14, 50)
    assert client.post(f"/api/production/work/{session_id}/resume", params=behalf).status_code == 200
    _at(16, 3)
    submitted = client.post(f"/api/production/work/{session_id}/submit", params=behalf, json={"good_quantity": "98", "defect_quantity": "2", "defect_character": "Обрыв", "defect_description": "Порван провод"})
    assert submitted.status_code == 200, submitted.text

    # Факт, коробка, брак и время — монтажника; админ ничего «своего» не получил.
    session = _rows(ProductionWorkSessionRow)[0]
    output = _rows(ProductionOutputRow)[0]
    box = _rows(ProductionBoxRow)[0]
    assert session.operator_id == operator_id and output.operator_id == operator_id
    assert box.operator_id == operator_id and box.closed_by == "Оператор"
    assert submitted.json()["defect"]["operator_id"] == str(operator_id)
    # Журнал действий: нажал админ, за монтажника.
    audit = [row for row in _rows(AuditEventRow) if row.event_type.startswith("production.work") or row.event_type == "production.output_recorded"]
    assert {row.actor_id for row in audit} == {MASTER_ID}
    assert all(row.details["performer_id"] == str(operator_id) for row in audit)
    assert audit[0].details["on_behalf"] == "Артемий за Оператор"
    # Журнал времени: четыре строки, исполнитель — она, нажал — он; факт без паузы 20 мин.
    rows = _log_rows()
    assert [row.event for row in rows] == ["start", "pause", "resume", "finish"]
    assert {row.operator_id for row in rows} == {operator_id} and {row.actor_id for row in rows} == {MASTER_ID}
    assert {row.actor_name for row in rows} == {"Артемий"} and {row.operator_name for row in rows} == {"Оператор"}
    finish = rows[-1]
    assert finish.running_seconds == 98 * 60  # 14:05–16:03 без 14:30–14:50
    assert (finish.good_quantity, finish.defect_quantity) == (Decimal("98"), Decimal("2"))
    unit, setup = Decimal(operation["seconds_per_unit"]), Decimal(operation["setup_seconds"])
    assert (finish.seconds_per_unit, finish.setup_seconds) == (unit, setup)
    assert finish.planned_norm_seconds == operation["norm_seconds"]
    assert finish.earned_norm_seconds == earned_norm_seconds(unit, setup, Decimal(100), first=True)
    assert (finish.project, finish.item, finish.operation_code, finish.task_number) == ("П3-0007", task["item"], "CUT_WIRE+2crimp", task["number"])


def test_finished_card_shows_fact_and_norm_and_the_viewer_may_act(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    operation = task["operations"][0]
    _as(MASTER_ID, "Артемий", {"admin"}, {})
    behalf = {"on_behalf_of": str(operator_id)}
    _at(14, 0)
    session_id = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", params=behalf, json={}).json()["session"]["id"]
    _at(15, 38)
    assert client.post(f"/api/production/work/{session_id}/submit", params=behalf, json={"good_quantity": "100"}).status_code == 200

    mine = client.get("/api/production/my", params={"operator_id": str(operator_id)}).json()

    assert mine["can_act"] is True
    fact = mine["tasks"][0]["operations"][0]["time_fact"]
    norm = earned_norm_seconds(Decimal(operation["seconds_per_unit"]), Decimal(operation["setup_seconds"]), Decimal(100), first=True)
    assert fact == {"actual_seconds": 98 * 60, "norm_seconds": norm, "percent": percent(98 * 60, norm), "finishes": 1}


def test_master_of_the_site_may_act_others_stay_read_only(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    operation = task["operations"][0]
    start = f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start"
    behalf = {"on_behalf_of": str(operator_id)}
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(id=ZHUK_MASTER, login="stepan", email="s@example.test", name="Степан", department="Производство", role="master", site="ЖУК", active=True, site_master=True))
        uow.commit()

    # Монтажник за напарника не работает, даже с правкой «Заданий».
    _as(operator_id, "Оператор", {"operator"}, {"myops": "edit"})
    assert client.post(start, params={"on_behalf_of": str(MASTER_ID)}, json={}).status_code == 403
    # Видит планирование, но не правит — только просмотр, даже с правкой «Заданий».
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "view", "myops": "edit"})
    assert client.post(start, params=behalf, json={}).status_code == 403
    assert client.get("/api/production/my", params={"operator_id": str(operator_id)}).json()["can_act"] is False
    # Правит планирование, но «Задания» у него на просмотре — гейт маршрута не пускает ни за себя, ни за сотрудника.
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "view"})
    assert client.post(start, params=behalf, json={}).status_code == 403
    assert client.post(start, json={}).status_code == 403
    assert client.post(f"/api/production/work/{ZHUK_MASTER}/pause", params=behalf).status_code == 403
    assert client.get("/api/production/my", params={"operator_id": str(operator_id)}).json()["can_act"] is False
    # Мастер другой площадки — нет; мастер её площадки — да.
    _as(ZHUK_MASTER, "Степан", {"foreman"}, {"production": "edit", "myops": "edit"})
    assert client.post(start, params=behalf, json={}).status_code == 403
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "edit"})
    assert client.get("/api/production/my", params={"operator_id": str(operator_id)}).json()["can_act"] is True
    started = client.post(start, params=behalf, json={})
    assert started.status_code == 200, started.text
    assert started.json()["session"]["operator_id"] == str(operator_id)
    assert _log_rows()[0].actor_name == "Мастер"


def test_a_repeated_click_does_not_start_or_pause_twice(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    operation = task["operations"][0]
    _as(operator_id, "Оператор", {"operator"}, {"myops": "edit"})
    start = f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start"
    _at(9, 0)

    first = client.post(start, headers={"Idempotency-Key": "start-1"}, json={})
    again = client.post(start, headers={"Idempotency-Key": "start-1"}, json={})
    session_id = first.json()["session"]["id"]
    _at(9, 10)
    paused = client.post(f"/api/production/work/{session_id}/pause", headers={"Idempotency-Key": "pause-1"})
    paused_again = client.post(f"/api/production/work/{session_id}/pause", headers={"Idempotency-Key": "pause-1"})
    misused = client.post(f"/api/production/work/{session_id}/resume", headers={"Idempotency-Key": "pause-1"})
    unkeyed = client.post(f"/api/production/work/{session_id}/pause")

    assert first.status_code == again.status_code == 200
    assert again.json()["session"]["id"] == session_id
    assert paused.status_code == paused_again.status_code == 200
    assert misused.status_code == 409
    assert unkeyed.status_code == 422  # уже на паузе: второе действие без ключа отказано по состоянию
    assert len(_rows(ProductionWorkSessionRow)) == 1
    assert [row.event for row in _log_rows()] == ["start", "pause"]
    assert [row.idempotency_key for row in _log_rows()] == ["start-1", "pause-1"]


def test_a_later_norm_change_does_not_rewrite_the_logged_norm(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id, unit=36, setup=60)
    operation = task["operations"][0]
    _as(operator_id, "Оператор", {"operator"}, {"myops": "edit"})
    _at(9, 0)
    session_id = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json={}).json()["session"]["id"]
    with app.state.production_uow_factory() as uow:
        row = uow.session.get(ProductionOperationRow, UUID(operation["id"]))
        row.seconds_per_unit = Decimal("99")
        uow.commit()
    _at(10, 0)
    assert client.post(f"/api/production/work/{session_id}/submit", json={"good_quantity": "10"}).status_code == 200

    start, finish = _log_rows()
    assert start.seconds_per_unit == Decimal(operation["seconds_per_unit"])
    assert finish.seconds_per_unit == Decimal("99")
    assert finish.earned_norm_seconds == earned_norm_seconds(Decimal("99"), Decimal(operation["setup_seconds"]), Decimal(10), first=True)


def test_summary_groups_finishes_by_operation_code_over_a_period(production_api):
    client, operator_id = production_api
    cut = _assigned_task(client, operator_id, key="sum-cut", code="CUT_WIRE+2crimp", project="П3-0007")
    solder = _assigned_task(client, operator_id, key="sum-solder", code="SOLDER", project="П3-0008")
    old = _assigned_task(client, operator_id, key="sum-old", code="SOLDER", project="П3-0009")
    _as(operator_id, "Оператор", {"operator"}, {"myops": "edit"})

    def work(task, day, minutes, good):
        operation = task["operations"][0]
        _at(9, 0, day)
        session_id = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json={}).json()["session"]["id"]
        _at(9, minutes, day)
        response = client.post(f"/api/production/work/{session_id}/submit", json={"good_quantity": str(good)})
        assert response.status_code == 200, response.text

    work(old, 20, 50, 5)  # до периода
    work(cut, 22, 30, 10)
    work(solder, 23, 40, 20)
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "view"})

    summary = client.get("/api/production/work-time/summary", params={"date_from": "2026-09-21", "date_to": "2026-09-30", "by": "operation_code"})
    entries = client.get("/api/production/work-time", params={"date_from": "2026-09-22", "date_to": "2026-09-22"})
    bad = client.get("/api/production/work-time/summary", params={"date_from": "2026-09-21", "date_to": "2026-09-30", "by": "color"})

    assert summary.status_code == 200, summary.text
    rows = {row["key"]: row for row in summary.json()["rows"]}
    assert set(rows) == {"CUT_WIRE+2crimp", "SOLDER"}
    assert (rows["CUT_WIRE+2crimp"]["actual_seconds"], rows["CUT_WIRE+2crimp"]["quantity"], rows["CUT_WIRE+2crimp"]["finishes"]) == (30 * 60, "10", 1)
    assert (rows["SOLDER"]["actual_seconds"], rows["SOLDER"]["quantity"]) == (40 * 60, "20")
    assert rows["SOLDER"]["label"] == "Автомат резка + опрессовка"
    assert rows["SOLDER"]["percent"] == percent(40 * 60, rows["SOLDER"]["norm_seconds"])
    assert [item["event"] for item in entries.json()["entries"]] == ["start", "finish"]
    assert bad.status_code == 422


def test_running_time_excludes_pauses_and_the_entry_refuses_a_half_finish():
    session = WorkSession(UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), datetime(2026, 10, 12, 14, 0), WorkStatus.ACTIVE)
    paused = session.pause(datetime(2026, 10, 12, 14, 30)).resume(datetime(2026, 10, 12, 14, 45)).pause(datetime(2026, 10, 12, 15, 0))

    assert paused.running_seconds(datetime(2026, 10, 12, 15, 20)) == 45 * 60  # 14:00–14:30 + 14:45–15:00
    assert earned_norm_seconds(Decimal("36"), Decimal("60"), Decimal("100"), first=False) == 3600
    assert percent(98 * 60, 90 * 60) == 109 and percent(10, 0) is None
    base = dict(
        id=UUID(int=9), idempotency_key="k", session_id=UUID(int=1), sequence=0, occurred_at=datetime(2026, 10, 12),
        task_id=UUID(int=2), operation_id=UUID(int=3), operator_id=UUID(int=4), operator_name="Элина",
        actor_id=UUID(int=5), actor_name="Артемий", project="П3", item="КБ1", task_number="ПЗ-1", operation_code="X",
        stage="Резка", position=1, seconds_per_unit=Decimal(1), setup_seconds=Decimal(0), planned_norm_seconds=10,
    )
    with pytest.raises(Exception):
        WorkTimeEntry(**base, event=WorkEvent.FINISH, good_quantity=Decimal(1))
    with pytest.raises(Exception):
        WorkTimeEntry(**base, event=WorkEvent.START, running_seconds=5)
    finished = WorkTimeEntry(**base, event=WorkEvent.FINISH, good_quantity=Decimal(1), defect_quantity=Decimal(0), running_seconds=60, earned_norm_seconds=30)
    assert finished.on_behalf and time_fact([finished]).percent == 200
