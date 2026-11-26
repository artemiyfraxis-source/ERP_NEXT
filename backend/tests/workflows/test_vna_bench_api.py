"""«ВАЦ — рабочее место» end to end: the S5 screen's API against a stub protocol-v2 agent (S3 + S4)."""

import base64
from pathlib import Path
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import (ProductionInstrumentFileRow, ProductionInstrumentRow,
                                                   SqlAlchemyProductionUnitOfWork)
from app.modules.production.mating_infrastructure import InstrumentMatingRow
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.modules.production.vna_check_infrastructure import VnaCheckRow
from app.modules.production.vna_checks import KitStandard, kit_response
from app.modules.production import vna_bench as bench
from app.modules.production.vna_bench_protocol import screen_hash
from app.modules.production.vna_screen_infrastructure import VnaScreenRepository
from app.modules.production.vna_screens import screen as bench_screen
from app.modules.production.vna_session_infrastructure import (VnaBenchItemRow, VnaCommandRow, VnaSessionRepository,
                                                               VnaSessionRow)
from app.modules.production.vna_sessions import Profile
from app.modules.warehouse.devices import DeviceRow, VnaBindingRow, VnaControlRow
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow
from app.workflows.vna_bench_api import _agent_code
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


AGENT_PS1 = Path(__file__).parents[3] / "tools" / "print-agent" / "agent.ps1"


def agent_lease_rule() -> tuple[set[str], set[str]]:
    """The real agent's Assert-VnaLease, read from agent.ps1: states it refuses every command in, and the only ops it
    still runs while `aborting`. The stub applies it, so a server flow the agent would refuse cannot pass here."""
    body = re.search(r"function Assert-VnaLease \{(.*?)\n\}", AGENT_PS1.read_text(encoding="utf-8"), re.S).group(1)
    names = lambda text: set(re.findall(r"'([a-z_]+)'", text))  # noqa: E731
    refused = re.search(r"\$Status\.state -in @\(([^)]*)\)", body).group(1)
    aborting = re.search(r"\$Status\.state -eq 'aborting' -and \$Command\.op -notin @\(([^)]*)\)", body).group(1)
    return names(refused), names(aborting)


class Agent:
    """A stub of agent 2.10.0: answers every op with a valid receipt; the test sets what is connected."""

    def __init__(self, env):
        self.env, self.dut_vswr, self.temps, self.acm_serial = env, 1.02, dict(TEMPS), ACM_SERIAL
        self.connected = None   # None: the default DUT; ("kit", definition) or ("load",) or ("thru",)
        self.confidence_offset = 0.0
        self.errors = {}
        self.corrupt = 0   # the next N sweeps come back unreadable: refused, nothing counted, the same connection again

    def sweep(self, profile):
        if self.corrupt:
            self.corrupt -= 1
            return b"# HZ S RI R 50\n1 nan 0 0 0 0 0 0 0\n"
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

    def assert_lease(self, command):
        """What the agent checks before touching the device (Assert-VnaLease): the session status it reads back."""
        response = TestClient(app).get(f"/api/stock/vna/sessions/{command['session_id']}",
                                       headers={**self.env["agent"], "X-Agent-Machine-Id": "pc-1"})
        assert response.status_code == 200, response.text
        status, (refused, aborting_ops) = response.json(), agent_lease_rule()
        assert status["lease_held"] and status["state"] not in refused, (command["op"], status["state"])
        assert status["state"] != "aborting" or command["op"] in aborting_ops, (command["op"], status["state"])

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
            self.assert_lease(command)
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
    # 26.11.2026: one step — the ACM calibration; no confidence test, no LOAD (owner's decision).
    assert [s["key"] for s in view["session"]["steps"]] == ["calibration"]
    assert view["session"]["steps"][0]["status"] == "pending" and not view["calibration"]["valid"]
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    return assert_calibrated(env, state(env))


def assert_calibrated(env, view):
    """The ACM calibration alone made the session valid: one passed step, temperatures from the device reading after
    it, and a «только калибровка» journal row."""
    assert [(s["key"], s["status"]) for s in view["session"]["steps"]] == [("calibration", "passed")], view["session"]
    assert view["calibration"]["valid"] and view["calibration"]["temperatures"] == env["stub"].temps
    with env["sessions"]() as session:
        check = session.get(VnaCheckRow, session.get(VnaSessionRow, UUID(view["session"]["id"])).calibration_check_id)
        assert (check.kind, check.passed, check.confidence_passed, check.load_p1_vswr, check.load_p2_vswr) == (
            "session", True, None, None, None)
        assert check.note == "доверительный тест и LOAD отменены решением владельца 26.11.2026"
    return view


def start_old_flex(env):
    """A bend check started before the owner dropped it (26.11.2026): the endpoint refuses a new one, so its steps are
    added as the old code added them — an open old session may still finish it, history must still render."""
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow).where(VnaSessionRow.state == "ready"))
        steps = bench.item_steps(Profile.model_validate(row.snapshot["profile"]),
                                 bench_screen(VnaScreenRepository(session).default("load")), None, bench.run("flex"))
        VnaSessionRepository(session).append(row, steps, env["clock"].now)
        session.commit()
    env["stub"].drive()
    return state(env)


def measure(env, view, target=None, expect=200):
    target = target or dict(kind="instrument", instrument_id=str(env["ids"]["dut"]))
    return post(env, session_path(view, "/items"), {"version": view["session"]["version"], "target": target}, expect)


