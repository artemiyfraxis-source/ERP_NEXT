"""ОБ.019 writes from ERP (slice 4, owner 19.11.2026): card writes and «Передать на станок» go through a queued command
the runner on the machine PC claims and confirms by read-back; only an idle machine (102/107, link «На связи») is
written; corrections after a trial — the machine's offset, the ETK revision «правка наладчика», or both — are
journaled on the machine."""

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.techcards.infrastructure import SqlAlchemyTechcardRepository
from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from app.workflows import machine_writes_api
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

CARD = "/api/production/machines/card/ОБ.019"
NEXT = "/api/production/machines/agent/writes/next"
AGENT = {"inventory": "ОБ.019", "machine_id": "guid-108"}  # a runner 1.1.0: it names no version
AGENT_NEW = {**AGENT, "runner_version": "1.2.0"}


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _events(sessions, equipment_id):
    with sessions() as session:
        return [(row.event_type, (row.details or {}).get("text", "")) for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == equipment_id).order_by(AuditEventRow.occurred_at))]


def _result(bridge, command_id, readback, ok=True, error="", agent=AGENT):
    return TestClient(app).post(f"/api/production/machines/agent/writes/{command_id}/result", headers=bridge,
                                json={**agent, "ok": ok, "readback": readback, "error": error})


def test_encoding_texts_zero_filled_ascii_lengths_in_hundredths():
    field, words, value = rules.encode_field("mark_front", "C1.5 to XS17.7")
    assert field.address == 440 and len(words) == 15 and words[:2] == [0x4331, 0x2E35] and words[7:] == [0] * 8
    assert value == "C1.5 to XS17.7"
    assert rules.encode_field("length_mm", "325")[1] == [0x7EF4, 0]
    assert rules.encode_field("length_mm", "700,5")[1] == [70050 & 0xFFFF, 70050 >> 16]
    assert rules.encode_field("knife_back_mm", "0,1")[1] == [10]
    assert rules.encode_field("mode", 2)[1] == [2]
    assert rules.encode_field("mark_back", "")[1] == [0] * 15  # an empty text clears the old one
    for key, value, message in (("mark_front", "Ж1", "кириллицу"), ("mark_front", "x" * 31, "30 знаков"),
                                ("length_mm", "325,001", "точность"), ("knife_back_mm", "6", "диапазона"),
                                ("mode", 3, "Режим"), ("strip_front_mm", "-1", "диапазона"),
                                ("length_mm", "abc", "число")):
        with pytest.raises(rules.MachineWriteInvalid, match=message):
            rules.encode_field(key, value)
    with pytest.raises(rules.MachineWriteInvalid):
        rules.encode_field("program", 5)  # not writable from ERP


def test_encoding_of_the_other_hmi_pages_switches_signed_and_limits():
    # A switch is only the intent for its bit; the word is read and written back on the PC (mbd `setbit`).
    field, words, value = rules.encode_field("marking", "Вкл")
    assert field.address == 400 and words == [1] and value is True
    assert rules.encode_field("middle_strip", "0")[1] == [0]
    with pytest.raises(rules.MachineWriteInvalid, match="Вкл"):
        rules.encode_field("marking", "да")
    with pytest.raises(rules.MachineWriteInvalid, match="не записывает"):
        rules.encode_field("mark_send", "1")  # D421: order transfer handshake, map open question 8
    assert rules.encode_field("middle_end_fix_mm", "-0,5")[1] == [0x10000 - 50]
    assert rules.encode_field("coil_speed_fix", "-9999")[1] == [0x10000 - 9999]
    assert rules.encode_field("split_front_mm", "12,5")[1] == [1250, 0]
    assert rules.encode_field("split_delay_s", "0,25")[1] == [250]
    assert rules.encode_field("speed_cutter", 5)[1] == [5]
    assert rules.encode_field("mark_font", 6)[1] == [6]
    for key, value in (("middle_dia_fix_mm", "5,01"), ("middle_dia_fix_mm", "-5,01"), ("speed_feed", "100"),
                       ("mark_font", "0"), ("mark_divider", "61"), ("coil_ratio_min", "0,49"), ("coil_speed", "-1")):
        with pytest.raises(rules.MachineWriteInvalid, match="диапазона"):
            rules.encode_field(key, value)


def test_switches_go_as_bits_and_one_register_is_written_by_one_field():
    with pytest.raises(rules.MachineWriteInvalid, match="D103"):
        commands.plan_writes([commands.FieldWrite("coil_speed", "5"), commands.FieldWrite("speed_clamp_front", "6")], {})
    blocks, bits, shown = commands.plan_writes(
        [commands.FieldWrite("marking", "1"), commands.FieldWrite("mark_step_mm", "10")], {400: 0b1010})
    assert blocks == [{"addr": 409, "values": [1000, 0]}]
    assert bits == [{"addr": 400, "bit": 0, "value": 1}]  # no word from the snapshot goes to the machine
    assert shown[0]["before"] == "Выкл" and shown[0]["after"] == "Вкл"


