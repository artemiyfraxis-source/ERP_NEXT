"""ОБ.019 speed API (plan machine-speed-history S2): «норма vs факт» per setup from the speed history against the БД.ОП
cyclogram, the runs of a setup, filters, rights, and accepting a suggestion — the cyclogram cells and the recomputed
times written through the references edit with the audit `production.machine_norm_accepted`, 409 on a stale old value."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from app.main import app
from app.modules.production.cutting_infrastructure import CuttingGroupRow
from app.modules.production.machine_history import MachineSpeedRow
from app.modules.production.machine_writes import MachineWriteRow
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.shared import http_auth
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import ADMIN_ID, NOW, _csrf, _login, tokens_api  # noqa: F401
from tests.production.test_machine_history import Bridge
from tests.workflows.test_machines_api import machines_api  # noqa: F401

URL = "/api/production/machine-speed"
ACCEPT = f"{URL}/norm-suggestions/accept"
SHORT = "пвам 0,35|L325|S5/5|P0/0|M0"
LONG = "пвам 0,35|L1000|S5/5|P0/0|M0"
ROLL, TOOL = "Скорость проката, сек/м", "Скорость работы инструмента"
HUMAN, MACHINE, OPERATION = "Время Чел, сек/оп; сек/м", "Время машины, сек/оп; сек/м", "Время Операции"
LINEAR = {
    "opNumber": "CUT_WIRE+2strip", "opName": "Резка с разделкой", "tOp": "6,50", "tPrep": "600", "tMachine": "6,50",
    "Номер": "CUT_WIRE+2strip", "Название": "Резка с разделкой", OPERATION: "6,50", "Время подготовки, сек": "600",
    HUMAN: "0,00", MACHINE: "6,50", "Тип операции": "Погонный", "Время ручных работ для взятия полуфабриката": "",
    "Время ручных работ": "", "Время ручных работ для снятия полуфабриката": "", ROLL: "3", TOOL: "0,5",
    "Кол-во операций инструмента": "7",
}
STATIC = {**LINEAR, "opNumber": "CUT_TEST", "Номер": "CUT_TEST", "Тип операции": "Статичный",
          "Время ручных работ для взятия полуфабриката": "2", "Время ручных работ": "3,5", ROLL: ""}


def _record(fields: dict) -> str:
    with app.state.references_uow_factory() as uow:
        record = TechOperationRecord.create(tab=TechOperationTab.OPERATION, display_text=fields["opName"],
                                            search_text=fields["opNumber"], source_sheet="БД.ОП",
                                            sort_key=fields["opNumber"], fields=dict(fields))
        uow.tech_operations.save(record)
        uow.commit()
    return str(record.id)


def _fields(record_id: str) -> dict:
    with app.state.references_uow_factory() as uow:
        return dict(uow.tech_operations.get(UUID(record_id)).fields)


def _history(sessions, equipment_id):
    """Six runs, 325 and 1000 mm by turns, 100 pieces each at t = 2 s + L / 2,5 m/s: 120 s setup («Готов/ждёт» → the
    first piece), rows every 20 pieces, a minute of «Тираж готов» before the next setup."""
    rows, moment, counter, seq = [], NOW - timedelta(days=2), 1000, 0

    def add(state, reason, key):
        nonlocal seq
        seq += 1
        rows.append(MachineSpeedRow(equipment_id=equipment_id, recorded_at=moment, boot="boot-s2", seq=seq,
                                    counter_total=counter, counter_batch=0, counter_order=0, uph=0, state_code=state,
                                    alarm_code=0, setup_key=key, reason=reason))

    for key, length in [(SHORT, 325), (LONG, 1000)] * 3:
        piece = 2 + length / 1000 / 2.5
        add(102, "setup", key)
        moment += timedelta(seconds=120)
        add(103, "state", key)
        for _ in range(5):
            moment += timedelta(seconds=20 * piece)
            counter += 20
            add(103 if _ < 4 else 107, "counter", key)
        moment += timedelta(seconds=60)
    with sessions.begin() as session:
        session.execute(delete(MachineSpeedRow))
        session.add_all(rows)


@pytest.fixture
def speed_api(machines_api):  # noqa: F811
    sessions, clock, admin, ob019, ob101 = machines_api
    Bridge(admin).report()  # binds ОБ.019 (its inventory number for the report)
    _history(sessions, ob019)
    linear = _record(LINEAR)
    return sessions, admin, ob019, ob101, linear


def test_the_report_gives_fact_against_the_cyclogram_and_one_suggestion(speed_api):
    sessions, admin, ob019, _, linear = speed_api
    body = admin.get(URL).json()
    assert (body["since"], body["until"], body["can_accept"]) == ("2026-09-05", "2026-10-05", True)
    assert [(item["equipment_id"], item["inventory_number"]) for item in body["machines"]] == [(str(ob019), "ОБ.019")]
    short, long = sorted(body["rows"], key=lambda row: row["setup"]["length_mm"] != "325")
    assert short["setup"] == {"wire": "пвам 0,35", "length_mm": "325", "strip_front_mm": "5", "strip_back_mm": "5",
                              "pull": "0/0", "marking": "Без маркировки"}
    assert short["operation"] == {"code": "CUT_WIRE+2strip", "name": "Резка с разделкой", "record_id": linear}
    assert (short["runs"], short["pieces"], short["running_seconds"]) == (3, 300, 639.0)
    # 2 + 0,325 / 2,5 = 2,13 s against 0,325 / 3 + 0,5 × 7 = 3,61 s
    assert (short["fact_seconds_per_piece"], short["norm_seconds_per_piece"], short["ratio"]) == ("2.13", "3.61", "0.59")
    assert (long["fact_seconds_per_piece"], long["norm_seconds_per_piece"]) == ("2.40", "3.83")
    assert (short["setup_fact_seconds"], short["setup_norm_seconds"]) == ("120", "600")
    assert short["stops"] == [{"reason": "idle", "label": "Готов/ждёт", "seconds": 540.0}]
    [suggestion] = [row["suggestion"] for row in body["rows"] if row["suggestion"]]
    assert suggestion == {
        "record_id": linear, "operation_code": "CUT_WIRE+2strip",
        "cells": [{"field": ROLL, "label": "Скорость проката, м/с", "old": "3", "new": "2,5"},
                  {"field": TOOL, "label": "Скорость работы инструмента, с", "old": "0,5", "new": "0,3"}],
        "evidence": {"pieces": 600, "runs": 6, "lengths": ["325", "1000"], "fit": "2 с + L / 2,5 м/с",
                     "since": "2026-09-05", "until": "2026-10-05"},
        "note": None,
    }
    runs = admin.get(f"{URL}/runs", params={"equipment_id": str(ob019), "setup_key": SHORT}).json()["runs"]
    assert [(run["pieces"], run["fact_seconds_per_piece"], run["setup_fact_seconds"], run["kind"]) for run in runs] \
        == [(100, "2.13", "120", None)] * 3


def test_filters_by_machine_and_period(speed_api):
    _, admin, ob019, ob101, _ = speed_api
    assert admin.get(URL, params={"equipment_id": str(ob101)}).json()["rows"] == []
    assert len(admin.get(URL, params={"equipment_id": str(ob019)}).json()["rows"]) == 2
    assert admin.get(URL, params={"since": "2026-10-04", "until": "2026-10-05"}).json()["rows"] == []
    assert len(admin.get(URL, params={"since": "2026-10-03", "until": "2026-10-03"}).json()["rows"]) == 2
    assert admin.get(URL, params={"since": "2026-10-05", "until": "2026-10-03"}).status_code == 422
    assert admin.get(URL, params={"since": "2026-01-01", "until": "2026-10-03"}).status_code == 422
    assert admin.get(f"{URL}/runs", params={"equipment_id": str(ob019), "setup_key": "нет"}).json() == {"runs": []}


def _accept(client, record_id, cells, ob019, **extra):
    body = {"record_id": record_id, "cells": cells, "equipment_id": str(ob019), "setup_key": SHORT,
            "since": "2026-09-05", "until": "2026-10-05", **extra}
    return client.post(ACCEPT, json=body, headers=_csrf(client))


def _audits(sessions, event):
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event)))


def test_accept_writes_the_cells_with_evidence_and_a_stale_old_is_409(speed_api):
    sessions, admin, ob019, _, linear = speed_api
    [suggestion] = [row["suggestion"] for row in admin.get(URL).json()["rows"] if row["suggestion"]]
    cells = [{key: cell[key] for key in ("field", "old", "new")} for cell in suggestion["cells"]]
    answer = _accept(admin, linear, cells, ob019)
    assert answer.status_code == 200, answer.text
    # «Погонный» with прокат: the times depend on the length — the sheet's formulas leave them
    assert answer.json() == {"record_id": linear, "written": [{"field": ROLL, "old": "3", "new": "2,5"},
                                                              {"field": TOOL, "old": "0,5", "new": "0,3"}]}
    fields = _fields(linear)
    assert (fields[ROLL], fields[TOOL], fields[OPERATION], fields["tOp"]) == ("2,5", "0,3", "6,50", "6,50")
    [audit] = _audits(sessions, "production.machine_norm_accepted")
    assert audit.aggregate_id == ob019
    assert audit.details["cells"] == answer.json()["written"]
    assert audit.details["evidence"]["pieces"] == 600 and audit.details["evidence"]["fit"] == "2 с + L / 2,5 м/с"
    assert audit.details["evidence"]["fact_seconds_per_piece"] == "2.13"
    assert (audit.details["operation_code"], audit.details["setup_key"]) == ("CUT_WIRE+2strip", SHORT)
    assert len(_audits(sessions, "references.tech_record_edited")) == 1
    # the norm now matches the fact: no suggestion; the same accept again is stale
    rows = admin.get(URL).json()["rows"]
    assert [row["suggestion"] for row in rows] == [None, None]
    assert {row["ratio"] for row in rows} == {"0.96"}  # 2,13 / (0,13 + 0,3 × 7) and 2,4 / (0,4 + 2,1)
    stale = _accept(admin, linear, cells, ob019)
    assert stale.status_code == 409 and "уже поменяли" in stale.json()["detail"]
    assert len(_audits(sessions, "production.machine_norm_accepted")) == 1
    assert _fields(linear)[ROLL] == "2,5"


def test_accept_recomputes_the_sheet_times_of_a_static_row(speed_api):
    sessions, admin, ob019, _, _ = speed_api
    static = _record(STATIC)
    answer = _accept(admin, static, [{"field": TOOL, "old": "0,5", "new": "1.2"}], ob019)
    assert answer.status_code == 200, answer.text
    # hands 2 + 3,5 = 5,5; machine 1,2 × 7 = 8,4; operation = the larger
    assert answer.json()["written"] == [{"field": TOOL, "old": "0,5", "new": "1,2"},
                                        {"field": OPERATION, "old": "6,50", "new": "8,40"},
                                        {"field": HUMAN, "old": "0,00", "new": "5,50"},
                                        {"field": MACHINE, "old": "6,50", "new": "8,40"}]
    fields = _fields(static)
    assert (fields[TOOL], fields[HUMAN], fields[MACHINE], fields[OPERATION]) == ("1,2", "5,50", "8,40", "8,40")
    assert fields["tOp"] == "8,40" and fields["tMachine"] == "8,40"  # the references sync follows the sheet columns
    [audit] = _audits(sessions, "production.machine_norm_accepted")
    assert audit.details["evidence"]["since"] == "2026-09-05"


def test_accept_refuses_a_stale_old_a_foreign_field_or_a_bad_number_and_writes_nothing(speed_api):
    sessions, admin, ob019, _, linear = speed_api
    stale = _accept(admin, linear, [{"field": ROLL, "old": "2,9", "new": "2,5"}], ob019)
    assert stale.status_code == 409
    assert _accept(admin, linear, [{"field": "Название", "old": "Резка с разделкой", "new": "1"}], ob019).status_code == 422
    assert _accept(admin, linear, [{"field": ROLL, "old": "3", "new": "0"}], ob019).status_code == 422
    assert _accept(admin, linear, [{"field": ROLL, "old": "3", "new": "быстро"}], ob019).status_code == 422
    assert _accept(admin, str(uuid4()), [{"field": ROLL, "old": "3", "new": "2"}], ob019).status_code == 404
    assert _fields(linear)[ROLL] == "3"
    assert _audits(sessions, "production.machine_norm_accepted") == []


def test_rights_view_needs_machines_and_accept_needs_the_references_edit(speed_api, monkeypatch):
    sessions, admin, ob019, _, linear = speed_api
    operator = _login("operator")

    allowed = {("production", "view"), ("myops", "view")}
    original = http_auth.section_allowed

    def only_machines(principal, sections, minimum):
        if principal.full_access:
            return original(principal, sections, minimum)
        return any((section, minimum) in allowed for section in sections)

    monkeypatch.setattr(http_auth, "section_allowed", only_machines)
    body = operator.get(URL)
    assert body.status_code == 200 and body.json()["can_accept"] is False
    refused = _accept(operator, linear, [{"field": ROLL, "old": "3", "new": "2,5"}], ob019)
    assert refused.status_code == 403
    allowed.clear()
    assert operator.get(URL).status_code == 403
    assert operator.get(f"{URL}/runs", params={"equipment_id": str(ob019), "setup_key": SHORT}).status_code == 403
    assert _fields(linear)[ROLL] == "3"


def test_a_cutting_run_names_its_etk_operation_and_its_timing_is_the_norm_without_a_bd_op_row(speed_api):
    sessions, admin, ob019, _, _ = speed_api
    group_id, write_id, key = uuid4(), uuid4(), "пвам 0,35|L500|S5/0|P0/0|M0"
    recipe = {"cable": "ПВАМ 0,35", "operations": [
        {"type": "cutWireStrip", "code": "CUT_WIRE+1strip_custom",
         "parameters": {"linearTiming": {"rollSpeed": "2", "toolSeconds": "1", "toolOps": "2"}}}]}
    moment = NOW - timedelta(days=1)
    with sessions.begin() as session:
        session.add(CuttingGroupRow(id=group_id, number="RG-1", idempotency_key="k-1", project_key="P-1", site="ЦАР",
                                    recipe_key="r-1", recipe_version=1, recipe=recipe, created_at=moment))
        session.add(MachineWriteRow(id=write_id, equipment_id=ob019, purpose="transfer", status="done",
                                    payload={"run": {"kind": "cutting", "group_id": str(group_id), "length_mm": "500"}},
                                    summary="Передать на станок", created_by=ADMIN_ID, created_at=moment,
                                    taken_at=moment, finished_at=moment))
        session.flush()
        for seq, (seconds, counter, state, reason) in enumerate(
                [(0, 0, 102, "run"), (30, 0, 103, "state"), (55, 10, 103, "counter"), (80, 20, 107, "counter")], 100):
            session.add(MachineSpeedRow(equipment_id=ob019, recorded_at=moment + timedelta(seconds=seconds), boot="b2",
                                        seq=seq, counter_total=counter, counter_batch=0, counter_order=0, uph=0,
                                        state_code=state, alarm_code=0, run_write_id=write_id, run_kind="cutting",
                                        setup_key=key, reason=reason))
    [row] = [item for item in admin.get(URL).json()["rows"] if item["setup_key"] == key]
    assert row["operation"] == {"code": "CUT_WIRE+1strip_custom", "name": "", "record_id": None}
    assert row["setup"]["wire"] == "ПВАМ 0,35"
    # 0,5 m / 2 m/s + 1 s × 2 = 2,25 s by the ETK timing; fact 50 s / 20 pcs
    assert (row["norm_seconds_per_piece"], row["fact_seconds_per_piece"], row["setup_fact_seconds"]) \
        == ("2.25", "2.50", "30")
    assert row["suggestion"] is None
    runs = admin.get(f"{URL}/runs", params={"equipment_id": str(ob019), "setup_key": key}).json()["runs"]
    assert [(run["kind"], run["pieces"]) for run in runs] == [("cutting", 20)]
