"""«Станки», slice 1 (read only): the bridge's report (scope, binding to the PC, snapshot order), the list and the live
card of ОБ.019, staleness → «Нет связи с ПК станка»."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machines as rules
from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyProductionUnitOfWork
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


def _unit(session, inventory, name, **extra):
    unit_id, model_id = uuid4(), uuid4()
    session.add_all([
        EquipmentModelRow(id=model_id),
        EquipmentUnitRow(id=unit_id, catalog="ob", model_id=model_id, ob_id=unit_id),
        EquipmentMachineRow(id=unit_id, tab="ob", equipment_model_id=model_id, display_text=name, normalized_search="",
                            source_sheet="", sort_key="",
                            fields={"Инв. номер": inventory, "Наименование": name, **extra}),
    ])
    return unit_id


@pytest.fixture
def machines_api(tokens_api):
    sessions, clock = tokens_api
    from app.workflows.instrument_catalog import SqlAlchemyInstrumentReferencesUnitOfWork
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.references_uow_factory = lambda: SqlAlchemyInstrumentReferencesUnitOfWork(sessions)
    with sessions.begin() as session:
        ob019 = _unit(session, "ОБ.019", "Автомат резки и зачистки провода с лазерной маркировкой")
        ob101 = _unit(session, "ОБ.101", "Godex EZ2350i")
    admin = _login("admin")
    yield sessions, clock, admin, ob019, ob101
    del app.state.production_uow_factory
    del app.state.references_uow_factory


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
    assert waiting["link"]["text"] == "Агент не установлен" and waiting["card"] is False
    assert waiting["service"] == "Агент ERP · станок"
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
            PrinterRow(machine_id="godex-pc", name="TSC MB341", driver="TSC", port="USB002", kind="tspl",
                       present=True, last_seen=now),
            DeviceRow(machine_id="godex-pc", kind="scan", device_id="pantum", name="Pantum BM2300 Series Scanner",
                      present=True, last_seen=now),
        ])
    rows = {row["key"].split(":")[0] + ":" + (row["inventory"] or row["device"]): row for row in admin.get(LIST).json()["rows"]}
    machine = rows["machine:ОБ.019"]
    assert machine["link"]["text"] == rules.HMI_OPEN
    assert (machine["pc"], machine["ip"], machine["version"], machine["card"]) == ("PC-108", "192.168.1.108", "1.0.0", True)
    assert machine["state"]["text"] == "Нет данных ПЛК"
    # The Godex bound to ОБ.101 is that machine's link — a «Маркировка» tile with the agent, no printer tile of its own.
    printer = rows["machine:ОБ.101"]
    assert (printer["device"], printer["link"]["text"], printer["state"]["text"], printer["controlled"]) == (
        "Godex EZ2350i", "На связи", "Принтер виден", True)
    assert "printer:ОБ.101" not in rows
    vna = rows["device:Планар S50180"]
    assert (vna["name"], vna["state"]["text"]) == ("Анализатор цепей", "Подключён")
    # Owner 20.11.2026: analysers are their own group, the warehouse printer and MFP another (21.11: «Принтеры и МФУ»).
    assert (vna["family"], vna["controlled"]) == ("Анализаторы цепей", True)
    label, scanner = rows["printer:TSC MB341"], rows["device:Pantum BM2300 Series Scanner"]
    assert (label["family"], label["controlled"], scanner["family"], scanner["controlled"]) == (
        "Принтеры и МФУ", True, "Принтеры и МФУ", True)
    # One name for the PC's agent, whatever it does there (owner 18.11: «Мост ERP» vs «Агент печати» confused).
    assert printer["service"] == vna["service"] == "Агент ERP · печать, приборы"
    assert machine["service"] == "Агент ERP · станок"
    clock.now = now + rules.STALE_AFTER + timedelta(seconds=1)
    stale = {row["key"]: row for row in admin.get(LIST).json()["rows"]}[f"machine:{ob019}"]
    assert stale["link"] == {"code": "stale", "text": "Нет связи с ПК станка", "tone": "danger"}
    card = admin.get(f"{LIST}/{ob019}").json()
    assert card["link"]["text"] == "Нет связи с ПК станка" and card["age_seconds"] == 11


def _instrument(session, number, name, app, serial=""):
    session.add(ProductionInstrumentRow(
        id=uuid4(), version=1, inventory_number=number, name=name, instrument_type="Анализатор цепей",
        kind="Средство измерений", serial_number=serial, vna_app=app, status="Поверка не подтверждена"))


def _agent(session, machine, pc, version, now, ip_lan="", ip_seen=""):
    session.add(ScanAgentRow(machine_id=machine, pc_name=pc, version=version, ip_lan=ip_lan, ip_seen=ip_seen,
                             first_seen=now, last_seen=now))


def test_one_analyser_is_one_row_on_the_pc_where_it_really_is(machines_api):
    """Owner 18.11: Arinst and Planar S50180 stood twice — on the ОТК PC (where they are) and on the owner's PC (Arinst
    VL installed, the Planar was there on 16.11). One row per СИ, on the PC with the latest real presence."""
    sessions, clock, admin, _, _ = machines_api
    now = clock.now
    planar = "Planar, S50180, 26030095, 26.2.2/2"
    with sessions.begin() as session:
        _instrument(session, "СИ.001", "Arinst VNA-DL 1–8800 МГц", "Arinst VL")
        _instrument(session, "СИ.002", "Planar S50180", "S2VNA", serial="26030095")
        _agent(session, "otk", "DESKTOP-PTA57QF", "2.9.7", now, ip_lan="192.168.1.121")
        _agent(session, "owner", "DESKTOP-5O46RH6", "2.9.2", now, ip_lan="172.19.0.1", ip_seen="100.75.209.90")
        session.add_all([
            DeviceRow(machine_id="otk", kind="arinst", device_id="arinst", name="Arinst VNA-DL", present=True, last_seen=now),
            DeviceRow(machine_id="owner", kind="arinst", device_id="arinst", name="Arinst VNA-DL", present=True,
                      last_seen=now),
            DeviceRow(machine_id="otk", kind="s2vna", device_id="26030095", name=planar, present=True,
                      last_seen=now - timedelta(minutes=1)),
            DeviceRow(machine_id="owner", kind="s2vna", device_id="26030095", name=planar, present=False,
                      last_seen=now - timedelta(days=2)),
        ])
    rows = [row for row in admin.get(LIST).json()["rows"] if row["kind"] == "device"]
    assert [(row["inventory"], row["pc"]) for row in rows] == [("СИ.001", "DESKTOP-PTA57QF"), ("СИ.002", "DESKTOP-PTA57QF")]
    arinst, vna = rows
    assert arinst["name"] == "Arinst VNA-DL 1–8800 МГц" and arinst["state"]["text"] == "Подключён"
    assert arinst["others"] == "DESKTOP-5O46RH6 — только программа, без прибора (агент 2.9.2)"
    assert vna["ip"] == "192.168.1.121" and vna["device"] == planar and vna["others"] == "DESKTOP-5O46RH6 — не найден"
    # The analyser card picks its work by the reporting program and opens the СИ it is (owner 20.11.2026).
    assert (arinst["vna"], vna["vna"]) == ("arinst", "s2vna") and arinst["instrument_id"] and vna["instrument_id"]
    # Nowhere now → the PC where it was seen last, «Не найден», and since when.
    with sessions.begin() as session:
        session.get(DeviceRow, ("otk", "s2vna", "26030095")).present = False
    vna = {row["inventory"]: row for row in admin.get(LIST).json()["rows"]}["СИ.002"]
    assert (vna["pc"], vna["state"]["text"]) == ("DESKTOP-PTA57QF", "Не найден")
    assert vna["last_seen"].startswith((now - timedelta(minutes=1)).isoformat()[:16])


def test_an_old_agent_claiming_arinst_loses_to_the_pc_that_captured_from_it(machines_api):
    from app.modules.warehouse.scan_jobs import DONE, ScanJobRow
    sessions, clock, admin, _, _ = machines_api
    now = clock.now
    with sessions.begin() as session:
        _instrument(session, "СИ.001", "Arinst VNA-DL", "Arinst VL")
        _agent(session, "otk", "DESKTOP-PTA57QF", "2.8.4", now, ip_lan="192.168.1.121")
        _agent(session, "owner", "DESKTOP-5O46RH6", "2.9.2", now)
        for machine in ("otk", "owner"):
            session.add(DeviceRow(machine_id=machine, kind="arinst", device_id="arinst", name="Arinst VNA-DL",
                                  present=True, last_seen=now if machine == "owner" else now - timedelta(seconds=30)))
        session.add(ScanJobRow(id=uuid4(), kind="vna", source="arinst", status=DONE, target_machine_id="otk",
                               requested_by="u", created_at=now - timedelta(hours=3),
                               claimed_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=3),
                               capture_content=b"! s1p", capture_file_name="trace.s1p", vna_name="Arinst VNA-DL"))
    [row] = [row for row in admin.get(LIST).json()["rows"] if row["kind"] == "device"]
    assert (row["pc"], row["state"]["text"]) == ("DESKTOP-PTA57QF", "Подключён")
    assert row["others"] == "DESKTOP-5O46RH6 — только программа, без прибора (агент 2.9.2)"


def test_the_ip_column_prefers_the_lan_then_tailscale_over_a_virtual_adapter():
    from app.workflows.machines_api import _address
    assert _address("172.19.0.1", "192.168.1.5") == "192.168.1.5"
    assert _address("172.19.0.1", "100.75.209.90") == "100.75.209.90"
    assert _address("192.168.1.121", "100.75.209.90") == "192.168.1.121"
    assert _address("", "172.19.0.1") == "172.19.0.1"
    assert _address("169.254.3.3", "") == "169.254.3.3"
    assert _address("", "") == ""


def test_both_ob099_presses_wait_for_their_agent_with_the_catalogue_name(machines_api):
    """ОБ.099 is two presses, ОБ.099.1 and ОБ.099.2: one row each until its read-only agent reports."""
    sessions, clock, admin, _, _ = machines_api
    with sessions.begin() as session:
        _unit(session, "ОБ.099.1", "Пресс сервоприводный для опрессовки контактов в ленте")
        _unit(session, "ОБ.099.2", "Пресс сервоприводный для опрессовки контактов в ленте")
    rows = {row["inventory"]: row for row in admin.get(LIST).json()["rows"] if row["inventory"].startswith("ОБ.099")}
    assert list(rows) == ["ОБ.099.1", "ОБ.099.2"]
    for press in rows.values():
        assert press["name"] == "Пресс сервоприводный для опрессовки контактов в ленте"
        assert (press["link"]["text"], press["card"], press["device"]) == ("Агент не установлен", False, "ПЛК пресса")
    report = _report(_bridge(admin), inventory="ОБ.099")
    assert report.status_code == 403  # the presses report by unit number only


@pytest.mark.parametrize("permissions, opens", [
    ({"production": "view"}, True), ({"myops": "view"}, True), ({"quality": "view"}, True), ({"warehouse": "edit"}, True), ({"warehouse": "view"}, True),
    ({}, False)])
def test_the_list_opens_for_production_myops_or_quality_view_only(permissions, opens):
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
    # Few requests (mbd opens COM8 per request): 5 every cycle, 4 slow pages; each within mbd's 120-word limit.
    assert len(ranges) == 9 and all(1 <= count <= 120 for _, count in ranges)
    assert rules.OB019_ALLOWED <= {a for first, count in ranges for a in range(first, first + count)}


CARD = "/api/production/machines/card/"


def test_the_machines_tab_lists_production_equipment_only(machines_api):
    """Owner 18.11: «Станки» are machines with cards; PCs, analysers and scanners live in «ПК и агенты»."""
    sessions, clock, admin, _, ob101 = machines_api
    now = clock.now
    with sessions.begin() as session:
        _unit(session, "ОБ.094", "Станок резки провода с роторной зачисткой")
        _agent(session, "otk", "DESKTOP-PTA57QF", "2.9.7", now, ip_lan="192.168.1.121")
        session.add(DeviceRow(machine_id="otk", kind="arinst", device_id="arinst", name="Arinst VNA-DL", present=True,
                              last_seen=now))
    body = admin.get(LIST).json()
    machines = {row["inventory"]: row for row in body["machines"]}
    assert list(machines) == ["ОБ.019", "ОБ.094", "ОБ.099.1", "ОБ.099.2"]  # ОБ.101 is not bound to a printer here
    assert machines["ОБ.099.1"]["kind"] == "ob099"
    assert machines["ОБ.094"]["state"] == {"text": "Не подключён", "tone": "neutral"}
    assert machines["ОБ.094"]["name"] == "Станок резки провода с роторной зачисткой"
    assert [row["device"] for row in body["rows"] if row["kind"] == "device"] == ["Arinst VNA-DL"]


def test_the_card_of_ob019_shows_live_values_settings_by_hmi_page_journal_and_connection(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    assert _report(_bridge(admin)).status_code == 200
    [row] = [row for row in admin.get(LIST).json()["machines"] if row["inventory"] == "ОБ.019"]
    assert row["state"] == {"text": "Готов, ждёт пуска", "tone": "ok"}
    assert (row["job"], row["counter"]) == ("ED F", {"value": 65537, "of": 300, "today": False})
    card = admin.get(CARD + "ОБ.019").json()
    assert card["kind"] == "ob019" and card["setup"] is True and card["live"]["mode"]["text"] == "Автомат"
    pages = {group["title"]: {field["label"]: field for field in group["fields"]} for group in card["settings"]}
    assert list(pages) == ["Задание", "Провод и зачистка", "Средняя зачистка", "Разделение жил", "Намотка",
                           "Маркировка", "Скорости"]
    assert pages["Провод и зачистка"]["Длина провода"] == {
        "label": "Длина провода", "value": 325.0, "unit": "мм", "read": True, "key": "length_mm", "kind": "u32"}
    assert pages["Задание"]["Тираж"]["value"] == 300 and pages["Задание"]["Заказ"]["value"] == "ED F"
    assert pages["Задание"]["Режим"]["text"] == "Автомат" and pages["Задание"]["Режим"]["key"] == "mode"
    # Every group is read now; a word the snapshot lacks is «—», a field ERP does not write has no key.
    assert all(field["read"] for group in pages.values() for field in group.values())
    assert pages["Задание"]["Пауза между пучками"]["value"] is None
    assert pages["Скорости"]["Нож"]["key"] == "speed_cutter" and pages["Маркировка"]["Текст у начала"]["key"] == "mark_front"
    assert pages["Задание"]["Программа"]["key"] is None
    assert pages["Намотка"]["Параметр D390 (назначение не установлено)"]["key"] is None
    assert card["positions"]["title"] == "Положение осей"
    assert card["connection"]["pc"] == "PC-108" and card["connection"]["agent"] == "Агент ERP · станок"
    assert card["journal"][-1]["text"] == "Агент станка привязан к ПК PC-108"


def test_the_card_of_a_machine_without_an_agent_and_an_unknown_number(machines_api):
    sessions, clock, admin, _, _ = machines_api
    card = admin.get(CARD + "ОБ.094").json()
    assert card["kind"] == "planned" and card["state"]["text"] == "Не подключён" and card["settings"] == []
    assert card["connection"]["link"]["text"] == "Агент не установлен"
    press = admin.get(CARD + "ОБ.099.2").json()
    assert press["kind"] == "ob099" and press["live"] is None and press["setup"] is False
    assert press["connection"]["link"]["text"] == "Агент не установлен"
    assert admin.get(CARD + "ОБ.099").status_code == 404
    unlinked = admin.get(CARD + "ОБ.019").json()
    assert unlinked["live"] is None and unlinked["settings"][1]["fields"][0]["value"] is None
    assert admin.get(CARD + "ОБ.555").status_code == 404


def test_the_card_of_a_godex_shows_its_queue_profiles_and_print_journal(machines_api):
    from app.modules.warehouse.label_jobs import LabelJobRow
    from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingProfileRow
    sessions, clock, admin, _, ob101 = machines_api
    now = clock.now
    with sessions.begin() as session:
        _agent(session, "godex-pc", "SK-20250818JOKI", "2.9.2", now, ip_lan="192.168.1.70")
        session.add(PrinterRow(machine_id="godex-pc", name="Godex EZ2350i", driver="Godex", port="USB001",
                               kind="gdi_png", present=True, last_seen=now))
        session.flush()
        session.add(MarkingBindingRow(equipment_id=ob101, version=1, machine_id="godex-pc", printer="Godex EZ2350i"))
        profile = uuid4()
        session.add(MarkingProfileRow(id=profile, equipment_id=ob101, inventory_number="ОБ.101", name="Флажок 30×10",
                                      version=2, calibrated=True, geometry={}))
        session.flush()
        batch = uuid4()
        session.add(MarkingBatchRow(id=batch, created_by=uuid4(), created_at=now, idempotency_key="k", fingerprint="f",
                                    profile_id=profile, manifest={}, total=2))
        session.flush()
        common = dict(kind="marking", copies=1, tspl=b"", format="gdi_png", png=b"x", target_machine_id="godex-pc",
                      target_printer="Godex EZ2350i", width_mm=30, height_mm=10, orientation="landscape", batch_id=batch)
        session.add_all([
            LabelJobRow(id=uuid4(), created_at=now, status="spooled", sequence=1, taken_at=now, finished_at=now,
                        attempt="a1", claim_token_id=uuid4(), **common),
            LabelJobRow(id=uuid4(), created_at=now, status="pending", sequence=2, **common),
        ])
    machines = {row["inventory"]: row for row in admin.get(LIST).json()["machines"]}
    godex = machines["ОБ.101"]
    assert godex["state"] == {"text": "Готов", "tone": "ok"} and godex["job"] == "В очереди: 1"
    assert godex["counter"] == {"value": 1, "of": None, "today": True}
    card = admin.get(CARD + "ОБ.101").json()
    assert card["kind"] == "marking" and card["setup"] is False
    assert card["marking"]["profiles"] == [{"id": str(profile), "name": "Флажок 30×10", "calibrated": True, "version": 2}]
    assert card["marking"]["last_job"]["status"] == "Передано принтеру"
    assert [item["text"] for item in card["journal"]] == ["Этикетка 2 · В очереди", "Этикетка 1 · Передано принтеру"] \
        or [item["text"] for item in card["journal"]] == ["Этикетка 1 · Передано принтеру", "Этикетка 2 · В очереди"]
    assert card["connection"]["pc"] == "SK-20250818JOKI" and card["connection"]["extra"] == [["Принтер", "Godex EZ2350i"]]


def test_the_screen_shows_ticked_machines_and_every_agent_linked_one(machines_api):
    """Owner 19.11: «просто галочку для вытягивания» — a БД.ОБ machine is on «Оборудование» when its checkbox
    «Показывать в «Оборудовании»» is on, or when an ERP agent reaches it (whatever the checkbox). СИ never. Every tile
    carries the article, site, operations and family; ОБ.101's printer agent is its link."""
    from app.modules.references.infrastructure import EquipmentMeasuringRow
    sessions, clock, admin, ob019, ob101 = machines_api
    now = clock.now
    with sessions.begin() as session:
        twist = _unit(session, "ОБ.005", "Полуавтомат для скрутки проводов", **{
            "Артикул": "SR-W01", "Расположение": "Цар", "Операции": "Свивка"})
        solder = _unit(session, "ОБ.070.1", "Станция паяльная термовоздушная", **{
            "Артикул": "8620DX-Pro", "Расположение": "Жук", "Операции": "Пайка, Усадка"})
        bath = _unit(session, "ОБ.017", "Ванна ультразвуковая 10 л с нагревом", **{"Артикул": "VBS-10DS"})
        session.flush()
        for unit_id in (twist, solder):
            session.get(EquipmentMachineRow, unit_id).show_in_equipment = True
        unit_id, model_id = uuid4(), uuid4()
        session.add_all([EquipmentModelRow(id=model_id),
                         EquipmentUnitRow(id=unit_id, catalog="ob_measuring", model_id=model_id, ob_measuring_id=unit_id),
                         EquipmentMeasuringRow(id=unit_id, tab="ob_measuring", equipment_model_id=model_id,
                                               display_text="Микрометр", normalized_search="", source_sheet="", sort_key="",
                                               fields={"Инв. номер": "СИ.110", "Наименование": "Микрометр",
                                                       "Артикул": "EW-2550"})])
        _agent(session, "godex-pc", "Цех", "2.9.2", now, ip_lan="192.168.1.70")
        session.add(PrinterRow(machine_id="godex-pc", name="Godex EZ2350i", driver="Godex", port="USB001",
                               kind="gdi_png", present=True, last_seen=now))
        session.flush()
        session.add(MarkingBindingRow(equipment_id=ob101, version=1, machine_id="godex-pc", printer="Godex EZ2350i"))
        ob102 = _unit(session, "ОБ.102", "Полуавтомат для печати и намотки этикеток на провода")
        session.flush()
        session.add(MarkingBindingRow(equipment_id=ob102, version=1, machine_id="godex-pc", printer="Godex EZ2350i"))
    body = admin.get(LIST).json()
    assert body["families"] == ["Резка", "Резка и опрессовка", "Зачистка", "Опрессовка", "Пайка и усадка", "Маркировка",
                                "Анализаторы цепей", "Принтеры и МФУ", "Прочее"]
    machines = {row["inventory"]: row for row in body["rows"] if row["kind"] == "machine"}
    # ОБ.017 is not ticked; СИ.110 is no machine; ОБ.019 / ОБ.094 wait for their bridge; ОБ.101 has an agent.
    assert sorted(machines) == ["ОБ.005", "ОБ.019", "ОБ.070.1", "ОБ.094", "ОБ.101", "ОБ.102"]
    # One Godex serves both marking machines: both are reached by its print agent.
    assert machines["ОБ.102"]["controlled"] is True and machines["ОБ.102"]["equipment_id"] == str(ob102)
    row = machines["ОБ.005"]
    assert (row["article"], row["site"], row["operations"], row["family"]) == ("SR-W01", "ЦАР", ["Свивка"], "Зачистка")
    assert row["link"] == {"code": "not_connected", "text": "Нет подключения", "tone": "neutral"}
    assert row["equipment_id"] == str(twist) and row["card"] is False and row["controlled"] is False
    assert (machines["ОБ.070.1"]["site"], machines["ОБ.070.1"]["family"]) == ("ЖУК", "Пайка и усадка")
    assert machines["ОБ.019"]["controlled"] is False and machines["ОБ.019"]["link"]["code"] == "not_installed"
    marking = machines["ОБ.101"]
    assert (marking["link"]["text"], marking["pc"], marking["equipment_id"], marking["controlled"]) == (
        "На связи", "Цех", str(ob101), True)
    # The Godex is the link of ОБ.101 and ОБ.102, not a third tile (owner 20.11.2026).
    assert [row for row in body["rows"] if row["kind"] == "printer"] == []
    # «Группа на экране» of the model in БД.ОБ wins over the keyword rule (owner 21.11.2026).
    with sessions.begin() as session:
        session.get(EquipmentModelRow, session.get(EquipmentMachineRow, solder).equipment_model_id).screen_group = "other"
    regrouped = {row["inventory"]: row for row in admin.get(LIST).json()["rows"] if row["kind"] == "machine"}
    assert (regrouped["ОБ.070.1"]["family"], regrouped["ОБ.005"]["family"]) == ("Прочее", "Зачистка")
    # The bridge of ОБ.019 reports → «С управлением», with or without the checkbox.
    assert _report(_bridge(admin)).status_code == 200
    linked = {row["inventory"]: row for row in admin.get(LIST).json()["rows"] if row["kind"] == "machine"}["ОБ.019"]
    assert linked["controlled"] is True and linked["card"] is True

    # The checkbox is changed from БД.ОБ (edit right, journaled) and the screen follows.
    shown = admin.put(f"/api/references/equipment/{bath}/shown", json={"shown": True}, headers=_csrf(admin))
    assert shown.status_code == 200, shown.text
    assert "ОБ.017" in {row["inventory"] for row in admin.get(LIST).json()["rows"]}
    assert str(bath) in admin.get("/api/references/equipment-shown").json()["ids"]
    assert admin.put(f"/api/references/equipment/{twist}/shown", json={"shown": False}, headers=_csrf(admin)).status_code == 200
    assert "ОБ.005" not in {row["inventory"] for row in admin.get(LIST).json()["rows"]}
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "references.equipment_shown")).all()
        assert sorted(event.details["shown"] for event in events) == ["да", "нет"]
    # A СИ has no such checkbox.
    assert admin.put(f"/api/references/equipment/{unit_id}/shown", json={"shown": True},
                     headers=_csrf(admin)).status_code == 404


