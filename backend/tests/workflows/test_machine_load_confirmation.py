"""The confirmed cutting source and active run are compared inside the load transaction (W1b)."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.production import machine_runs as runs
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.machine_writes import MachineOffsetRow, MachineWriteRow
from app.shared.http_auth import Principal
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow
from app.workflows import machine_runs_api, machine_writes_api
from tests.identity.test_api_tokens import ADMIN_ID, _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_runs_api import Machine, _counter, START
from tests.workflows.test_machine_writes_api import CARD, _job
from tests.workflows.test_machines_api import machines_api  # noqa: F401


def _preview(client, group_id, length="325"):
    response = client.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": length})
    assert response.status_code == 200, response.text
    return response.json()


def _body(plan):
    return {"group_id": plan["group_id"], "length_mm": plan["length_mm"],
            "expected_source": plan["expected_source"], "expected_active_run_id": plan["expected_active_run_id"],
            "confirm_state": 102}


def _load(client, endpoint, body):
    if endpoint == "/run":
        body = {"kind": "cutting", **body}
    return client.post(CARD + endpoint, json=body, headers=_csrf(client))


def _writes(sessions):
    with sessions() as session:
        return list(session.scalars(select(MachineWriteRow).where(MachineWriteRow.purpose == "transfer")))


@pytest.mark.parametrize("endpoint", ["/run", "/transfer"])
@pytest.mark.parametrize("change", ["version", "recipe", "cancelled", "length", "quantity", "offset", "marking"])
def test_a_source_changed_since_confirmation_is_refused_without_writes(machines_api, endpoint, change):
    sessions, clock, admin, ob019, _ = machines_api
    Machine(admin, clock)
    group_id = _job(sessions)
    plan = _preview(admin, group_id)
    with sessions.begin() as session:
        group = session.get(CuttingGroupRow, group_id)
        if change == "version":
            group.version += 1
        elif change == "cancelled":
            group.state = "cancelled"
        elif change in ("recipe", "marking"):
            recipe = deepcopy(group.recipe)
            fields = recipe["operations"][0]["fields"]
            if change == "recipe":
                fields["strip"]["lenA"] = "7"
            else:
                fields["laserMark"] = {"mode": "ends", "front": "W1", "back": "W2", "middle": ""}
            group.recipe = recipe  # Even an unchanged group.version cannot hide a changed plan.
        elif change in ("length", "quantity"):
            for member in session.scalars(select(CuttingMemberRow).where(CuttingMemberRow.group_id == group_id)):
                if change == "length":
                    member.row_snapshot = {"cut_length_mm": "400"}
                else:
                    member.blank_quantity += 1
        else:
            session.add(MachineOffsetRow(equipment_id=ob019, parameter="strip_front_mm", offset_hundredths=50,
                                         reason="Проба", updated_by_name="Admin", updated_at=clock.now, version=1))
    response = _load(admin, endpoint, _body(plan))
    assert response.status_code == 409, response.text
    assert "обновите карточку" in response.json()["detail"]
    assert _writes(sessions) == []
    with sessions() as session:
        assert session.scalar(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.machine_write_queued")) is None


@pytest.mark.parametrize("endpoint", ["/run", "/transfer"])
def test_matching_confirmation_enqueues_once_and_cannot_be_replayed(machines_api, endpoint):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    plan = _preview(admin, _job(sessions))
    assert plan["expected_source"]["version"] == 1
    assert plan["expected_source"]["length_mm"] == "325"
    assert plan["expected_active_run_id"] is None
    body = {**_body(plan), "length_mm": "325.000"}
    sent = _load(admin, endpoint, body)
    assert sent.status_code == 200, sent.text
    assert _load(admin, endpoint, body).status_code == 409
    [row] = _writes(sessions)
    assert str(row.id) == sent.json()["id"] and not runs.info(row).get("closed_at")


@pytest.mark.parametrize("endpoint", ["/run", "/transfer"])
def test_missing_source_confirmation_cannot_load_a_cutting_job(machines_api, endpoint):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    body = _body(_preview(admin, _job(sessions)))
    del body["expected_source"]
    response = _load(admin, endpoint, body)
    assert response.status_code == 409 and "Подтвердите" in response.json()["detail"]
    assert _writes(sessions) == []


@pytest.mark.parametrize("endpoint", ["/run", "/transfer"])
@pytest.mark.parametrize("change", ["group", "length", "hash"])
def test_confirmation_is_bound_to_the_selected_source(machines_api, endpoint, change):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    body = deepcopy(_body(_preview(admin, _job(sessions))))
    if change == "group":
        body["group_id"] = str(_job(sessions, number="ГР-0008"))
    elif change == "length":
        body["length_mm"] = "150"
    else:
        body["expected_source"]["plan_hash"] = "0" * 64
    assert _load(admin, endpoint, body).status_code == 409
    assert _writes(sessions) == []


@pytest.mark.parametrize("endpoint", ["/run", "/transfer"])
def test_another_run_is_never_superseded_without_its_explicit_id(machines_api, endpoint):
    sessions, clock, admin, _, _ = machines_api
    machine = Machine(admin, clock)
    first_plan = _preview(admin, _job(sessions))
    stale = _body(_preview(admin, _job(sessions, number="ГР-0008")))
    first = _load(admin, "/run", _body(first_plan)).json()["id"]
    machine.written(first)
    for body in (stale, {key: value for key, value in stale.items() if key != "expected_active_run_id"}):
        response = _load(admin, endpoint, body)
        assert response.status_code == 409 and "Задание на станке изменилось" in response.json()["detail"]
        [row] = _writes(sessions)
        assert not runs.info(row).get("closed_at")
    # The actual active ID is available both from the run state and from a fresh plan preview.
    assert admin.get(CARD + "/run").json()["expected_active_run_id"] == first
    fresh = _preview(admin, stale["group_id"])
    assert fresh["expected_active_run_id"] == first
    sent = _load(admin, endpoint, _body(fresh))
    assert sent.status_code == 200, sent.text
    with sessions() as session:
        assert runs.info(session.get(MachineWriteRow, UUID(first)))["closed_why"] == "Передано другое задание"


def test_a_closed_run_invalidates_the_confirmed_active_id(machines_api):
    sessions, clock, admin, _, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    first = _load(admin, "/run", _body(_preview(admin, group_id))).json()["id"]
    machine.written(first)
    plan = _preview(admin, group_id)
    closed = admin.post(CARD + "/run/close", json={"run_id": first}, headers=_csrf(admin))
    assert closed.status_code == 200, closed.text
    assert _load(admin, "/run", _body(plan)).status_code == 409
    assert admin.get(CARD + "/run").json()["expected_active_run_id"] is None
    assert len(_writes(sessions)) == 1


def test_live_comparison_values_do_not_invalidate_the_plan_but_remaining_quantity_does(machines_api):
    sessions, clock, admin, _, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    plan = _preview(admin, group_id)
    machine.report({**machine.last, "34": 700})
    assert _preview(admin, group_id)["expected_source"] == plan["expected_source"]
    first = _load(admin, "/run", _body(plan))
    assert first.status_code == 200, first.text
    machine.written(first.json()["id"])
    plan = _preview(admin, group_id)
    machine.report(_counter(START + 1))
    response = _load(admin, "/run", _body(plan))
    assert response.status_code == 409, response.text
    assert len(_writes(sessions)) == 1


def test_manual_run_cannot_replace_a_cutting_run_without_confirmation(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    first = _load(admin, "/run", _body(_preview(admin, _job(sessions)))).json()["id"]
    machine.written(first)
    job = admin.post("/api/production/manual-jobs", headers=_csrf(admin), json={"equipment_id": str(ob019),
                     "purpose": "Проба", "length_mm": "100", "quantity_planned": 3}).json()
    response = admin.post(CARD + "/run", headers=_csrf(admin), json={"kind": "manual", "job_id": job["id"]})
    assert response.status_code == 409
    [row] = _writes(sessions)
    assert not runs.info(row).get("closed_at")


def test_typed_values_cannot_bypass_the_source_guard(machines_api):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    group_id = _job(sessions)
    body = _body(_preview(admin, group_id))
    with sessions.begin() as session:
        session.get(CuttingGroupRow, group_id).version += 1
    response = _load(admin, "/run", {**body, "values": {"total_set": "12", "length_mm": "326"}})
    assert response.status_code == 409
    assert _writes(sessions) == []


def test_enqueue_failure_rolls_back_superseding_the_confirmed_run(machines_api):
    sessions, clock, admin, _, _ = machines_api
    machine = Machine(admin, clock)
    first = _load(admin, "/run", _body(_preview(admin, _job(sessions)))).json()["id"]
    machine.written(first)
    plan = _preview(admin, _job(sessions, number="ГР-0008"))
    queued = admin.post(CARD + "/write", json={"values": {"strip_front_mm": "6"}}, headers=_csrf(admin))
    assert queued.status_code == 200, queued.text
    response = _load(admin, "/run", _body(plan))
    assert response.status_code == 409 and "уже идёт запись" in response.json()["detail"]
    [row] = _writes(sessions)
    assert str(row.id) == first and not runs.info(row).get("closed_at")


def test_load_locks_machine_snapshot_and_group_before_queueing(machines_api):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    body = _body(_preview(admin, _job(sessions)))
    locked = []

    def spy(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locked.append(str(state.statement))

    event.listen(Session, "do_orm_execute", spy)
    try:
        response = _load(admin, "/run", body)
    finally:
        event.remove(Session, "do_orm_execute", spy)
    assert response.status_code == 200, response.text
    assert any("production_machine_links" in statement for statement in locked)
    assert any("production_machine_snapshots" in statement for statement in locked)
    assert any("production_cutting_groups" in statement for statement in locked)


@pytest.mark.parametrize("endpoints", [("/run", "/run"), ("/transfer", "/transfer"), ("/run", "/transfer")])
def test_two_concurrent_confirmed_loads_make_exactly_one_run(machines_api, tmp_path, endpoints):
    sessions, clock, admin, _, _ = machines_api
    Machine(admin, clock)
    body = _body(_preview(admin, _job(sessions)))
    # Separate real SQLite connections, rather than StaticPool's single fixture connection.
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'loads.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    try:
        with sessions.kw["bind"].connect() as source, engine.connect() as target:
            source.connection.driver_connection.backup(target.connection.driver_connection)
        isolated = sessionmaker(engine, expire_on_commit=False)
        factory = lambda: SqlAlchemyProductionUnitOfWork(isolated)
        principal = Principal(Actor(ADMIN_ID, "Admin", "ИТ"), "admin@example.test", frozenset({"admin"}), {})
        barrier = Barrier(2)

        def load(endpoint):
            barrier.wait(timeout=10)
            try:
                if endpoint == "/run":
                    machine_runs_api.start_run("ОБ.019", machine_runs_api.RunPayload(kind="cutting", **body),
                                               principal, factory, clock.now)
                else:
                    machine_writes_api.transfer("ОБ.019", machine_writes_api.TransferPayload(**body),
                                                 principal, factory, clock.now)
                return 200
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(load, endpoints)) == [200, 409]
        [row] = _writes(isolated)
        assert not runs.info(row).get("closed_at")
    finally:
        engine.dispose()
