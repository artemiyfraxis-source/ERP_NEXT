"""«Выполнение задания» on the ОБ.019 card (owner 19.11.2026, S2): a run starts with a write read back right, counts
from T.Finish (D649) at that moment, shows done of planned, closes with the machine count — the cutting hand-in is
pre-filled from it, a manual job is finished with it; a counter that went back or was not read means manual entry."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session as OrmSession

from app.main import app
from app.modules.production import machine_runs as runs
from app.modules.production import machines as rules
from app.modules.production import manual_jobs as manual
from app.modules.production.cutting_infrastructure import CuttingGroupRow
from app.modules.production.machine_writes import MachineWriteRow
from app.shared.clock import MOSCOW
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_writes_api import (AGENT, CARD, NEXT, _claimed_writes, _confirmed_body, _job, _result,
                                                     _text_at)
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

RUN = CARD + "/run"
START = 0x10001  # D649 = 1, D650 = 1 in WORDS


def _post(client, path, body):
    body = _confirmed_body(client, path, body)
    return client.post(path, json=body, headers=_csrf(client))


def _u32(words: dict, low: int) -> int:
    return words[str(low)] | words[str(low + 1)] << 16


def _counter(value: int, state: int = 102, alarm: int = 0) -> dict:
    return {**WORDS, "649": value & 0xFFFF, "650": value >> 16, "766": state, "782": alarm}


class Machine:
    """The bridge and the runner on the machine PC; the clock moves a second per write so runs keep their order. The
    words written stay on the machine: every later report carries them (D22 = the target written last)."""

    def __init__(self, admin, clock):
        self.clock = clock
        self.bridge = _bridge(admin)
        self.seq = 0
        self.held: dict = {}
        self.last = WORDS
        self.report()

    def report(self, registers=None, **extra):
        self.seq += 1
        self.last = self.last if registers is None else registers
        answer = _report(self.bridge, seq=self.seq, registers={**self.last, **self.held}, **extra)
        assert answer.status_code == 200, answer.text

    def written(self, command_id: str, fresh: bool = True) -> dict:
        """The runner claims the command and reads back exactly what it was asked to write; `fresh` — the bridge
        reports the machine while it writes (as it does twice a second)."""
        claimed = TestClient(app).post(NEXT, headers=self.bridge, json=AGENT).json()["command"]
        assert claimed["id"] == command_id
        readback = {str(block["addr"] + index): word for block in claimed["blocks"]
                    for index, word in enumerate(block["values"])}
        self.held.update(readback)
        if fresh:
            self.report()
        assert _result(self.bridge, command_id, readback).json()["status"] == "done"
        self.clock.now += timedelta(seconds=1)
        return readback


def test_a_cutting_run_counts_from_the_verified_write_and_prefills_the_hand_in(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    assert admin.get(RUN).json()["run"] is None
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["kind"], run["label"], run["quantity"]) == ("writing", "cutting", "ГР-0007 · ПВАМ 0,35 · 325,00 мм", 12)
    words = machine.written(sent.json()["id"])
    # The same words as «Передать на станок»; «Тираж» 12 = the target over T.Finish at the moment it was queued.
    assert (words["32"], _u32(words, 22)) == (32500, START + 12)
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["counter_start"], run["done"]) == ("ready", START, 0)
    # Still «Тираж готов» from the previous job: not finished before anything was cut.
    machine.report(_counter(START, state=107, alarm=5))
    assert admin.get(RUN).json()["run"]["stage"] == "ready"
    machine.report(_counter(START + 5, state=103))
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["done"]) == ("running", 5)
    machine.report(_counter(START + 12, state=107, alarm=5))
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["done"]) == ("finished", 12)
    closed = _post(admin, RUN + "/close", {"run_id": run["id"]})
    assert closed.status_code == 200, closed.text
    assert (closed.json()["run"]["stage"], closed.json()["run"]["machine_count"]) == ("closed", 12)
    assert _post(admin, RUN + "/close", {"run_id": run["id"]}).status_code == 409
    handed = admin.get(f"/api/production/machines/runs/cutting/{group_id}").json()["runs"]
    assert [(item["inventory"], item["length_mm"], item["count"]) for item in handed] == [("ОБ.019", "325", 12)]
    with sessions() as session:
        texts = [row.details["text"] for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == ob019, AuditEventRow.event_type.like("production.machine_run%"))
            .order_by(AuditEventRow.occurred_at))]
    assert texts == [f"Задание на станке: ГР-0007 · ПВАМ 0,35 · 325,00 мм — счётчик станка {START}, цель {START + 12}",
                     "Задание завершено: ГР-0007 · ПВАМ 0,35 · 325,00 мм — по станку 12 шт"]


def test_a_reset_counter_or_a_failed_write_never_gives_a_machine_count(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    first = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}).json()["id"]
    TestClient(app).post(NEXT, headers=machine.bridge, json=AGENT)
    assert _result(machine.bridge, first, {"32": 1}).json()["status"] == "failed"
    clock.now += timedelta(seconds=1)
    assert admin.get(RUN).json()["run"]["stage"] == "failed"
    assert _post(admin, RUN + "/close", {"run_id": first}).status_code == 409  # nothing ran
    second = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}).json()["id"]
    machine.written(second)
    machine.report(_counter(4, state=103))  # the counter went back below the start
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["reset"], run["done"]) == ("reset", True, None)
    cutting = _post(admin, RUN + "/close", {"run_id": second})  # never closed under a cutting machine
    assert cutting.status_code == 409 and cutting.json()["detail"] == runs.CUTTING
    machine.report(_counter(4, state=102))  # «Стоп» pressed
    closed = _post(admin, RUN + "/close", {"run_id": second}).json()["run"]
    assert closed["machine_count"] is None and closed["closed_reset"] is True and closed["closed_unread"] is False
    assert admin.get(f"/api/production/machines/runs/cutting/{group_id}").json()["runs"][0]["count"] is None


def test_the_same_job_written_again_keeps_its_start_another_job_closes_the_first(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}
    machine.written(_post(admin, RUN, body).json()["id"])
    machine.report(_counter(START + 2))
    # After a trial Слава corrects a size and writes the job again: only the rest is asked of the machine, the two
    # pieces already cut still count.
    plan = admin.get(CARD + "/transfer", params={"group_id": str(group_id), "length_mm": "325"}).json()
    assert (plan["planned"], plan["done"], plan["quantity"], plan["job"]["total_set"]) == (12, 2, 10, 10)
    again = _post(admin, RUN, {**body, "length_mm": "325,00", "values": {"length_mm": "326", "total_set": "10"}})
    assert again.status_code == 200, again.text
    machine.written(again.json()["id"])
    run = admin.get(RUN).json()["run"]
    assert (run["counter_start"], run["done"], run["quantity"]) == (START, 2, 12)
    other = _post(admin, RUN, {**body, "length_mm": "150"}).json()["id"]
    with sessions() as session:
        old = runs.info(session.get(MachineWriteRow, UUID(run["id"])))
    assert (old["closed_why"], old["machine_count"]) == ("Передано другое задание", 2)
    assert admin.get(RUN).json()["run"]["id"] == other


def test_a_manual_job_runs_on_the_machine_counter_and_blocks_a_cutting_job(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба для ОТК",
                                                       "wire": "ПВАМ 0,35", "length_mm": "100", "quantity_planned": 5})
    job = job.json()
    assert admin.get(RUN).json()["manual"]["number"] == "Р-000001"
    blocked = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"})
    assert blocked.status_code == 409 and "Р-000001" in blocked.json()["detail"]
    old_path = _post(admin, CARD + "/transfer", {"group_id": str(group_id), "length_mm": "325"})
    assert old_path.status_code == 409
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job["id"], "values": {"strip_front_mm": "5"},
                              "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    words = machine.written(sent.json()["id"])
    assert (words["32"], words["34"], _u32(words, 22)) == (10000, 500, START + 5)
    assert rules._text({int(key): value for key, value in words.items()}, 940, 15) == "R-000001"
    assert rules._text({int(key): value for key, value in words.items()}, 955, 15) == "PVAM 0,35 L100"
    state = admin.get(RUN).json()
    assert (state["run"]["label"], state["manual"]["count_text"]) == ("Р-000001 · ПВАМ 0,35 · 100 мм", "Счётчик станка")
    machine.report(_counter(START + 5, state=107, alarm=5))
    assert admin.get(RUN).json()["run"]["stage"] == "finished"
    version = admin.get(RUN).json()["manual"]["version"]
    assert _post(admin, RUN + "/close", {"run_id": state["run"]["id"]}).status_code == 422  # good is required
    closed = _post(admin, RUN + "/close", {"run_id": state["run"]["id"], "version": version, "good": 4, "defect": 1,
                                           "defect_reason": "надрез", "material": "pending"})
    assert closed.status_code == 200, closed.text
    finished = closed.json()["manual"]
    assert (finished["status"], finished["good"], finished["defect"], finished["machine_count"], finished["count_text"]) \
        == ("done", 4, 1, 5, "Счётчик станка")
    with sessions() as session:
        row = session.get(manual.ManualJobRow, UUID(job["id"]))
        assert (row.counter_start, row.write_id) == (START, UUID(sent.json()["id"]))
        assert row.params["length_mm"] == "100" and row.params["strip_front_mm"] == "5"
    assert _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}).status_code == 200


def test_a_manual_job_with_a_reset_counter_is_finished_by_hand_with_a_mark(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба"}).json()
    no_quantity = _post(admin, RUN, {"kind": "manual", "job_id": job["id"], "values": {"length_mm": "50"}})
    assert no_quantity.status_code == 422 and "Укажите тираж" in no_quantity.json()["detail"]
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job["id"],
                              "values": {"length_mm": "50", "total_set": "7"}}).json()["id"]
    machine.written(sent)
    machine.report(_counter(0, state=100))
    state = admin.get(RUN).json()
    assert state["run"]["stage"] == "reset"
    closed = _post(admin, RUN + "/close", {"run_id": sent, "version": state["manual"]["version"], "good": 7,
                                           "material": "not_issued", "material_note": "обрезки"}).json()["manual"]
    assert (closed["count_text"], closed["machine_count"], closed["good"]) == ("Вручную", None, 7)
    with sessions() as session:
        assert session.get(manual.ManualJobRow, UUID(job["id"])).params["counter_reset"] is True


def test_the_operator_panel_open_on_the_pc_is_said_in_plain_words(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    body_error = "mbd: busy: PXS_HMI is running, bus refused"
    answer = TestClient(app).post("/api/production/machines/agent/report", headers=bridge, json={
        "proto": 1, "inventory": "ОБ.019", "machine_id": "guid-108", "pc_name": "PC-108", "ip_lan": "192.168.1.108",
        "bridge_version": "1.0.0", "service_version": "mbd 1", "boot": "boot-1", "seq": 1, "link": "port_busy",
        "error": body_error, "registers": {}})
    assert answer.status_code == 200, answer.text
    card = admin.get(f"/api/production/machines/{ob019}").json()
    assert card["link"]["text"] == rules.HMI_OPEN == "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком"
    assert card["error"] == rules.HMI_OPEN
    assert admin.get(RUN).json()["problem"] == rules.HMI_OPEN
    assert rules.agent_error("Access to the port 'COM8' is denied.") == rules.HMI_OPEN
    assert rules.agent_error("PLC: no reply") == "ПЛК не отвечает"
    assert rules.agent_error("что-то своё") == "что-то своё"


def test_tirazh_is_pieces_in_this_job_written_as_a_target_over_the_machine_counter(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    machine.report(_counter(570, state=107, alarm=5))  # the prod card: «задано 570 / сделано 570»
    sent = _post(admin, CARD + "/write", {"values": {"total_set": "12"}, "confirm_state": 107})
    assert sent.status_code == 200, sent.text
    assert [(item["label"], item["after"]) for item in sent.json()["fields"]] == [("Тираж", "582")]
    words = machine.written(sent.json()["id"])  # the read-back is compared with D649 + N
    assert _u32(words, 22) == 582
    bare = _post(admin, CARD + "/write", {"values": {"total_set": "3"}}).json()["id"]
    TestClient(app).post(NEXT, headers=machine.bridge, json=AGENT)
    assert _result(machine.bridge, bare, {"22": 3, "23": 0}).json() == {"status": "failed", "error": "Станок не принял: D22"}
    assert _post(admin, CARD + "/write", {"values": {"total_set": "0"}}).status_code == 422
    clock.now += timedelta(seconds=6)  # the link is still «На связи», but the counter is older than 5 s
    stale = _post(admin, CARD + "/write", {"values": {"total_set": "12"}})
    assert stale.status_code == 409 and "нет свежего счётчика станка" in stale.json()["detail"]
    machine.report(_counter(575, state=103))
    running = _post(admin, CARD + "/write", {"values": {"total_set": "12"}})
    assert running.status_code == 409 and running.json()["detail"].startswith("Тираж не записан: станок не готов")
    with sessions() as session:
        texts = [row.details["text"] for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == ob019, AuditEventRow.event_type == "production.machine_write_queued"))]
    assert "тираж 12 шт: счётчик станка 570 → цель 582" in texts[0]


def test_a_counter_that_moved_while_writing_voids_the_run_and_a_reset_one_blocks_start(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}
    sent = _post(admin, RUN, body).json()["id"]  # target D22 = START + 12
    machine.report(_counter(START + 3))  # the counter moved before the write was checked
    machine.written(sent)
    run = admin.get(RUN).json()["run"]
    assert run["stage"] == "moved"
    start = _post(admin, CARD + "/command", {"command": "start", "confirm_state": 102, "hands_clear": True})
    assert start.status_code == 409 and start.json()["detail"] == runs.MOVED
    assert _post(admin, RUN + "/close", {"run_id": sent}).status_code == 409  # nothing to count
    # Written again: a new run from the current counter, «Пуск» allowed; then the counter goes back below the start.
    again = _post(admin, RUN, body).json()["id"]
    machine.written(again)
    assert admin.get(RUN).json()["run"]["counter_start"] == START + 3
    machine.report(_counter(2, state=102))
    start = _post(admin, CARD + "/command", {"command": "start", "confirm_state": 102, "hands_clear": True})
    assert start.status_code == 409 and start.json()["detail"] == runs.RESET_START


def test_a_manual_job_closed_in_its_own_window_closes_its_run_and_the_write_stays_verified(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба",
                                                       "quantity_planned": 4}).json()
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job["id"]}).json()["id"]
    # Cancelled in «Ручная работа» while the runner is still writing: the run closes in the same transaction…
    cancelled = _post(admin, f"/api/production/manual-jobs/{job['id']}/cancel", {"version": 1, "reason": "не тот провод"})
    assert cancelled.status_code == 200, cancelled.text
    assert admin.get(RUN).json()["run"]["closed_why"] == "Ручная работа отменена"
    # …and the runner's true answer is still recorded as written and checked.
    machine.written(sent)
    with sessions() as session:
        row = session.get(manual.ManualJobRow, UUID(job["id"]))
        assert (row.status, row.count_source, row.write_id) == ("cancelled", "manual", None)
    # A job finished by hand in its window closes its verified run with the machine count.
    second = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба",
                                                          "quantity_planned": 4}).json()
    machine.written(_post(admin, RUN, {"kind": "manual", "job_id": second["id"]}).json()["id"])
    machine.report(_counter(START + 4, state=107, alarm=5))
    version = admin.get(RUN).json()["manual"]["version"]
    done = _post(admin, f"/api/production/manual-jobs/{second['id']}/finish",
                 {"version": version, "good": 4, "material": "pending"})
    assert done.status_code == 200, done.text
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["closed_why"], run["machine_count"]) == ("closed", "Ручная работа завершена", 4)


def test_the_hand_in_sums_every_run_of_a_length_not_handed_in_yet(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}
    first = _post(admin, RUN, body).json()["id"]
    machine.written(first)
    machine.report(_counter(START + 3, state=107, alarm=5))
    assert _post(admin, RUN + "/close", {"run_id": first}).status_code == 200
    second = _post(admin, RUN, body).json()["id"]  # a new run: the first one was closed
    machine.written(second)
    machine.report(_counter(START + 7, state=107, alarm=5))
    assert _post(admin, RUN + "/close", {"run_id": second}).status_code == 200
    [entry] = admin.get(f"/api/production/machines/runs/cutting/{group_id}").json()["runs"]
    assert (entry["length_mm"], entry["count"], entry["runs"], entry["unknown"], entry["inventory"]) == \
        ("325", 7, 2, False, "ОБ.019")


def _start(admin):
    return _post(admin, CARD + "/command", {"command": "start", "confirm_state": 102, "hands_clear": True})


def test_start_goes_only_for_an_open_run_whose_target_is_on_the_machine(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    # «Тираж 500» from the settings, no job: the machine would cut 500 pieces nobody asked for.
    machine.written(_post(admin, CARD + "/write", {"values": {"total_set": "500"}, "confirm_state": 102}).json()["id"])
    assert _start(admin).json()["detail"] == runs.NO_JOB
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}
    machine.written(_post(admin, RUN, body).json()["id"])  # target START + 12
    # «Тираж» written again from the settings while the run is open: D22 is no longer the job's.
    machine.written(_post(admin, CARD + "/write", {"values": {"total_set": "7"}}).json()["id"])
    assert _start(admin).json()["detail"] == runs.MISMATCH
    sent = _post(admin, RUN, body).json()["id"]  # written again: the target is the job's once more
    machine.written(sent)
    # A target farther than the pieces left of the job (the run says fewer remain than D22 − D649).
    with sessions.begin() as session:
        row = session.get(MachineWriteRow, UUID(sent))
        row.payload = {**row.payload, "run": {**row.payload["run"], "quantity": 11}}
    assert _start(admin).json()["detail"] == runs.TOO_MANY
    with sessions.begin() as session:
        row = session.get(MachineWriteRow, UUID(sent))
        row.payload = {**row.payload, "run": {**row.payload["run"], "quantity": 12}}
    clock.now += timedelta(seconds=6)  # the link is on, but the snapshot is older than 5 s
    assert _start(admin).json()["detail"] == runs.NOT_FRESH
    machine.report()
    started = _start(admin)
    assert started.status_code == 200, started.text
    machine.report(_counter(START + 12, state=107, alarm=5))
    assert _start(admin).json()["detail"] == runs.DONE_ALREADY
    assert _post(admin, RUN + "/close", {"run_id": sent}).status_code == 200
    # The run is closed; D22 still holds its target — a later «Пуск» would cut pieces nobody counts.
    machine.report(_counter(START, state=102))
    assert _start(admin).json()["detail"] == runs.NO_JOB


def _check(sessions, command_id: str) -> str | None:
    with sessions() as session:
        return runs.info(session.get(MachineWriteRow, UUID(command_id))).get("check")


def test_a_write_answered_before_the_next_report_waits_for_it_then_starts(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}).json()["id"]
    clock.now += timedelta(seconds=1)  # the runner takes it a second after the last report
    machine.written(sent, fresh=False)  # and answers before the next one: «проверяется», not void
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["write"]["status_text"], _check(sessions, sent)) == (
        "writing", "Проверяется по счётчику станка", "pending")
    assert _start(admin).json()["detail"] == runs.CHECKING
    machine.report()  # the bridge's next report, a second later: the card's next read settles the check
    run = admin.get(RUN).json()["run"]
    assert (run["stage"], run["counter_start"], _check(sessions, sent)) == ("ready", START, "ok")
    # The check is the system's, not the person's whose read triggered it (review of #999, 20.11.2026).
    with sessions() as session:
        verified = session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.machine_run_verified")).all()
    assert [(row.actor_name, row.actor_department) for row in verified] == [("ERP · проверка станка", "Система")]
    started = _start(admin)
    assert started.status_code == 200, started.text


def test_no_snapshot_after_the_write_for_10_s_makes_its_check_unknown(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}).json()["id"]
    clock.now += timedelta(seconds=1)
    machine.written(sent, fresh=False)
    assert _check(sessions, sent) == "pending"
    clock.now += runs.PENDING_WAIT  # still no report after the write: the control poll gives up
    assert admin.get(CARD + "/control").status_code == 200
    assert _check(sessions, sent) == "unknown"
    assert admin.get(RUN).json()["run"]["stage"] == "moved"
    machine.report()  # a report now is too late: the counter may have moved meanwhile
    assert _start(admin).json()["detail"] == runs.UNREAD
    # SQLite gives the moments back naive in shop time, PostgreSQL aware: compared as the same instant.
    naive, utc = datetime(2026, 11, 19, 10, 0), datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc)
    assert runs.aware(naive) == utc and runs.aware(naive).tzinfo == MOSCOW and runs.aware(utc) is utc


def test_a_close_locks_the_run_and_a_stale_close_after_a_re_send_counts_nothing_twice(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325"}
    first = _post(admin, RUN, body).json()["id"]
    machine.written(first)
    machine.report(_counter(START + 2, state=102))
    # Another window re-sends the same job: the first run is continued into the second.
    second = _post(admin, RUN, body).json()["id"]
    machine.written(second)
    # The close pressed in the first window, on the run it still shows, is refused: no double count.
    assert _post(admin, RUN + "/close", {"run_id": first}).status_code == 409
    locked: list[str] = []

    def spy(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locked.append(str(state.statement))

    event.listen(OrmSession, "do_orm_execute", spy)
    try:
        machine.report(_counter(START + 5, state=102))
        assert _post(admin, RUN + "/close", {"run_id": second}).status_code == 200
    finally:
        event.remove(OrmSession, "do_orm_execute", spy)
    assert any("production_machine_writes" in text for text in locked)  # FOR UPDATE on PostgreSQL
    [entry] = admin.get(f"/api/production/machines/runs/cutting/{group_id}").json()["runs"]
    assert (entry["count"], entry["runs"]) == (5, 1)


def test_an_unread_counter_closes_a_run_as_unknown_not_as_reset(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба",
                                                       "quantity_planned": 4}).json()
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job["id"]}).json()["id"]
    machine.written(sent)
    version = admin.get(RUN).json()["manual"]["version"]
    clock.now += timedelta(seconds=30)  # the bridge is silent: no link
    assert admin.get(RUN).json()["run"]["stage"] == "nolink"
    closed = _post(admin, RUN + "/close", {"run_id": sent, "version": version, "good": 4, "material": "pending"})
    assert closed.status_code == 200, closed.text
    run, finished = closed.json()["run"], closed.json()["manual"]
    assert (run["closed_reset"], run["closed_unread"], run["machine_count"]) == (False, True, None)
    assert (finished["count_text"], finished["machine_count"], finished["good"]) == ("Вручную", None, 4)
    with sessions() as session:
        params = session.get(manual.ManualJobRow, UUID(job["id"])).params
        texts = [row.details["text"] for row in session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.machine_run_closed"))]
    assert params["counter_unread"] is True and "counter_reset" not in params
    assert texts[-1].endswith("счётчик станка не прочитан — вручную")


def test_a_manual_job_is_not_finished_or_cancelled_from_its_window_while_the_machine_cuts(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба",
                                                       "quantity_planned": 4}).json()
    machine.written(_post(admin, RUN, {"kind": "manual", "job_id": job["id"]}).json()["id"])
    machine.report(_counter(START + 2, state=103))
    version = admin.get(RUN).json()["manual"]["version"]
    path = f"/api/production/manual-jobs/{job['id']}"
    finished = _post(admin, path + "/finish", {"version": version, "good": 2, "material": "pending"})
    assert finished.status_code == 409 and finished.json()["detail"] == runs.CUTTING
    cancelled = _post(admin, path + "/cancel", {"version": version, "reason": "не тот провод"})
    assert cancelled.status_code == 409 and cancelled.json()["detail"] == runs.CUTTING
    state = admin.get(RUN).json()
    assert (state["manual"]["status"], state["run"]["stage"]) == ("running", "running")  # nothing saved
    machine.report(_counter(START + 2, state=102))  # «Стоп»
    done = _post(admin, path + "/finish", {"version": version, "good": 2, "material": "pending"})
    assert done.status_code == 200, done.text
    assert admin.get(RUN).json()["run"]["machine_count"] == 2


def test_a_manual_job_is_not_closed_from_its_window_while_its_write_is_still_checked(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    machine.report()
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба",
                                                       "quantity_planned": 4}).json()
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job["id"]}).json()["id"]
    clock.now += timedelta(seconds=1)
    machine.written(sent, fresh=False)  # answered before the bridge's next report: «проверяется»
    version = admin.get(RUN).json()["manual"]["version"]
    path = f"/api/production/manual-jobs/{job['id']}"
    cancelled = _post(admin, path + "/cancel", {"version": version, "reason": "не тот провод"})
    assert cancelled.status_code == 409 and cancelled.json()["detail"] == runs.CLOSE_CHECKING
    assert _check(sessions, sent) == "pending"  # nothing saved
    machine.report()  # the next report settles the check; now the window may close the job
    state = admin.get(RUN).json()
    assert state["run"]["stage"] == "ready"
    cancelled = _post(admin, path + "/cancel", {"version": state["manual"]["version"], "reason": "не тот провод"})
    assert cancelled.status_code == 200, cancelled.text


def _transfers(sessions) -> int:
    with sessions() as session:
        return len(list(session.scalars(select(MachineWriteRow).where(MachineWriteRow.purpose == "transfer"))))


def test_a_job_run_with_typed_values_writes_the_etk_marking_and_a_blocked_one_writes_nothing(machines_api):
    """The card sends a job with the drawing edited by hand (`values`): the ETK marking goes with it exactly as without."""
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    blocked = _job(sessions, laser={"mode": "ends", "front": "SN {№}"}, number="ГР-0200")
    refused = _post(admin, RUN, {"kind": "cutting", "group_id": str(blocked), "length_mm": "325",
                                 "values": {"strip_front_mm": "5,5", "total_set": "12"}, "confirm_state": 102})
    assert refused.status_code == 422 and refused.json()["detail"].startswith("Маркировка: ")
    assert "Серийные номера на лазер" in refused.json()["detail"] and _transfers(sessions) == 0
    group_id = _job(sessions, laser={"mode": "ends", "front": "C1.5 to XS17.7", "back": "XS17.7", "middle": ""})
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325",
                              "values": {"strip_front_mm": "5,5", "total_set": "12"}, "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    claimed, words, bits = _claimed_writes(bridge)
    assert claimed["id"] == sent.json()["id"] and bits == {400: 1, 422: 0}
    assert _text_at(words, 440) == "C1.5 to XS17.7" and _text_at(words, 470) == "XS17.7"
    assert 405 in words and words[34] == 550  # the marking margin and the strip typed by hand
    assert _text_at(words, 955) == "PVAM 0,35 L325,00"  # the job's product, written for the run's check


def _pult_edit(machine, address: int, text: str) -> None:
    """A text changed on the machine's own panel, not through ERP (the words stay on the machine)."""
    raw = text.encode("ascii").ljust(30, b"\x00")
    machine.held.update({str(address + index): (raw[2 * index] << 8) | raw[2 * index + 1] for index in range(15)})
    machine.report()