class _FreeMachines:
    """Test reservations: every window is free (occupancy has its own tests)."""

    def __init__(self, session):
        self.session = session

    def acquire(self):
        pass

    def reserve(self, task):
        pass

    def release(self, task):
        pass

    def block(self, task):
        pass


def _person(session, login, level, site, master=False):
    from app.modules.identity.domain import IdentityUser, hash_password
    from app.modules.identity.infrastructure import IdentityUserRow, provision_user
    user_id = uuid4()
    provision_user(session, IdentityUser(id=user_id, login=login, email=f"{login}@example.test", name=login.title(),
                                         department="Цех", role="installer", site=site, active=True, access_level=level,
                                         site_master=master), hash_password(f"{login}-password"))
    session.flush()
    session.get(IdentityUserRow, user_id).site_master = master
    return user_id


@pytest.fixture
def setup_world(machines_api):
    """ОБ.019 on ЦАР with a setup model, a recipe and one «Назначена» task of the technician `tech`; people of both
    sites; the setup unit of work over the same database (the issued operation resolves to ОБ.019, windows are free)."""
    from datetime import timedelta as delta
    from app.modules.equipment_setup.application import IssuedSetupOperation
    from app.modules.equipment_setup.domain import IssuedOperationRef, RecipeVersionRef, SetupTask
    from app.modules.equipment_setup.infrastructure import SqlAlchemySetupTasks, SqlAlchemySetupUnitOfWork
    from app.modules.equipment_setup.recipe_infrastructure import RecipeVersionRow
    from app.modules.equipment_setup.setup_model_infrastructure import SetupModelParameterRow, SetupModelRow
    from app.workflows.equipment_setup_context import SqlAlchemySetupContext
    sessions, clock, admin, ob019, _ = machines_api
    now = clock.now
    with sessions.begin() as session:
        machine = session.get(EquipmentMachineRow, ob019)
        machine.fields = {**machine.fields, "Тип": "Оборудование", "Расположение": "ЦАР"}
        machine.setup_by = "technician"
        model_id = session.get(EquipmentUnitRow, ob019).model_id
        session.add(SetupModelRow(model_id=model_id, requires_setup="full", updated_at=now, version=1))
        session.flush()
        session.add(SetupModelParameterRow(model_id=model_id, param_key="strip_mm", position=0, label="Зачистка",
                                           unit="мм", source="etk", source_field="Зачистка А"))
        people = {login: _person(session, login, level, site, master) for login, level, site, master in (
            ("tech", "operator", "ЦАР", False), ("fitter", "operator", "ЦАР", False),
            ("master", "foreman", "ЦАР", True), ("stranger", "foreman", "ЖУК", True), ("reader", "pm", "ЦАР", False))}
        recipe = uuid4()
        session.add(RecipeVersionRow(recipe_id=recipe, version=1, machine_id=ob019, title="Рецепт", geometry=[],
                                     panel_settings=[], source="тест", created_by=uuid4(), created_at=now))
        session.flush()
        task = SetupTask.create(operation=IssuedOperationRef(uuid4(), 1, uuid4(), uuid4(), 3), machine_id=ob019,
                                technician_id=people["tech"], recipe=RecipeVersionRef(recipe, 1), planned_start=now,
                                planned_finish=now + delta(hours=1), now=now)
        SqlAlchemySetupTasks(session).save(task, expected_version=0)

    def context(session):
        return SqlAlchemySetupContext(session, resolve_issued_operation=lambda reference: IssuedSetupOperation(
            reference, "", (ob019,)))
    before = getattr(app.state, "equipment_setup_uow_factory", None)
    app.state.equipment_setup_uow_factory = lambda: SqlAlchemySetupUnitOfWork(
        sessions, context_factory=context, reservations_factory=_FreeMachines)
    yield sessions, admin, ob019, task.id
    app.state.equipment_setup_uow_factory = before


