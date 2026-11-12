"""«Сканер с любого ПК» (owner 28.10.2026): the registry of scan agents, routing of a press to one PC, targeted claims,
the old agent without an id, the one-time install token and the files the server hands out."""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.domain import LEVEL_SEED, AccessMode, level_permissions
from app.modules.warehouse import agent_release, scan_agents
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from tests.access_levels import principal_as
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)
from tests.warehouse.test_scan_jobs import old_agent_alive

JOBS = "/api/stock/receipts/scan-jobs"
CLAIM = "/api/stock/scan/agent/claim"
BEAT = "/api/stock/scan/agent/heartbeat"
SCANNER = {"id": "{wia-1}", "name": "Pantum BM2300W"}


@pytest.fixture()
def stand(tokens_api):  # noqa: F811
    sessions, clock = tokens_api
    admin = _login("admin")
    agent = _bearer(_issue(admin, scopes=("labels:print", "scan:upload"), name="Агент ПК")["token"])
    return admin, agent, clock, sessions


def _beat(agent, machine_id, *, pc="PC", scanners=(SCANNER,), lan="", version="2.2.0"):
    body = {"machine_id": machine_id, "pc_name": pc, "version": version, "lan_ip": lan, "scanners": list(scanners)}
    response = TestClient(app).post(BEAT, json=body, headers=agent)
    assert response.status_code == 200, response.text
    return response.json()


def _press(admin, agent_id=None, scanner=SCANNER["id"]):
    body = {"agent_id": agent_id, "target_scanner": scanner} if agent_id else None
    response = admin.post(JOBS, json=body, headers=_csrf(admin))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("agent_id,scanner", [("missing", "{wia-1}"), ("m-1", "{gone}"), ("m-1", "")])
def test_invalid_explicit_choice_never_queues_on_another_scanner(stand, agent_id, scanner):
    admin, agent, _clock, _sessions = stand
    _beat(agent, "m-1")
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", scanners=(SECOND,))
    assert _press(admin, agent_id, scanner)["status"] == "choose"
    assert _claimed(agent, "m-1") is None
    assert _claimed(agent, "m-2") is None


def test_offline_or_pre_strict_agent_never_receives_a_new_scan(stand):
    admin, agent, clock, _sessions = stand
    _beat(agent, "m-1")
    clock.now += scan_agents.LIVE_FOR + timedelta(seconds=1)
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2")
    assert _press(admin, "m-1")["status"] == "choose"
    _beat(agent, "m-1", version="2.1.0")
    assert _press(admin, "m-1")["status"] == "choose"
    assert _claimed(agent, "m-1") is None
    _beat(agent, "m-1", version="2.2.0")
    assert _press(admin, "m-1")["status"] == "pending"


def test_even_one_matching_ip_requires_the_pc_and_scanner_choice(stand):
    admin, agent, clock, sessions = stand
    _beat(agent, "m-1")
    answer = _press(admin)
    assert answer["status"] == "choose"
    assert answer["agents"][0]["scanners"] == [SCANNER]
    assert scan_agents.route(_uow(sessions), browser_ip="testclient", remembered="", now=clock.now).kind == scan_agents.CHOOSE
    assert _claimed(agent, "m-1") is None


def _claim(agent, machine_id=None, host="PC"):
    body = {"host": host, **({"machine_id": machine_id} if machine_id else {})}
    job = TestClient(app).post(CLAIM, json=body, headers=agent).json()["job"]
    return job["id"] if job else None


def _uow(sessions):
    return SqlAlchemyWarehouseUnitOfWork(sessions)


# --- registry -------------------------------------------------------------------------------------------------------


def test_the_heartbeat_keeps_one_row_per_pc_with_its_scanners_and_addresses(stand):
    _admin, agent, clock, sessions = stand
    _beat(agent, "m-1", pc="SKLAD", lan="192.168.1.20")
    clock.now += timedelta(minutes=2)
    _beat(agent, "m-1", pc="SKLAD", lan="192.168.1.21", scanners=())
    with sessions() as session:
        rows = session.query(scan_agents.ScanAgentRow).all()
    assert [(row.machine_id, row.pc_name, row.ip_lan, row.scanners, row.legacy) for row in rows] == [
        ("m-1", "SKLAD", "192.168.1.21", "[]", False)], "a second report rewrites the row: no scanner now"
    assert rows[0].ip_seen == "testclient"


