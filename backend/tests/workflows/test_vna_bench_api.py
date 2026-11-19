"""«ВАЦ — рабочее место» end to end: the S5 screen's API against a stub protocol-v2 agent (S3 + S4)."""

import base64
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production.infrastructure import (ProductionInstrumentFileRow, ProductionInstrumentRow,
                                                   SqlAlchemyProductionUnitOfWork)
from app.modules.production.mating_infrastructure import InstrumentMatingRow
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.modules.production.vna_check_infrastructure import VnaCheckRow
from app.modules.production.vna_checks import KitStandard, kit_response
from app.modules.production.vna_session_infrastructure import VnaBenchItemRow, VnaSessionRow
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401
from tests.production.test_api import production_api  # noqa: F401
from tests.warehouse.test_vna_jobs import CLAIM, HEARTBEAT, NAME, S2_INSTRUMENT, capture  # noqa: F401

URL = "/api/production/vna-bench"
SERIAL, ACM_SERIAL = "26030095", "26140008"
OPS = ["device_status", "apply_screen", "acm_confidence", "store_memory", "release", "read_kit"]
TEMPS = {"vna_c": [30.0, 30.5], "acm_c": 29.8}
KIT = [dict(label="OPEN-F", type="OPEN"), dict(label="SHORT-F", type="SHORT"), dict(label="LOAD-F", type="LOAD"),
       dict(label="MA6666A", type="THRU")]


def port(series="3.5mm", male=True, maximum=26.5):
    return RfInterface(series, "вилка" if male else "розетка", "штырь" if male else "гнездо", 50, maximum, True, "Паспорт")


def control_heartbeat(agent, *, protocol=2, ops=OPS):
    data = dict(machine_id="pc-1", pc_name="ОТК-ВАЦ", version="2.10.0", vna=dict(name=NAME, ready=True),
                vna_control=dict(version=protocol, methods=["acm"], **({"ops": ops} if protocol == 2 else {})))
    response = TestClient(app).post(HEARTBEAT, json=data, headers=agent)
    assert response.status_code == 200, response.text


def touchstone(frequencies, s11, s21, s12=None, s22=None):
    s12, s22 = s12 or s21, s22 or s11
    lines = ["# HZ S RI R 50"]
    for i, f in enumerate(frequencies):
        values = [s11[i], s21[i], s12[i], s22[i]]
        lines.append(" ".join([repr(f)] + [f"{v.real!r} {v.imag!r}" for v in values]))
    return ("\n".join(lines) + "\n").encode()


def grid(profile):
    count = profile["points"]
    return [profile["start_hz"] + (profile["stop_hz"] - profile["start_hz"]) * i / (count - 1) for i in range(count)]


def gamma(vswr):
    return complex((vswr - 1) / (vswr + 1), 0)