SETUP_CARD = "/api/production/setup-model/ОБ.019"
CONFIGURE = f"{SETUP_CARD}/configuration"


def test_waiting_for_setup_badge_only_for_technician_machines(setup_world):
    """«Ждёт наладки» (owner 20.11): only a unit whose «Кто налаживает» is «Наладчик»; an operator's own unit never
    shows it, even with an open setup task."""
    sessions, admin, ob019, task = setup_world
    find = lambda: {r["inventory"]: r for r in admin.get(LIST).json()["rows"]}["ОБ.019"]
    assert find()["pending_setup"] == 1
    with sessions.begin() as session:
        session.get(EquipmentMachineRow, ob019).setup_by = "operator"
    assert find()["pending_setup"] == 0


def test_configuring_by_task_moves_the_task_to_ready_and_clears_waiting(setup_world):
    """«Ждёт наладки» — an open task; «Настроено по заданию» by its technician takes it New → В работе → Готова through
    the existing transitions, in one transaction with the configuration; «Настроено вручную» needs a reason."""
    from app.modules.equipment_setup.infrastructure import SetupTaskRow, SetupTransitionRow
    from app.modules.equipment_setup.setup_model_infrastructure import SetupConfigurationRow
    sessions, admin, ob019, task = setup_world
    row = {r["inventory"]: r for r in admin.get(LIST).json()["rows"]}["ОБ.019"]
    assert row["setup"] == {"code": "full", "label": "полная"} and row["pending_setup"] == 1
    tech = _login("tech")
    card = tech.get(SETUP_CARD).json()
    assert [(item["id"], item["version"], item["status"], item["operation"], item["mine"])
            for item in card["open_tasks"]] == [(str(task), 1, "Назначена", "ЭТК 3", True)]
    # The press card reads the required values from this ETK operation (read-only source metadata).
    with sessions() as session:
        operation = session.get(SetupTaskRow, task).snapshot["operation"]
    assert card["open_tasks"][0]["source"] == {"task_id": operation["production_task_id"],
                                               "operation_id": operation["production_operation_id"], "etk_position": 3}
    manual = tech.post(CONFIGURE, json={"expected_version": 0, "values": {"strip_mm": "5"}}, headers=_csrf(tech))
    assert manual.status_code == 422 and "причину" in manual.json()["detail"]
    stray = tech.post(CONFIGURE, json={"expected_version": 0, "task_id": str(task), "values": {"length": "5"}},
                      headers=_csrf(tech))
    assert stray.status_code == 422
    stale_task = tech.post(CONFIGURE, json={"expected_version": 0, "task_id": str(task), "task_version": 2,
                                            "values": {"strip_mm": "5"}}, headers=_csrf(tech))
    assert stale_task.status_code == 409
    with sessions() as session:  # a refused request leaves nothing behind
        assert session.get(SetupConfigurationRow, ob019) is None
        assert session.get(SetupTaskRow, task).status == "New"
    done = tech.post(CONFIGURE, json={"expected_version": 0, "task_id": str(task), "task_version": 1,
                                      "values": {"strip_mm": "5,5"}}, headers=_csrf(tech))
    assert done.status_code == 200, done.text
    assert done.json() == {"ok": True, "version": 1, "values": {"strip_mm": "5.5"}, "task_status": "Ready"}
    with sessions() as session:
        assert (session.get(SetupTaskRow, task).status, session.get(SetupTaskRow, task).version) == ("Ready", 3)
        assert [item.version for item in session.scalars(select(SetupTransitionRow).where(
            SetupTransitionRow.task_id == task).order_by(SetupTransitionRow.version))] == [2, 3]
        assert {event.event_type for event in session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == task))} == {"equipment_setup.start", "equipment_setup.ready"}
    row = {r["inventory"]: r for r in admin.get(LIST).json()["rows"]}["ОБ.019"]
    assert row["pending_setup"] == 0
    again = tech.post(CONFIGURE, json={"expected_version": 1, "task_id": str(task), "values": {"strip_mm": "6"}},
                      headers=_csrf(tech))
    assert again.status_code == 409  # the task is closed
    stale = tech.post(CONFIGURE, json={"expected_version": 0, "values": {"strip_mm": "6"}, "reason": "смена ножа"},
                      headers=_csrf(tech))
    assert stale.status_code == 409
    by_hand = tech.post(CONFIGURE, json={"expected_version": 1, "values": {"strip_mm": "6"}, "reason": "смена ножа"},
                        headers=_csrf(tech))
    assert by_hand.status_code == 200 and by_hand.json()["version"] == 2
    card = tech.get(SETUP_CARD).json()
    assert card["configuration"]["values"] == {"strip_mm": "6"} and card["configuration"]["version"] == 2
    assert [(item["reason"], item["operation"], item["values"]) for item in card["history"]] == [
        ("смена ножа", "", {"strip_mm": "6"}), ("", "ЭТК 3", {"strip_mm": "5.5"})]
    assert card["open_tasks"] == []


