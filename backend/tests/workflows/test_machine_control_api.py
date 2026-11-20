"""ОБ.019 machine commands on the write queue (production/machine_control.py): Stop for everyone who sees the card and
first in line, Reset / Start / Clear alarm with «Управление станками» + confirmation of the state, one controller with
the parameter writes, nothing older than 5 s runs, no answer → «Результат неизвестен», every step journaled."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machine_control as control
from app.modules.production import machine_runs as runs
from app.modules.production import machine_writes as queue
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, _login, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

CARD = "/api/production/machines/card/ОБ.019"
NEXT = "/api/production/machines/agent/writes/next"
AGENT = {"inventory": "ОБ.019", "machine_id": "guid-108"}


def _send(client, command, state=None, hands_clear=True):
    body = {"command": command, "hands_clear": hands_clear}
    if state is not None:
        body["confirm_state"] = state
    return client.post(f"{CARD}/command", json=body, headers=_csrf(client))


def _next(bridge, **agent):
    return TestClient(app).post(NEXT, headers=bridge, json={**AGENT, **agent})


def _result(bridge, command_id, outcome="done", reason="ok", before=None, after=None, detail="", **agent):
    return TestClient(app).post(
        f"/api/production/machines/agent/writes/{command_id}/result", headers=bridge,
        json={**AGENT, **agent, "ok": outcome == "done", "outcome": outcome, "reason": reason, "detail": detail,
              "before": before, "after": after})


def _commands(client):
    return client.get(f"{CARD}/control").json()["commands"]


@pytest.fixture
def ob019(machines_api, monkeypatch):
    sessions, clock, admin, unit, _ = machines_api
    # The command mechanics here; «Пуск» only for an open job run — test_machine_runs_api.py.
    monkeypatch.setattr(runs, "start_blocker", lambda *_args: "")
    bridge = _bridge(admin)
    assert _report(bridge, registers={**WORDS, "766": 102, "782": 0}).status_code == 200
    return sessions, clock, admin, unit, bridge


_seq = iter(range(2, 10_000))


def _state(bridge, clock, state, alarm=0):
    clock.now = clock.now + timedelta(milliseconds=100)
    assert _report(bridge, seq=next(_seq), registers={**WORDS, "766": state, "782": alarm}).status_code == 200


def test_start_goes_through_the_runner_with_the_state_before_and_after_and_is_journaled(ob019):
    sessions, clock, admin, unit, bridge = ob019
    sent = _send(admin, "start", 102)
    assert sent.status_code == 200, sent.text
    assert (sent.json()["summary"], sent.json()["status_text"], sent.json()["command"]) == (
        "Пуск", "Передаётся на станок", "start")
    clock.now = clock.now + timedelta(seconds=2)
    claimed = _next(bridge).json()["command"]
    assert (claimed["kind"], claimed["action"], claimed["deadline_ms"]) == ("control", "start", 3000)
    assert _next(bridge).json() == {"command": None}  # taken once
    answer = _result(bridge, claimed["id"], before={"766": 102, "782": 0}, after={"766": 103, "782": 0},
                     detail="held 1500 ms")
    assert answer.status_code == 200, answer.text
    view = admin.get(f"{CARD}/control").json()
    assert view["can_control"] is True
    [row] = view["commands"]
    assert (row["status"], row["status_text"], row["tone"]) == ("done", "Выполнена", "ok")
    assert (row["state_before"], row["state_after"], row["created_by"]) == ("Готов, ждёт пуска", "Работает", "Admin")
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == unit)
                                 .order_by(AuditEventRow.occurred_at)).all()
    journal = [event for event in events if event.event_type.startswith("production.machine_command")]
    assert [event.event_type for event in journal] == [
        "production.machine_command_requested", "production.machine_command_finished"]
    requested, finished = journal
    # One press on the card, no dialog: the journal does not claim a person confirmed «руки убраны».
    assert requested.details["state_seen"] == "102" and "руки убраны" not in requested.details["text"]
    assert "одним нажатием с карточки" in requested.details["text"] and requested.details["auto_confirm"] == "1"
    assert (finished.details["status"], finished.details["state_before"], finished.details["state_after"],
            finished.details["detail"]) == ("done", "102", "103", "held 1500 ms")
    assert _result(bridge, claimed["id"]).status_code == 409  # the answer is written once


def test_a_command_older_than_five_seconds_is_never_handed_out(ob019):
    sessions, clock, admin, unit, bridge = ob019
    _send(admin, "start", 102)
    clock.now = clock.now + queue.CONTROL_TTL + timedelta(milliseconds=1)
    assert _next(bridge).json() == {"command": None}
    [row] = _commands(admin)
    assert (row["status"], row["error"]) == ("expired", queue.CONTROL_TOO_OLD)
    # The runner refuses one it got too late by its own count: «опоздала».
    _state(bridge, clock, 102)
    _send(admin, "start", 102)
    claimed = _next(bridge).json()["command"]
    assert _result(bridge, claimed["id"], outcome="expired", reason="too_late").status_code == 200
    row = _commands(admin)[0]
    assert (row["status_text"], row["error"]) == ("Не выполнена — опоздала", control.REASONS["too_late"])


def test_stop_needs_only_the_card_jumps_the_queue_and_cancels_queued_commands(ob019):
    sessions, clock, admin, unit, bridge = ob019
    operator = _login("operator")  # «Задания» on edit: sees the card, no «Управление станками»
    start = _send(admin, "start", 102).json()
    stop = operator.post(f"{CARD}/command", json={"command": "stop"}, headers=_csrf(operator))
    assert stop.status_code == 200, stop.text
    again = operator.post(f"{CARD}/command", json={"command": "stop"}, headers=_csrf(operator))
    assert again.json()["id"] == stop.json()["id"]  # a second press while the first waits — the same Stop
    statuses = {row["id"]: (row["status"], row["status_text"]) for row in _commands(admin)}
    assert statuses == {start["id"]: ("cancelled", "Отменена стопом"), stop.json()["id"]: ("queued", "Передаётся на станок")}
    assert _next(bridge).json()["command"]["action"] == "stop"
    # A Start already running on the PC is not cancelled; a Stop pressed meanwhile is claimed next.
    _state(bridge, clock, 102)
    running = _send(admin, "start", 102).json()
    assert _next(bridge).json()["command"]["id"] == running["id"]
    stop2 = operator.post(f"{CARD}/command", json={"command": "stop"}, headers=_csrf(operator)).json()
    assert _next(bridge).json()["command"]["id"] == stop2["id"]


def test_stop_cancels_a_queued_parameter_write_and_one_controller_holds_across_writes_and_commands(ob019):
    sessions, clock, admin, unit, bridge = ob019
    write = admin.post(f"{CARD}/write", json={"values": {"length_mm": "250"}, "confirm_state": 102},
                       headers=_csrf(admin))
    assert write.status_code == 200, write.text
    busy = _send(admin, "start", 102)
    assert busy.status_code == 409 and "уже выполняет" in busy.json()["detail"]
    assert _send(admin, "stop").status_code == 200
    assert {row["id"]: row["status"] for row in _commands(admin)}[write.json()["id"]] == "cancelled"
    assert _next(bridge).json()["command"]["kind"] == "control"


@pytest.mark.parametrize("state, command, alarm", [(102, "reset", 0), (100, "start", 0), (103, "start", 0),
                                                   (102, "alarm_clear", 0)])
def test_preconditions_refuse_in_the_wrong_state(ob019, state, command, alarm):
    sessions, clock, admin, unit, bridge = ob019
    _state(bridge, clock, state, alarm)
    assert _send(admin, command, state).status_code == 409
    assert _commands(admin) == []


@pytest.mark.parametrize("state, command, alarm", [(100, "reset", 0), (105, "reset", 0), (107, "reset", 5),
                                                   (102, "start", 0), (104, "alarm_clear", 6)])
def test_preconditions_allow_in_the_right_state(ob019, state, command, alarm):
    sessions, clock, admin, unit, bridge = ob019
    _state(bridge, clock, state, alarm)
    assert _send(admin, command, state).status_code == 200


def test_access_and_confirmation_refusals(ob019):
    sessions, clock, admin, unit, bridge = ob019
    operator = _login("operator")
    for command in ("start", "reset", "alarm_clear"):
        refused = _send(operator, command, 102)
        assert refused.status_code == 403 and "Управление станками" in refused.json()["detail"]
    assert operator.get(f"{CARD}/control").json()["can_control"] is False
    assert TestClient(app).post(f"{CARD}/command", json={"command": "stop"}).status_code == 401
    assert _send(admin, "start", 102, hands_clear=False).status_code == 409
    changed = _send(admin, "start", 100)
    assert changed.status_code == 409 and "изменилось" in changed.json()["detail"]
    assert _send(admin, "start").status_code == 409  # no state confirmed at all
    assert admin.post(f"{CARD}/command", json={"command": "estop"}, headers=_csrf(admin)).status_code == 422
    clock.now = clock.now + timedelta(seconds=30)  # the bridge is silent: no link → no command
    stale = _send(admin, "start", 102)
    assert stale.status_code == 409 and "нет связи" in stale.json()["detail"]


def test_only_the_bound_runner_takes_commands(ob019):
    sessions, clock, admin, unit, bridge = ob019
    stranger = _bridge(admin, "Чужой мост")
    _send(admin, "stop")
    assert _next(stranger).status_code == 403
    assert _next(bridge, machine_id="guid-other").status_code == 403
    assert admin.post(NEXT, json=AGENT, headers=_csrf(admin)).status_code == 403  # a person
    claimed = _next(bridge).json()["command"]
    assert _result(stranger, claimed["id"]).status_code == 403


def test_no_answer_means_unknown_and_a_late_answer_still_wins(ob019):
    sessions, clock, admin, unit, bridge = ob019
    _send(admin, "start", 102)
    claimed = _next(bridge).json()["command"]
    clock.now = clock.now + queue.CONTROL_RESULT_WAIT + timedelta(seconds=1)
    [row] = _commands(admin)
    assert (row["status"], row["status_text"], row["tone"], row["error"]) == (
        "unknown", "Результат неизвестен", "danger", queue.CONTROL_NO_ANSWER)
    assert _result(bridge, claimed["id"], outcome="failed", reason="hmi_open").status_code == 200
    assert (_commands(admin)[0]["status"], _commands(admin)[0]["error"]) == ("failed", control.REASONS["hmi_open"])
    # The runner itself reports unknown when mbd gave no reply during the hold; that answer stands.
    _state(bridge, clock, 102)
    _send(admin, "start", 102)
    claimed = _next(bridge).json()["command"]
    assert _result(bridge, claimed["id"], outcome="unknown", reason="rumour").status_code == 422
    assert _result(bridge, claimed["id"], outcome="unknown", reason="no_reply").status_code == 200
    assert _commands(admin)[0]["error"] == control.REASONS["no_reply"]
    assert _result(bridge, claimed["id"]).status_code == 409


def test_the_runner_holds_the_same_preconditions_and_only_the_four_bits():
    source = (Path(__file__).parents[3] / "tools" / "machine-agent" / "ob019-writer.ps1").read_text(encoding="ascii")
    table = dict(re.findall(r"^\s+(\w+) = @\{ addr = \d+; bit = \d; states = @\(([\d, ]*)\)", source, re.MULTILINE))
    assert set(table) == set(control.ALLOWED_STATES) == set(queue.COMMANDS) - set(queue.HMI_COMMANDS)
    assert set(control.NAMES) == set(queue.COMMANDS)
    # The panel commands are the runner's own, never a bit: the same names on both sides.
    hmi = re.search(r"^\$script:HmiCommands = @\(([^)]*)\)", source, re.MULTILINE).group(1)
    assert {name.strip(" '") for name in hmi.split(",")} == set(queue.HMI_COMMANDS)
    for command, states in table.items():
        assert ({int(code) for code in states.split(",") if code.strip()} or None) == control.ALLOWED_STATES[command]
    pulses = set(re.findall(r"addr = (\d+); bit = (\d)", source))
    assert pulses == {("599", "2"), ("599", "0"), ("600", "1"), ("600", "4")}  # never D599.3 (E-stop)
    assert set(control.REASONS) >= set(re.findall(r"New-Outcome '\w+' '(\w+)'", source))
    blocked = dict(re.findall(r"^\s+(\w+) = @\{ addr = \d+; bit = \d; states = @\([\d, ]*\); off = @\(([^)]*)\)",
                              source, re.MULTILINE))
    assert {command: tuple(tuple(int(part) for part in item.strip(" '").split(".")) for item in bits.split(","))
            for command, bits in blocked.items()} == control.BLOCKED_BY_BITS


def test_start_is_refused_while_the_feed_rollers_are_lifted(ob019):
    sessions, clock, admin, unit, bridge = ob019
    clock.now = clock.now + timedelta(milliseconds=100)
    assert _report(bridge, seq=next(_seq), registers={**WORDS, "766": 102, "782": 0, "604": 0x0A01}).status_code == 200
    refused = _send(admin, "start", 102)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "«Пуск» невозможен: подающие ролики подняты — сначала опустите их"
    assert _commands(admin) == []
    assert _send(admin, "stop").status_code == 200  # Stop is never blocked
    clock.now = clock.now + timedelta(milliseconds=100)
    assert _report(bridge, seq=next(_seq), registers={**WORDS, "766": 102, "782": 0, "604": 0x0A00}).status_code == 200
    _next(bridge)  # the Stop is taken off the queue
    assert control.precondition_error("start", 102, 0, {604: 0x0A00}) == ""
    assert control.precondition_error("start", 102, 0, {}) == ""  # not read (older bridge): not blocked
    assert control.precondition_error("reset", 107, 0, {604: 1}) == ""


# --- «Закрыть / Открыть пульт на ПК» (20.11.2026) ---

AGENT_14 = {"runner_version": "1.4.0"}


def _pult(bridge, clock, last_state=102):
    """The machine was seen in `last_state`, then the operator panel took the port: a report without words."""
    if last_state is not None:
        _state(bridge, clock, last_state)
    clock.now = clock.now + timedelta(milliseconds=100)
    assert _report(bridge, seq=next(_seq), link="port_busy", registers={}).status_code == 200


def _journal(sessions, unit):
    with sessions() as session:
        return [event.details for event in session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == unit).order_by(AuditEventRow.occurred_at))
            if event.event_type.startswith("production.machine_command")]


def test_close_the_panel_goes_through_the_runner_and_the_card_learns_how(ob019):
    sessions, clock, admin, unit, bridge = ob019
    queue._runners.clear()
    _pult(bridge, clock, 107)
    sent = _send(admin, "hmi_close")
    assert sent.status_code == 200, sent.text
    assert (sent.json()["command"], sent.json()["summary"]) == ("hmi_close", "Закрыть пульт на ПК")
    claimed = _next(bridge, **AGENT_14).json()["command"]
    assert claimed["kind"] == "control" and claimed["action"] == "hmi_close" and 0 < claimed["deadline_ms"] <= 5000
    # A graceful close waits up to 15 s, then the forced one: no «Результат неизвестен» after the usual 15 s.
    clock.now = clock.now + queue.CONTROL_RESULT_WAIT + timedelta(seconds=5)
    assert _commands(admin)[0]["status"] == "taken"
    answer = _result(bridge, claimed["id"], method="forced", **AGENT_14)
    assert answer.status_code == 200, answer.text
    [row] = _commands(admin)
    assert (row["status"], row["method"], row["error"]) == ("done", "forced", "")
    requested, finished = _journal(sessions, unit)
    assert requested["text"] == "Закрыть пульт на ПК: станок «Тираж готов» (до открытия пульта), подтверждено на карточке"
    assert finished["text"] == "Закрыть пульт на ПК: Выполнена — закрыт принудительно" and finished["method"] == "forced"


@pytest.mark.parametrize("last_state", [103, 104, 106])
def test_close_is_refused_when_the_machine_was_in_a_cycle_before_the_panel_opened(ob019, last_state):
    sessions, clock, admin, unit, bridge = ob019
    queue._runners.clear()
    _pult(bridge, clock, last_state)
    refused = _send(admin, "hmi_close")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Станок в цикле — пульт закрывать нельзя, дождитесь остановки"
    assert _commands(admin) == []


def test_close_is_refused_in_a_cycle_live_without_link_or_state_and_without_the_right(ob019):
    sessions, clock, admin, unit, bridge = ob019
    from app.modules.production import machines as rules
    queue._runners.clear()
    _state(bridge, clock, 103)  # the panel is closed, the machine cuts
    assert _send(admin, "hmi_close").json()["detail"] == control.IN_CYCLE_TEXT
    _state(bridge, clock, 102)
    operator = _login("operator")
    assert _send(operator, "hmi_close").status_code == 403
    assert _send(admin, "hmi_close", hands_clear=False).status_code == 409
    with sessions.begin() as session:  # a machine ERP never read before the panel took the port
        snapshot = session.get(rules.MachineSnapshotRow, unit)
        snapshot.last_state, snapshot.last_state_at = None, None
    _pult(bridge, clock, None)
    unknown = _send(admin, "hmi_close")
    assert unknown.status_code == 409 and "не ясно, стоит ли он" in unknown.json()["detail"]
    clock.now = clock.now + timedelta(seconds=30)  # the PC is silent
    stale = _send(admin, "hmi_close")
    assert stale.status_code == 409 and stale.json()["detail"] == "Закрыть пульт на ПК нельзя: нет связи с ПК станка"
    assert _commands(admin) == []


def test_open_the_panel_only_when_it_is_closed_and_an_old_runner_never_gets_either(ob019):
    sessions, clock, admin, unit, bridge = ob019
    queue._runners.clear()
    _next(bridge, runner_version="1.3.0")  # the runner on the PC is 1.3.0
    old = _send(admin, "hmi_open")
    assert old.status_code == 409 and old.json()["detail"] == queue.RUNNER_HMI_OUTDATED
    queue._runners.clear()
    assert _send(admin, "hmi_open").status_code == 200
    assert _next(bridge, runner_version="1.3.0").json() == {"command": None}  # failed unsent
    [row] = _commands(admin)
    assert (row["status"], row["error"]) == ("failed", queue.RUNNER_HMI_OUTDATED)
    queue._runners.clear()
    clock.now = clock.now + timedelta(milliseconds=100)
    sent = _send(admin, "hmi_open").json()
    claimed = _next(bridge, **AGENT_14).json()["command"]
    assert claimed["id"] == sent["id"] and claimed["action"] == "hmi_open"
    assert _result(bridge, claimed["id"], outcome="failed", reason="hmi_not_started", **AGENT_14).status_code == 200
    assert _commands(admin)[0]["error"] == control.REASONS["hmi_not_started"]
    _pult(bridge, clock)
    assert _send(admin, "hmi_open").json()["detail"] == "Пульт на ПК уже открыт"


def test_the_runner_reports_only_known_reasons_and_methods(ob019):
    sessions, clock, admin, unit, bridge = ob019
    queue._runners.clear()
    _pult(bridge, clock)
    assert _send(admin, "hmi_close").status_code == 200
    claimed = _next(bridge, **AGENT_14).json()["command"]
    assert _result(bridge, claimed["id"], outcome="failed", reason="in_cycle", method="kill", **AGENT_14).status_code == 422
    assert _result(bridge, claimed["id"], outcome="failed", reason="in_cycle", **AGENT_14).status_code == 200
    assert _commands(admin)[0]["error"] == control.IN_CYCLE_TEXT
    # The panel's refusals never read as the «panel is open» banner on the card (machineControlApi.operatorText).
    for reason in ("in_cycle", "no_log", "hmi_still_open", "hmi_already_open", "hmi_not_started", "no_helper"):
        assert not re.search(r"PXS_HMI|busy|refused|открыт пульт станка", control.REASONS[reason], re.IGNORECASE)


def test_the_last_state_outlives_a_report_without_words_and_only_a_live_one_moves_it(ob019):
    sessions, clock, admin, unit, bridge = ob019
    from app.modules.production import machines as rules
    _state(bridge, clock, 107)
    _pult(bridge, clock, None)
    with sessions() as session:
        snapshot = session.get(rules.MachineSnapshotRow, unit)
        assert (snapshot.link_state, snapshot.registers, snapshot.last_state) == ("port_busy", {}, 107)
        assert control.hmi_problem("hmi_close", snapshot, clock.now) == ("", 107)
        assert control.hmi_problem("hmi_open", snapshot, clock.now)[0] == "Пульт на ПК уже открыт"
