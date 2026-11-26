"""Wire-only batches keep the PLC counter, explicit reset preserves previous production counts."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
from app.modules.equipment_setup.recipes import ManualRecipeVersion, ob019_recipe_fields
from app.modules.production import machine_runs as runs
from app.modules.production.machine_writes import MachineWriteRow
from app.modules.references.infrastructure import HookupWireRow, WireGaugeRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_runs_api import Machine, START, _counter, _u32
from tests.workflows.test_machine_writes_api import AGENT_NEW, CARD, NEXT, _job, _post, _result, _text_at
from tests.workflows.test_machines_api import machines_api  # noqa: F401

RUN = CARD + "/run"
WIRE = "ПВАМ 0,35"
VALUES = {"length_mm": "325", "total_set": "10", "strip_front_mm": "5", "pull_front_mm": "7",
          "strip_back_mm": "5", "pull_back_mm": "7", "diameter_mm": "0.79", "knife_back_mm": "0.1"}


@pytest.fixture
def wire_world(machines_api):
    sessions, clock, admin, equipment, _ = machines_api
    with sessions.begin() as session:
        gauge = WireGaugeRow(id=uuid4(), awg="22", section_mm2=Decimal("0.35"), conductor_d_mm=Decimal("0.64"))
        session.add(gauge)
        session.flush()
        session.add(HookupWireRow(id=uuid4(), mark=WIRE + " красный", mark_key="пвам035красный", series="ПВАМ",
                                 gauge_id=gauge.id, updated_at=clock.now))
    machine = Machine(admin, clock)
    return sessions, clock, admin, equipment, machine


def _wire(admin, values=None, **extra):
    body = {"kind": "wire", "wire": WIRE, "values": VALUES if values is None else values,
            "expected_active_run_id": admin.get(RUN).json()["expected_active_run_id"], "confirm_state": 102, **extra}
    return admin.post(RUN, headers=_csrf(admin), json=body)


def _written(machine, command_id):
    claimed = TestClient(app).post(NEXT, headers=machine.bridge, json=AGENT_NEW).json()["command"]
    assert claimed is not None and claimed["id"] == command_id
    readback = {str(block["addr"] + i): value for block in claimed["blocks"] for i, value in enumerate(block["values"])}
    for bit in claimed["bits"]:
        address, mask = str(bit["addr"]), 1 << bit["bit"]
        previous = machine.held.get(address, machine.last.get(address, 0))
        readback[address] = previous | mask if bit["value"] else previous & ~mask
    machine.held.update(readback)
    machine.report()
    answer = _result(machine.bridge, command_id, readback, agent=AGENT_NEW)
    assert answer.json()["status"] == "done", answer.text
    machine.clock.now += timedelta(seconds=1)
    return readback


def _reset(admin, **extra):
    return admin.post(RUN + "/reset", headers=_csrf(admin), json={
        "expected_active_run_id": admin.get(RUN).json()["expected_active_run_id"], "confirm_state": 102, **extra})


def test_wire_run_is_startable_without_manual_job_and_clears_previous_task(wire_world):
    sessions, clock, admin, equipment, machine = wire_world
    sent = _wire(admin)
    assert sent.status_code == 200, sent.text
    words = _written(machine, sent.json()["id"])
    run = admin.get(RUN).json()["run"]
    assert (run["kind"], run["wire"], run["group_id"], run["job_id"]) == ("wire", WIRE, None, None)
    assert (run["done"], run["quantity"], run["counter_start"]) == (0, 10, START)
    assert _u32(words, 22) == START + 10
    assert not ({"645", "646", "649", "650"} & words.keys())
    assert words["400"] == words["422"] == 0
    assert _text_at({int(k): v for k, v in words.items()}, 940) == ""
    assert all(words[str(address)] == 0 for address in range(440, 485))
    with sessions() as session:
        assert runs.start_blocker(session, equipment, clock.now) == ""


def test_wire_resend_preserves_manual_marking_and_count_baseline(wire_world):
    sessions, clock, admin, equipment, machine = wire_world
    first = _wire(admin, {**VALUES, "marking": "1", "mark_by_step": "1", "mark_middle": "CABLE"})
    _written(machine, first.json()["id"])
    machine.report(_counter(START + 2))
    again = _wire(admin, {"total_set": "8"})
    assert again.status_code == 200, again.text
    words = _written(machine, again.json()["id"])
    assert not ({"400", "455", "32", "940"} & words.keys())
    run = admin.get(RUN).json()["run"]
    assert (run["counter_start"], run["done"], run["quantity"]) == (START, 2, 10)


@pytest.mark.parametrize("include_speed", [False, True])
def test_wire_confirmation_never_injects_later_recipe_changes(wire_world, include_speed):
    sessions, clock, admin, equipment, machine = wire_world
    recipe_id, author = uuid4(), uuid4()
    panel = {key: value for key, value in VALUES.items() if key not in ("length_mm", "total_set")}

    def save(version, speed):
        geometry, settings = ob019_recipe_fields(WIRE, {**panel, "speed_feed": speed}, {})
        with sessions.begin() as session:
            SqlAlchemyRecipes(session).append(ManualRecipeVersion(recipe_id, version, equipment, WIRE, geometry,
                settings, "Проба", author, clock.now), expected_version=version - 1)

    machine.report({**_counter(START), "100": 30})
    save(1, "30")
    preview = admin.get(CARD + "/transfer", params={"wire": WIRE, "length_mm": "325"}).json()
    assert next(row for row in preview["rows"] if row["key"] == "speed_feed")["value"] == "30,00"
    save(2, "80")
    incomplete = _wire(admin, {key: value for key, value in VALUES.items() if key != "strip_front_mm"})
    assert incomplete.status_code == 422  # The latest recipe must not silently supply an unconfirmed size either.
    sent = _wire(admin, {**VALUES, **({"speed_feed": "30"} if include_speed else {})})
    assert sent.status_code == 200, sent.text
    words = _written(machine, sent.json()["id"])
    assert words.get("100") == (30 if include_speed else None)


def test_reset_closes_counted_run_clears_payload_and_next_run_starts_at_current_counter(wire_world):
    sessions, clock, admin, equipment, machine = wire_world
    sent = _wire(admin)
    _written(machine, sent.json()["id"])
    machine.report(_counter(START + 12))
    assert admin.get(RUN).json()["run"]["stage"] == "finished"
    reset = _reset(admin)
    assert reset.status_code == 200, reset.text
    words = _written(machine, reset.json()["id"])
    assert _u32(words, 22) == START + 12
    assert not ({"645", "646", "649", "650"} & words.keys())
    with sessions() as session:
        old = runs.info(session.get(MachineWriteRow, UUID(sent.json()["id"])))
        assert old["machine_count"] == 12 and old["closed_at"]
        assert runs.start_blocker(session, equipment, clock.now) == runs.NO_JOB
    assert admin.get(RUN).json()["expected_active_run_id"] is None
    next_run = _wire(admin)
    _written(machine, next_run.json()["id"])
    fresh = admin.get(RUN).json()["run"]
    assert (fresh["counter_start"], fresh["done"], fresh["quantity"]) == (START + 12, 0, 10)


def test_reset_without_run_clears_untracked_completed_batch(wire_world):
    _sessions, _clock, admin, _equipment, machine = wire_world
    machine.report({**_counter(2056), "22": 2050, "23": 0})
    reset = _reset(admin)
    assert reset.status_code == 200, reset.text
    assert _u32(_written(machine, reset.json()["id"]), 22) == 2056


@pytest.mark.parametrize("values,extra,expected", [
    ({**VALUES, "total_set": "0"}, {}, 422),
    ({**VALUES, "length_mm": "0"}, {}, 422),
    ({"total_set": "10", "length_mm": "325"}, {}, 422),
    (VALUES, {"wire": "unknown"}, 422),
    (VALUES, {"expected_active_run_id": str(uuid4())}, 409),
])
def test_wire_validation_never_enqueues_partial_job(wire_world, values, extra, expected):
    sessions, _clock, admin, _equipment, _machine = wire_world
    answer = _wire(admin, values, **extra)
    assert answer.status_code == expected, answer.text
    with sessions() as session:
        assert session.scalar(select(MachineWriteRow)) is None


def test_reset_stale_guard_running_or_pending_write_keeps_current_run(wire_world):
    sessions, _clock, admin, _equipment, machine = wire_world
    sent = _wire(admin)
    run_id = sent.json()["id"]
    assert _reset(admin).status_code == 409
    _written(machine, run_id)
    assert _reset(admin, expected_active_run_id=None).status_code == 409
    machine.report(_counter(START + 1, state=103))
    assert _reset(admin, confirm_state=103).status_code == 409
    with sessions() as session:
        assert runs.is_open(session.get(MachineWriteRow, UUID(run_id)))


def test_reset_enqueue_refusal_rolls_back_closure_and_stale_counter_is_not_used(wire_world):
    sessions, clock, admin, _equipment, machine = wire_world
    sent = _wire(admin)
    run_id = sent.json()["id"]
    _written(machine, run_id)
    queued = _post(admin, CARD + "/write", {"values": {"batch_set": "20"}, "confirm_state": 102})
    assert queued.status_code == 200, queued.text
    refused = _reset(admin)
    assert refused.status_code == 409 and "идёт запись" in refused.json()["detail"]
    with sessions() as session:
        assert runs.is_open(session.get(MachineWriteRow, UUID(run_id)))
    _written(machine, queued.json()["id"])
    clock.now += timedelta(seconds=6)
    stale = _reset(admin)
    assert stale.status_code == 409 and "свежего счётчика" in stale.json()["detail"]
    with sessions() as session:
        assert runs.is_open(session.get(MachineWriteRow, UUID(run_id)))


def test_wire_cannot_replace_open_cutting_task_or_omit_confirmation(wire_world):
    sessions, _clock, admin, _equipment, machine = wire_world
    no_confirmation = admin.post(RUN, headers=_csrf(admin), json={"kind": "wire", "wire": WIRE, "values": VALUES})
    assert no_confirmation.status_code == 409
    group = _job(sessions)
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group), "length_mm": "325"})
    _written(machine, sent.json()["id"])
    refused = _wire(admin)
    assert refused.status_code == 409 and "сначала завершите" in refused.json()["detail"]


def test_new_cutting_without_marking_clears_but_partial_resend_preserves_it(wire_world):
    sessions, _clock, admin, _equipment, machine = wire_world
    group = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group), "length_mm": "325"}
    sent = _post(admin, RUN, body)
    assert sent.status_code == 200, sent.text
    words = _written(machine, sent.json()["id"])
    assert words["400"] == 0 and all(words[str(address)] == 0 for address in range(440, 485))
    again = _post(admin, RUN, {**body, "values": {"total_set": "12"}})
    assert again.status_code == 200, again.text
    words = _written(machine, again.json()["id"])
    assert not ({"400", "422", "440", "455", "470"} & words.keys())