class Agent:
    """A stub of agent 2.10.0: answers every op with a valid receipt; the test sets what is connected."""

    def __init__(self, env):
        self.env, self.dut_vswr, self.temps, self.acm_serial = env, 1.02, dict(TEMPS), ACM_SERIAL
        self.connected = None   # None: the default DUT; ("kit", definition) or ("load",) or ("thru",)
        self.confidence_offset = 0.0
        self.errors = {}

    def sweep(self, profile):
        f = grid(profile)
        n = len(f)
        if self.connected and self.connected[0] == "kit":
            standard = KitStandard(**{k: v for k, v in self.connected[1].items() if k in ("type", "label")})
            model = [kit_response(standard, hz) for hz in f]
            if standard.type == "THRU":
                return touchstone(f, [0j] * n, model)
            return touchstone(f, model, [complex(1e-6)] * n, [complex(1e-6)] * n, model)
        if self.connected == ("load",):
            return touchstone(f, [complex(0.005)] * n, [complex(1e-6)] * n)
        if self.connected == ("thru",):
            return touchstone(f, [complex(0.005)] * n, [complex(0.99)] * n)
        return touchstone(f, [gamma(self.dut_vswr)] * n, [complex(0.99)] * n)

    def answer(self, command):
        op, args = command["op"], command["args"]
        result = dict(serial=SERIAL)
        if op in self.errors:
            return dict(session_id=command["session_id"], expected_version=command["expected_version"],
                        expected_step=command["expected_step"], error=self.errors[op])
        if op == "prepare":
            result.update(ready=True, backup_id=str(uuid4()), state_saved=True, kit_assignments_saved=True)
        elif op == "apply_profile":
            result.update(profile=args["profile"])
        elif op == "begin_calibration":
            result.update(kit_fingerprint=args["kit_fingerprint"], definition_hash="a" * 64)
        elif op == "acm_calibrate":
            result.update(acquired=True, module_serial=args["module_serial"], module_min_hz=1e5, module_max_hz=20e9)
        elif op == "save_calibration":
            result.update(method=args["method"], correction={t: "COR" for t in args["traces"]})
        elif op == "acm_confidence":
            f = grid(args["profile"])
            memory = {t: [[0.5, 0.0]] * len(f) for t in args["profile"]["traces"]}
            data = {t: [[0.5 * (1 + self.confidence_offset), 0.0]] * len(f) for t in args["profile"]["traces"]}
            result.update(module_serial=args["module_serial"], frequencies_hz=f, data=data, memory=memory,
                          temperatures=self.temps)
        elif op == "device_status":
            result.update(name=NAME, ready=True, warmup_min=55.0, temperatures=self.temps,
                          acm=dict(model="ACM2520", serial=self.acm_serial, min_hz=1e5, max_hz=20e9),
                          correction={t: "COR" for t in ("S11", "S21", "S12", "S22")}, screen_hash=None)
        elif op == "apply_screen":
            result.update(screen=args["screen"], screen_hash=args["screen_hash"])
        elif op == "capture":
            traces = args["profile"]["traces"]
            result.update(profile=args["profile"], correction={t: "COR" for t in traces}, temperatures=self.temps,
                          screen_hash=args["screen_hash"], artifacts=[dict(
                              file_name="trace.s2p", traces=traces,
                              content_base64=base64.b64encode(self.sweep(args["profile"])).decode())])
        elif op == "store_memory":
            result.update(screen_hash=args["screen_hash"], memorized=args["traces"])
        elif op == "read_kit":
            result.update(label=args["label"], standards=KIT)
        elif op == "release":
            result.update(released=True, continuous=True, screen_hash=args["screen_hash"])
        elif op == "abort":
            result.update(stopped=True, calibration_cleared=True)
        elif op == "restore":
            result.update(restored=True)
        return dict(session_id=command["session_id"], expected_version=command["expected_version"],
                    expected_step=command["expected_step"], result=result)

    def poll(self):
        response = TestClient(app).post(CLAIM, json=dict(machine_id="pc-1", kinds=["vna_control"],
                                                         observed_vna_serial=SERIAL), headers=self.env["agent"])
        assert response.status_code == 200, response.text
        return response.json()["job"]

    def send(self, command, payload=None):
        return TestClient(app).post(f"/api/stock/vna/commands/{command['id']}/result", json=payload or self.answer(command),
                                    headers={**self.env["agent"], "X-Agent-Machine-Id": "pc-1"})

    def drive(self, limit=60):
        """Execute every queued command until the session waits for the operator."""
        for _ in range(limit):
            command = self.poll()
            if command is None:
                return
            assert command["protocol_version"] == 2
            response = self.send(command)
            assert response.status_code == 200, response.text
        pytest.fail("Агент не дождался паузы")