def test_start_is_refused_when_the_machine_was_set_to_another_job_outside_erp(machines_api):
    """The open job's length, order and product changed past ERP (on the machine's panel): «Пуск» would cut something
    else into job A. Picking the job again settles it."""
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102}
    machine.written(_post(admin, RUN, body).json()["id"])
    _pult_edit(machine, 955, "PV-3 0,5 L325")
    refused = _start(admin)
    assert refused.status_code == 409
    assert refused.json()["detail"] == runs.other_setup("ГР-0007 · ПВАМ 0,35 · 325,00 мм")
    assert refused.json()["detail"].startswith("Станок настроен не на задание «ГР-0007")
    machine.written(_post(admin, RUN, body).json()["id"])  # the job picked again
    started = _start(admin)
    assert started.status_code == 200, started.text


def test_erp_own_length_edit_or_correction_during_an_open_job_keeps_start_allowed(machines_api):
    """An ERP length edit and a non-length ERP offset after «Проба и поправка» keep the open run startable.

    Length calibration uses the separate measured-value PLC path, never an ERP length offset.
    """
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102}
    sent = machine.written(_post(admin, RUN, body).json()["id"])
    machine.written(_post(admin, CARD + "/write", {"values": {"length_mm": "326"}}).json()["id"])
    machine.written(_post(admin, CARD + "/write", {"values": {"order_number": "GR-0007-2"}}).json()["id"])
    stored = _post(admin, CARD + "/correction", {"parameter": "strip_front_mm", "case": "machine", "offset": "1,5",
                                                 "note": "проба"})
    assert stored.status_code == 200, stored.text
    machine.written(_post(admin, CARD + "/write", {"values": {"strip_front_mm": "6,5"}}).json()["id"])  # the card's write
    started = _start(admin)
    assert started.status_code == 200, started.text
    assert sent["32"] == 32500  # the run itself was written with the job's length


