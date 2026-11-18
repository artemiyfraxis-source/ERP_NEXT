"""ОБ.099 (BZW crimp press), read only: the agent's FINS snapshot and per-crimp / alarm records, the live card with
the shop day's crimps OK/NG and NG reasons."""

from datetime import timedelta
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.production import machines as rules
from app.modules.production import ob099
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from tests.identity.test_api_tokens import _bearer, _issue, _login, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import _unit

REPORT = "/api/production/machines/agent/report"
EVENTS = "/api/production/machines/agent/events"
LIST = "/api/production/machines"


def _string(first: int, text: str) -> dict[str, int]:
    raw = text.encode("ascii").ljust(20, b"\x00")
    return {f"D{first + i}": (raw[2 * i] << 8) | raw[2 * i + 1] for i in range(10)}


WORDS = {
    "D5005": 3, "D5006": 2500, "D5032": 50, "D5046": 4000, "D5047": 500,
    "D5100": 1400, "D5101": 0, "D5102": 700, "D5103": 0, "D5104": 156, "D5105": 0, "D5106": 156, "D5107": 0,
    "D5108": 34463, "D5109": 1, "D5110": 3406, "D5111": 0, "D5112": 34463, "D5113": 1, "D5114": 34454, "D5115": 1,
    "H60": 0x08D7, "H61": 0x0400, "W80": 0, "W81": 0, "W82": 0, "W83": 1 << 14, "W84": 0,
    **_string(5500, "61117-4"), **_string(5510, "A"), **_string(5520, "A"), **_string(5530, "A-12"),
    **_string(5540, "260122-A2"),
}


@pytest.fixture
def press_api(tokens_api):
    sessions, clock = tokens_api
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    with sessions.begin() as session:
        press = _unit(session, "ОБ.099.1", "Пресс сервоприводный для опрессовки контактов")
    admin = _login("admin")
    yield sessions, clock, admin, press
    del app.state.production_uow_factory


def _agent(admin, name="Агент ОБ.099"):
    return _bearer(_issue(admin, scopes=("machine:report",), name=name, days=365)["token"])


def _report(headers, *, seq=1, registers=None, link="ok", machine="guid-82"):
    body = {"proto": 1, "inventory": "ОБ.099.1", "machine_id": machine, "pc_name": "DESKTOP-K6M7O0U",
            "ip_lan": "192.168.1.82", "bridge_version": "1.0.0", "service_version": "FINS/UDP", "boot": "boot-1",
            "seq": seq, "link": link, "error": "", "registers": WORDS if registers is None else registers}
    return TestClient(app).post(REPORT, headers=headers, json=body)


def _crimp(offset, result="OK", at="2026-10-05T08:20:37+03:00", peak=519):
    return {"key": f"20261005@{offset}", "at": at, "result": result, "peak": peak, "equipment_no": "260122-A2",
            "product": "61117-4", "wire": "A", "terminal": "A", "applicator": "A-12"}


def _events(headers, crimps=(), alarms=(), machine="guid-82", inventory="ОБ.099.1"):
    body = {"proto": 1, "inventory": inventory, "machine_id": machine, "crimps": list(crimps), "alarms": list(alarms)}
    return TestClient(app).post(EVENTS, headers=headers, json=body)


def test_the_press_reports_fins_words_and_the_card_decodes_state_job_height_and_blades(press_api):
    sessions, clock, admin, press = press_api
    agent = _agent(admin)
    answer = _report(agent)
    assert answer.status_code == 200, answer.text
    assert answer.json()["bound"] is True
    with sessions() as session:
        assert session.get(rules.MachineLinkRow, press).kind == "ob099_fins"
    card = admin.get(f"{LIST}/{press}").json()
    assert card["kind"] == "ob099_fins" and card["service"] == "Агент ERP · станок"
    live = card["live"]
    assert live["state"] == {"text": "Работа", "tone": "ok"}
    assert live["alarms"] == []
    assert live["job"] == {"product": "61117-4", "wire": "A", "terminal": "A", "applicator": "A-12",
                           "equipment_no": "260122-A2"}
    assert live["crimp"]["height_mm"] == 2.5 and live["crimp"]["tolerance_mm"] == 0.05
    assert live["crimp"]["cfm_on"] is True and live["crimp"]["height_max_mm"] == 4.0
    assert live["counters"] == {"total_set": 1400, "batch_set": 700, "total_done": 156, "batch_done": 156}
    crimp_blade, cutter = live["tools"]
    assert (crimp_blade["life"], crimp_blade["used"], crimp_blade["used_pct"], crimp_blade["tone"]) == (99999, 3406, 3.4, "ok")
    assert (cutter["used"], cutter["left"], cutter["tone"]) == (99990, 9, "warn")
    assert card["today"] == {"day": "2026-10-05", "total": 0, "ok": 0, "ng": 0, "ng_pct": None, "ng_reasons": [],
                             "last": None, "alarms": []}
    [row] = [row for row in admin.get(LIST).json()["rows"] if row["inventory"] == "ОБ.099.1"]
    assert (row["state"]["text"], row["device"], row["card"]) == ("Работа", "ПЛК пресса", True)