def test_the_heartbeat_tells_an_old_agent_where_the_new_file_is_and_a_current_one_nothing(stand, tmp_path, monkeypatch):
    _admin, agent, _clock, _sessions = stand
    (tmp_path / "agent.ps1").write_bytes("﻿$script:AgentVersion = '2.1.0'\n".encode("utf-8"))
    monkeypatch.setenv("ERP_AGENT_DIR", str(tmp_path))
    old = _beat(agent, "m-1", version="2.0.0")["update"]
    assert old["version"] == "2.1.0" and old["path"] == "/api/stock/scan/agent/files/agent.ps1"
    assert old["sha256"] == hashlib.sha256((tmp_path / "agent.ps1").read_bytes()).hexdigest()
    assert _beat(agent, "m-1", version="2.1.0")["update"] is None
    assert _beat(agent, "m-1", version="2.2.0")["update"] is None, "never a downgrade"


def test_only_the_agent_reports(stand):
    admin, _agent, _clock, _sessions = stand
    response = admin.post(BEAT, json={"machine_id": "m", "scanners": []}, headers=_csrf(admin))
    assert response.status_code == 403


# --- routing --------------------------------------------------------------------------------------------------------


def test_nobody_alive_is_no_agent_and_a_pc_without_a_scanner_is_named(stand):
    admin, agent, clock, _sessions = stand
    assert _press(admin) == {"status": "no_agent", "idle": []}
    _beat(agent, "m-1", pc="BUHGALTER", scanners=())
    assert _press(admin) == {"status": "no_agent", "idle": ["BUHGALTER"]}
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", pc="SKLAD")
    clock.now += scan_agents.LIVE_FOR + timedelta(seconds=1)
    answer = _press(admin)
    assert answer["status"] == "choose" and answer["agents"][0]["live"] is False, "offline devices remain visible"


def test_addresses_and_legacy_preferences_never_replace_an_explicit_pair(stand):
    admin, agent, clock, sessions = stand
    _beat(agent, "m-1", pc="SKLAD", lan="192.168.1.20")
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", pc="OFFICE", lan="192.168.1.30")
    with sessions() as session:  # as Caddy sees them: different public addresses, the LAN ones as reported
        session.get(scan_agents.ScanAgentRow, "m-1").ip_seen = "203.0.113.5"
        session.get(scan_agents.ScanAgentRow, "m-2").ip_seen = "203.0.113.5"
        session.commit()

    def route(ip, remembered=""):
        return scan_agents.route(_uow(sessions), browser_ip=ip, remembered=remembered, now=clock.now)

    by_lan = route("192.168.1.30")
    assert (by_lan.kind, by_lan.agent_id) == (scan_agents.CHOOSE, None)
    both = route("203.0.113.5")
    assert both.kind == scan_agents.CHOOSE and len(both.agents) == 2, "one shop address, two PCs: choose"
    assert route("203.0.113.5", "m-1").kind == scan_agents.CHOOSE, "a PC id alone is incomplete"
    assert route("198.51.100.7", "m-1").kind == scan_agents.CHOOSE
    other = route("198.51.100.7")
    assert other.kind == scan_agents.CHOOSE and [a.pc for a in other.agents] == ["OFFICE", "SKLAD"]
    assert route("198.51.100.7", "gone").kind == scan_agents.CHOOSE, "a remembered id nobody reports is ignored"
    clock.now += scan_agents.LIVE_FOR + timedelta(seconds=1)
    offline = route("192.168.1.30")
    assert offline.kind == scan_agents.CHOOSE and all(not item.live for item in offline.agents)
    old_agent_alive(sessions, clock)
    assert route("192.168.1.30").kind == scan_agents.CHOOSE, "legacy claims never enable untargeted creation"


def test_a_job_is_made_for_the_chosen_agent_and_only_that_agent_claims_it(stand):
    admin, agent, _clock, _sessions = stand
    _beat(agent, "m-1", pc="SKLAD")
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", pc="OFFICE")
    picked = _press(admin)
    assert picked["status"] == "choose" and {a["id"] for a in picked["agents"]} == {"m-1", "m-2"}
    assert picked["agents"][0]["scanners"] == [SCANNER] and picked["agents"][0]["pc"] == "OFFICE"
    job = _press(admin, "m-1")
    assert job["status"] == "pending"
    assert _claim(agent, "m-2") is None, "another PC's agent does not take it"
    assert _claim(agent) is None, "an old agent does not take a targeted job either"
    assert _claim(agent, "m-1") == job["id"]