def test_unknown_registers_are_labelled_and_never_written():
    labels = [field.label for _title, fields in rules.OB019_SETTINGS for field in fields]
    assert not [label for label in labels if label.startswith(("Колонка", "Переключатель"))]
    for _title, fields in rules.OB019_SETTINGS:
        for field in fields:
            if "назначение не установлено" in field.label:
                assert field.label == f"Параметр D{field.address} (назначение не установлено)" and not field.key
    assert [title for title, _fields in rules.OB019_SETTINGS] == [
        "Задание", "Провод и зачистка", "Средняя зачистка", "Разделение жил", "Намотка", "Маркировка", "Скорости"]


ROOT = Path(__file__).parents[3] / "tools" / "machine-agent"


def _runner_writable() -> set[int]:
    source = (ROOT / "ob019-writer.ps1").read_text(encoding="ascii")
    line = next(row for row in source.splitlines() if row.startswith("foreach ($a in "))
    allowed = {int(item) for group in re.findall(r"@\(([\d, ]+)\)", line) for item in group.split(",")}
    return allowed | {address for low, high in re.findall(r"\((\d+)\.\.(\d+)\)", line)
                      for address in range(int(low), int(high) + 1)}


def _mbd_writable() -> set[int]:
    mbd = (ROOT / "mbd.ps1").read_text(encoding="utf-8-sig")
    line = next(row for row in mbd.splitlines() if row.startswith("$script:allowedWrite"))
    return {address for low, high in re.findall(r"@\((\d+), (\d+)\)", line)
            for address in range(int(low), int(high) + 1)}


def _bits(path: str, prefix: str) -> set[tuple[int, int]]:
    source = (ROOT / path).read_text(encoding="utf-8-sig")
    line = next(row for row in source.splitlines() if row.startswith(prefix))
    return {(int(address), int(bit)) for address, group in re.findall(r"(\d+) = @\(([\d, ]+)\)", line)
            for bit in group.split(",")}


def test_erp_the_runner_and_mbd_write_exactly_the_same_registers_and_bits():
    assert rules.OB019_WRITTEN_WORDS | {address for address, _bit in rules.OB019_WRITTEN_BITS} == {
        address for field in rules.OB019_FIELDS.values() for address in rules.field_words(field)}
    assert _runner_writable() == rules.OB019_WRITTEN_WORDS
    assert _mbd_writable() == rules.OB019_WRITTEN_WORDS
    assert rules.OB019_WRITTEN_BITS == {(132, 0), (400, 0), (604, 0), (422, 0), (434, 0), (435, 0), (436, 0)}
    assert _bits("ob019-writer.ps1", "$script:WritableBits") == rules.OB019_WRITTEN_BITS
    assert _bits("mbd.ps1", "$script:allowedBits") == rules.OB019_WRITTEN_BITS
    # A switch word is never written whole.
    assert not {address for address, _bit in rules.OB019_WRITTEN_BITS} & (_runner_writable() | _mbd_writable())


def test_passwords_sha_command_bits_page_date_and_order_handshake_are_never_writable():
    denied = set(range(1682, 1688)) | set(range(1203, 1212)) | {599, 600, 730} | set(range(1087, 1090))
    assert rules.OB019_NEVER_WRITTEN == denied
    erp = {address for field in rules.OB019_FIELDS.values() for address in rules.field_words(field)}
    for name, allowed in (("ERP", erp), ("ob019-writer.ps1", _runner_writable()), ("mbd.ps1", _mbd_writable()),
                          ("writer bits", {a for a, _ in _bits("ob019-writer.ps1", "$script:WritableBits")}),
                          ("mbd bits", {a for a, _ in _bits("mbd.ps1", "$script:allowedBits")})):
        assert not allowed & (denied | {421}), f"{name} may write {sorted(allowed & (denied | {421}))}"