def test_a_manual_run_expects_its_length_order_and_product(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    job = _post(admin, "/api/production/manual-jobs", {"equipment_id": str(ob019), "purpose": "Проба для ОТК",
                                                       "wire": "ПВАМ 0,35", "length_mm": "100", "quantity_planned": 5})
    sent = _post(admin, RUN, {"kind": "manual", "job_id": job.json()["id"], "confirm_state": 102}).json()["id"]
    machine.written(sent)
    with sessions() as session:
        expected = runs.info(session.get(MachineWriteRow, UUID(sent)))["expect"]
    assert expected["32"] == 10000 and "940" in expected and "955" in expected
    _pult_edit(machine, 940, "R-000099")
    assert _start(admin).json()["detail"].startswith("Станок настроен не на задание «Р-000001")


def test_a_run_written_before_the_check_has_no_expect_and_is_not_checked(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102})
    machine.written(sent.json()["id"])
    with sessions.begin() as session:
        row = session.get(MachineWriteRow, UUID(sent.json()["id"]))
        run = dict(row.payload["run"])
        run.pop("expect")
        row.payload = {**row.payload, "run": run}
    _pult_edit(machine, 955, "PV-3 0,5 L325")
    started = _start(admin)
    assert started.status_code == 200, started.text


def test_a_marking_value_typed_wins_over_the_etk_marking(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge)
    group_id = _job(sessions, laser={"mode": "ends", "front": "C1.5 to XS17.7", "back": "XS17.7", "middle": ""})
    sent = _post(admin, RUN, {"kind": "cutting", "group_id": str(group_id), "length_mm": "325",
                              "values": {"mark_front": "W12", "total_set": "12"}, "confirm_state": 102})
    assert sent.status_code == 200, sent.text
    claimed, words, bits = _claimed_writes(bridge)
    assert _text_at(words, 440) == "W12" and _text_at(words, 470) == "XS17.7"