def test_only_a_legacy_agent_never_enables_a_new_untargeted_job(stand):
    admin, agent, clock, sessions = stand
    assert _claim(agent, host="SKLAD") is None, "the first claim of an old agent registers it"
    assert _press(admin)["status"] == "no_agent"
    assert _claim(agent, host="SKLAD") is None
    with sessions() as session:
        legacy = session.query(scan_agents.ScanAgentRow).all()
    assert [(row.machine_id, row.legacy) for row in legacy] == [("legacy:SKLAD", True)]
    assert _press(admin)["status"] == "no_agent"
    assert _claim(agent, "m-new", host="NEW") is None


def test_a_claim_keeps_the_agent_alive_between_heartbeats(stand):
    admin, agent, clock, _sessions = stand
    _beat(agent, "m-1")
    for _ in range(4):
        clock.now += timedelta(minutes=1)
        assert _claim(agent, "m-1") is None
    assert _press(admin, "m-1")["status"] == "pending", "four minutes of claims, no heartbeat: still alive"


# --- install --------------------------------------------------------------------------------------------------------


def test_the_install_token_is_issued_by_whoever_may_scan_and_spent_once(stand):
    admin, _agent, clock, sessions = stand
    operator = _login("operator")
    assert operator.post("/api/stock/scan/agent/install-token", headers=_csrf(operator)).status_code == 403

    issued = admin.post("/api/stock/scan/agent/install-token", headers=_csrf(admin))
    assert issued.status_code == 200, issued.text
    command = issued.json()["command"]
    secret = command.split("-InstallToken '")[1].split("'")[0]
    assert secret.startswith("frxi_") and "https://testserver/api/stock/scan/agent/install.ps1" in command
    assert command.startswith("powershell -NoProfile -ExecutionPolicy Bypass -Command")
    with sessions() as session:
        stored = session.query(scan_agents.ScanAgentInstallRow).one()
    assert secret not in stored.token_hash, "only the hash is kept"

    body = {"install_token": secret, "machine_id": "m-9", "pc_name": "NEW PC"}
    first = TestClient(app).post("/api/stock/scan/agent/enroll", json=body)
    assert first.status_code == 200, first.text
    token = first.json()["token"]
    assert token.startswith("erp_") and first.json()["server"] == "https://testserver"
    # Enrollment is already bound: neither heartbeat nor a VNA claim may pick another PC.
    assert TestClient(app).post(BEAT, json={"machine_id": "other"}, headers=_bearer(token)).status_code == 403
    assert TestClient(app).post(CLAIM, json={"machine_id": "other", "kinds": ["vna"]}, headers=_bearer(token)).status_code == 403
    from app.modules.warehouse.scan_jobs import AgentTokenRow
    with sessions() as session:
        assert session.query(AgentTokenRow).one().machine_id == "m-9"
    # The enrolled token works as the agent's: it reports and claims, and the admin's token list shows it.
    assert TestClient(app).post(BEAT, json={"machine_id": "m-9", "scanners": []}, headers=_bearer(token)).status_code == 200
    listed = admin.get("/api/auth/api-tokens").json()["tokens"]
    assert any(t["name"] == "Агент ПК NEW PC" and set(t["scopes"]) == {"labels:print", "scan:upload"} for t in listed)

    second = TestClient(app).post("/api/stock/scan/agent/enroll", json=body)
    assert second.status_code == 403, "one-time"
    assert TestClient(app).post("/api/stock/scan/agent/enroll", json={**body, "install_token": "frxi_nope"}).status_code == 403


def test_an_install_token_dies_in_an_hour(stand):
    admin, _agent, clock, _sessions = stand
    secret = admin.post("/api/stock/scan/agent/install-token", headers=_csrf(admin)).json()["command"] \
        .split("-InstallToken '")[1].split("'")[0]
    clock.now += scan_agents.INSTALL_TTL + timedelta(seconds=1)
    refused = TestClient(app).post("/api/stock/scan/agent/enroll", json={"install_token": secret, "machine_id": "m"})
    assert refused.status_code == 403