def test_setup_task_source_is_none_when_the_snapshot_lacks_the_operation():
    """The press card reads required values only from a complete task/operation/ETK-position triple."""
    from uuid import uuid4 as new_id
    from app.workflows.equipment_setup_model import operation_source
    task, step = str(new_id()), str(new_id())
    assert operation_source({}) is None
    assert operation_source({"operation": None}) is None
    assert operation_source({"operation": {"production_task_id": task, "etk_position": 3}}) is None
    assert operation_source({"operation": {"production_task_id": "не id", "production_operation_id": step, "etk_position": 3}}) is None
    assert operation_source({"operation": {"production_task_id": task, "production_operation_id": step, "etk_position": "3"}}) is None
    assert operation_source({"operation": {"production_task_id": task, "production_operation_id": step, "etk_position": True}}) is None
    assert operation_source({"operation": {"production_task_id": task, "production_operation_id": step, "etk_position": 3}}) == {
        "task_id": task, "operation_id": step, "etk_position": 3}


def test_setup_card_and_configuration_follow_the_task_list_rules(setup_world):
    """Old «Наладка» rules: another site sees no tasks and no journal and may not record; on the site a master sees
    every task, anyone else only their own; «Настроено по заданию» — the assigned technician or the master of the
    machine's site (owner 19.11.2026), the journal names who; view-only users may not record at all."""
    from app.modules.equipment_setup.infrastructure import SetupTaskRow, SetupTransitionRow
    sessions, admin, ob019, task = setup_world
    cards = {login: _login(login).get(SETUP_CARD).json()["open_tasks"] for login in ("tech", "fitter", "master", "stranger")}
    assert {login: [(item["id"], item["can_confirm"]) for item in items] for login, items in cards.items()} == {
        "tech": [(str(task), True)], "fitter": [], "master": [(str(task), True)], "stranger": []}
    assert [(item["id"], item["can_confirm"]) for item in admin.get(SETUP_CARD).json()["open_tasks"]] == [(str(task), False)]
    by_hand = {"expected_version": 0, "values": {"strip_mm": "5"}, "reason": "смена ножа"}
    stranger = _login("stranger")
    refused = stranger.post(CONFIGURE, json=by_hand, headers=_csrf(stranger))
    assert refused.status_code == 403 and "другой площадки" in refused.json()["detail"]
    reader = _login("reader")
    assert reader.get(SETUP_CARD).status_code == 200
    assert reader.post(CONFIGURE, json=by_hand, headers=_csrf(reader)).status_code == 403
    fitter = _login("fitter")
    assert fitter.post(CONFIGURE, json=by_hand, headers=_csrf(fitter)).status_code == 200
    assert stranger.get(SETUP_CARD).json()["history"] == [] and len(fitter.get(SETUP_CARD).json()["history"]) == 1
    body = {"expected_version": 1, "task_id": str(task), "values": {"strip_mm": "5"}}
    for login in ("fitter", "stranger"):  # a plain colleague; the master of another site
        client = _login(login)
        refused = client.post(CONFIGURE, json=body, headers=_csrf(client))
        assert refused.status_code == 403, (login, refused.text)
        assert "мастер площадки" in refused.json()["detail"]
    master = _login("master")
    done = master.post(CONFIGURE, json=body, headers=_csrf(master))
    assert done.status_code == 200, done.text
    assert done.json()["task_status"] == "Ready"
    with sessions() as session:
        row = session.get(SetupTaskRow, task)
        transitions = session.scalars(select(SetupTransitionRow).where(SetupTransitionRow.task_id == task)
                                      .order_by(SetupTransitionRow.version)).all()
        master_id = str(session.scalar(select(AuditEventRow.actor_id).where(
            AuditEventRow.aggregate_id == task, AuditEventRow.event_type == "equipment_setup.ready")))
    assert row.status == "Ready" and {item.snapshot["actor_id"] for item in transitions} == {master_id}
    assert master_id != str(row.technician_id)
    assert master.get(SETUP_CARD).json()["history"][0]["by"] == "Master"