@pytest.fixture()
def env(capture, monkeypatch):  # noqa: F811
    admin, agent, clock, sessions = capture
    control_heartbeat(agent)
    monkeypatch.setattr(app.state, "production_uow_factory", lambda: SqlAlchemyProductionUnitOfWork(sessions), raising=False)
    ids = dict(acm=uuid4(), cable1=uuid4(), cable2=uuid4(), dut=uuid4(), standard=uuid4(), kit=uuid4())
    vna = UUID(S2_INSTRUMENT)
    with sessions() as session:
        def card(key, number, name, parent=vna, serial=""):
            session.add(ProductionInstrumentRow(id=ids[key], version=1, inventory_number=number, name=name,
                        instrument_type="Оснастка", kind="Принадлежность", status="Действует", parent_id=parent,
                        serial_number=serial))
        session.get(ProductionInstrumentRow, vna).doc_designation = "S50180"   # the model gives U (табл. 23)
        card("acm", "СИ.104", "АКМ ACM2520-111", serial=ACM_SERIAL)
        card("cable1", "СИ.100", "Кабель C50SMNM.2 порт 1")
        card("cable2", "СИ.101", "Кабель C50SMNM.2 порт 2")
        card("dut", "СИ.035", "Переход коаксиальный ADP1A 3,5 мм", parent=None, serial="N8B035")
        card("standard", "СИ.009", "Переход коаксиальный ADP1A 3,5 мм", parent=None, serial="N8B140")
        card("kit", "СИ.003", "Набор мер 6650F27", parent=None, serial="K1")
        session.flush()
        rf = RfRepository(session)
        rf.save(vna, RfDetails((port("N", False, 18), port("N", False, 18)), "vna", True, "Паспорт", 0.0001, 200001))
        rf.save(ids["acm"], RfDetails((port(male=False), port(male=False)), "auto_calibration", True, "Паспорт"))
        for key in ("cable1", "cable2"):
            rf.save(ids[key], RfDetails((port("N"), port(male=False)), "test_cable", True, "Паспорт"))
        for key in ("dut", "standard"):
            rf.save(ids[key], RfDetails((port(), port(male=False)), "adapter", True, "Паспорт", norm_id="adp1a-35"))
        rf.save(ids["kit"], RfDetails((port(), port(male=False)), "solt_kit", True, "Паспорт", kit_definition="6650F27-full"))
        session.commit()
    data = dict(admin=admin, agent=agent, clock=clock, sessions=sessions, ids=ids)
    data["stub"] = Agent(data)
    return data


def post(env, path, body=None, expect=200):
    response = env["admin"].post(URL + path, json=body if body is not None else {}, headers=_csrf(env["admin"]))
    assert response.status_code == expect, response.text
    return response.json()


def state(env):
    response = env["admin"].get(URL + "/state")
    assert response.status_code == 200, response.text
    return response.json()


def session_path(view, tail=""):
    return f"/sessions/{view['session']['id']}{tail}"


def calibrate(env):
    view = post(env, "/sessions")
    assert view["session"]["command"]["label"] == "сохранение настроек оператора"
    env["stub"].drive()
    view = state(env)
    assert [s["key"] for s in view["session"]["steps"]] == ["calibration", "confidence", "load_port1", "load_port2"]
    assert view["session"]["steps"][0]["status"] == "pending" and not view["calibration"]["valid"]
    for key in ("calibration", "load_port1", "load_port2"):
        post(env, session_path(view, f"/steps/{key}"), {"version": view["session"]["version"]})
        env["stub"].drive()
        view = state(env)
    assert [s["status"] for s in view["session"]["steps"]] == ["passed"] * 4, view["session"]["steps"]
    assert view["calibration"]["valid"] and view["calibration"]["temperatures"] == TEMPS
    return view


def measure(env, view, target=None, expect=200):
    target = target or dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))
    return post(env, session_path(view, "/items"), {"version": view["session"]["version"], "target": target}, expect)


def matings(env, instrument_id):
    with env["sessions"]() as session:
        return session.scalar(select(func.coalesce(func.sum(InstrumentMatingRow.delta), 0)).where(
            InstrumentMatingRow.instrument_id == instrument_id))


def test_calibration_journal_item_record_and_close(env):
    view = calibrate(env)
    with env["sessions"]() as session:
        journal = session.scalars(select(VnaCheckRow)).all()
        assert [(r.kind, r.passed, r.load_p1_vswr <= 1.06) for r in journal] == [("session", True, True)]
        row = session.scalar(select(VnaSessionRow))
        assert row.calibration_check_id == journal[0].id and row.purpose == "bench" and row.task_id is None
    assert matings(env, env["ids"]["acm"]) == 2 and matings(env, env["ids"]["cable1"]) == 1
    plan = post(env, session_path(view, "/plan"), {"target": dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))})
    assert plan["refusal"] is None and plan["norm_vswr"] == 1.06 and plan["band_mhz"] == [10.0, 18000.0]
    assert plan["uncertainty"] and plan["screen_name"] == "ОТК — переход"
    view = measure(env, view)
    assert view["session"]["command"]["state"] == "queued"
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "pass" and item["verdict"]["final"] and item["connections_total"] == 1
    assert item["trace"] and item["recorded_at"] is None
    assert matings(env, env["ids"]["dut"]) == 2
    view = state(env)
    view = post(env, session_path(view, f"/items/{item['id']}/record"), {"version": view["session"]["version"]})
    item = view["session"]["items"][0]
    assert item["recorded_to"] == "карточка СИ.035" and item["label_instrument_id"] == str(env["ids"]["dut"])
    with env["sessions"]() as session:
        files = session.scalars(select(ProductionInstrumentFileRow).where(
            ProductionInstrumentFileRow.instrument_id == env["ids"]["dut"])).all()
        assert len(files) == 1 and files[0].file_name.startswith("Замер ВАЦ S50180, АКМ, ")
        assert files[0].file_name.endswith(", СИ.035.s2p")
    again = post(env, session_path(view, f"/items/{item['id']}/record"), {"version": view["session"]["version"]})
    assert again["session"]["items"][0]["recorded_at"] == item["recorded_at"]   # idempotent: no second file
    post(env, session_path(again, "/close"), {"version": again["session"]["version"]})
    env["stub"].drive()
    assert state(env)["session"] is None
    with env["sessions"]() as session:
        assert session.scalar(select(VnaSessionRow.state)) == "recorded"