def test_a_switch_is_claimed_as_a_bit_and_confirmed_by_its_bit_only(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    command_id = _post(admin, CARD + "/write", {"values": {"marking": "1"}}).json()["id"]
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW).json()["command"]
    assert claimed["blocks"] == [] and claimed["bits"] == [{"addr": 400, "bit": 0, "value": 1}]
    assert _result(bridge, command_id, {"400": 0x0A0B}, agent=AGENT_NEW).json() == {"status": "done", "error": ""}
    second = _post(admin, CARD + "/write", {"values": {"marking": "0"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW)
    assert _result(bridge, second, {"400": 0x0A0B}, agent=AGENT_NEW).json() == {
        "status": "failed", "error": "Станок не принял: D400.0"}
    third = _post(admin, CARD + "/write", {"values": {"marking": "0"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW)
    old = _result(bridge, third, {}, ok=False, error="mbd outdated: no version, need 1.2.0", agent=AGENT_NEW).json()
    assert old == {"status": "failed", "error": "Служба обмена со станком (mbd) устарела — обновите до 1.2.0"}


def test_an_old_runner_never_gets_a_write_it_could_do_only_in_part(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    commands._runners.clear()
    bridge = _bridge(admin)
    _report(bridge)
    # Nothing heard from the runner yet: the card does not block, the claim decides.
    assert admin.get(CARD + "/control").json()["runner_outdated"] is False
    mixed = _post(admin, CARD + "/write", {"values": {"length_mm": "326", "marking": "1"}}).json()["id"]
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT).json() == {"command": None}
    with sessions() as session:
        row = session.get(commands.MachineWriteRow, UUID(mixed))
        assert (row.status, row.error, row.taken_at) == ("failed", commands.RUNNER_OUTDATED, None)
    assert any("устарела" in text for kind, text in _events(sessions, ob019) if kind == "production.machine_write_failed")
    # The card now knows the runner is old: new pages are refused, the old ones still go to it.
    control = admin.get(CARD + "/control").json()
    assert control["runner_outdated"] is True and "marking" in control["runner_keys"]
    assert "speed_cutter" in control["runner_keys"] and "length_mm" not in control["runner_keys"]
    refused = _post(admin, CARD + "/write", {"values": {"speed_cutter": "5"}})
    assert refused.status_code == 409 and refused.json()["detail"] == commands.RUNNER_OUTDATED
    legacy = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"]
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT).json()["command"]
    assert claimed["id"] == legacy and claimed["bits"] == []
    _result(bridge, legacy, {"32": 32600, "33": 0})
    # The runner 1.2.0 names itself and gets the new pages.
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW).json() == {"command": None}
    assert admin.get(CARD + "/control").json()["runner_outdated"] is False
    speed = _post(admin, CARD + "/write", {"values": {"speed_cutter": "5", "marking": "0"}}).json()["id"]
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW).json()["command"]
    assert claimed["id"] == speed and claimed["blocks"] == [{"addr": 101, "values": [5]}]
    assert claimed["bits"] == [{"addr": 400, "bit": 0, "value": 0}]
    # A runner that is newer still is current; garbage is not.
    assert commands.runner_current("1.10.0") and not commands.runner_current("1.1.9")
    assert not commands.runner_current("") and not commands.runner_current("abc")


def test_card_write_is_queued_claimed_written_and_confirmed_by_read_back(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    assert _report(bridge).status_code == 200
    control = admin.get(CARD + "/control").json()
    assert control["problem"] == "" and control["state"] == 102 and control["can_edit"] is True
    queued = _post(admin, CARD + "/write", {"values": {"length_mm": "326", "mark_front": "W1", "mode": 1},
                                            "confirm_state": 102})
    assert queued.status_code == 200, queued.text
    view = queued.json()
    assert view["status"] == "queued" and view["status_text"] == "Ждёт агента станка"
    assert {item["label"]: (item["before"], item["after"]) for item in view["fields"]} == {
        "Длина провода": ("325", "326"), "Текст у начала": ("", "W1"), "Режим": ("2", "1")}
    busy = _post(admin, CARD + "/write", {"values": {"length_mm": "327"}})
    assert busy.status_code == 409 and "уже идёт запись" in busy.json()["detail"]

    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT).json()["command"]
    assert claimed["id"] == view["id"] and claimed["states"] == [102, 107]
    assert claimed["blocks"] == [{"addr": 20, "values": [1]}, {"addr": 32, "values": [32600, 0]},
                                 {"addr": 440, "values": [0x5731] + [0] * 14}]
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT).json() == {"command": None}
    readback = {"20": 1, "32": 32600, "33": 0, **{str(440 + index): word for index, word in
                                                 enumerate([0x5731] + [0] * 14)}}
    assert _result(bridge, view["id"], readback).json() == {"status": "done", "error": ""}
    assert _result(bridge, view["id"], {}).json()["status"] == "done"  # a repeated report changes nothing
    control = admin.get(CARD + "/control").json()
    assert control["commands"][0]["status_text"] == "Записано и проверено"
    # Times carry the shop zone even from SQLite: the screen shows them next to «Последний сигнал» without a shift.
    assert control["commands"][0]["created_at"].endswith("+03:00") and control["commands"][0]["finished_at"].endswith("+03:00")
    texts = [text for kind, text in _events(sessions, ob019) if kind.startswith("production.machine_write")]
    assert texts[0].startswith("Запись настроек с карточки: Длина провода 325 → 326")
    assert texts[1] == "Записано и проверено: Запись настроек с карточки"
    journal = admin.get("/api/production/machines/card/ОБ.019").json()["journal"]
    assert any(item["text"].startswith("Записано и проверено") for item in journal)


def test_a_write_the_machine_did_not_take_fails_with_the_registers(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    command_id = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT)
    failed = _result(bridge, command_id, {"32": 32500, "33": 0}).json()
    assert failed == {"status": "failed", "error": "Станок не принял: D32"}
    second = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT)
    busy = _result(bridge, second, {}, ok=False, error="busy: PXS_HMI is running, bus refused").json()
    assert busy["error"] == rules.HMI_OPEN
    third = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT)
    assert _result(bridge, third, {}, ok=False, error="state 103").json()["error"] == "Станок не в ожидании: 103"