def test_only_a_reference_editor_ticks_the_shown_checkbox(setup_world):
    sessions, admin, ob019, task = setup_world
    reader = _login("reader")  # «База данных»: view only
    assert reader.get("/api/references/equipment-shown").status_code == 200
    refused = reader.put(f"/api/references/equipment/{ob019}/shown", json={"shown": True}, headers=_csrf(reader))
    assert refused.status_code == 403
    with sessions() as session:
        assert session.get(EquipmentMachineRow, ob019).show_in_equipment is False


def test_a_machine_without_a_setup_model_takes_free_values(machines_api):
    sessions, clock, admin, _, _ = machines_api
    with sessions.begin() as session:
        _unit(session, "ОБ.025", "Машина бандажная для кабельных жгутов")
    url = "/api/production/setup-model/ОБ.025/configuration"
    answer = admin.post(url, json={"expected_version": 0, "values": {"Натяжение ленты": "3", "Лента": ""},
                                   "reason": "новая лента"}, headers=_csrf(admin))
    assert answer.status_code == 200, answer.text
    assert answer.json()["values"] == {"Натяжение ленты": "3"}
    empty = admin.post(url, json={"expected_version": 1, "values": {}, "reason": "x"}, headers=_csrf(admin))
    assert empty.status_code == 422