def test_the_server_hands_out_the_agent_files_without_a_login(stand, tmp_path, monkeypatch):
    (tmp_path / "agent.ps1").write_bytes("﻿$script:AgentVersion = '3.0.0'\nWrite-Host 'агент'\n".encode("utf-8"))
    (tmp_path / "install.ps1").write_bytes("﻿[CmdletBinding()]\nparam()\n".encode("utf-8"))
    monkeypatch.setenv("ERP_AGENT_DIR", str(tmp_path))
    client = TestClient(app)
    manifest = client.get("/api/stock/scan/agent/manifest").json()
    served = client.get("/api/stock/scan/agent/files/agent.ps1").content
    assert manifest["version"] == "3.0.0" and manifest["sha256"] == hashlib.sha256(served).hexdigest()
    assert served == (tmp_path / "agent.ps1").read_bytes(), "byte for byte, BOM included: the agent stores it as is"
    installer = client.get("/api/stock/scan/agent/install.ps1")
    assert installer.content.startswith(b"[CmdletBinding()]"), "no BOM: it runs from memory"
    assert agent_release.read_file("secrets.txt") is None
    monkeypatch.setenv("ERP_AGENT_DIR", str(tmp_path / "missing"))
    assert client.get("/api/stock/scan/agent/manifest").status_code == 404


def test_the_repo_agent_carries_a_version_the_server_can_read():
    release = agent_release.release()
    assert release is not None and release.version, "tools/print-agent/agent.ps1 must hold $script:AgentVersion = 'x.y.z'"
    assert Path(agent_release.agent_dir(), "install.ps1").exists()


def test_the_strict_release_manifest_and_heartbeat_use_the_same_served_script(stand):
    admin, agent, _clock, _sessions = stand
    served = admin.get("/api/stock/scan/agent/files/agent.ps1").content
    manifest = admin.get("/api/stock/scan/agent/manifest").json()
    assert manifest["version"] == "2.5.0"
    assert manifest["sha256"] == hashlib.sha256(served).hexdigest()
    update = _beat(agent, "m-old", version="2.1.0")["update"]
    assert update["version"] == "2.5.0" and update["sha256"] == manifest["sha256"]
    assert _press(admin, "m-old")["status"] == "choose", "update offered is not update installed"
    assert _beat(agent, "m-old", version="2.5.0")["update"] is None
    assert _press(admin, "m-old")["status"] == "pending"


def test_duplicate_scanner_names_require_the_actual_id():
    import json

    raw = json.dumps([SCANNER, {"id": "{wia-3}", "name": SCANNER["name"]}])
    assert scan_agents.matching_scanner(raw, SCANNER["name"]) is None
    assert scan_agents.matching_scanner(raw, "{wia-3}") == "{wia-3}"


def test_the_install_command_names_the_server_over_https_unless_a_local_address():
    # 28.10.2026: TLS ends at Tailscale Funnel, the request reached the API as http — the command must say https
    from starlette.requests import Request
    from app.workflows.scan_agent_install import _base

    def base(url):
        scheme, rest = url.split("://")
        host = rest.split("/")[0]
        return _base(Request({"type": "http", "scheme": scheme, "server": (host.split(":")[0], 80), "path": "/",
                              "headers": [(b"host", host.encode())], "query_string": b"", "root_path": ""}))
    assert base("http://erp-server.tail811f20.ts.net/") == "https://erp-server.tail811f20.ts.net"
    assert base("http://192.168.1.85:8080/") == "http://192.168.1.85:8080"
    assert base("http://localhost:8021/") == "http://localhost:8021"


# --- «Сканирование документов» and the person's own scanner (owner 28.10.2026) ---------------------------------------

SECOND = {"id": "{wia-2}", "name": "Canon LiDE"}


def _claimed(agent, machine_id=None, host="PC"):
    body = {"host": host, **({"machine_id": machine_id} if machine_id else {})}
    return TestClient(app).post(CLAIM, json=body, headers=agent).json()["job"]


@pytest.fixture()
def person():
    """A person of a level with the plan's matrix row (LEVEL_SEED) or the given one, through the session override."""

    def use(level: str, rows: dict | None = None) -> TestClient:
        row = LEVEL_SEED[level] if rows is None else rows
        permissions = {key: mode.value for key, mode in level_permissions(level, row, {}).items()}
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=principal_as(level).actor, email=f"{level}@example.test", roles=frozenset({level}),
            permissions=permissions)
        return TestClient(app)

    yield use
    app.dependency_overrides.pop(authenticated_principal, None)