def test_only_an_idle_linked_machine_is_written_and_a_late_command_never_runs(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    assert _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).status_code == 404  # no agent yet
    bridge = _bridge(admin)
    _report(bridge, registers={**WORDS, "766": 103})
    running = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}})
    assert running.status_code == 409 and "сейчас: Работает" in running.json()["detail"]
    _report(bridge, seq=2, link="port_busy", registers={})
    busy = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["detail"]
    assert busy == rules.HMI_OPEN and "PXS_HMI" not in busy
    _report(bridge, seq=3)
    moved = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}, "confirm_state": 107})
    assert moved.status_code == 409 and "Состояние станка изменилось" in moved.json()["detail"]
    assert _post(admin, CARD + "/write", {"values": {"speed": "5"}}).status_code == 422
    assert _post(admin, CARD + "/write", {"values": {"mark_front": "Провод"}}).status_code == 422
    command_id = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"]
    clock.now += timedelta(seconds=11)
    _report(bridge, seq=4)
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT).json() == {"command": None}
    with sessions() as session:
        row = session.get(commands.MachineWriteRow, UUID(command_id))
        assert row.status == "expired" and row.finished_at is not None
    clock.now += timedelta(seconds=20)
    stale = _post(admin, CARD + "/write", {"values": {"length_mm": "326"}})
    assert stale.status_code == 409 and stale.json()["detail"] == "Нет связи с ПК станка"
    other = TestClient(app).post(NEXT, headers=bridge, json={**AGENT, "machine_id": "guid-other"})
    assert other.status_code == 403


def test_operator_without_edit_reads_but_does_not_write(machines_api):
    from tests.identity.test_api_tokens import _login

    sessions, clock, admin, ob019, _ = machines_api
    _report(_bridge(admin))
    operator = _login("operator")
    if operator.get(CARD + "/control").status_code != 200:
        pytest.skip("this operator level has no view of «Станки»")
    assert operator.get(CARD + "/control").json()["can_edit"] in (True, False)


def _job(sessions, *, card_id=None, number="ГР-0007", lengths=(("325", 10), ("325", 2), ("150", 4)), laser=None):
    group_id = uuid4()
    with sessions.begin() as session:
        session.add(CuttingGroupRow(
            id=group_id, number=number, idempotency_key=str(uuid4()), project_key="708К.", site="ЦАР",
            recipe_key="k", recipe_version=1, state="open", joinable=True, created_at=machine_writes_api_now(),
            recipe={"cable": "ПВАМ 0,35 красный", "operations": [{"type": "cutWireStrip", "fields": {"strip": {
                "a": True, "b": True, "lenA": "5", "lenB": "6,5", "modeA": "full", "modeB": "full"},
                **({"laserMark": laser} if laser else {})}}]}))
        session.flush()
        for position, (length, quantity) in enumerate(lengths):
            session.add(CuttingMemberRow(
                id=uuid4(), group_id=group_id, task_id=uuid4(), operation_id=uuid4(), position=position + 1,
                assembly_quantity=quantity, blank_quantity=Decimal(quantity), row_snapshot={"cut_length_mm": length},
                state="active"))
    return group_id


def machine_writes_api_now():
    from tests.identity.test_api_tokens import NOW
    return NOW


