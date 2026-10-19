"""Стенд, опция warehouse.tsd: /api/devices — вне стенда и с выключенной опцией 404, переключатель сети — только
администратору, запись от просмотра — 403 раньше 404, QR только на свои адреса, журналы сканера и ошибок браузера."""

from __future__ import annotations

import io
import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared import device_gate, stage_options
from app.shared.http_auth import authenticated_principal
from app.workflows import devices_api
from tests.access_levels import principal_as
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture

ADDRESSES = [device_gate.Address("192.168.1.124", "сеть офиса"), device_gate.Address("100.81.148.15", "Tailscale")]


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.stdin = io.BytesIO()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, _timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class Launches:
    def __init__(self):
        self.calls: list[device_gate.Launch] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, launch, log):
        self.calls.append(launch)
        self.processes.append(FakeProcess())
        return self.processes[-1]

    def alive(self, _port) -> bool:
        return bool(self.processes) and self.processes[-1].poll() is None


@pytest.fixture()
def launches(monkeypatch, tmp_path):
    record = Launches()

    def certificate(directory, host, ips):
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("server.crt", "server.key"):
            if not (directory / name).exists():
                (directory / name).write_text(name, encoding="utf-8")
        return directory / "server.crt", directory / "server.key"

    supervisor = device_gate.GateSupervisor(launch=record, answers=record.alive, certificate=certificate)
    supervisor.upstream_port = 8030
    monkeypatch.setattr(device_gate, "SUPERVISOR", supervisor)
    monkeypatch.setattr(device_gate, "local_addresses", lambda: ADDRESSES)
    monkeypatch.setattr(devices_api, "SCANNER_LIMIT", devices_api._Limiter(devices_api.SCANNER_POSTS_PER_HOUR))
    monkeypatch.setattr(devices_api, "CLIENT_ERROR_LIMIT", devices_api._Limiter(devices_api.CLIENT_ERRORS_PER_HOUR))
    monkeypatch.delenv(device_gate.GATE_PORT_ENV, raising=False)
    return record


@pytest.fixture()
def stage(monkeypatch, tmp_path, launches):
    path = tmp_path / "options.json"
    monkeypatch.setenv(stage_options.ENV, str(path))
    path.write_text(json.dumps({"warehouse.tsd": True}), encoding="utf-8")
    return lambda **options: path.write_text(json.dumps(options), encoding="utf-8")


@pytest.fixture()
def client():
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(authenticated_principal, None)


def as_(principal):
    app.dependency_overrides[authenticated_principal] = lambda: principal


ADMIN = principal_as("admin")
STOREKEEPER = principal_as("foreman", {"warehouse": "edit"}, name="Кладовщик Петров")
VIEWER = principal_as("foreman", {"warehouse": "view"})


def test_outside_the_stage_every_route_says_so(monkeypatch, client, launches):
    monkeypatch.delenv(stage_options.ENV, raising=False)
    as_(ADMIN)
    for method, path in (("GET", "/api/devices/status"), ("GET", "/api/devices/entry"), ("PUT", "/api/devices/network"),
                         ("GET", "/api/devices/scanner-log"), ("POST", "/api/devices/client-error"), ("GET", "/tsd")):
        response = client.request(method, path, json={"enabled": True})
        assert response.status_code == 404, (method, path)
        assert response.json()["detail"] == "Вход с устройств есть только на стенде.", (method, path)
    assert launches.calls == []


def test_option_off_hides_everything_but_a_viewer_write_is_403_first(stage, client):
    stage(**{"warehouse.tsd": False})
    as_(ADMIN)
    assert client.get("/api/devices/status").json()["detail"] == "Опция выключена"
    assert client.get("/api/devices/entry").status_code == 404
    assert client.get("/tsd", follow_redirects=False).status_code == 404
    as_(VIEWER)
    assert client.post("/api/devices/scanner-log", json={"lines": ["x"]}).status_code == 403
    assert client.put("/api/devices/network", json={"enabled": True}).status_code == 403