def test_border_repeats_three_connections_then_records_every_file(env):
    view = calibrate(env)
    env["stub"].dut_vswr = 1.065   # within ±U of the 1,06 norm: «на границе»
    measure(env, view)
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "border" and not item["verdict"]["final"] and item["connections_total"] == 3
    view = state(env)
    assert post(env, session_path(view, f"/items/{item['id']}/record"), {"version": view["session"]["version"]},
                409)["detail"]["code"] == "not_final"
    for _ in range(2):
        view = state(env)
        post(env, session_path(view, f"/items/{item['id']}/connections"), {"version": view["session"]["version"]})
        env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "pass" and item["verdict"]["final"]   # worst ≤ norm + U after 12.4
    assert [c["no"] for c in item["connections"]] == [1, 2, 3]
    view = state(env)
    assert post(env, session_path(view, f"/items/{item['id']}/connections"), {"version": view["session"]["version"]},
                409)["detail"]["code"] == "stale"
    post(env, session_path(view, f"/items/{item['id']}/record"), {"version": view["session"]["version"]})
    with env["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(ProductionInstrumentFileRow).where(
            ProductionInstrumentFileRow.instrument_id == env["ids"]["dut"])) == 3
    assert matings(env, env["ids"]["dut"]) == 6


def test_refusals_before_and_during_items(env):
    view = post(env, "/sessions")
    env["stub"].drive()
    view = state(env)
    assert measure(env, view, expect=409)["detail"]["code"] == "calibration_expired"
    assert post(env, session_path(view, "/steps/load_port1"), {"version": view["session"]["version"]},
                409)["detail"]["code"] == "stale"
    assert post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"] - 1},
                409)["detail"]["code"] == "stale"
    view = calibrate_from_waiting(env, view)
    # ±1 °C (11.5): a sweep 1,2 °C warmer is not accepted and the calibration is gone.
    env["stub"].temps = {"vna_c": [31.2, 30.5], "acm_c": 29.8}
    measure(env, view)
    env["stub"].drive()
    view = state(env)
    assert not view["calibration"]["valid"]
    assert view["session"]["items"][0]["refusal"]["code"] == "temperature_drift"
    assert measure(env, view, expect=409)["detail"]["code"] == "calibration_expired"


def calibrate_from_waiting(env, view):
    for key in ("calibration", "load_port1", "load_port2"):
        post(env, session_path(view, f"/steps/{key}"), {"version": view["session"]["version"]})
        env["stub"].drive()
        view = state(env)
    assert view["calibration"]["valid"]
    return view


def test_calibration_expires_after_four_hours_and_recalibrates_in_the_same_session(env):
    view = calibrate(env)
    env["clock"].now += timedelta(hours=4, minutes=1)
    control_heartbeat(env["agent"])   # the agent keeps reporting; only the calibration aged
    view = state(env)
    assert not view["calibration"]["valid"] and "4 часов" in view["calibration"]["reason"]
    assert measure(env, view, expect=409)["detail"]["code"] == "calibration_expired"
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = state(env)
    for key in ("load_port1", "load_port2"):
        post(env, session_path(view, f"/steps/{key}"), {"version": view["session"]["version"]})
        env["stub"].drive()
        view = state(env)
    assert view["calibration"]["valid"]