def test_transfer_writes_etk_nominal_plus_machine_corrections(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions)
    choices = admin.get(CARD + "/transfer").json()["choices"]
    assert [(item["length_mm"], item["quantity"]) for item in choices] == [("150", "4"), ("325", "12")]
    stored = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "offset": "0,4",
                                                 "measured": "4,6", "note": "проба 3 шт"})
    assert stored.status_code == 200, stored.text
    assert stored.json() == {"offset": "0,40", "etk": [], "written": "5,00", "measured": "4,60"}
    plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
    rows = {row["key"]: row for row in plan["rows"]}
    assert (rows["strip_front_mm"]["nominal"], rows["strip_front_mm"]["offset"], rows["strip_front_mm"]["value"]) == (
        "5,00", "0,40", "5,40")
    assert rows["length_mm"]["value"] == "325,00" and rows["strip_back_mm"]["value"] == "6,50"
    assert rows["knife_back_mm"]["value"] is None and plan["blocking"] == []
    assert plan["job"] == {"total_set": 12, "order_quantity": 12, "order_number": "GR-0007",
                           "product": "PVAM 0,35 L325,00"}
    sent = _post(admin, CARD + "/transfer", {"group_id": str(group_id), "length_mm": "325", "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT).json()["command"]
    words = {block["addr"] + index: word for block in claimed["blocks"] for index, word in enumerate(block["values"])}
    assert (words[32], words[34], words[38], words[24]) == (32500, 540, 650, 12)
    # «Тираж» 12 pieces is the target over the cumulative T.Finish: D22 = D649 (65537 in WORDS) + 12.
    assert words[22] | words[23] << 16 == 0x10001 + 12
    assert 43 not in words  # knife back-off: no эталон yet — the machine keeps its own
    kinds = [kind for kind, _ in _events(sessions, ob019)]
    assert "production.machine_offset_set" in kinds and "production.machine_write_queued" in kinds
    offsets = {row["key"]: row for row in admin.get(CARD + "/control").json()["offsets"]}
    assert offsets["strip_front_mm"]["offset"] == "0,40" and "получилось 4,60 мм" in offsets["strip_front_mm"]["reason"]


def _issued_card(sessions):
    now = machine_writes_api_now()
    operation = {"type": "cutWireStrip", "code": "CUT_WIRE", "position": 1, "connector": "",
                 "fields": {"strip": {"a": True, "b": True, "lenA": "5", "lenB": "6,5", "modeA": "full",
                                      "modeB": "full"}},
                 "card": {"name": "Резка провода", "steps": [{"number": "1", "kind": "Операция", "text": "Отрезать провод",
                                                              "tolerance": ""}], "tooling": [], "safety": [],
                          "defects": []}}
    card = ProjectTechcard(
        id=uuid4(), version=1, project="708К.", item_index="КБ1", product_name="Жгут", source_calculation="",
        deadline=None, status=TechcardStatus.ISSUED, revision=1, document={"operations": [operation]},
        manual_operation_keys=(), created_at=now, updated_at=now, issued_at=now)
    with sessions.begin() as session:
        SqlAlchemyTechcardRepository(session).save(card)
    return card.id


def test_etk_correction_makes_a_revision_notifies_the_technologist_and_journals_the_machine(machines_api, monkeypatch):
    sessions, clock, admin, ob019, _ = machines_api
    _report(_bridge(admin))
    group_id = _job(sessions)
    card_id = _issued_card(sessions)
    monkeypatch.setattr(machine_writes_api, "_etk_cards", lambda session, group: [card_id])
    answer = _post(admin, CARD + "/correction", {"parameter": "strip_back_mm", "case": "both", "offset": "-0,2",
                                                 "etk_value": "7", "group_id": str(group_id), "measured": "7,2"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["offset"] == "-0,20" and answer.json()["etk"][0]["revision"] == 2
    with sessions() as session:
        card = SqlAlchemyTechcardRepository(session).get(card_id)
        assert card.document["operations"][0]["fields"]["strip"]["lenB"] == "7"
        [revision] = [item for item in SqlAlchemyTechcardRepository(session).revisions(card_id) if item.revision == 2]
        assert revision.reason.startswith("правка наладчика: зачистка конца 6,5 → 7,00 мм (ОБ.019)")
        notice = session.scalar(select(OutboxMessageRow).where(OutboxMessageRow.topic == "notification.requested"))
        assert notice.payload["recipient_audiences"] == "technologist"
        assert notice.payload["title"] == "Правка ЭТК наладчиком"
    kinds = [kind for kind, _ in _events(sessions, ob019)]
    assert "production.machine_etk_corrected" in kinds and "production.machine_offset_set" in kinds
    length = _post(admin, CARD + "/correction", {"parameter": "length_mm", "case": "etk", "etk_value": "330",
                                                 "group_id": str(group_id)})
    assert length.status_code == 422 and "длину" in length.json()["detail"]
    assert _post(admin, CARD + "/correction", {"parameter": "strip_back_mm", "case": "machine"}).status_code == 422


# --- «Правка после пробы» from the measured value; the wire plan without a job (owner 19.11.2026) ---

def _offsets(admin):
    return {row["key"]: row["offset"] for row in admin.get(CARD + "/control").json()["offsets"]}


def test_measured_correction_comes_from_the_live_value_not_the_nominal_and_needs_no_job(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    first = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "offset": "0,4"})
    assert first.status_code == 200, first.text
    # Слава wrote 5,40 on the panel by hand; the trial gave 4,60 → the machine cuts 0,80 short: offset = 5,40 − 4,60.
    _report(bridge, seq=2, registers={**WORDS, "34": 540})
    answer = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "measured": "4,6"})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"offset": "0,80", "etk": [], "written": "5,40", "measured": "4,60"}
    # The same trial again without a new transfer: the machine still holds 5,40 — the same offset, not a doubled one.
    again = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "measured": "4,60"})
    assert again.json()["offset"] == "0,80" and _offsets(admin)["strip_front_mm"] == "0,80"
    texts = [text for kind, text in _events(sessions, ob019) if kind == "production.machine_offset_set"]
    assert ("Поправка станка «Зачистка начала»: 0,40 → 0,80 мм — станок режет не так, на станке 5,40, "
            "получилось 4,60 мм") in texts
    # The length: D32 holds 325,00, the piece came out 326,50 → −1,50.
    length = _post(admin, CARD + "/correction", {"parameter": "length_mm", "case": "machine", "measured": "326,5"})
    assert length.json()["offset"] == "-1,50" and length.json()["written"] == "325,00"
    # An explicit offset still wins (manual entry); the bound of ±100 мм holds for the computed one too.
    manual = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "offset": "0,3",
                                                 "measured": "4,6"})
    assert manual.json()["offset"] == "0,30"
    far = _post(admin, CARD + "/correction", {"parameter": "length_mm", "case": "machine", "measured": "500"})
    assert far.status_code == 422 and "100 мм" in far.json()["detail"]
    for bad in ("4,605", "-1", "abc"):
        assert _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine",
                                                   "measured": bad}).status_code == 422


