"""«Выполнение задания» on the ОБ.019 card (owner 19.11.2026, S2): a run starts with a write read back right, counts
from T.Finish (D649) at that moment, shows done of planned, closes with the machine count — the cutting hand-in is
pre-filled from it, a manual job is finished with it; a counter that went back or was not read means manual entry."""

from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.production import machine_runs as runs
from app.modules.production import machines as rules
from app.modules.production import manual_jobs as manual
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_writes_api import AGENT, CARD, NEXT, _job, _result
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

RUN = CARD + "/run"
START = 0x10001  # D649 = 1, D650 = 1 in WORDS


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _u32(words: dict, low: int) -> int:
    return words[str(low)] | words[str(low + 1)] << 16


def _counter(value: int, state: int = 102, alarm: int = 0) -> dict:
    return {**WORDS, "649": value & 0xFFFF, "650": value >> 16, "766": state, "782": alarm}


class Machine:
    """The bridge and the runner on the machine PC; the clock moves a second per write so runs keep their order."""

    def __init__(self, admin, clock):
        self.clock = clock
        self.bridge = _bridge(admin)
        self.seq = 0
        self.report()

    def report(self, registers=None, **extra):
        self.seq += 1
        answer = _report(self.bridge, seq=self.seq, registers=registers, **extra)
        assert answer.status_code == 200, answer.text

    def written(self, command_id: str) -> dict:
        """The runner claims the command and reads back exactly what it was asked to write."""
        claimed = TestClient(app).post(NEXT, headers=self.bridge, json=AGENT).json()["command"]
        assert claimed["id"] == command_id
        readback = {str(block["addr"] + index): word for block in claimed["blocks"]
                    for index, word in enumerate(block["values"])}
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
    closed = _post(admin, RUN + "/close", {"run_id": second}).json()["run"]
    assert closed["machine_count"] is None and closed["closed_reset"] is True
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
        from app.modules.production.machine_writes import MachineWriteRow
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