@pytest.mark.parametrize("number, name, vna_app, kind, serial", [
    ("СИ.001", "Arinst VNA-DL", "Arinst VL", "arinst", "arinst"),
    ("СИ.002", "Planar S50180", "S2VNA", "s2vna", "26030095"),
])
def test_the_article_of_an_instrument_comes_from_its_si_card(machines_api, number, name, vna_app, kind, serial):
    from app.modules.references.infrastructure import EquipmentMeasuringRow
    sessions, clock, admin, _, _ = machines_api
    now = clock.now
    with sessions.begin() as session:
        _instrument(session, number, name, vna_app, serial=serial)
        instrument = session.scalar(select(ProductionInstrumentRow).where(ProductionInstrumentRow.inventory_number == number))
        instrument.article = "Canonical article"
        instrument_id = str(instrument.id)
        unit_id, model_id = uuid4(), uuid4()
        session.add_all([EquipmentModelRow(id=model_id),
                         EquipmentUnitRow(id=unit_id, catalog="ob_measuring", model_id=model_id, ob_measuring_id=unit_id),
                         EquipmentMeasuringRow(id=unit_id, tab="ob_measuring", equipment_model_id=model_id,
                                               display_text="ВАЦ", normalized_search="", source_sheet="", sort_key="",
                                               fields={"Инв. номер": number, "Артикул": "Frozen article"})])
        _agent(session, "otk", "DESKTOP-PTA57QF", "2.9.7", now)
        session.add(DeviceRow(machine_id="otk", kind=kind, device_id=serial, name=name, present=True,
                              last_seen=now))
    [row] = [row for row in admin.get(LIST).json()["rows"] if row["kind"] == "device"]
    assert (row["inventory"], row["name"], row["article"]) == (number, name, "Canonical article")
    assert row["instrument_id"] == instrument_id
    assert (row["family"], row["controlled"]) == ("Анализаторы цепей", True)

    for version, changes in enumerate([
        {"article": "Edited article", "name": "Edited analyser"},
        {"inventory_number": "СИ.100"},
        {"article": ""},
    ], start=1):
        response = admin.patch(f"/api/references/instruments/{instrument_id}", headers=_csrf(admin),
                               json={"version": version, **changes})
        assert response.status_code == 200, response.text
        saved = response.json()["instrument"]
        [tile] = [item for item in admin.get(LIST).json()["rows"] if item.get("instrument_id") == instrument_id]
        assert (tile["inventory"], tile["name"], tile["article"]) == (
            saved["inventory_number"], saved["name"], saved["article"])
        assert tile["key"] == row["key"]