def matings(env, instrument_id):
    with env["sessions"]() as session:
        return session.scalar(select(func.coalesce(func.sum(InstrumentMatingRow.delta), 0)).where(
            InstrumentMatingRow.instrument_id == instrument_id))


def test_target_list_reuses_rf_registry_and_excludes_vna_acm_and_non_rf(env):
    with env["sessions"]() as db:
        for number, name, details in (
            ("СИ.201", "Термометр", None),
            ("СИ.202", "Кабель", RfDetails((port(), port(male=False)))),
            ("СИ.203", "Комплект нагрузок", RfDetails(role="load_kit")),
            ("СИ.204", "Переход SMA розетка – SMA вилка", None),
            ("СИ.205", "Другой ВАЦ", RfDetails((port(), port()), "vna")),
        ):
            card_id = uuid4()
            db.add(ProductionInstrumentRow(id=card_id, version=1, inventory_number=number, name=name,
                                          instrument_type="Оснастка", kind="Принадлежность", status="Действует"))
            db.flush()
            if details:
                RfRepository(db).save(card_id, details)
        db.commit()
    response = env["admin"].get(URL + "/targets")
    assert response.status_code == 200, response.text
    rows = {row["number"]: row for row in response.json()["targets"]}
    assert {"СИ.035", "СИ.009", "СИ.003", "СИ.100", "СИ.101", "СИ.202", "СИ.203", "СИ.204"} <= rows.keys()
    assert not {"СИ.104", "СИ.201", "СИ.205"} & rows.keys()
    assert S2_INSTRUMENT not in {row["instrument_id"] for row in rows.values()}
    assert rows["СИ.035"] == dict(kind="instrument", instrument_id=str(env["ids"]["dut"]), number="СИ.035",
                                  name="Переход коаксиальный ADP1A 3,5 мм", serial_number="N8B035",
                                  interfaces_text="3.5mm вилка – 3.5mm розетка", last_measured_at=None)


# The list belongs to the bench: same right as the bench state (ОТК edit, the contract gate production_quality).
@pytest.mark.parametrize("permissions,status", [({"quality": "edit"}, 200), ({"quality": "view"}, 403), ({"production": "edit"}, 403), ({}, 403)])
def test_target_list_follows_the_bench_right(env, monkeypatch, permissions, status):
    monkeypatch.setitem(app.dependency_overrides, authenticated_principal, lambda: Principal(
        actor=Actor(id=uuid4(), name="Наблюдатель", department="ОТК"), email="viewer@example.test",
        roles=frozenset(), permissions=permissions))
    response = env["admin"].get(URL + "/targets")
    assert response.status_code == status, response.text


def test_target_list_uses_accepted_captures_and_imported_measurement_date(env):
    view = calibrate(env)
    measure(env, view)

    def rows():
        response = env["admin"].get(URL + "/targets")
        assert response.status_code == 200, response.text
        return {row["number"]: row for row in response.json()["targets"]}

    assert rows()["СИ.035"]["last_measured_at"] is None  # queued command is not a measurement
    env["stub"].drive()
    assert rows()["СИ.035"]["last_measured_at"][:10] == env["clock"].now.date().isoformat()
    with env["sessions"]() as db:
        for key, content in (("standard", touchstone([1e7, 18e9], [0j, 0j], [1 + 0j, 1 + 0j])),
                             ("cable1", b"invalid touchstone")):
            db.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=env["ids"][key], kind="Прочее",
                file_name="Замер ВАЦ S50180, 01.10.2026.s2p", content=content, uploaded_at=env["clock"].now,
                uploaded_by=db.scalar(select(VnaSessionRow.operator_id))))
        db.commit()
    listed = rows()
    assert listed["СИ.009"]["last_measured_at"] == "2026-10-01"  # not the later upload date
    assert listed["СИ.100"]["last_measured_at"] is None
    with env["sessions"]() as db:
        for day, content in (("01.09.2026", touchstone([1e7, 18e9], [0j, 0j], [1 + 0j, 1 + 0j])),
                             ("01.11.2026", b"invalid touchstone")):
            db.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=env["ids"]["standard"], kind="Прочее",
                file_name=f"Замер ВАЦ S50180, {day}.s2p", content=content, uploaded_at=env["clock"].now + timedelta(hours=1),
                uploaded_by=db.scalar(select(VnaSessionRow.operator_id))))
        db.commit()
    assert rows()["СИ.009"]["last_measured_at"] == "2026-10-01"


@pytest.mark.parametrize("valid", [True, False])
def test_target_list_bounds_history_parsing_and_body_loads(env, monkeypatch, valid):
    from app.workflows import vna_bench_api
    from app.modules.production.infrastructure import SqlAlchemyInstrumentFileRepository

    content = touchstone([1e7, 18e9], [0j, 0j], [1 + 0j, 1 + 0j]) if valid else b"invalid touchstone"
    with env["sessions"]() as db:
        for index in range(100):
            day = datetime(2026, 7, 1) + timedelta(days=index)
            db.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=env["ids"]["dut"], kind="Прочее",
                file_name=f"Замер ВАЦ S50180, {day:%d.%m.%Y}.s2p", content=content,
                uploaded_at=env["clock"].now + timedelta(seconds=100 - index),
                uploaded_by=db.scalar(select(IdentityUserRow.id))))
        db.commit()
    parsed, loaded = [], []
    parse, get = vna_bench_api.characterization, SqlAlchemyInstrumentFileRepository.get

    def tracked_parse(file):
        parsed.append(file.file_name)
        return parse(file)

    def tracked_get(self, instrument_id, file_id):
        loaded.append(file_id)
        return get(self, instrument_id, file_id)

    monkeypatch.setattr(vna_bench_api, "characterization", tracked_parse)
    monkeypatch.setattr(SqlAlchemyInstrumentFileRepository, "get", tracked_get)
    response = env["admin"].get(URL + "/targets")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["targets"] if row["number"] == "СИ.035")
    assert row["last_measured_at"] == ("2026-10-08" if valid else None)
    assert len(parsed) == len(loaded) == (1 if valid else 3)
    assert parsed[0] == "Замер ВАЦ S50180, 08.10.2026.s2p"


