"""«Обнулить счётчики» ОБ.019 (owner 23.11.2026): D645..646 / D649..650 → exactly 0 through the runner 1.5.2, only while
the machine stands, journaled with D645..650 before and after; «Новый тираж» can carry it in the same command."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machine_runs as runs
from app.modules.production import machine_writes as commands
from app.modules.production.machine_writes import MachineWriteRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_runs_api import Machine, _counter
from tests.workflows.test_machine_writes_api import AGENT, CARD, NEXT, _events
from tests.workflows.test_machine_wire_runs_api import _wire, _written, wire_world  # noqa: F401
from tests.workflows.test_machines_api import machines_api  # noqa: F401

RUNNER = {**AGENT, "runner_version": "1.5.2"}
RESET = CARD + "/counters/reset"
RESULT = "/api/production/machines/agent/writes/{}/result"
ZEROS = [{"addr": 645, "values": [0, 0]}, {"addr": 649, "values": [0, 0]}]
BEFORE = {"645": 6, "646": 0, "647": 1234, "648": 0, "649": 2056, "650": 0}
AFTER = {"645": 0, "646": 0, "647": 1234, "648": 0, "649": 0, "650": 0}


def finished(state=107, total=2056, batch=6):
    return {**_counter(total, state=state), "645": batch, "646": 0, "22": 2058, "23": 0}


@pytest.fixture
def machine(machines_api):
    sessions, clock, admin, equipment, _ = machines_api
    machine = Machine(admin, clock)
    machine.report(finished())
    TestClient(app).post(NEXT, headers=machine.bridge, json=RUNNER)  # the runner 1.5.2 is on the PC
    return sessions, clock, admin, equipment, machine


def ask(admin, state=107, total=2056, batch=6):
    return admin.post(RESET, headers=_csrf(admin), json={"confirm_state": state, "seen_total": total, "seen_batch": batch})


def claim(machine, agent=RUNNER):
    return TestClient(app).post(NEXT, headers=machine.bridge, json=agent).json()["command"]


def report(machine, command_id, **changes):
    body = {**RUNNER, "ok": True, "before": BEFORE, "outcome": "done", "readback": AFTER, **changes}
    return TestClient(app).post(RESULT.format(command_id), headers=machine.bridge, json=body)


def test_reset_sends_exactly_zeros_and_journals_counters_with_history(machine):
    sessions, _clock, admin, equipment, bridge = machine
    queued = ask(admin)
    assert queued.status_code == 200, queued.text
    assert queued.json()["reset_counters"] == {"before": {"total": 2056, "batch": 6}, "after": None}
    command = claim(bridge)
    assert command["kind"] == "reset_counters"
    assert command["reset"] == ZEROS
    assert all(word == 0 for block in command["reset"] for word in block["values"])
    assert command["blocks"] == [] and command["bits"] == []
    assert command["states"] == [100, 102, 106, 107]
    assert command["seen"] == {"645": 6, "646": 0, "649": 2056, "650": 0}
    assert report(bridge, command["id"]).json() == {"status": "done", "error": ""}
    view = admin.get(CARD + "/control").json()["commands"][0]
    assert view["reset_counters"]["after"] == {"total": 0, "batch": 0}
    texts = [text for kind, text in _events(sessions, equipment) if kind == "production.machine_counters_reset"]
    assert texts == ["Счётчики станка: сделано 2056 → 0, пучок 6 → 0; история (D647) 1234 → 1234"]


@pytest.mark.parametrize("state,allowed", [(100, True), (102, True), (106, True), (107, True),
                                           (101, False), (103, False), (104, False), (105, False)])
def test_reset_only_while_the_machine_stands(machine, state, allowed):
    sessions, _clock, admin, _equipment, bridge = machine
    bridge.report(finished(state=state))
    answer = ask(admin, state=state)
    assert answer.status_code == (200 if allowed else 409), answer.text
    if not allowed:
        assert "только когда станок стоит" in answer.json()["detail"]
        with sessions() as session:
            assert session.scalar(select(MachineWriteRow)) is None


@pytest.mark.parametrize("scenario", ["counted", "stale", "open_write", "busy", "confirm_state"])
def test_reset_refusals_queue_nothing(machine, scenario):
    sessions, clock, admin, _equipment, bridge = machine
    body = {}
    if scenario == "counted":
        bridge.report(finished(total=2057))
    elif scenario == "stale":
        clock.now += timedelta(seconds=6)
    elif scenario == "open_write":
        assert admin.post(CARD + "/write", headers=_csrf(admin),
                          json={"values": {"batch_set": "20"}, "confirm_state": 107}).status_code == 200
    elif scenario == "busy":
        bridge.report(link="port_busy")
    else:
        body = {"state": 102}
    answer = ask(admin, **body)
    assert answer.status_code == 409, answer.text
    with sessions() as session:
        assert not [row for row in session.scalars(select(MachineWriteRow)) if row.payload.get("reset_counters")]


def test_old_runner_is_refused_with_a_russian_reason_and_never_gets_the_command(machine):
    sessions, _clock, admin, _equipment, bridge = machine
    claim(bridge, {**AGENT, "runner_version": "1.5.1"})
    refused = ask(admin)
    assert refused.status_code == 409 and "нужна 1.5.2" in refused.json()["detail"]
    claim(bridge)  # the runner was updated
    queued = ask(admin).json()
    assert claim(bridge, {**AGENT, "runner_version": "1.5.1"}) is None  # an old runner answers again: failed unsent
    with sessions() as session:
        row = session.get(MachineWriteRow, UUID(queued["id"]))
        assert row.status == "failed" and row.error == commands.RUNNER_COUNTERS_OUTDATED


def test_a_counter_that_did_not_become_zero_fails_and_no_answer_is_unknown(machine):
    _sessions, _clock, admin, _equipment, bridge = machine
    first = ask(admin).json()
    claimed = claim(bridge)
    answer = report(bridge, claimed["id"], readback={**AFTER, "649": 2056})
    assert answer.json()["status"] == "failed" and "D649" in answer.json()["error"]
    bridge.report(finished())
    second = ask(admin).json()
    claimed = claim(bridge)
    answer = report(bridge, claimed["id"], ok=False, outcome="unknown", readback={}, error="mbd does not answer")
    assert answer.json() == {"status": "unknown", "error": "Результат обнуления неизвестен — проверьте счётчик на станке"}
    assert first["id"] != second["id"]


def test_reset_is_refused_while_an_erp_job_run_is_open(wire_world):
    _sessions, _clock, admin, _equipment, machine = wire_world
    sent = _wire(admin)
    _written(machine, sent.json()["id"])
    TestClient(app).post(NEXT, headers=machine.bridge, json=RUNNER)
    answer = ask(admin, state=102, total=0x10001, batch=4)
    assert answer.status_code == 409 and "«Новый тираж»" in answer.json()["detail"]


def test_new_batch_with_reset_is_one_command_zeroes_first_and_target_follows(wire_world):
    sessions, _clock, admin, _equipment, machine = wire_world
    sent = _wire(admin)
    _written(machine, sent.json()["id"])
    machine.report({**_counter(0x10001 + 12), "645": 2, "646": 0})
    TestClient(app).post(NEXT, headers=machine.bridge, json=RUNNER)
    body = {"expected_active_run_id": admin.get(CARD + "/run").json()["expected_active_run_id"], "confirm_state": 102,
            "reset_counters": True, "seen_total": 0x10001 + 12, "seen_batch": 2}
    reset = admin.post(CARD + "/run/reset", headers=_csrf(admin), json=body)
    assert reset.status_code == 200, reset.text
    command = claim(machine)
    assert command["kind"] == "reset_counters" and command["reset"] == ZEROS
    assert command["states"] == [102, 107]
    words = {block["addr"] + i: value for block in command["blocks"] for i, value in enumerate(block["values"])}
    assert words[22] == 0 and words[23] == 0
    assert not set(words) & set(range(645, 651))
    readback = {**AFTER, **{str(address): value for address, value in words.items()},
                **{str(bit["addr"]): bit["value"] << bit["bit"] for bit in command["bits"]}}
    before = {"645": 2, "646": 0, "647": 5, "648": 0, "649": 13, "650": 1}
    assert report(machine, command["id"], readback=readback, before=before).json()["status"] == "done"
    with sessions() as session:
        old = runs.info(session.get(MachineWriteRow, UUID(sent.json()["id"])))
        assert old["machine_count"] == 12 and old["closed_at"]


def test_new_batch_reset_needs_the_shown_counters(wire_world):
    _sessions, _clock, admin, _equipment, machine = wire_world
    machine.report({**_counter(2056), "645": 6, "646": 0})
    TestClient(app).post(NEXT, headers=machine.bridge, json=RUNNER)
    body = {"expected_active_run_id": None, "confirm_state": 102, "reset_counters": True}
    assert admin.post(CARD + "/run/reset", headers=_csrf(admin), json=body).status_code == 422
    changed = admin.post(CARD + "/run/reset", headers=_csrf(admin),
                         json={**body, "seen_total": 2000, "seen_batch": 6})
    assert changed.status_code == 409 and "Счётчик станка изменился" in changed.json()["detail"]


@pytest.mark.parametrize("error,status,text", [
    ("reset_counters refused: seen value changed D649", "failed", "Счётчик станка изменился — обновите карточку"),
    ("machine not standing after counter reset: state 103", "unknown", "Счётчики обнулены, но станок уже не стоит"),
    ("no valid echo writing D22", "failed", "Счётчики не обнулены: no valid echo writing D22"),
])
def test_runner_refusals_read_in_operator_words(machine, error, status, text):
    _sessions, _clock, admin, _equipment, bridge = machine
    ask(admin)
    claimed = claim(bridge)
    answer = report(bridge, claimed["id"], ok=False, outcome="unknown" if status == "unknown" else "failed",
                    readback=AFTER if status == "unknown" else {}, error=error).json()
    assert answer["status"] == status and answer["error"].startswith(text)