def test_failed_confidence_and_acm_change_and_failed_control_stop_work(env):
    env["stub"].confidence_offset = 0.05   # ≈ 0,42 dB transmission: outside ±0,1 dB
    view = post(env, "/sessions")
    env["stub"].drive()
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = state(env)
    assert view["session"]["steps"][1]["status"] == "failed" and not view["calibration"]["valid"]
    with env["sessions"]() as session:
        assert session.scalar(select(VnaCheckRow.passed)) is False
    env["stub"].confidence_offset = 0.0
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = state(env)
    view = calibrate_loads(env, view)
    # The ACM swapped on USB: the item's device reading refuses and the calibration is gone.
    env["stub"].acm_serial = "OTHER"
    measure(env, view)
    env["stub"].drive()
    view = state(env)
    assert view["session"]["error"].startswith("АКМ заменён") and not view["calibration"]["valid"]
    env["stub"].acm_serial = ACM_SERIAL
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = calibrate_loads(env, state(env))
    with env["sessions"]() as session:
        session.add(VnaCheckRow(id=uuid4(), instrument_id=UUID(S2_INSTRUMENT), kind="control_standard", passed=False,
                                performed_at=env["clock"].now, recorded_at=env["clock"].now, source="weekly-failed",
                                payload_hash="b" * 64, operator="", actor="", calibration_id="", acm_serial="", profile="",
                                standard_inventory="СИ.009", reference="", vswr_dev=0.05, vswr_limit=0.02,
                                s21_dev_db=0.01, s21_limit_db=0.1, note=""))
        session.commit()
    assert measure(env, view, expect=409)["detail"]["code"] == "control_failed"
    assert state(env)["due"][1] == {**state(env)["due"][1], "kind": "weekly", "status": "failed"}


def calibrate_loads(env, view):
    for key in ("load_port1", "load_port2"):
        post(env, session_path(view, f"/steps/{key}"), {"version": view["session"]["version"]})
        env["stub"].drive()
        view = state(env)
    assert view["calibration"]["valid"]
    return view


def test_agent_without_protocol_2_ops_cannot_open_a_bench_session(env):
    control_heartbeat(env["agent"], protocol=1)
    assert post(env, "/sessions", expect=409)["detail"]["code"] == "agent_old"
    control_heartbeat(env["agent"], ops=OPS[:3])
    assert post(env, "/sessions", expect=409)["detail"]["code"] == "agent_old"
    assert state(env)["device"]["refusal"]["code"] == "agent_old"


def test_identical_receipt_is_acknowledged_once_and_different_one_conflicts(env):
    post(env, "/sessions")
    stub = env["stub"]
    command = stub.poll()
    payload = stub.answer(command)
    assert stub.send(command, payload).status_code == 200
    before = state(env)["session"]["version"]
    assert stub.send(command, payload).status_code == 200
    assert state(env)["session"]["version"] == before
    assert stub.send(command, stub.answer(command)).status_code == 409   # another backup id is another receipt


def test_flex_weekly_and_monthly_checks_write_the_journal(env):
    view = calibrate(env)
    stub = env["stub"]
    view = post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "flex"})
    stub.drive()
    check = state(env)["session"]["checks"][0]
    assert [p["key"] for p in check["phases"]] == ["load_p1", "load_p2", "thru_memory", "thru_flex"]
    for connected in (("load",), ("load",), ("thru",), ("thru",)):
        stub.connected = connected
        view = state(env)
        post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
        stub.drive()
    check = state(env)["session"]["checks"][0]
    assert check["status"] == "passed", check
    # Weekly: СИ.009 against its reference curve, measured on the same grid.
    stub.connected = None
    with env["sessions"]() as session:
        profile = session.scalar(select(VnaSessionRow)).snapshot["profile"]
        session.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=env["ids"]["standard"], kind="Прочее",
                    file_name="Замер ВАЦ S50180, калибровка АКМ, 16.11.2026, зав. N8B140.s2p", content=stub.sweep(profile),
                    uploaded_at=env["clock"].now, uploaded_by=session.scalar(select(VnaSessionRow.operator_id)),
                    is_reference=True))
        session.commit()
    view = state(env)
    view = post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "weekly"})
    stub.drive()
    check = state(env)["session"]["checks"][1]
    view = state(env)
    post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
    stub.drive()
    assert state(env)["session"]["checks"][1]["status"] == "passed"
    # Monthly: 6650F27 read from S2VNA, each standard three times.
    view = state(env)
    post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "monthly"})
    stub.drive()
    check = state(env)["session"]["checks"][2]
    assert len(check["phases"]) == 21
    for phase in check["phases"]:
        kind = phase["key"].split("_")[0].upper()
        stub.connected = ("kit", next(s for s in KIT if s["type"] == kind))
        view = state(env)
        post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
        stub.drive()
    check = state(env)["session"]["checks"][2]
    assert check["status"] == "passed", check
    with env["sessions"]() as session:
        kinds = [(r.kind, r.passed) for r in session.scalars(select(VnaCheckRow).order_by(VnaCheckRow.recorded_at))]
        assert kinds == [("session", True), ("flex", True), ("control_standard", True), ("kit_standards", True)]
        assert session.scalar(select(func.count()).select_from(VnaBenchItemRow).where(
            VnaBenchItemRow.kind == "kit_standards")) == 21
    assert [d["status"] for d in state(env)["due"]] == ["ok", "ok", "ok"]