def test_the_storekeeper_scans_a_level_without_the_scan_right_is_refused(stand, person):
    _admin, agent, _clock, _sessions = stand
    _beat(agent, "m-1", pc="SKLAD")
    storekeeper = person("storekeeper")
    assert storekeeper.post(JOBS, json={"agent_id": "m-1", "target_scanner": SCANNER["id"]}).json()["status"] == "pending"
    assert storekeeper.get("/api/stock/scan/agents").status_code == 200
    assert storekeeper.post("/api/stock/scan/agent/install-token").status_code == 200, "whoever scans installs"

    foreman = person("foreman", {**LEVEL_SEED["foreman"], "warehouse": AccessMode.EDIT})
    for method, path in (("POST", JOBS), ("GET", f"{JOBS}/{'0' * 32}"), ("DELETE", f"{JOBS}/{'0' * 32}"),
                         ("GET", "/api/stock/scan/agents"), ("POST", "/api/stock/scan/agent/install-token")):
        response = foreman.request(method, path, json={})
        assert response.status_code == 403, (method, path, response.text)
        assert "Сканирование документов" in response.json()["detail"]
    # «Файл…» and the page merge are no scanning: the warehouse right alone lets them through (the request is then
    # refused for its body, not for the right).
    for path in ("/api/stock/receipts/scan", "/api/stock/receipts/scan-merge"):
        assert foreman.post(path, json={}).status_code != 403, path
    scan_only = person("operator", {"scan": AccessMode.EDIT})
    assert scan_only.post(JOBS, json={"agent_id": "m-1"}).status_code == 200, "the section alone opens it"
    assert person("operator", {"scan": AccessMode.VIEW}).post(JOBS, json={}).status_code == 403, "view is not enough"


def test_the_agents_list_carries_each_pcs_scanners(stand):
    admin, agent, clock, _sessions = stand
    _beat(agent, "m-1", pc="SKLAD", scanners=(SCANNER, SECOND), version="2.1.0")
    clock.now += scan_agents.LIVE_FOR + timedelta(seconds=1)
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", pc="OFFICE", scanners=({"name": "Сканер без id"},))
    _claimed(agent, host="OLD")  # an old agent: it cannot be targeted, so it is not listed
    agents = admin.get("/api/stock/scan/agents").json()["agents"]
    assert [(a["id"], a["pc"], a["live"], a["scanners"]) for a in agents] == [
        ("m-2", "OFFICE", True, [{"id": "", "name": "Сканер без id"}]),
        ("m-1", "SKLAD", False, [SCANNER, SECOND]),
    ], "live first; every scanner as {id, name}"
    assert agents[1]["version"] == "2.1.0" and agents[1]["last_seen"]


def test_a_job_for_the_chosen_scanner_hands_it_to_the_agent(stand):
    admin, agent, _clock, _sessions = stand
    _beat(agent, "m-1", pc="SKLAD", scanners=(SCANNER, SECOND))

    def press(**body):
        response = admin.post(JOBS, json=body, headers=_csrf(admin))
        assert response.status_code == 200, response.text
        return response.json()

    by_id = press(agent_id="m-1", target_scanner="{wia-2}")
    assert _claimed(agent, "m-1") == {"id": by_id["id"], "target_scanner": "{wia-2}"}
    press(agent_id="m-1", target_scanner="Canon LiDE")
    assert _claimed(agent, "m-1")["target_scanner"] == "{wia-2}", "a name becomes the id the agent reported"
    assert press(agent_id="m-1", target_scanner="{gone}")["status"] == "choose"
    assert _claimed(agent, "m-1") is None
    assert press(agent_id="m-1")["status"] == "choose"
    assert _claimed(agent, "m-1") is None


def test_the_chosen_scanner_wins_over_the_browsers_address(stand):
    admin, agent, clock, sessions = stand
    _beat(agent, "m-1", pc="SKLAD", lan="192.168.1.20")
    _beat(_bearer(_issue(admin, scopes=("scan:upload",))["token"]), "m-2", pc="OFFICE", lan="192.168.1.30", scanners=(SECOND,))

    def route(remembered="", scanner=""):
        return scan_agents.route(_uow(sessions), browser_ip="192.168.1.20", remembered=remembered, now=clock.now,
                                 scanner=scanner)

    assert route().kind == scan_agents.CHOOSE, "no choice requires selecting both identities"
    chosen = route("m-2", "{wia-2}")
    assert (chosen.kind, chosen.agent_id, chosen.scanner) == (scan_agents.AGENT, "m-2", "{wia-2}")
    assert route("m-2", "{gone}").kind == scan_agents.CHOOSE
    assert route("m-1", "{wia-2}").kind == scan_agents.CHOOSE


def test_a_scanner_without_a_pc_never_creates_an_untargeted_job(stand):
    admin, agent, _clock, _sessions = stand
    _claimed(agent, host="SKLAD")
    job = admin.post(JOBS, json={"target_scanner": "{wia-2}"}, headers=_csrf(admin)).json()
    assert job["status"] == "no_agent"
    assert _claimed(agent, host="SKLAD") is None
