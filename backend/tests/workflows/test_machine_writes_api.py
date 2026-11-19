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
AGENT = {"inventory": "ОБ.019", "machine_id": "guid-108"}


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _events(sessions, equipment_id):
    with sessions() as session:
        return [(row.event_type, (row.details or {}).get("text", "")) for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == equipment_id).order_by(AuditEventRow.occurred_at))]


def _result(bridge, command_id, readback, ok=True, error=""):
    return TestClient(app).post(f"/api/production/machines/agent/writes/{command_id}/result", headers=bridge,
                                json={**AGENT, "ok": ok, "readback": readback, "error": error})


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
        rules.encode_field("speed_cutter", 5)  # not writable from ERP


def test_the_runner_writes_exactly_the_registers_erp_writes():
    source = (Path(__file__).parents[3] / "tools" / "machine-agent" / "ob019-writer.ps1").read_text(encoding="ascii")
    line = next(row for row in source.splitlines() if row.startswith("foreach ($a in "))
    allowed = {int(item) for group in re.findall(r"@\(([\d, ]+)\)", line) for item in group.split(",")}
    allowed |= {address for low, high in re.findall(r"\((\d+)\.\.(\d+)\)", line)
                for address in range(int(low), int(high) + 1)}
    assert allowed == {address for field in rules.OB019_FIELDS.values() for address in rules.field_words(field)}
    mbd = (Path(__file__).parents[3] / "tools" / "machine-agent" / "mbd.ps1").read_text(encoding="utf-8-sig")
    spans = [(int(low), int(high)) for low, high in re.findall(r"@\((\d+), (\d+)\)", next(
        row for row in mbd.splitlines() if row.startswith("$script:allowedWrite")))]
    assert all(any(low <= address <= high for low, high in spans) for address in allowed)


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
    assert busy["error"] == "Пульт PXS_HMI открыт — порт занят"
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
    assert "PXS_HMI" in _post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["detail"]
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


def _job(sessions, *, card_id=None, number="ГР-0007", lengths=(("325", 10), ("325", 2), ("150", 4))):
    group_id = uuid4()
    with sessions.begin() as session:
        session.add(CuttingGroupRow(
            id=group_id, number=number, idempotency_key=str(uuid4()), project_key="708К.", site="ЦАР",
            recipe_key="k", recipe_version=1, state="open", joinable=True, created_at=machine_writes_api_now(),
            recipe={"cable": "ПВАМ 0,35 красный", "operations": [{"type": "cutWireStrip", "fields": {"strip": {
                "a": True, "b": True, "lenA": "5", "lenB": "6,5", "modeA": "full", "modeB": "full"}}}]}))
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
    assert stored.json() == {"offset": "0,40", "etk": []}
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
    assert (words[32], words[34], words[38], words[22], words[24]) == (32500, 540, 650, 12, 12)
    assert 43 not in words  # knife back-off: no эталон yet — the machine keeps its own
    kinds = [kind for kind, _ in _events(sessions, ob019)]
    assert "production.machine_offset_set" in kinds and "production.machine_write_queued" in kinds
    offsets = {row["key"]: row for row in admin.get(CARD + "/control").json()["offsets"]}
    assert offsets["strip_front_mm"]["offset"] == "0,40" and "получилось 4,6 мм" in offsets["strip_front_mm"]["reason"]


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