def test_target_list_imported_capture_uses_moscow_day(env):
    content = b"! captured=2026-10-31T22:30:00+00:00\n" + touchstone([1e7, 18e9], [0j, 0j], [1 + 0j, 1 + 0j])
    with env["sessions"]() as db:
        db.add(ProductionInstrumentFileRow(id=uuid4(), instrument_id=env["ids"]["dut"], kind="Прочее",
            file_name="Замер ВАЦ S50180.s2p", content=content, uploaded_at=env["clock"].now,
            uploaded_by=db.scalar(select(IdentityUserRow.id))))
        db.commit()
    response = env["admin"].get(URL + "/targets")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["targets"] if row["number"] == "СИ.035")
    assert row["last_measured_at"] == "2026-11-01"


def test_calibration_journal_item_record_and_close(env):
    view = calibrate(env)
    with env["sessions"]() as session:
        journal = session.scalars(select(VnaCheckRow)).all()
        assert [(r.kind, r.passed, r.load_p1_vswr) for r in journal] == [("session", True, None)]
        row = session.scalar(select(VnaSessionRow))
        assert row.calibration_check_id == journal[0].id and row.purpose == "bench" and row.task_id is None
    assert matings(env, env["ids"]["acm"]) == 2 and matings(env, env["ids"]["cable1"]) == 1   # ECAL only, no LOAD
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
    for dropped in ("confidence", "load_port1", "load_port2"):   # dropped by the owner on 26.11.2026
        post(env, session_path(view, f"/steps/{dropped}"), {"version": view["session"]["version"]}, 422)
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
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    return assert_calibrated(env, state(env))


def test_calibration_expires_after_four_hours_and_recalibrates_in_the_same_session(env):
    view = calibrate(env)
    env["clock"].now += timedelta(hours=4, minutes=1)
    control_heartbeat(env["agent"])   # the agent keeps reporting; only the calibration aged
    view = state(env)
    assert not view["calibration"]["valid"] and "4 часов" in view["calibration"]["reason"]
    assert measure(env, view, expect=409)["detail"]["code"] == "calibration_expired"
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert_calibrated(env, state(env))