def test_measured_correction_without_fresh_machine_data_is_refused(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    body = {"parameter": "strip_front_mm", "case": "machine", "measured": "4,6"}
    _report(bridge, registers={key: value for key, value in WORDS.items() if key != "34"})
    missing = _post(admin, CARD + "/correction", body)
    assert missing.status_code == 422 and missing.json()["detail"] == machine_writes_api.STALE_LIVE
    _report(bridge, seq=2)
    clock.now += timedelta(seconds=11)
    stale = _post(admin, CARD + "/correction", body)
    assert stale.status_code == 422 and stale.json()["detail"] == (
        "Нет свежих данных со станка — повторите, когда станок на связи")
    assert _offsets(admin)["strip_front_mm"] == "0,00"
    manual = _post(admin, CARD + "/correction", {**body, "offset": "0,5"})
    assert manual.status_code == 200 and manual.json()["written"] is None and manual.json()["offset"] == "0,50"


def test_measured_correction_refuses_a_value_the_card_did_not_show_and_an_open_write(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge, registers={**WORDS, "34": 540})
    body = {"parameter": "strip_front_mm", "case": "machine", "measured": "4,6"}
    # The card showed 5,30 but the machine holds 5,40 now (a hand edit on the panel after the trial): refused.
    changed = _post(admin, CARD + "/correction", {**body, "seen": "5,30"})
    assert changed.status_code == 409 and changed.json()["detail"] == machine_writes_api.LIVE_CHANGED
    assert _offsets(admin)["strip_front_mm"] == "0,00"
    # A write of the card still on its way: the live value is about to change — wait.
    queued = _post(admin, CARD + "/write", {"values": {"strip_front_mm": "5,5"}})
    assert queued.status_code == 200, queued.text
    busy = _post(admin, CARD + "/correction", {**body, "seen": "5,40"})
    assert busy.status_code == 409 and busy.json()["detail"] == machine_writes_api.WRITE_OPEN
    clock.now += timedelta(seconds=11)  # not claimed in 10 s → expired, no longer open
    _report(bridge, seq=2, registers={**WORDS, "34": 540})
    ok = _post(admin, CARD + "/correction", {**body, "seen": "5,40"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["offset"] == "0,80" and _offsets(admin)["strip_front_mm"] == "0,80"
    # Without `seen` (an older card) the live value is used as before.
    assert _post(admin, CARD + "/correction", body).json()["offset"] == "0,80"


def _wire_reference(sessions):
    from app.modules.references.infrastructure import HookupWireRow, WireGaugeRow
    with sessions.begin() as session:
        gauge = WireGaugeRow(id=uuid4(), awg="22", section_mm2=Decimal("0.35"), conductor_d_mm=Decimal("0.77"))
        session.add(gauge)
        session.flush()
        for colour in ("красный", "синий"):
            session.add(HookupWireRow(id=uuid4(), mark=f"ПВАМ 0,35 {colour}", mark_key=f"пвам0,35{colour}",
                                      series="ПВАМ", gauge_id=gauge.id, updated_at=machine_writes_api_now()))


def _recipe(sessions, machine_id, wire="ПВАМ 0,35"):
    from app.modules.equipment_setup.recipe_infrastructure import SqlAlchemyRecipes
    from app.modules.equipment_setup.recipes import ManualRecipeVersion, ob019_recipe_fields
    panel = {"strip_front_mm": "5.30", "pull_front_mm": "5.30", "strip_back_mm": "6.50", "pull_back_mm": "6.50",
             "diameter_mm": "0.80", "knife_back_mm": "0.10"}
    etk = {"strip_front_mm": Decimal(5), "strip_back_mm": Decimal("6.5")}
    geometry, settings = ob019_recipe_fields(wire, panel, etk)
    with sessions.begin() as session:
        SqlAlchemyRecipes(session).append(ManualRecipeVersion(
            uuid4(), 1, machine_id, f"{wire} · ОБ.019", geometry, settings, "Проба", uuid4(), machine_writes_api_now()),
            expected_version=0)


def _set_offsets(admin, **offsets):
    for key, value in offsets.items():
        body = {"parameter": key, "case": "machine", "offset": value}
        assert _post(admin, CARD + "/correction", body).status_code == 200


def _rows(plan):
    return {row["key"]: (row["value"], row["offset"], row["source"]) for row in plan["rows"]}


def test_wire_plan_without_a_job_falls_back_to_the_reference_plus_the_offset(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    _report(_bridge(admin))
    _wire_reference(sessions)
    _set_offsets(admin, diameter_mm="-0,05", length_mm="0,5")
    choices = admin.get(CARD + "/transfer").json()
    assert choices["wires"] == [{"label": "ПВАМ 0,35", "conductor_mm": "0,77", "has_recipe": False}]
    plan = admin.get(CARD + "/transfer", params={"wire": "ПВАМ 0,35 красный", "length_mm": "120"}).json()
    assert plan["group_id"] is None and plan["wire"] == "ПВАМ 0,35" and plan["length_mm"] == "120,00"
    rows = _rows(plan)
    assert rows["length_mm"] == ("120,50", "0,50", "Задание")
    assert rows["diameter_mm"] == ("0,87", "-0,05", "Ø жилы + 0,15")
    assert rows["strip_front_mm"][0] is None and rows["knife_back_mm"][0] is None
    assert plan["blocking"] == ["Зачистка начала", "Зачистка конца"]
    assert plan["job"]["product"] == "PVAM 0,35 L120,00" and plan["quantity"] is None
    assert admin.get(CARD + "/transfer", params={"wire": "ПВАМ 0,35", "length_mm": "0"}).status_code == 422


def test_a_recipe_value_is_final_and_the_offset_is_not_counted_twice(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    _report(_bridge(admin))
    _wire_reference(sessions)
    _set_offsets(admin, strip_front_mm="0,4", diameter_mm="-0,05", knife_back_mm="0,02", length_mm="0,5")
    # A job with no recipe yet: the job's diameter is the fallback + the offset.
    group_id = _job(sessions)
    rows = _rows(admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json())
    assert rows["diameter_mm"] == ("0,87", "-0,05", "Ø жилы + 0,15") and rows["knife_back_mm"][0] is None
    _recipe(sessions, ob019)
    assert admin.get(CARD + "/transfer").json()["wires"] == [
        {"label": "ПВАМ 0,35", "conductor_mm": "0,77", "has_recipe": True}]
    # No job: everything but the length from the recipe, as recorded.
    rows = _rows(admin.get(CARD + "/transfer", params={"wire": "ПВАМ 0,35", "length_mm": "120"}).json())
    assert rows["strip_front_mm"] == ("5,30", None, "Рецепт, версия 1")
    assert rows["pull_back_mm"] == ("6,50", None, "Рецепт, версия 1")
    assert rows["diameter_mm"] == ("0,80", None, "Рецепт, версия 1")
    assert rows["knife_back_mm"] == ("0,10", None, "Рецепт, версия 1")
    assert rows["length_mm"] == ("120,50", "0,50", "Задание")
    # The ETK job: length and strip = ETK + offset; diameter and knife back-off = the recipe, no offset on top.
    rows = _rows(admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json())
    assert rows["strip_front_mm"] == ("5,40", "0,40", "ЭТК") and rows["length_mm"] == ("325,50", "0,50", "Задание")
    assert rows["diameter_mm"] == ("0,80", None, "Рецепт, версия 1")
    assert rows["knife_back_mm"] == ("0,10", None, "Рецепт, версия 1")


def _claimed_writes(bridge):
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT_NEW).json()["command"]
    words = {block["addr"] + index: word for block in claimed["blocks"] for index, word in enumerate(block["values"])}
    return claimed, words, {bit["addr"]: bit["value"] for bit in claimed["bits"]}


def _text_at(words, address):
    raw = b"".join(bytes((words[address + index] >> 8, words[address + index] & 255)) for index in range(15))
    return raw.rstrip(b"\x00").decode()


def _transfer(admin, group_id):
    return _post(admin, CARD + "/transfer", {"group_id": str(group_id), "length_mm": "325", "confirm_state": 102})


def test_transfer_without_etk_marking_has_no_marking_in_plan_and_the_same_write(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions)
    plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
    assert plan["marking"] is None
    assert admin.get(CARD + "/transfer", params={"wire": "ПВАМ 0,35", "length_mm": "120"}).json()["marking"] is None
    assert _transfer(admin, group_id).status_code == 200
    claimed, words, bits = _claimed_writes(bridge)
    # Golden: the job and the ETK lengths only; the machine's marking registers stay untouched.
    assert claimed["bits"] == []
    assert not any(address in words for address in (405, 407, 409, 411, 440, 455, 470))


def test_marking_plan_json_and_ends_write_clears_the_middle_text(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    laser = {"mode": "ends", "front": "C1.5 to XS17.7", "back": "XS17.7", "middle": "", "frontMm": "25", "backMm": "30"}
    group_id = _job(sessions, laser=laser)
    plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
    marking = plan["marking"]
    assert marking["mode"] == "ends" and marking["source"] == "ЭТК"
    assert marking["warnings"] == [] and marking["blocking"] == []
    assert [(row["key"], row["value"]) for row in marking["rows"]] == [
        ("marking", "Вкл"), ("mark_by_step", "Выкл"), ("mark_count", "0"), ("mark_front", "C1.5 to XS17.7"),
        ("mark_back", "XS17.7"), ("mark_middle", ""), ("mark_pos_front_mm", "25"), ("mark_pos_back_mm", "30")]
    assert all(set(row) == {"key", "label", "value"} for row in marking["rows"])
    assert _transfer(admin, group_id).status_code == 200
    claimed, words, bits = _claimed_writes(bridge)
    assert bits == {400: 1, 422: 0}
    assert _text_at(words, 440) == "C1.5 to XS17.7" and _text_at(words, 470) == "XS17.7"
    assert all(words[455 + index] == 0 for index in range(15))  # the middle text is cleared, not skipped
    assert (words[405], words[407], words[411]) == (2500, 3000, 0) and 32 in words


def test_full_length_marking_and_a_text_changed_to_latin_warns(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions, laser={"mode": "full", "front": "Ж1", "back": "Ж2", "middle": "Провод 1"})
    plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
    assert plan["marking"]["warnings"] == ["Текст переведён в латиницу: «Провод 1» → «Provod 1»"]
    assert _transfer(admin, group_id).status_code == 200
    claimed, words, bits = _claimed_writes(bridge)
    assert bits == {400: 1, 422: 1}
    assert _text_at(words, 455) == "Provod 1" and not _text_at(words, 440) and not _text_at(words, 470)
    assert (words[409], words[405], words[407]) == (10000, 2000, 2000)


def test_a_serial_token_or_a_long_text_blocks_the_transfer(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    for number, (laser, message) in enumerate((({"mode": "ends", "front": "SN {№}"}, "Серийные номера на лазер"),
                                               ({"mode": "ends", "front": "x" * 31}, "не длиннее 30"))):
        group_id = _job(sessions, laser=laser, number=f"ГР-010{number}")
        plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
        assert message in plan["marking"]["blocking"][0]
        refused = _transfer(admin, group_id)
        assert refused.status_code == 422 and message in refused.json()["detail"]


def test_a_job_run_from_the_card_writes_the_etk_marking_too_and_a_blocked_one_is_refused(machines_api):
    """The card picks a job through `POST …/run` (workflows/machine_runs_api.py): the same marking as «Передать на станок»."""
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions, laser={"mode": "ends", "front": "C1.5 to XS17.7", "back": "XS17.7", "middle": ""})
    sent = _post(admin, CARD + "/run", {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    claimed, words, bits = _claimed_writes(bridge)
    assert bits == {400: 1, 422: 0}
    assert _text_at(words, 440) == "C1.5 to XS17.7" and _text_at(words, 470) == "XS17.7"
    blocked = _job(sessions, laser={"mode": "ends", "front": "SN {№}"}, number="ГР-0200")
    refused = _post(admin, CARD + "/run", {"kind": "cutting", "group_id": str(blocked), "length_mm": "325",
                                           "confirm_state": 102})
    assert refused.status_code == 422 and "Серийные номера на лазер" in refused.json()["detail"]


def test_none_mode_writes_only_the_marking_switch(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions, laser={"mode": "none"})
    assert _transfer(admin, group_id).status_code == 200
    claimed, words, bits = _claimed_writes(bridge)
    assert bits == {400: 0} and not any(address in words for address in (405, 407, 409, 411, 440, 455, 470))


def test_the_inner_layer_is_written_from_runner_and_mbd_1_4_0_and_the_unknown_stay_read_only():
    keys = ("inner_strip_front_mm", "inner_pull_front_mm", "inner_strip_back_mm", "inner_pull_back_mm",
            "inner_diameter_mm", "inner_knife_back_mm")
    assert [rules.OB019_FIELDS[key].address for key in keys] == [44, 46, 48, 50, 52, 53]
    assert rules.encode_field("inner_strip_front_mm", "5,5")[1] == [550, 0]
    assert rules.encode_field("inner_knife_back_mm", "0,10")[1] == [10]
    with pytest.raises(rules.MachineWriteInvalid):
        rules.encode_field("inner_knife_back_mm", "5,01")
    assert commands.runner_needed_for([], [52, 53]) == commands.RUNNER_HMI
    assert commands.runner_needed_for([{"addr": 604, "bit": 0, "value": 1}], [44]) == commands.RUNNER_HMI
    assert commands.runner_needed_for([{"addr": 604, "bit": 0, "value": 1}], [32]) == commands.RUNNER_FEED_WHEEL
    assert commands.runner_refusal(commands.RUNNER_HMI) == commands.RUNNER_HMI_OUTDATED
    writer = (ROOT / "ob019-writer.ps1").read_text(encoding="ascii")
    assert "foreach ($w in (44..53)) { $script:MbdWordVersions[[int]$w] = '1.4.0' }" in writer
    assert set(commands.RUNNER_WORDS) == set(range(44, 54))
    read_only = {field.label for _title, fields in rules.OB019_SETTINGS for field in fields if not field.key}
    assert {"Строка заказа", "Программа", "Зазор переднего прижима", "Зазор заднего прижима",
            "Передача текста в маркиратор"} <= read_only