@pytest.mark.parametrize("operations, name, article, maker, family", [
    (["Резка", "Резка с разделкой", "Резка с разделкой+ маркировка"], "Автомат", "", "", "Резка"),
    (["Автомат резка + опрессовка", "Лужение"], "Автомат", "", "", "Резка и опрессовка"),
    ([], "Станок резки/ обжим+обжим", "", "", "Резка и опрессовка"),
    (["Разделка"], "Полуавтомат для снятия изоляции с проводов", "", "", "Зачистка"),
    ([], "Станок для зачистки чего?", "", "", "Зачистка"),
    (["Свивка"], "Машина крутильная", "", "", "Зачистка"),
    (["Опрессовка наконечников", "Опрессовка"], "Пресс", "", "", "Опрессовка"),
    (["Опрессовка терминалов", "Ленточный обжим"], "Автомат для снятия изоляции и опрессовки контактов", "", "",
     "Опрессовка"),
    (["Пайка", "Монтаж корпуса"], "Установка", "", "", "Пайка и усадка"),
    (["Монтаж маркировки"], "Полуавтомат", "", "", "Маркировка"),
    ([], "Маркиратор флажковый", "", "", "Маркировка"),
    ([], "Принтер маркировочный портативный", "SPP-L410", "Bixolon", "Принтеры и МФУ"),
    ([], "Принтер кабельных маркеров", "TP80E", "Supvan technology", "Принтеры и МФУ"),
    ([], "МФУ лазерное", "", "", "Принтеры и МФУ"),
    (["ОТК"], "Измеритель коэффициента стоячей волны по напряжению", "", "Agilent", "Анализаторы цепей"),
    ([], "ВАЦ Планар", "S50180", "", "Анализаторы цепей"),
    (["Литье пластика"], "Машина литьевая", "", "", "Прочее"),
    ([], "Принтер 3D экструзионный", "X1-Carbon", "Bambu Lab", "Прочее"),
    ([], "Активатор поверхности", "", "", "Прочее"),  # «вац» only as a word
])
def test_machine_family_falls_back_to_the_keyword_rule(operations, name, article, maker, family):
    assert rules.machine_family(None, operations, name, article, maker) == family


@pytest.mark.parametrize("group, family", [(code, label) for code, label in rules.SCREEN_GROUPS.items()])
def test_the_stored_screen_group_wins_over_the_keywords(group, family):
    assert rules.machine_family(group, ["Опрессовка"], "Пресс", "", "Supvan") == family