def test_refused_calibration_reading_and_acm_change_and_failed_control_stop_work(env):
    # The reading after the ACM calibration has no ACM temperature: the calibration is not accepted, nothing journaled.
    env["stub"].temps = {"vna_c": [30.0, 30.5], "acm_c": None}
    view = post(env, "/sessions")
    env["stub"].drive()
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = state(env)
    assert view["session"]["steps"][0]["status"] == "failed" and not view["calibration"]["valid"]
    assert view["session"]["steps"][0]["refusal"]["code"] == "temperature_unknown"
    with env["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(VnaCheckRow)) == 0
    env["stub"].temps = dict(TEMPS)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = assert_calibrated(env, state(env))
    # The ACM swapped on USB: the item's device reading refuses and the calibration is gone.
    env["stub"].acm_serial = "OTHER"
    measure(env, view)
    env["stub"].drive()
    view = state(env)
    assert view["session"]["error"].startswith("АКМ заменён") and not view["calibration"]["valid"]
    env["stub"].acm_serial = ACM_SERIAL
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = assert_calibrated(env, state(env))
    with env["sessions"]() as session:
        session.add(VnaCheckRow(id=uuid4(), instrument_id=UUID(S2_INSTRUMENT), kind="control_standard", passed=False,
                                performed_at=env["clock"].now, recorded_at=env["clock"].now, source="weekly-failed",
                                payload_hash="b" * 64, operator="", actor="", calibration_id="", acm_serial="", profile="",
                                standard_inventory="СИ.009", reference="", vswr_dev=0.05, vswr_limit=0.02,
                                s21_dev_db=0.01, s21_limit_db=0.1, note=""))
        session.commit()
    assert measure(env, view, expect=409)["detail"]["code"] == "control_failed"
    assert state(env)["due"][0] == {**state(env)["due"][0], "kind": "weekly", "status": "failed"}


def test_agent_without_protocol_2_ops_cannot_open_a_bench_session(env):
    control_heartbeat(env["agent"], protocol=1)
    assert post(env, "/sessions", expect=409)["detail"]["code"] == "agent_old"
    control_heartbeat(env["agent"], ops=OPS[:3])
    assert post(env, "/sessions", expect=409)["detail"]["code"] == "agent_old"
    assert state(env)["device"]["refusal"]["code"] == "agent_old"


@pytest.mark.parametrize("message,code", [
    ("Подключённый ВАЦ с нужным серийным номером не найден", "vna_not_connected"),
    ("Агент на закреплённом ПК не отвечает", "agent_offline"),
    ("Агент не поддерживает актуальный протокол vna_control версии 1 или 2", "agent_old"),
    ("Метод калибровки не подтверждён агентом на этом ПК", "agent_capability_missing"),
    ("ВАЦ не закреплён за этим ПК", "station_setup"),
    ("Привязка ВАЦ изменилась", "conflict"),
    ("Серийный номер ВАЦ изменился", "serial_changed"),
    ("ВАЦ занят", "device_busy"),
    ("Неизвестная причина", "refused"),
])
def test_station_refusal_codes(message, code):
    assert _agent_code(message) == code


def test_online_agent_without_control_advertisement_is_not_offline(env):
    with env["sessions"]() as db:
        db.delete(db.get(VnaControlRow, ("pc-1", SERIAL)))
        db.commit()
    device = state(env)["device"]
    assert device["agent_online"] is True
    assert device["vna_online"] is True
    assert device["refusal"]["code"] == "agent_old"


@pytest.mark.parametrize("online", [True, False])
def test_agent_and_vna_presence_are_separate(env, online):
    with env["sessions"]() as db:
        db.get(DeviceRow, ("pc-1", "s2vna", SERIAL)).present = False
        db.delete(db.get(VnaControlRow, ("pc-1", SERIAL)))
        if not online:
            db.get(ScanAgentRow, "pc-1").last_seen = env["clock"].now - timedelta(minutes=4)
        db.commit()
    device = state(env)["device"]
    assert device["agent_online"] is online
    assert device["vna_online"] is False
    assert device["serial"] is None and device["s2vna_ready"] is None
    assert device["refusal"]["code"] == ("vna_not_connected" if online else "agent_offline")
    if online:
        assert "Включите ВАЦ, подключите USB и запустите S2VNA" in device["refusal"]["message"]
    assert post(env, "/sessions", expect=409)["detail"]["code"] == device["refusal"]["code"]


@pytest.mark.parametrize("present,target_live,age", [(False, True, 1440), (True, True, 0), (True, False, 0), (True, True, 4)])
@pytest.mark.parametrize("owner_reported", [True, False])
def test_last_seen_elsewhere_and_explicit_transfer_offer(env, present, target_live, age, owner_reported):
    now = env["clock"].now
    with env["sessions"]() as db:
        old = db.get(DeviceRow, ("pc-1", "s2vna", SERIAL))
        old.present, old.last_seen = False, now - timedelta(days=3)
        if not owner_reported:
            db.delete(old)
        db.add(ScanAgentRow(machine_id="pc-2", pc_name="DESKTOP-PTA57QF", version="2.12.0",
                            first_seen=now, last_seen=now if target_live else now - timedelta(minutes=4)))
        db.add(DeviceRow(machine_id="pc-2", kind="s2vna", device_id=SERIAL, name=NAME, present=present,
                         last_seen=now - timedelta(minutes=age)))
        db.commit()
    device = state(env)["device"]
    assert device["bound_pc"] == device["pc_name"] == "ОТК-ВАЦ"
    assert device["last_seen_pc"] == "DESKTOP-PTA57QF"
    assert datetime.fromisoformat(device["last_seen_at"]) == now - timedelta(minutes=age)
    assert "DESKTOP-PTA57QF" in device["refusal"]["message"]
    if present and target_live and age <= 3:
        assert device["transfer"]["owner_id"] == "pc-1"
        assert device["transfer"]["serial"] == SERIAL
        assert [a["id"] for a in device["transfer"]["agents"]] == ["pc-2"]
        response = env["admin"].post("/api/stock/vna/jobs/transfer", headers=_csrf(env["admin"]),
                    json=dict(instrument_id=S2_INSTRUMENT, serial=SERIAL, owner_id="pc-1", agent_id="pc-2"))
        assert response.status_code == 200, response.text
        assert state(env)["device"]["bound_pc"] == "DESKTOP-PTA57QF"
        with env["sessions"]() as db:
            assert db.scalar(select(func.count()).select_from(AuditEventRow).where(
                AuditEventRow.event_type == "warehouse.vna_transferred")) == 1
    else:
        assert device["transfer"] is None
        with env["sessions"]() as db:
            assert db.get(VnaBindingRow, SERIAL).machine_id == "pc-1"


def test_transfer_offer_is_admin_only_and_current_presence_is_rechecked(env, monkeypatch):
    now = env["clock"].now
    with env["sessions"]() as db:
        old = db.get(DeviceRow, ("pc-1", "s2vna", SERIAL))
        old.present, old.last_seen = False, now - timedelta(days=1)
        db.add(ScanAgentRow(machine_id="pc-2", pc_name="Другой ПК", version="2.12.0", first_seen=now, last_seen=now))
        db.add(DeviceRow(machine_id="pc-2", kind="s2vna", device_id=SERIAL, name=NAME, present=True, last_seen=now))
        db.commit()
    assert state(env)["device"]["transfer"]
    body = dict(instrument_id=S2_INSTRUMENT, serial=SERIAL, owner_id="pc-1", agent_id="pc-2")
    with monkeypatch.context() as patch:
        patch.setitem(app.dependency_overrides, authenticated_principal, lambda: Principal(
            actor=Actor(id=uuid4(), name="Контролёр", department="ОТК"), email="qc@example.test",
            roles=frozenset({"qc"}), permissions={"quality": "edit"}))
        assert state(env)["device"]["transfer"] is None
        assert env["admin"].post("/api/stock/vna/jobs/transfer", json=body,
                                 headers=_csrf(env["admin"])).status_code == 403
    with env["sessions"]() as db:
        db.get(DeviceRow, ("pc-2", "s2vna", SERIAL)).present = False
        db.commit()
    response = env["admin"].post("/api/stock/vna/jobs/transfer", json=body, headers=_csrf(env["admin"]))
    assert response.status_code == 409, response.text
    with env["sessions"]() as db:
        assert db.get(VnaBindingRow, SERIAL).machine_id == "pc-1"


def test_old_session_readiness_is_not_live_after_unplugging(env):
    post(env, "/sessions")
    env["stub"].drive()
    assert state(env)["device"]["s2vna_ready"] is True
    with env["sessions"]() as db:
        db.get(DeviceRow, ("pc-1", "s2vna", SERIAL)).present = False
        db.commit()
    view = state(env)["device"]
    assert view["vna_online"] is False
    assert view["s2vna_ready"] is None
    assert view["refusal"]["code"] == "vna_not_connected"


def test_transfer_cannot_strand_a_calibrated_session_on_the_old_pc(env):
    calibrated = calibrate(env)
    now = env["clock"].now
    with env["sessions"]() as db:
        old = db.get(DeviceRow, ("pc-1", "s2vna", SERIAL))
        old.present, old.last_seen = False, now - timedelta(minutes=1)
        db.add(ScanAgentRow(machine_id="pc-2", pc_name="Другой ПК", version="2.12.0", first_seen=now, last_seen=now))
        db.add(DeviceRow(machine_id="pc-2", kind="s2vna", device_id=SERIAL, name=NAME, present=True, last_seen=now))
        db.commit()
    device = state(env)["device"]
    assert device["transfer"] is None
    assert "завершите сеанс" in device["transfer_blocked_reason"]
    response = env["admin"].post("/api/stock/vna/jobs/transfer", headers=_csrf(env["admin"]),
                json=dict(instrument_id=S2_INSTRUMENT, serial=SERIAL, owner_id="pc-1", agent_id="pc-2"))
    assert response.status_code == 409 and "завершите сеанс" in response.text
    with env["sessions"]() as db:
        assert db.get(VnaBindingRow, SERIAL).machine_id == "pc-1"
        row = db.get(VnaSessionRow, UUID(calibrated["session"]["id"]))
        assert row.machine_id == "pc-1" and row.state == "ready"
        assert db.scalar(select(func.count()).select_from(AuditEventRow).where(
            AuditEventRow.event_type == "warehouse.vna_transferred")) == 0


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


def test_a_measurement_is_accepted_right_after_the_acm_calibration(env):
    """Owner, 26.11.2026: no confidence test and no LOAD after the ACM — «Измерить» works at once."""
    view = calibrate(env)
    with env["sessions"]() as session:
        ops = [s["op"] for s in session.get(VnaSessionRow, UUID(view["session"]["id"])).steps]
        assert ops[-4:] == ["begin_calibration", "acm_calibrate", "save_calibration", "device_status"]
        assert session.scalar(select(func.count()).select_from(VnaCommandRow).where(
            VnaCommandRow.op.in_(("acm_confidence", "capture")))) == 0
    measure(env, view)
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    assert item["verdict"]["code"] == "pass" and item["verdict"]["final"]
    with env["sessions"]() as session:
        result = session.scalar(select(VnaBenchItemRow).where(VnaBenchItemRow.kind == "instrument")).result
        assert result["calibration"]["load_check"] == "not_required"


def old_calibration_steps(snapshot):
    """The calibration as the code before 26.11.2026 built it: the confidence test and LOAD on each port after the ACM."""
    profile = Profile.model_validate(snapshot["profile"])
    head = bench.calibration_steps(snapshot)[:3]
    calibration = head[0]["item"]
    load_screen = bench.fit_screen(bench_screen("load_check"), profile)
    loads = bench.run("load", calibration_run=calibration["run_id"])
    return [*head, bench.step("acm_confidence", dict(profile=profile, module_serial=snapshot["acm"]["serial"]),
                              item=calibration),
            bench.screen_step(load_screen, calibration),
            bench.capture_step(profile, screen_hash(load_screen), "LOAD порт 1", bench.connection(loads, "port1")),
            bench.capture_step(profile, screen_hash(load_screen), "LOAD порт 2", bench.connection(loads, "port2"))]


def confirm_directly(env):
    """What a click confirmed before 26.11.2026 (the LOAD steps are no longer an endpoint key)."""
    with env["sessions"]() as session:
        row = session.scalar(select(VnaSessionRow).where(VnaSessionRow.state.in_(("awaiting_standard", "awaiting_dut"))))
        VnaSessionRepository(session).act(row, "confirm", row.version, row.step, env["clock"].now)
        session.commit()
    env["stub"].drive()


def open_old_session(env):
    """A session opened before the change: its calibration still holds the confidence test and both LOAD steps."""
    view = post(env, "/sessions")
    env["stub"].drive()
    with env["sessions"]() as session:
        row = session.get(VnaSessionRow, UUID(view["session"]["id"]))
        assert row.steps[row.step]["op"] == "acm_calibrate"
        row.steps = row.steps[:2] + old_calibration_steps(row.snapshot)
        session.commit()
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    return state(env)


def test_an_old_format_session_renders_and_may_finish_its_old_steps(env):
    view = open_old_session(env)
    steps = view["session"]["steps"]
    assert [(s["key"], s["status"]) for s in steps] == [("calibration", "passed"), ("confidence", "passed"),
                                                        ("load_port1", "pending"), ("load_port2", "pending")]
    assert steps[1]["result_text"].startswith("передача ") and not view["calibration"]["valid"]
    for _ in range(2):   # a LOAD already confirmed before the release is still judged as it was
        confirm_directly(env)
    view = state(env)
    assert [s["status"] for s in view["session"]["steps"]] == ["passed"] * 4 and view["calibration"]["valid"]
    with env["sessions"]() as session:
        check = session.scalar(select(VnaCheckRow))
        assert (check.passed, check.confidence_passed, check.load_p1_vswr is not None) == (True, True, True)
    measure(env, view)
    env["stub"].drive()
    assert state(env)["session"]["items"][0]["verdict"]["code"] == "pass"
    # The next calibration of the same session follows the new flow.
    env["clock"].now += timedelta(hours=4, minutes=1)
    control_heartbeat(env["agent"])
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert_calibrated(env, state(env))


def test_an_old_session_waiting_for_load_calibrates_anew_on_click(env):
    view = open_old_session(env)
    assert view["session"]["steps"][2]["status"] == "pending"
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = assert_calibrated(env, state(env))
    with env["sessions"]() as session:
        ops = [s["op"] for s in session.get(VnaSessionRow, UUID(view["session"]["id"])).steps]
        assert "capture" not in ops   # the waiting LOAD steps were dropped, never sent to the agent


def test_flex_is_neither_due_nor_started(env):
    view = calibrate(env)
    assert [d["kind"] for d in view["due"]] == ["weekly", "monthly"]
    refused = post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "flex"}, 422)
    assert refused["detail"]["message"].startswith("Шевеление кабелей отменено владельцем")
    assert state(env)["session"]["checks"] == []