def test_a_tirazh_re_send_of_n_minus_done_ends_with_the_job_quantity_and_start_allowed(machines_api):
    """The card shows the job's «Тираж» N and sends N − done: the run counts N from its first start."""
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102}
    machine.written(_post(admin, RUN, body).json()["id"])
    machine.report(_counter(START + 2))
    sent = _post(admin, RUN, {**body, "values": {"total_set": "13"}})  # N 15 for the job, 2 done
    assert sent.status_code == 200, sent.text
    machine.written(sent.json()["id"])
    run = admin.get(RUN).json()["run"]
    assert (run["quantity"], run["done"], run["counter_start"]) == (15, 2, START)
    started = _start(admin)
    assert started.status_code == 200, started.text


def test_a_tirazh_re_send_of_the_open_job_keeps_sizes_typed_for_a_job_missing_them(machines_api):
    """A job whose ETK lacks a strip size goes once with the typed size; «Тираж» changed later re-sends the same job with
    only the new quantity — the size stays as written, not refused again."""
    sessions, clock, admin, ob019, _ = machines_api
    machine = Machine(admin, clock)
    group_id = _job(sessions)
    with sessions.begin() as session:
        group = session.get(CuttingGroupRow, group_id)
        recipe = dict(group.recipe)
        recipe["operations"] = [{"type": "cutWireStrip", "fields": {"strip": {"a": True, "b": True, "lenB": "6,5",
                                                                              "modeA": "full", "modeB": "full"}}}]
        group.recipe = recipe
    body = {"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102}
    refused = _post(admin, RUN, {**body, "values": {"total_set": "12"}})
    assert refused.status_code == 422 and refused.json()["detail"].startswith("В ЭТК не задано: ")
    # The size the ETK lacks and a length of the operator's own go with the first send.
    machine.written(_post(admin, RUN, {**body, "values": {"strip_front_mm": "5", "length_mm": "326",
                                                          "total_set": "12"}}).json()["id"])
    again = _post(admin, RUN, {**body, "values": {"total_set": "10"}})
    assert again.status_code == 200, again.text
    words = machine.written(again.json()["id"])
    # The run keeps its own length: the plan's 325 is not written back over the operator's 326, and «Пуск» goes.
    assert words.get("32") in (None, 32600)
    with sessions() as session:
        assert runs.info(session.get(MachineWriteRow, UUID(again.json()["id"])))["expect"]["32"] == 32600
    started = _start(admin)
    assert started.status_code == 200, started.text
