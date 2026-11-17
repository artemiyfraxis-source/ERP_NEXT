"""Exercise the production formatter through actual local Uvicorn HTTP requests."""

import http.client
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from app.shared import device_logs
from app.shared.safe_logging import PassportSafeFormatter, redact_passport_urls


BACKEND = Path(__file__).resolve().parents[1]
SIGNATURE = "S" * 32
LOCATOR = "a" * 32 + "." + SIGNATURE


@pytest.mark.parametrize("url", [
    "/p/" + LOCATOR,
    "https://erp.example.test/p/" + LOCATOR + ".pdf?copy=" + SIGNATURE,
    "/p/invalid." + SIGNATURE,
    "/p%2F" + LOCATOR,
    "/%70/" + LOCATOR,
    "%2F%70%2F" + LOCATOR,
])
def test_redacts_public_locator_and_query(url):
    assert SIGNATURE not in redact_passport_urls("GET " + url + " HTTP/1.1")
    assert redact_passport_urls("GET /api/health HTTP/1.1") == "GET /api/health HTTP/1.1"


def test_exception_and_interpolated_url_are_redacted():
    try:
        raise ValueError("cannot render /p/" + LOCATOR)
    except ValueError:
        record = logging.LogRecord("app", logging.ERROR, __file__, 1,
                                   "request %s failed", ("/p/" + LOCATOR,), sys.exc_info())
    output = PassportSafeFormatter().format(record)
    assert SIGNATURE not in output
    assert "ValueError" in output and "[redacted]" in output


def test_device_file_logs_redact_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_DEVICE_LOG_DIR", str(tmp_path))
    device_logs.append_client_error("127.0.0.1", "/p/" + LOCATOR, "failed /p/" + LOCATOR, "browser")
    device_logs.append_scanner_log("127.0.0.1", "preview", "scanner", ["/p/" + LOCATOR])
    for path in (device_logs.client_errors_path(), device_logs.scanner_log_path()):
        output = path.read_text(encoding="utf-8")
        assert SIGNATURE not in output and "[redacted]" in output


def test_uvicorn_requests_do_not_persist_public_signature(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = '''
import uvicorn
async def app(scope, receive, send):
    path = scope["path"]
    if path.endswith("/error"):
        raise RuntimeError("Rendering failed for " + path)
    status = 404 if path.endswith("/missing") else 200
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})
uvicorn.run(app, host="127.0.0.1", port=PORT, lifespan="off", proxy_headers=False,
            log_config="logging_config.json")
'''.replace("PORT", str(port))
    # Files avoid pipe-buffer deadlocks even when a test failure produces a traceback.
    output_path = tmp_path / "uvicorn.log"
    with output_path.open("w", encoding="utf-8") as output:
        # A Windows venv executable is a redirector: terminating it can leave the server child alive.
        executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        process = subprocess.Popen([executable, "-c", script], cwd=BACKEND, env=environment,
                                   stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        pytest.fail("Local Uvicorn did not start: " + output_path.read_text(encoding="utf-8"))
                    time.sleep(.05)
            for suffix, status in (("", 200), (".pdf", 200), ("/missing", 404), ("/error", 500)):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request("GET", "/p/" + LOCATOR + suffix + "?copy=" + SIGNATURE)
                    response = connection.getresponse()
                    assert response.status == status
                    response.read()
                finally:
                    connection.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    captured = output_path.read_text(encoding="utf-8")
    assert SIGNATURE not in captured
    assert captured.count('GET /p/[redacted] HTTP/1.1') == 4
    assert "RuntimeError: Rendering failed for /p/[redacted]" in captured


def test_both_entrypoint_modes_use_safe_logging_without_changing_proxy_configuration():
    entrypoint = (BACKEND / "docker-entrypoint.sh").read_text(encoding="utf-8")
    starts = [line for line in entrypoint.splitlines() if "exec uvicorn " in line]
    assert len(starts) == 2
    assert all("--log-config logging_config.json" in line and "--proxy-headers --forwarded-allow-ips='*'" in line for line in starts)