def test_old_flex_weekly_and_monthly_checks_write_the_journal(env):
    view = calibrate(env)
    stub = env["stub"]
    start_old_flex(env)
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
    assert [(d["kind"], d["status"]) for d in state(env)["due"]] == [("weekly", "ok"), ("monthly", "ok")]


def test_abort_returns_the_device_and_frees_the_lease(env):
    view = calibrate(env)
    post(env, session_path(view, "/abort"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert state(env)["session"] is None
    with env["sessions"]() as session:
        assert session.scalar(select(VnaSessionRow.state)) == "aborted"
    assert post(env, "/sessions")["session"]["status"] == "open"


def _stuck(env, view, operator_id=None):
    with env["sessions"]() as session:
        row = session.get(VnaSessionRow, UUID(view["session"]["id"]))
        row.state, row.error = "recovery_required", "Ответ ВАЦ потерян; требуется остановка и восстановление"
        if operator_id is not None:
            row.operator_id = operator_id
        session.commit()
    return state(env)


def _events(env, event_type):
    with env["sessions"]() as db:
        return db.scalar(select(func.count()).select_from(AuditEventRow).where(AuditEventRow.event_type == event_type))


def test_a_failing_restore_is_closed_without_restoring_by_an_admin(env):
    """26.11 СИ.002: restore failed its readback, the session fell back to recovery_required and «Восстановить»
    would loop. The admin closes it without loading the backup; the agent releases the VNA, the bench is free."""
    view = _stuck(env, calibrate(env))
    assert view["session"]["can_release_unrestored"] is True
    env["stub"].errors["restore"] = "ВАЦ: проверка чтением не совпала: восстановленное состояние и назначения набора"
    post(env, session_path(view, "/abort"), {"version": view["session"]["version"]})
    env["stub"].drive()
    view = state(env)
    assert view["session"]["state"] == "recovery_required" and "проверка чтением" in view["session"]["error"]
    post(env, session_path(view, "/release-unrestored"), {"version": view["session"]["version"]})
    with env["sessions"]() as session:
        command = session.scalars(select(VnaCommandRow).where(VnaCommandRow.status == "pending")).one()
        assert (command.op, command.args["screen_hash"]) == ("release", None)
    # Until the agent takes it (offline PC), the card shows the queued closing — not a silent dead end.
    view = state(env)
    assert view["session"]["state"] == "releasing" and view["session"]["command"]["label"] == "закрытие сеанса"
    assert view["session"]["command"]["state"] == "queued"
    env["stub"].drive()   # the stub refuses, like the agent, anything Assert-VnaLease would refuse
    assert state(env)["session"] is None
    with env["sessions"]() as session:
        assert session.scalar(select(VnaSessionRow.state)) == "aborted"
    assert _events(env, "production.vna_bench_closed_unrestored") == 1
    assert post(env, "/sessions")["session"]["status"] == "open"


def test_the_lease_rule_is_read_from_the_agent():
    refused, aborting_ops = agent_lease_rule()
    assert {"aborted", "recorded", "failed", "recovery_required"} <= refused and aborting_ops == {"abort", "restore"}


def test_only_an_admin_closes_without_restoring_and_only_a_stuck_session(env, monkeypatch):
    view = calibrate(env)
    refused = post(env, session_path(view, "/release-unrestored"), {"version": view["session"]["version"]}, expect=409)
    assert refused["detail"]["code"] == "stale"
    view = _stuck(env, view)
    with monkeypatch.context() as patch:
        patch.setitem(app.dependency_overrides, authenticated_principal, lambda: Principal(
            actor=Actor(id=uuid4(), name="Контролёр", department="ОТК"), email="qc@example.test",
            roles=frozenset({"qc"}), permissions={"quality": "edit"}))
        # Someone else's session is invisible to a controller: neither recovery nor the escape.
        for tail in ("/abort", "/release-unrestored"):
            assert env["admin"].post(URL + session_path(view, tail), json={"version": view["session"]["version"]},
                                     headers=_csrf(env["admin"])).status_code == 404
    assert _events(env, "production.vna_bench_closed_unrestored") == 0


def test_an_admin_recovers_someone_elses_stuck_session_once(env):
    """The opener is away: an administrator recovers the session (journaled); a double click is refused as stale."""
    view = _stuck(env, calibrate(env), operator_id=uuid4())
    assert view["session"]["owned"] is False and view["session"]["can_recover"] is True
    version = view["session"]["version"]
    post(env, session_path(view, "/abort"), {"version": version})
    refused = post(env, session_path(view, "/abort"), {"version": version}, expect=409)
    assert refused["detail"]["code"] == "stale"
    assert _events(env, "production.vna_bench_session_stopped_by_admin") == 1
    env["stub"].drive()
    assert state(env)["session"] is None


def test_a_lost_device_answer_is_recovered_from_the_card(env):
    """24.11: a session stuck in recovery_required had no command, so the card offered no way out."""
    view = calibrate(env)
    with env["sessions"]() as session:
        row = session.get(VnaSessionRow, UUID(view["session"]["id"]))
        row.state, row.error = "recovery_required", "Ответ ВАЦ потерян; требуется остановка и восстановление"
        session.commit()
    view = state(env)
    assert view["session"]["state"] == "recovery_required" and view["session"]["command"] is None
    # Any other action — and a new session — names the way out instead of a generic «занят».
    refused = post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]}, expect=409)
    assert refused["detail"]["code"] == "recovery_required" and "Восстановить" in refused["detail"]["message"]
    refused = post(env, "/sessions", expect=409)
    assert refused["detail"]["code"] == "recovery_required"
    post(env, session_path(view, "/abort"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert state(env)["session"] is None
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
    assert matings(env, env["ids"]["cable1"]) == 1 + 4   # the ACM calibration (no LOAD since 26.11) + four sweeps
    # «Измерить заново»: a new item of the same position starts a fresh series, even after an open border one.
    for _ in range(2):   # the first new item ends «на границе»; the second must not become its repeat
        view = state(env)
        measure(env, view, target)
        env["stub"].drive()
    new = state(env)["session"]["items"][-1]
    assert new["verdict"]["code"] == "border" and new["connections_total"] == 2
    with env["sessions"]() as session:
        rows = session.scalars(select(VnaBenchItemRow).where(VnaBenchItemRow.kind == "assembly")).all()
        last_two = sorted(rows, key=lambda r: r.result["attempt"])[-2:]
        assert all(r.result["repeat_of"] is None for r in last_two)   # each new item is its own origin


def connect_next(env, item):
    view = state(env)
    post(env, session_path(view, f"/items/{item['id']}/connections"), {"version": view["session"]["version"]})
    env["stub"].drive()
    return state(env)["session"]["items"][-1]


def test_refused_sweep_holds_no_connection_and_the_same_connection_is_taken_again(env):
    view = calibrate(env)
    env["stub"].dut_vswr = 1.065
    measure(env, view)
    env["stub"].drive()
    item = state(env)["session"]["items"][0]
    env["stub"].corrupt = 1
    item = connect_next(env, item)
    assert [c["status"] for c in item["connections"]] == ["passed", "pending", "pending"]   # 2 is not used up
    assert item["refusal"] and state(env)["calibration"]["valid"]
    item = connect_next(env, item)
    item = connect_next(env, item)
    assert item["verdict"]["final"] and [c["no"] for c in item["connections"]] == [1, 2, 3]
    with env["sessions"]() as session:
        refused = session.execute(select(func.count()).select_from(VnaBenchItemRow)).scalar()
        assert refused == 3   # three counted connections; the refused sweep is not a row
    assert matings(env, env["ids"]["dut"]) == 2 * 4                        # the refused sweep was a mating too
    view = state(env)
    post(env, session_path(view, f"/items/{item['id']}/record"), {"version": view["session"]["version"]})
    with env["sessions"]() as session:
        assert session.scalar(select(func.count()).select_from(ProductionInstrumentFileRow).where(
            ProductionInstrumentFileRow.instrument_id == env["ids"]["dut"])) == 3


def test_a_border_series_restarts_after_a_recalibration(env):
    view = calibrate(env)
    env["stub"].dut_vswr = 1.065
    measure(env, view)
    env["stub"].drive()
    item = connect_next(env, state(env)["session"]["items"][0])
    assert len(item["connections"]) == 3 and item["connections"][1]["status"] == "passed", (item["connections"], item["refusal"])
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert_calibrated(env, state(env))
    item = connect_next(env, item)
    with env["sessions"]() as session:
        rows = session.scalars(select(VnaBenchItemRow).where(VnaBenchItemRow.kind == "instrument")
                               .order_by(VnaBenchItemRow.created_at)).all()
        assert [r.connection_no for r in rows] == [1, 2, 1] and len({r.group_id for r in rows}) == 2
        steps = session.scalar(select(VnaSessionRow.steps))
        assert any("серия повтора начата заново" in s.get("label", "") for s in steps)
        first, last = rows[0].result["calibration"], rows[-1].result["calibration"]
        assert first["check_id"] != last["check_id"]
    # The new series: its connection 1 measured, 2 and 3 still to do under the new calibration.
    assert [c["status"] for c in item["connections"]] == ["passed", "pending", "pending"]


def test_refused_phases_are_retaken_in_flex_and_monthly_checks(env):
    view = calibrate(env)
    stub = env["stub"]
    start_old_flex(env)
    check = state(env)["session"]["checks"][0]
    for connected, corrupt in ((("load",), 1), (("load",), 0), (("load",), 0), (("thru",), 1), (("thru",), 0),
                               (("thru",), 0)):
        stub.connected, stub.corrupt = connected, corrupt
        view = state(env)
        post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
        stub.drive()
    assert state(env)["session"]["checks"][0]["status"] == "passed"
    view = state(env)
    post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "monthly"})
    stub.drive()
    check = state(env)["session"]["checks"][1]
    corrupted, sweeps = False, 0
    while (pending := [p for p in state(env)["session"]["checks"][1]["phases"] if p["status"] == "pending"]):
        phase = pending[0]
        stub.connected = ("kit", next(s for s in KIT if s["type"] == phase["key"].split("_")[0].upper()))
        if (phase["key"], phase["connection_no"]) == ("open_p1", 2) and not corrupted:
            stub.corrupt, corrupted = 1, True   # refused once: OPEN, connection 2 is taken again
        view = state(env)
        post(env, session_path(view, f"/checks/{check['id']}/next"), {"version": view["session"]["version"]})
        stub.drive()
        sweeps += 1
    assert corrupted and sweeps == 22
    phases = state(env)["session"]["checks"][1]["phases"]
    assert all(p["result_text"] and "годен" in p["result_text"] for p in phases), phases
    assert phases[6]["result_text"].startswith("|Г| ") and phases[0]["result_text"].startswith("Δ отражения")
    assert all(p["result_text"] for p in state(env)["session"]["checks"][0]["phases"])
    assert state(env)["session"]["checks"][1]["status"] == "passed"


