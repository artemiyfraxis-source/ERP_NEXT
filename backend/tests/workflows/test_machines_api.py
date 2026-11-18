"""«Станки», slice 1 (read only): the bridge's report (scope, binding to the PC, snapshot order), the list and the live
card of ОБ.019, staleness → «Нет связи с ПК станка»."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machines as rules
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.references.infrastructure import EquipmentMachineRow, EquipmentModelRow, EquipmentUnitRow
from app.modules.warehouse.devices import DeviceRow, PrinterRow
from app.modules.warehouse.marking_profiles import MarkingBindingRow
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401

REPORT = "/api/production/machines/agent/report"
LIST = "/api/production/machines"
WORDS = {"20": 2, "22": 300, "23": 0, "26": 10, "27": 0, "32": 0x7EF4, "33": 0, "34": 500, "35": 0, "42": 35, "43": 10,
         "641": 3621, "642": 0, "645": 4, "646": 0, "649": 0x0001, "650": 0x0001, "711": 1, "766": 102, "782": 0,
         "940": 0x4544, "941": 0x2046, **{str(address): 0 for address in range(942, 955)}, "1280": 3}


def _unit(session, inventory, name):
    unit_id, model_id = uuid4(), uuid4()
    session.add_all([
        EquipmentModelRow(id=model_id),
        EquipmentUnitRow(id=unit_id, catalog="ob", model_id=model_id, ob_id=unit_id),
        EquipmentMachineRow(id=unit_id, tab="ob", equipment_model_id=model_id, display_text=name, normalized_search="",
                            source_sheet="", sort_key="", fields={"Инв. номер": inventory, "Наименование": name}),
    ])
    return unit_id


@pytest.fixture
def machines_api(tokens_api):
    sessions, clock = tokens_api
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    with sessions.begin() as session:
        ob019 = _unit(session, "ОБ.019", "Автомат резки и зачистки провода с лазерной маркировкой")
        ob101 = _unit(session, "ОБ.101", "Godex EZ2350i")
    admin = _login("admin")
    yield sessions, clock, admin, ob019, ob101
    del app.state.production_uow_factory


def _bridge(admin, name="Мост ОБ.019", scopes=("machine:report",)):
    return _bearer(_issue(admin, scopes=scopes, name=name, days=365)["token"])


def _report(headers, *, machine="guid-108", seq=1, boot="boot-1", link="ok", registers=None, inventory="ОБ.019"):
    body = {"proto": 1, "inventory": inventory, "machine_id": machine, "pc_name": "PC-108", "ip_lan": "192.168.1.108",
            "bridge_version": "1.0.0", "service_version": "mbd 1", "boot": boot, "seq": seq, "link": link,
            "error": "" if link == "ok" else "COM8 занят", "registers": WORDS if registers is None else registers}
    return TestClient(app).post(REPORT, headers=headers, json=body)


def test_first_report_binds_token_and_pc_then_the_card_decodes_the_plc(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    answer = _report(bridge)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "accepted": True, "bound": True}
    with sessions() as session:
        link = session.get(rules.MachineLinkRow, ob019)
        assert (link.machine_id, link.pc_name, link.kind) == ("guid-108", "PC-108", "ob019_modbus")
        assert session.scalar(select(AuditEventRow.event_type).where(AuditEventRow.aggregate_id == ob019)) \
            == "production.machine_bound"
    card = admin.get(f"{LIST}/{ob019}").json()
    assert card["link"] == {"code": "ok", "text": "На связи", "tone": "ok"}
    live = card["live"]
    assert live["state"] == {"code": 102, "text": "Готов, ждёт пуска", "tone": "ok"}
    assert live["mode"]["text"] == "Автомат" and live["alarm"]["text"] == "Аварий нет"
    assert live["wire"]["length_mm"] == 325.0 and live["wire"]["strip_front_mm"] == 5.0
    assert live["wire"]["diameter_mm"] == 0.35 and live["wire"]["knife_back_mm"] == 0.1
    assert live["job"]["total_set"] == 300 and live["job"]["total_finished"] == 65537
    assert live["job"]["order_number"] == "ED F" and live["job"]["order_running"] is True
    assert live["job"]["order_quantity"] is None  # D24 was not in the snapshot → «—»
    assert _report(bridge, seq=2).json() == {"ok": True, "accepted": True, "bound": False}


def test_report_refuses_a_person_a_token_without_the_scope_another_pc_and_another_token(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    body = {"proto": 1, "inventory": "ОБ.019", "machine_id": "guid-108", "boot": "b", "seq": 1, "link": "ok"}
    assert admin.post(REPORT, json=body, headers=_csrf(admin)).status_code == 403
    assert _report(_bridge(admin, "Чужой", scopes=("labels:print",))).status_code == 403
    assert _report(bridge).status_code == 200
    wrong_pc = _report(bridge, machine="guid-other", seq=2)
    assert wrong_pc.status_code == 403 and "не привязан к ОБ.019" in wrong_pc.json()["detail"]
    assert _report(_bridge(admin, "Второй мост"), seq=3).status_code == 403
    assert _report(bridge, inventory="ОБ.101").status_code == 403  # no bridge exists for that ОБ
    with sessions() as session:
        assert session.get(rules.MachineSnapshotRow, ob019).seq == 1


def test_one_token_binds_one_ob_and_release_lets_a_new_pc_bind(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    assert _report(bridge).status_code == 200
    with sessions.begin() as session:
        assert rules.release(session, "ОБ.019") is True
        assert rules.release(session, "ОБ.019") is False
    assert _report(bridge, machine="guid-new").json()["bound"] is True


def test_snapshot_protocol_allow_list_and_order(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    assert _report(bridge, seq=5).status_code == 200
    assert _report(bridge, seq=4, registers={"766": 105}).json()["accepted"] is False  # an older one is ignored
    assert _report(bridge, seq=5, registers={"766": 105}).json()["accepted"] is False
    assert _report(bridge, boot="boot-2", seq=0, registers={"766": 105}).json()["accepted"] is True  # bridge restarted
    assert _report(bridge, boot="boot-2", seq=1, registers={"599": 1}).status_code == 422  # not on the allow-list
    assert _report(bridge, boot="boot-2", seq=1, registers={"766": 70000}).status_code == 422
    assert _report(bridge, boot="boot-2", seq=1, registers={"766": -1}).status_code == 422
    with sessions() as session:
        snapshot = session.get(rules.MachineSnapshotRow, ob019)
        assert (snapshot.boot, snapshot.seq, snapshot.registers) == ("boot-2", 0, {"766": 105})
    card = admin.get(f"{LIST}/{ob019}").json()
    assert card["live"]["state"] == {"code": 105, "text": "Аварийный стоп", "tone": "danger"}


def test_list_shows_the_machine_printers_and_devices_and_staleness_means_no_link(machines_api):
    sessions, clock, admin, ob019, ob101 = machines_api
    rows = admin.get(LIST).json()["rows"]
    [waiting] = [row for row in rows if row["inventory"] == "ОБ.019"]
    assert waiting["link"]["text"] == "Мост не установлен" and waiting["card"] is False
    assert admin.get(f"{LIST}/{ob019}").status_code == 404
    bridge = _bridge(admin)
    _report(bridge, link="port_busy", registers={})
    now = clock.now
    with sessions.begin() as session:
        session.add_all([
            ScanAgentRow(machine_id="godex-pc", pc_name="Цех", version="2.9.1", ip_lan="192.168.1.50", first_seen=now,
                         last_seen=now),
            PrinterRow(machine_id="godex-pc", name="Godex EZ2350i", driver="Godex", port="USB001", kind="gdi_png",
                       present=True, last_seen=now),
            MarkingBindingRow(equipment_id=ob101, version=1, machine_id="godex-pc", printer="Godex EZ2350i"),
            DeviceRow(machine_id="godex-pc", kind="s2vna", device_id="S50180", name="Планар S50180", present=True,
                      last_seen=now),
        ])
    rows = {row["key"].split(":")[0] + ":" + (row["inventory"] or row["device"]): row for row in admin.get(LIST).json()["rows"]}
    machine = rows["machine:ОБ.019"]
    assert machine["link"]["text"] == "Пульт PXS_HMI открыт — порт занят"
    assert (machine["pc"], machine["ip"], machine["version"], machine["card"]) == ("PC-108", "192.168.1.108", "1.0.0", True)
    assert machine["state"]["text"] == "Нет данных ПЛК"
    printer = rows["printer:ОБ.101"]
    assert (printer["name"], printer["device"], printer["link"]["text"], printer["state"]["text"]) == (
        "Godex EZ2350i", "Godex EZ2350i", "На связи", "Принтер виден")
    vna = rows["device:Планар S50180"]
    assert (vna["name"], vna["state"]["text"]) == ("Анализатор цепей", "Подключён")
    clock.now = now + rules.STALE_AFTER + timedelta(seconds=1)
    stale = {row["key"]: row for row in admin.get(LIST).json()["rows"]}[f"machine:{ob019}"]
    assert stale["link"] == {"code": "stale", "text": "Нет связи с ПК станка", "tone": "danger"}
    card = admin.get(f"{LIST}/{ob019}").json()
    assert card["link"]["text"] == "Нет связи с ПК станка" and card["age_seconds"] == 11


@pytest.mark.parametrize("permissions, opens", [
    ({"production": "view"}, True), ({"myops": "view"}, True), ({"warehouse": "edit"}, False), ({}, False)])
def test_the_list_opens_for_production_or_myops_view_only(permissions, opens):
    from fastapi import HTTPException
    from dataclasses import replace
    from tests.identity.test_level_gates import _principal
    from app.workflows.machines_api import machines_view_principal
    principal = _principal("operator", permissions)
    try:
        machines_view_principal(principal)
        passed = True
    except HTTPException as error:
        assert error.status_code == 403
        passed = False
    assert passed is opens
    with pytest.raises(HTTPException):
        machines_view_principal(replace(principal, service=True, scopes=frozenset({"machine:report"})))


def test_the_list_needs_a_login(machines_api):
    assert TestClient(app).get(LIST).status_code == 401


def test_decode_unknown_codes_and_missing_words():
    decoded = rules.decode_ob019({766: 999, 782: 19, 20: 7})
    assert decoded["state"]["text"] == "Код 999" and decoded["alarm"] == {"code": 19, "text": "Авария двигателя ножа",
                                                                          "tone": "danger"}
    assert decoded["mode"]["text"] == "Код 7"
    empty = rules.decode_ob019({})
    assert empty["state"]["text"] == "—" and empty["wire"]["length_mm"] is None and empty["job"]["product"] is None


def test_the_bridge_reads_and_sends_exactly_the_server_allow_list():
    import re
    from pathlib import Path
    source = (Path(__file__).parents[3] / "tools" / "machine-agent" / "ob019-bridge.ps1").read_text(encoding="ascii")
    line = next(row for row in source.splitlines() if row.startswith("foreach ($a in "))
    sent = {int(item) for group in re.findall(r"@\(([\d, ]+)\)", line) for item in group.split(",")}
    sent |= {address for low, high in re.findall(r"\((\d+)\.\.(\d+)\)", line) for address in range(int(low), int(high) + 1)}
    assert sent == rules.OB019_ALLOWED
    ranges = [(int(first), int(count)) for first, count in re.findall(r"^\s+@\((\d+), (\d+)\),?\s+#", source, re.MULTILINE)]
    # Few requests (mbd opens COM8 per request), each within mbd's 120-word read limit.
    assert len(ranges) == 5 and all(1 <= count <= 120 for _, count in ranges)
    assert rules.OB019_ALLOWED <= {a for first, count in ranges for a in range(first, first + count)}