def test_alarm_bits_and_warnings_decode_to_russian_and_alarm_wins_the_state():
    decoded = ob099.decode({**WORDS, "W80": (1 << 3) | (1 << 6), "W83": (1 << 5) | (1 << 12), "W84": 1 << 13})
    assert decoded["state"] == {"text": "Авария", "tone": "danger"}
    assert [alarm["text"] for alarm in decoded["alarms"]] == [
        "Брак обжима (CFM)", "Авария W80.06", "Ресурс ножа обжима исчерпан", "Неисправность педали"]
    assert ob099.decode({**WORDS, "H60": 0, "W83": 1 << 12})["state"]["text"] == "Нужен сброс"
    empty = ob099.decode({})
    assert empty["state"]["text"] == "—" and empty["crimp"]["height_mm"] is None and empty["job"]["product"] is None
    assert empty["tools"][0]["used_pct"] is None


def test_snapshot_allow_list_is_by_area_and_address(press_api):
    sessions, clock, admin, press = press_api
    agent = _agent(admin)
    assert _report(agent, registers={"D5006": 2500}).status_code == 200
    assert _report(agent, seq=2, registers={"D7000": 1}).status_code == 422  # the servo teach table is not read
    assert _report(agent, seq=2, registers={"W60": 1}).status_code == 422  # command bits are not read
    assert _report(agent, seq=2, registers={"766": 1}).status_code == 422  # an ОБ.019 address
    assert _report(agent, seq=2, registers={"H60": 70000}).status_code == 422


def test_crimps_and_alarms_are_appended_once_and_summarised_for_the_shop_day(press_api):
    sessions, clock, admin, press = press_api
    agent = _agent(admin)
    early = _events(agent, [_crimp(0)])
    assert early.status_code == 409  # no snapshot has bound the agent yet
    assert _report(agent).status_code == 200
    crimps = [_crimp(0), _crimp(80, "NG", "2026-10-05T09:00:00+03:00", 611), _crimp(160, at="2026-10-05T06:30:00+00:00"),
              _crimp(240, at="2026-10-04T23:59:59+03:00")]
    alarms = [{"key": "2026-10-05 08:59:59|Abnormal terminal crimping", "at": "2026-10-05T08:59:59+03:00",
               "text": "Abnormal terminal crimping"},
              {"key": "2026-10-05 09:10:00|The safety guard is not closed", "at": "2026-10-05T09:10:00+03:00",
               "text": "The safety guard is not closed"},
              {"key": "2026-10-05 09:20:00|Abnormal terminal crimping", "at": "2026-10-05T09:20:00+03:00",
               "text": "Abnormal terminal crimping"}]
    answer = _events(agent, crimps, alarms)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "crimps": 4, "alarms": 3}
    assert _events(agent, crimps, alarms).json() == {"ok": True, "crimps": 0, "alarms": 0}  # a resend after a lost reply
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ob099.CrimpRecordRow)) == 4
    today = admin.get(f"{LIST}/{press}").json()["today"]
    assert (today["total"], today["ok"], today["ng"], today["ng_pct"]) == (3, 2, 1, 33.3)  # 04.10 23:59:59 is not today
    assert today["ng_reasons"] == [{"text": "Отклонение кривой усилия (CFM)", "count": 1}]
    assert today["last"]["result"] == "OK" and today["last"]["at"].startswith("2026-10-05T09:30:00")  # 06:30 UTC
    assert [(alarm["text"], alarm["count"]) for alarm in today["alarms"]] == [
        ("Брак обжима (CFM)", 2), ("Защитный кожух не закрыт", 1)]


def test_records_refuse_another_pc_another_token_a_naive_time_and_ob019(press_api):
    sessions, clock, admin, press = press_api
    agent = _agent(admin)
    assert _report(agent).status_code == 200
    assert _events(agent, [_crimp(0)], machine="guid-other").status_code == 403
    assert _events(_agent(admin, "Чужой"), [_crimp(0)]).status_code == 403
    assert _events(agent, [_crimp(0)], inventory="ОБ.019").status_code == 403
    assert _events(agent, [{**_crimp(0), "at": "2026-10-05T08:20:37"}]).status_code == 422  # no zone
    assert _events(agent, [{**_crimp(0), "result": "SIGMA"}]).status_code == 422
    assert _events(agent, [{**_crimp(0), "peak": -1}]).status_code == 422
    assert admin.post(EVENTS, json={"proto": 1, "inventory": "ОБ.099.1", "machine_id": "guid-82"}).status_code == 403


def test_stale_link_keeps_the_last_words_and_the_list_shows_no_link(press_api):
    sessions, clock, admin, press = press_api
    agent = _agent(admin)
    assert _report(agent).status_code == 200
    clock.now = clock.now + rules.STALE_AFTER + timedelta(seconds=1)
    [row] = [row for row in admin.get(LIST).json()["rows"] if row["inventory"] == "ОБ.099.1"]
    assert row["link"]["code"] == "stale" and row["state"]["text"] == "—"
    assert admin.get(f"{LIST}/{press}").json()["live"]["crimp"]["height_mm"] == 2.5


def test_the_agent_reads_and_sends_exactly_the_server_allow_list():
    source = (Path(__file__).parents[3] / "tools" / "machine-agent" / "ob099-agent.ps1").read_text(encoding="ascii")
    line = next(row for row in source.splitlines() if row.startswith("$script:Allowed = @("))
    sent = set(re.findall(r"'([DHW]\d+)'", line))
    for first, last in re.findall(r"D(\d+)\.\.D?(\d+)", line):
        sent |= {f"D{address}" for address in range(int(first), int(last) + 1)}
    assert sent == ob099.ALLOWED
    blocks = re.findall(r"^\s+@\('(\w+)', (\d+), (\d+)\),?\s+#", source, re.MULTILINE)
    covered = {f"{area}{int(first) + i}" for area, first, count in blocks for i in range(int(count))}
    assert len(blocks) == 5 and ob099.ALLOWED <= covered