def test_a_bench_session_idle_for_twelve_hours_frees_the_instrument(env):
    calibrate(env)
    env["clock"].now += timedelta(hours=12, minutes=1)
    control_heartbeat(env["agent"])
    assert state(env)["session"] is None
    with env["sessions"]() as session:
        assert session.scalar(select(VnaSessionRow.state)) == "aborted"
    assert post(env, "/sessions")["session"]["status"] == "open"



def run_monthly(env, check_id, standard_for=lambda kind: kind, stop=None):
    """Sweep pending monthly phases; `standard_for` says which standard is really connected; `stop(phase)` pauses."""
    stub = env["stub"]
    while (pending := [p for c in state(env)["session"]["checks"] if c["id"] == check_id
                       for p in c["phases"] if p["status"] == "pending"]):
        phase = pending[0]
        if stop and stop(phase):
            return phase
        kind = standard_for(phase["key"].split("_")[0].upper())
        stub.connected = ("kit", next(s for s in KIT if s["type"] == kind))
        view = state(env)
        post(env, session_path(view, f"/checks/{check_id}/next"), {"version": view["session"]["version"]})
        stub.drive()
    return None


def start_monthly(env):
    view = state(env)
    post(env, session_path(view, "/checks"), {"version": view["session"]["version"], "kind": "monthly"})
    env["stub"].drive()
    return state(env)["session"]["checks"][-1]["id"]


