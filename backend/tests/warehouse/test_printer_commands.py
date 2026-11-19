"""The agent's side of «Очистить принтер» and the peel-off setting in the claim reply (migration 0203)."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentUnitRow
from app.modules.warehouse.marking_profiles import MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.printer_commands import PrinterCommandRow, agent_executes_commands
from app.shared.identity import Actor
from tests.identity.test_api_tokens import _bearer, _issue, _login, tokens_api  # noqa: F401

CLAIM = "/api/stock/labels/jobs/claim"
HEARTBEAT = "/api/stock/scan/agent/heartbeat"


def _agent(admin, machine, version="2.10.0"):
    headers = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name=machine)["token"])
    payload = {"machine_id": machine, "pc_name": machine, "version": version,
               "printers": [{"name": "Godex EZ2350i", "driver": "Godex", "port": "USB001", "kind": "gdi_png", "present": True}]}
    assert TestClient(app).post(HEARTBEAT, headers=headers, json=payload).status_code == 200
    return headers


def _bound(sessions, now, peel=True):
    equipment_id, model_id = uuid4(), uuid4()
    with sessions.begin() as session:
        session.add_all([EquipmentModelRow(id=model_id), EquipmentUnitRow(id=equipment_id, catalog="ob", model_id=model_id, ob_id=equipment_id)])
        session.flush()
        session.add(MarkingProfileRow(id=uuid4(), equipment_id=equipment_id, inventory_number="ОБ.101", name="Флажок", version=1,
                                      calibrated=True, geometry={**presets()[-1][2], "peel": peel}))
        session.add(MarkingBindingRow(equipment_id=equipment_id, version=1, machine_id="pc-1", printer="Godex EZ2350i"))
    return equipment_id


def _command(sessions, now, equipment_id):
    from app.modules.warehouse.printer_commands import request_clear

    with sessions.begin() as session:
        row = request_clear(session, equipment_id=equipment_id, machine_id="pc-1", printer="Godex EZ2350i",
                            reason="Буфер", actor=Actor(uuid4(), "Мастер", ""), now=now)
        return row.id


def test_claim_reply_carries_peel_setting_and_the_command_before_any_job(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    other = _agent(admin, "pc-2")
    equipment_id = _bound(sessions, clock.now)
    body = {"machine_id": "pc-1", "capabilities": []}
    reply = service.post(CLAIM, json=body, headers=agent).json()
    assert reply == {"job": None, "marking_printers": [{"name": "Godex EZ2350i", "peel": True}]}
    command_id = _command(sessions, clock.now, equipment_id)
    reply = service.post(CLAIM, json={**body, "capabilities": ["gdi_png"]}, headers=agent).json()
    command = reply["command"]
    assert reply["job"] is None and command["id"] == str(command_id) and command["kind"] == "clear"
    assert "command" not in service.post(CLAIM, json=body, headers=agent).json()  # handed out once
    path = f"/api/stock/labels/printer-commands/{command_id}/result"
    result = {"attempt": command["attempt"], "outcome": "done", "message": "Удалено 1", "removed_jobs": 1, "buffer_cleared": True}
    assert service.post(path, json={**result, "machine_id": "pc-2"}, headers=other).status_code == 403
    assert service.post(path, json={**result, "attempt": str(uuid4())}, headers=agent).status_code == 409
    assert service.post(path, json=result, headers=agent).json() == {"ok": True, "status": "done"}
    assert service.post(path, json=result, headers=agent).json() == {"ok": True, "status": "done"}  # idempotent
    assert service.post(path, json={**result, "outcome": "failed"}, headers=agent).status_code == 409
    assert service.post(f"/api/stock/labels/printer-commands/{uuid4()}/result", json=result, headers=agent).status_code == 404
    with sessions() as session:
        row = session.get(PrinterCommandRow, command_id)
        assert (row.status, row.result) == ("done", {"removed_jobs": 1, "buffer_cleared": True})


def test_failed_command_keeps_the_agent_reason(tokens_api):
    sessions, clock = tokens_api
    admin, service = _login("admin"), TestClient(app)
    agent = _agent(admin, "pc-1")
    command_id = _command(sessions, clock.now, _bound(sessions, clock.now, peel=False))
    reply = service.post(CLAIM, json={"machine_id": "pc-1", "capabilities": []}, headers=agent).json()
    assert reply["marking_printers"] == [{"name": "Godex EZ2350i", "peel": False}]
    result = {"attempt": reply["command"]["attempt"], "outcome": "failed", "message": "Принтер не принял ~S,BUFCLR"}
    path = f"/api/stock/labels/printer-commands/{command_id}/result"
    assert service.post(path, json=result, headers=agent).json()["status"] == "failed"
    with sessions() as session:
        assert session.get(PrinterCommandRow, command_id).message == "Принтер не принял ~S,BUFCLR"


def test_agent_version_gate():
    assert agent_executes_commands("2.10.0") and agent_executes_commands("3.0.0")
    assert not agent_executes_commands("2.9.7") and not agent_executes_commands("") and not agent_executes_commands("2.10")
