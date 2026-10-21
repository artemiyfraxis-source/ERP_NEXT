"""Стенд, опция warehouse.tsd: ворота для устройств — HTTP и HTTPS на одном порту перед API, X-Forwarded-For — настоящий
адрес устройства, keep-alive, потоковые тела и большие ответы, выход по файлу-стопу и по уходу родителя."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import stage_gate

BACKEND = Path(__file__).resolve().parents[1]
BIG = 5 * 1024 * 1024 + 17


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _send(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = b""
            while True:
                size = int(self.rfile.readline().strip(), 16)
                if size == 0:
                    self.rfile.readline()
                    return out
                out += self.rfile.read(size)
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        if self.path.startswith("/echo"):
            seen = {
                "client": self.client_address[0],
                "forwarded_for": self.headers.get_all("X-Forwarded-For") or [],
                "proto": self.headers.get_all("X-Forwarded-Proto") or [],
                "host": self.headers.get("Host"),
                "real_ip": self.headers.get("X-Real-IP"),
                "path": self.path,
            }
            self._send(200, json.dumps(seen).encode())
        elif self.path == "/big":
            self._send(200, bytes(range(256)) * (BIG // 256) + b"x" * (BIG % 256), "application/octet-stream")
        elif self.path == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in (b"one-", b"two-", b"three"):
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self._send(404, b'{"detail":"Not Found"}')

    def do_POST(self):
        body = self._body()
        self._send(200, json.dumps({"length": len(body), "sha": hashlib.sha256(body).hexdigest()}).encode())


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _openssl_or_skip() -> str:
    found = stage_gate.find_openssl()
    if not found:
        pytest.skip("нет openssl")
    return found


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    _openssl_or_skip()
    directory = tmp_path_factory.mktemp("tls")
    return stage_gate.ensure_certificate(directory, socket.gethostname(), ["127.0.0.1"])


def _gate(upstream_port: int, tls=None) -> stage_gate.Gate:
    gate = stage_gate.Gate(0, ("127.0.0.1", upstream_port), tls, host="127.0.0.1")
    gate.bind()
    threading.Thread(target=gate.serve_forever, daemon=True).start()
    return gate


@pytest.fixture()
def gate(upstream, certificate):
    gate = _gate(upstream, stage_gate.tls_context(*certificate))
    yield gate
    gate.close()


def _https(port: int) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ssl._create_unverified_context())


def _json(connection, method: str, path: str, **kwargs):
    connection.request(method, path, **kwargs)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def test_http_and_https_on_one_port_reach_the_api_with_the_real_address(gate):
    plain = http.client.HTTPConnection("127.0.0.1", gate.port, timeout=10)
    status, seen = _json(plain, "GET", "/echo?a=1", headers={
        "X-Forwarded-For": "6.6.6.6", "X-Forwarded-Proto": "https", "X-Real-IP": "6.6.6.6",
    })
    assert status == 200
    # Whatever the device claims, the API hears the gate's word: the peer address and how it came.
    assert seen["forwarded_for"] == ["127.0.0.1"] and seen["proto"] == ["http"] and seen["real_ip"] is None
    assert seen["path"] == "/echo?a=1" and seen["host"] == f"127.0.0.1:{gate.port}"
    secure = _https(gate.port)
    status, seen = _json(secure, "GET", "/echo", headers={"X-Forwarded-For": "6.6.6.6"})
    assert status == 200 and seen["forwarded_for"] == ["127.0.0.1"] and seen["proto"] == ["https"]
    assert secure.sock.version() in {"TLSv1.2", "TLSv1.3"}


@pytest.mark.parametrize("secure", [False, True], ids=["http", "https"])
def test_keep_alive_serves_several_requests_on_one_device_connection(gate, secure):
    connection = _https(gate.port) if secure else http.client.HTTPConnection("127.0.0.1", gate.port, timeout=10)
    _json(connection, "GET", "/echo")
    first_socket = connection.sock
    for _ in range(3):
        status, _seen = _json(connection, "GET", "/echo")
        assert status == 200
    status, answer = _json(connection, "POST", "/upload", body=b"abc", headers={"Content-Type": "text/plain"})
    assert status == 200 and answer["length"] == 3
    assert connection.sock is first_socket


def test_large_responses_and_streamed_bodies_pass_whole(gate):
    connection = _https(gate.port)
    connection.request("GET", "/big")
    response = connection.getresponse()
    data = response.read()
    assert response.status == 200 and len(data) == BIG and response.getheader("Content-Length") == str(BIG)
    assert data == bytes(range(256)) * (BIG // 256) + b"x" * (BIG % 256)
    connection.request("GET", "/chunked")
    assert connection.getresponse().read() == b"one-two-three"
    payload = os.urandom(3 * 1024 * 1024)
    status, answer = _json(connection, "POST", "/upload", body=payload)
    assert status == 200 and answer == {"length": len(payload), "sha": hashlib.sha256(payload).hexdigest()}

    def pieces():
        for start in range(0, len(payload), 100_000):
            yield payload[start:start + 100_000]

    status, answer = _json(connection, "POST", "/upload", body=pieces(), encode_chunked=True)
    assert status == 200 and answer["sha"] == hashlib.sha256(payload).hexdigest()


def test_api_down_answers_502_in_russian(upstream):
    gate = _gate(1)  # nothing listens on port 1
    try:
        connection = http.client.HTTPConnection("127.0.0.1", gate.port, timeout=10)
        status, answer = _json(connection, "GET", "/api/health")
        assert status == 502 and "ERP не отвечает" in answer["detail"]
    finally:
        gate.close()


def test_without_a_certificate_https_is_closed_and_http_still_works(upstream):
    gate = _gate(upstream)
    try:
        with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
            _json(_https(gate.port), "GET", "/echo")
        status, _seen = _json(http.client.HTTPConnection("127.0.0.1", gate.port, timeout=10), "GET", "/echo")
        assert status == 200
    finally:
        gate.close()


def test_a_broken_request_gets_400_and_the_connection_closes(gate):
    with socket.create_connection(("127.0.0.1", gate.port), timeout=10) as sock:
        sock.sendall(b"NOT HTTP AT ALL\r\n\r\n")
        assert sock.recv(100).startswith(b"HTTP/1.1 400")


def test_headers_rewrite_drops_hop_by_hop_and_forwarding():
    headers = [(b"host", b"x"), (b"x-forwarded-for", b"1.1.1.1"), (b"forwarded", b"for=1.1.1.1"),
               (b"connection", b"keep-alive"), (b"keep-alive", b"300"), (b"expect", b"100-continue"), (b"cookie", b"a=b")]
    assert stage_gate.request_headers(headers, "192.168.1.50", "https") == [
        (b"host", b"x"), (b"cookie", b"a=b"), (b"x-forwarded-for", b"192.168.1.50"), (b"x-forwarded-proto", b"https"),
        (b"connection", b"close"),
    ]
    assert stage_gate.response_headers([(b"connection", b"close"), (b"content-length", b"3")]) == [(b"content-length", b"3")]


def test_certificate_is_kept_until_an_address_is_new(tmp_path):
    openssl = _openssl_or_skip()
    cert, key = stage_gate.ensure_certificate(tmp_path, "PC-1", ["192.168.1.5"], openssl)
    made = cert.stat().st_mtime_ns
    assert stage_gate.ensure_certificate(tmp_path, "pc-1", ["192.168.1.5"], openssl) == (cert, key)
    assert cert.stat().st_mtime_ns == made
    stage_gate.ensure_certificate(tmp_path, "pc-1", ["192.168.1.5", "100.81.1.2"], openssl)
    meta = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    assert meta["ips"] == ["100.81.1.2", "127.0.0.1", "192.168.1.5"] and meta["host"] == "pc-1"
    text = subprocess.run([openssl, "x509", "-in", str(cert), "-noout", "-text"], capture_output=True, check=True).stdout
    assert b"IP Address:100.81.1.2" in text and b"DNS:pc-1" in text


def test_no_openssl_is_a_reason_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(stage_gate, "find_openssl", lambda: None)
    with pytest.raises(stage_gate.CertificateUnavailable, match="openssl не найден"):
        stage_gate.ensure_certificate(tmp_path, "pc", ["10.0.0.2"])


def _spawn(tmp_path, *extra, stdin=subprocess.DEVNULL):
    env = {**os.environ, "ERP_STOP_FILE": str(tmp_path / "erp.stop"), "PYTHONUTF8": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "app.stage_gate", "--port", "0", "--host", "127.0.0.1", "--upstream", "127.0.0.1:1",
         *extra],
        cwd=BACKEND, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    line = process.stdout.readline().decode("utf-8")
    assert "ворота" in line, line
    return process, int(line.split("127.0.0.1:")[1].split()[0])


def _wait_exit(process, seconds: float = 8.0) -> int | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return process.returncode
        time.sleep(0.1)
    process.kill()
    return None


def test_the_gate_process_exits_on_a_fresh_stop_file(tmp_path):
    process, port = _spawn(tmp_path)
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass
    (tmp_path / "erp.stop").write_text("stop", encoding="utf-8")
    assert _wait_exit(process) == 0
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_the_gate_process_exits_when_its_parent_is_gone(tmp_path):
    process, _port = _spawn(tmp_path, "--watch-stdin", stdin=subprocess.PIPE)
    time.sleep(0.3)
    assert process.poll() is None
    process.stdin.close()  # what the OS does to the pipe when the API process dies
    assert _wait_exit(process) == 0


def test_a_taken_port_is_reported(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        result = subprocess.run(
            [sys.executable, "-m", "app.stage_gate", "--port", str(port), "--host", "127.0.0.1"],
            cwd=BACKEND, capture_output=True, timeout=30, env={**os.environ, "PYTHONUTF8": "1"},
        )
    assert result.returncode == 2 and "занят" in result.stdout.decode("utf-8")