def test_monthly_check_restarts_whole_after_a_recalibration_and_journals_every_standard(env):
    calibrate(env)
    check_id = start_monthly(env)
    phase = run_monthly(env, check_id, stop=lambda p: p["key"] == "short_p1")
    assert phase is not None
    view = state(env)
    post(env, session_path(view, "/steps/calibration"), {"version": view["session"]["version"]})
    env["stub"].drive()
    assert_calibrated(env, state(env))
    check = next(c for c in state(env)["session"]["checks"] if c["id"] == check_id)
    assert [p["status"] for p in check["phases"]][:3] == ["pending"] * 3   # OPEN p1 is measured again
    assert run_monthly(env, check_id) is None
    with env["sessions"]() as session:
        steps = session.scalar(select(VnaSessionRow.steps))
        assert any("проверка начата заново" in s.get("label", "") for s in steps)
        note = session.scalar(select(VnaCheckRow.note).where(VnaCheckRow.kind == "kit_standards"))
        assert note.count(";") == 6 and "OPEN-F п.1" in note and "MA6666A" in note   # all seven standards


def test_monthly_check_fails_when_only_open_fails(env):
    calibrate(env)
    check_id = start_monthly(env)
    run_monthly(env, check_id, standard_for=lambda kind: "LOAD" if kind == "OPEN" else kind)
    check = next(c for c in state(env)["session"]["checks"] if c["id"] == check_id)
    assert check["status"] == "failed"
    assert check["phases"][-1]["status"] == "passed"   # the last phase (the thru) itself is good
    with env["sessions"]() as session:
        assert session.scalar(select(VnaCheckRow.passed).where(VnaCheckRow.kind == "kit_standards")) is False


def test_a_direct_open_after_twelve_idle_hours_is_not_blocked(env):
    calibrate(env)
    env["clock"].now += timedelta(hours=12, minutes=1)
    control_heartbeat(env["agent"])
    assert post(env, "/sessions")["session"]["status"] == "open"   # no state read in between
