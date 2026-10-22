"""/api/devices: ТСД открывает <адрес ERP>/tsd и входит по PIN; QR только на адрес того же хоста, запись от просмотра —
403, журналы «Проверки сканера» и ошибок браузера лежат в ERP_DEVICE_LOG_DIR."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared import device_logs
from app.shared.http_auth import authenticated_principal
from app.workflows import devices_api
from tests.access_levels import principal_as


@pytest.fixture()
def logs(monkeypatch, tmp_path):
    monkeypatch.setenv(device_logs.LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(devices_api, "SCANNER_LIMIT", devices_api._Limiter(devices_api.SCANNER_POSTS_PER_HOUR))
    monkeypatch.setattr(devices_api, "CLIENT_ERROR_LIMIT", devices_api._Limiter(devices_api.CLIENT_ERRORS_PER_HOUR))
    return tmp_path


@pytest.fixture()
def client():
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(authenticated_principal, None)


def as_(principal):
    app.dependency_overrides[authenticated_principal] = lambda: principal


STOREKEEPER = principal_as("foreman", {"warehouse": "edit"}, name="Кладовщик Петров")
VIEWER = principal_as("foreman", {"warehouse": "view"})


def test_entry_and_the_short_address_are_always_open(client):
    assert client.get("/api/devices/entry").json() == {"tsd": True}  # no sign-in needed
    response = client.get("/tsd", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/?section=tsd"


def test_the_gate_routes_are_gone(client):
    as_(principal_as("admin"))
    assert client.get("/api/devices/status").status_code == 404
    assert client.put("/api/devices/network", json={"enabled": True}).status_code in {404, 405}


def test_qr_only_for_the_host_the_request_came_to(client):
    as_(VIEWER)
    own = "https://erp.example.test"
    response = client.get("/api/devices/qr.svg", params={"url": f"{own}/tsd"}, headers={"Host": "erp.example.test"})
    assert response.status_code == 200 and response.headers["content-type"] == "image/svg+xml"
    assert response.content.startswith(b"<svg")
    behind_proxy = client.get("/api/devices/qr.svg", params={"url": f"{own}/tsd"},
                              headers={"Host": "api:8000", "X-Forwarded-Host": "erp.example.test"})
    assert behind_proxy.status_code == 200
    for url in ("https://evil.example/tsd", "https://erp.example.test:8443/tsd", "javascript:alert(1)",
                "https://erp.example.test/<script>", "https://user:pw@erp.example.test/"):
        answer = client.get("/api/devices/qr.svg", params={"url": url}, headers={"Host": "erp.example.test"})
        assert answer.status_code == 422, url


def test_scanner_check_lines_land_in_the_log_folder(logs, client):
    as_(STOREKEEPER)
    response = client.post("/api/devices/scanner-log", json={"lines": ["   120ms keydown key=\"F\"", "  140ms keydown key=\"Enter\""]},
                           headers={"User-Agent": "Mozilla/5.0 (Linux; Android 8.1.0; AUTOID Lim) Chrome/70.0.3538.110"})
    assert response.json() == {"ok": True}
    text = (logs / "scanner-log.txt").read_text(encoding="utf-8")
    assert "testclient Кладовщик Петров · Mozilla/5.0" in text and 'key="Enter"' in text
    as_(VIEWER)
    lines = client.get("/api/devices/scanner-log").json()["lines"]
    assert lines[-1].endswith('key="Enter"') and lines[0].startswith("--- ")
    assert client.post("/api/devices/scanner-log", json={"lines": ["x"]}).status_code == 403  # a viewer cannot write


def test_scanner_log_refuses_oversized_batches(logs, client):
    as_(STOREKEEPER)
    assert client.post("/api/devices/scanner-log", json={"lines": ["x"] * 61}).status_code == 422
    assert client.post("/api/devices/scanner-log", json={"lines": ["x" * 301]}).status_code == 422


def test_client_errors_need_no_sign_in_and_are_capped_per_address(logs, client):
    body = {"message": "TypeError: Object.fromEntries is not a function", "url": "https://erp.example.test/?section=tsd"}
    for _ in range(devices_api.CLIENT_ERRORS_PER_HOUR):
        assert client.post("/api/devices/client-error", json=body).json() == {"ok": True}
    assert client.post("/api/devices/client-error", json=body).json() == {"ok": False}
    as_(VIEWER)
    errors = client.get("/api/devices/client-errors").json()["errors"]
    assert len(errors) == devices_api.CLIENT_ERRORS_PER_HOUR
    assert errors[0]["message"].startswith("TypeError") and errors[0]["ip"] == "testclient"


def test_logs_keep_the_newer_half_when_they_grow(logs, monkeypatch):
    monkeypatch.setattr(device_logs, "SCANNER_LOG_LIMIT", 2000)
    for number in range(100):
        device_logs.append_scanner_log("10.0.0.5", "Петров", "UA", [f"line {number:03d} " + "x" * 20])
    text = (logs / "scanner-log.txt").read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= 2000 and "line 099" in text and "line 000" not in text


def test_without_the_folder_variable_logs_go_to_the_system_temp_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(device_logs.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(device_logs.tempfile, "gettempdir", lambda: str(tmp_path))
    assert device_logs.scanner_log_path() == tmp_path / "erp-devices" / "scanner-log.txt"