def test_entry_and_the_short_address_open_with_the_option(stage, client):
    assert client.get("/api/devices/entry").json() == {"tsd": True}  # no sign-in needed
    response = client.get("/tsd", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/?section=tsd"


def test_only_an_administrator_lets_devices_in_and_the_gate_follows_the_switch(stage, client, launches, tmp_path):
    as_(STOREKEEPER)
    status = client.get("/api/devices/status").json()
    assert status["enabled"] is False and status["listening"] is False and status["addresses"] == []
    assert client.put("/api/devices/network", json={"enabled": True}).status_code == 403
    as_(ADMIN)
    status = client.put("/api/devices/network", json={"enabled": True}).json()
    assert json.loads((tmp_path / "devices.json").read_text(encoding="utf-8")) == {"network": True}
    assert [(call.port, call.upstream_port, bool(call.cert)) for call in launches.calls] == [(8031, 8030, True)]
    assert status["listening"] is True and status["https"] is True and status["reason"] == ""
    assert status["addresses"] == [
        {"url": "http://192.168.1.124:8031", "label": "сеть офиса"},
        {"url": "http://100.81.148.15:8031", "label": "Tailscale"},
    ]
    device_gate.SUPERVISOR.apply()  # the supervisor's next round: nothing to change
    assert len(launches.calls) == 1
    status = client.put("/api/devices/network", json={"enabled": False}).json()
    assert status == {**status, "enabled": False, "listening": False, "addresses": []}
    assert launches.processes[0].returncode == 0


def test_switching_the_option_off_stops_a_running_gate(stage, client, launches):
    as_(ADMIN)
    client.put("/api/devices/network", json={"enabled": True})
    stage(**{"warehouse.tsd": False})
    device_gate.SUPERVISOR.apply()
    assert launches.processes[0].returncode == 0
    stage(**{"warehouse.tsd": True})
    device_gate.SUPERVISOR.apply()  # the switch is still on: back by itself
    assert len(launches.calls) == 2 and launches.processes[1].poll() is None


def test_a_gate_that_died_is_started_again_and_a_dead_start_gives_its_reason(stage, client, launches, tmp_path):
    as_(ADMIN)
    client.put("/api/devices/network", json={"enabled": True})
    launches.processes[0].returncode = 1
    device_gate.SUPERVISOR.apply()
    assert len(launches.calls) == 2

    def dead(launch, log):
        log.write_text("19.10.2026 10:00:00 порт 8031 занят другой программой: [WinError 10048]\n", encoding="utf-8")
        process = FakeProcess()
        process.returncode = 2
        return process

    device_gate.SUPERVISOR._launch = dead
    launches.processes[-1].returncode = 1
    device_gate.SUPERVISOR.apply()
    status = client.get("/api/devices/status").json()
    assert status["listening"] is False and "порт 8031 занят" in status["reason"]


def test_without_openssl_http_still_opens_and_the_status_says_why(stage, client, launches):
    def no_openssl(*_args):
        raise device_gate.stage_gate.CertificateUnavailable("openssl не найден (ставится с Git для Windows)")

    device_gate.SUPERVISOR._certificate = no_openssl
    as_(ADMIN)
    status = client.put("/api/devices/network", json={"enabled": True}).json()
    assert status["listening"] is True and status["https"] is False and "openssl не найден" in status["reason"]
    assert launches.calls[0].cert is None


def test_qr_only_for_the_gate_addresses(stage, client):
    as_(VIEWER)
    response = client.get("/api/devices/qr.svg", params={"url": "http://192.168.1.124:8031/tsd"})
    assert response.status_code == 200 and response.headers["content-type"] == "image/svg+xml"
    assert response.content.startswith(b"<svg")
    for url in ("http://evil.example:8031/tsd", "http://192.168.1.124:8030/tsd", "javascript:alert(1)",
                "http://192.168.1.124:8031/<script>", "http://user:pw@192.168.1.124:8031/"):
        assert client.get("/api/devices/qr.svg", params={"url": url}).status_code == 422, url


def test_scanner_check_lines_land_in_the_stage_folder(stage, client, tmp_path):
    as_(STOREKEEPER)
    response = client.post("/api/devices/scanner-log", json={"lines": ["   120ms keydown key=\"F\"", "  140ms keydown key=\"Enter\""]},
                           headers={"User-Agent": "Mozilla/5.0 (Linux; Android 8.1.0; AUTOID Lim) Chrome/70.0.3538.110"})
    assert response.json() == {"ok": True}
    text = (tmp_path / "scanner-log.txt").read_text(encoding="utf-8")
    assert "testclient Кладовщик Петров · Mozilla/5.0" in text and 'key="Enter"' in text
    as_(VIEWER)
    lines = client.get("/api/devices/scanner-log").json()["lines"]
    assert lines[-1].endswith('key="Enter"') and lines[0].startswith("--- ")
    assert client.post("/api/devices/scanner-log", json={"lines": ["x"] * 61}).status_code == 403  # viewer first


def test_scanner_log_refuses_oversized_batches(stage, client):
    as_(STOREKEEPER)
    assert client.post("/api/devices/scanner-log", json={"lines": ["x"] * 61}).status_code == 422
    assert client.post("/api/devices/scanner-log", json={"lines": ["x" * 301]}).status_code == 422


def test_client_errors_need_no_sign_in_and_are_capped_per_address(stage, client, tmp_path):
    body = {"message": "TypeError: Object.fromEntries is not a function", "url": "http://192.168.1.124:8031/?section=tsd"}
    for _ in range(devices_api.CLIENT_ERRORS_PER_HOUR):
        assert client.post("/api/devices/client-error", json=body).json() == {"ok": True}
    assert client.post("/api/devices/client-error", json=body).json() == {"ok": False}
    as_(VIEWER)
    errors = client.get("/api/devices/client-errors").json()["errors"]
    assert len(errors) == devices_api.CLIENT_ERRORS_PER_HOUR
    assert errors[0]["message"].startswith("TypeError") and errors[0]["ip"] == "testclient"


def test_logs_keep_the_newer_half_when_they_grow(stage, monkeypatch, tmp_path):
    monkeypatch.setattr(device_gate, "SCANNER_LOG_LIMIT", 2000)
    for number in range(100):
        device_gate.append_scanner_log("10.0.0.5", "Петров", "UA", [f"line {number:03d} " + "x" * 20])
    text = (tmp_path / "scanner-log.txt").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 2000 and "line 099" in text and "line 000" not in text


def test_the_scan_opens_with_the_tsd_option_alone(warehouse_api, stage, client):  # noqa: F811
    stage(**{"warehouse.tsd": True})
    response = client.get("/api/stock/scan", params={"code": "FRX.26.0000001"})
    assert response.status_code == 200 and response.json()["kind"] == "lot"
    stage(**{})
    assert client.get("/api/stock/scan", params={"code": "FRX.26.0000001"}).status_code == 404


def test_api_port_comes_from_the_environment_or_the_uvicorn_command_line(monkeypatch):
    monkeypatch.delenv(device_gate.API_PORT_ENV, raising=False)
    assert device_gate.api_port(["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8042"]) == 8042
    assert device_gate.api_port(["uvicorn", "app.main:app", "--port=8050"]) == 8050
    assert device_gate.api_port(["pytest"]) == 8030
    monkeypatch.setenv(device_gate.API_PORT_ENV, "8099")
    assert device_gate.api_port(["uvicorn", "--port", "8042"]) == 8099


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_real_gate_process_starts_and_stops_with_the_switch(monkeypatch, tmp_path):
    """The supervisor with the real launcher: the venv's python starts the gate; «не пускать» frees the port."""
    monkeypatch.setenv(stage_options.ENV, str(tmp_path / "options.json"))
    (tmp_path / "options.json").write_text(json.dumps({"warehouse.tsd": True}), encoding="utf-8")
    port = _free_port()
    monkeypatch.setenv(device_gate.GATE_PORT_ENV, str(port))
    supervisor = device_gate.GateSupervisor(certificate=lambda *_: (_ for _ in ()).throw(
        device_gate.stage_gate.CertificateUnavailable("без сертификата в тесте")))
    device_gate.set_network(True)
    try:
        supervisor.apply()
        assert supervisor.listening(), (tmp_path / "device-gate.log").read_text(encoding="utf-8", errors="replace")
    finally:
        device_gate.set_network(False)
        supervisor.apply()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and device_gate.port_answers(port, 0.2):
        time.sleep(0.1)
    assert not device_gate.port_answers(port, 0.2)