def test_abort_returns_the_device_and_frees_the_lease(env):
    view = calibrate(env)
    post(env, session_path(view, "/abort"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert state(env)["session"] is None
    with env["sessions"]() as session:
        assert session.scalar(select(VnaSessionRow.state)) == "aborted"
    assert post(env, "/sessions")["session"]["status"] == "open"


def test_assembly_session_records_each_connection_through_the_vswr_rule(env, monkeypatch):
    from copy import deepcopy

    from app.modules.production.infrastructure import ProductionOperationRow, ProductionTaskRow
    from app.workflows import vna_bench_api as bench_api
    from tests.production.test_vna_sessions import session_plan

    plan_data, _ = session_plan("acm")
    task_id, operation_id = uuid4(), uuid4()
    with env["sessions"]() as session:
        session.add(ProductionTaskRow(id=task_id, number="VNA-1", source_idempotency_key="vna", source_fingerprint="x",
                    version=1, created_at=env["clock"].now, project="Тест", item="КБ1", name="Кабель", quantity=2,
                    status="Запущено", launched_by="Admin"))
        session.flush()
        session.add(ProductionOperationRow(id=operation_id, task_id=task_id, position=1, code="VSWR", stage="Контроль",
                    description="Контроль КСВН", seconds_per_unit=1, setup_seconds=1, norm_seconds=2))
        session.commit()
    monkeypatch.setattr(bench_api, "vna_plan", lambda *args: deepcopy(plan_data))
    monkeypatch.setattr(bench_api, "_task_etk", lambda *args: (None, {"current_revision": 3}, None))
    monkeypatch.setattr(bench_api, "task_norm", lambda *args: plan_data["profile"]["norm"])
    view = post(env, "/sessions", {"assembly": {"task_id": str(task_id)}})
    assert view["session"]["status"] == "open"
    env["stub"].drive()
    view = calibrate_from_waiting(env, state(env))
    target = dict(kind="assembly", task_id=str(task_id), position=1)
    # A СИ cannot be measured on the КБ grid: the plan says so instead of measuring.
    refused = post(env, session_path(view, "/plan"), {"target": dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))})
    assert refused["refusal"]["code"] == "other_calibration"
    plan = post(env, session_path(view, "/plan"), {"target": target})
    assert plan["refusal"] is None and plan["band_mhz"] == [1.0, 6000.0] and plan["norm_vswr"] == 1.5
    env["stub"].dut_vswr = 1.5   # exactly the norm: «на границе», then the 3-connection repeat (record_vswr)
    measure(env, view, target)
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "border" and item["recorded_to"] == "VNA-1, изделие № 1"
    for _ in range(3):
        view = state(env)
        post(env, session_path(view, f"/items/{item['id']}/connections"), {"version": view["session"]["version"]})
        env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "pass" and item["verdict"]["final"] and len(item["connections"]) == 4
    with env["sessions"]() as session:
        rows = session.scalars(select(VnaBenchItemRow).where(VnaBenchItemRow.kind == "assembly")
                               .order_by(VnaBenchItemRow.created_at)).all()
        assert [r.connection_no for r in rows] == [1, 1, 2, 3] and all(r.vswr_measurement_id for r in rows)
        assert len({r.group_id for r in rows}) == 2
    assert matings(env, env["ids"]["cable1"]) == 1 + 4
