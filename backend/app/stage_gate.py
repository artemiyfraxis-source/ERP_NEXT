"""Стенд, опция warehouse.tsd: «ворота» для устройств (ТСД, телефон) перед API, которое слушает только 127.0.0.1.

One port for the office Wi-Fi and Tailscale, both protocols on it (Roller_Buyer dual-listen.ts): Chrome on the ТСД
upgrades a typed address to https:// by itself and a plain HTTP port answers that with ERR_SSL_PROTOCOL_ERROR. So the
first byte of every connection decides — 0x16 opens a TLS handshake (self-signed certificate made by the `openssl`
CLI, the browser warns once), anything else is plain HTTP.

The gate speaks HTTP/1.1 itself (h11, which uvicorn already brings) instead of copying bytes: it SETS
X-Forwarded-For to the device's real address and X-Forwarded-Proto to how it came, dropping whatever the device
sent. uvicorn trusts those headers from 127.0.0.1 only, so every device gets its own `request.client.host` — its
own sign-in rate-limit key — instead of all of them sharing the owner's 127.0.0.1. Keep-alive towards the device,
streamed bodies both ways, any response size. Upstream gets a fresh loopback connection per request (uvicorn drops
idle keep-alive connections after 5 s; a request sent into one of those would be lost).

    python -m app.stage_gate --port 8031 --upstream 127.0.0.1:8030 [--cert c.pem --key k.pem] [--watch-stdin]

Exits on a fresh ERP_STOP_FILE (stage.ps1 down), and with --watch-stdin when the parent API process is gone (its end
of the stdin pipe closes). The API starts and stops it (app/shared/device_gate.py)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import h11

TLS_HANDSHAKE = 0x16
CHUNK = 64 * 1024
FIRST_BYTE_TIMEOUT = 30.0
IDLE_TIMEOUT = 120.0  # a device's idle keep-alive connection
UPSTREAM_TIMEOUT = 600.0  # a slow report or a large download from the API
MAX_CONNECTIONS = 64
# Who the device is and how it came are set by the gate, never taken from the device.
FORWARDING = frozenset({
    b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-port", b"x-real-ip", b"forwarded",
})
HOP_BY_HOP = frozenset({
    b"connection", b"keep-alive", b"proxy-connection", b"te", b"trailer", b"upgrade", b"proxy-authorization",
    b"proxy-authenticate",
})
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UPSTREAM_DOWN = "ERP не отвечает: сервер перезапускается или остановлен. Обновите страницу через минуту."


def request_headers(headers, peer: str, scheme: str) -> list[tuple[bytes, bytes]]:
    """The device's request headers as the API gets them: forwarding headers replaced, hop-by-hop ones dropped."""
    kept = [
        (name, value) for name, value in headers
        if name not in FORWARDING and name not in HOP_BY_HOP and name != b"expect"
    ]
    return kept + [
        (b"x-forwarded-for", peer.encode("ascii")),
        (b"x-forwarded-proto", scheme.encode("ascii")),
        (b"connection", b"close"),
    ]


def response_headers(headers) -> list[tuple[bytes, bytes]]:
    return [(name, value) for name, value in headers if name not in HOP_BY_HOP]


class _Upstream:
    """One request to the API over its own loopback connection."""

    def __init__(self, address: tuple[str, int]):
        self.sock = socket.create_connection(address, timeout=UPSTREAM_TIMEOUT)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.conn = h11.Connection(h11.CLIENT)

    def send(self, event) -> None:
        data = self.conn.send(event)
        if data:
            self.sock.sendall(data)

    def next_event(self):
        while True:
            event = self.conn.next_event()
            if event is h11.NEED_DATA:
                self.conn.receive_data(self.sock.recv(CHUNK))
                continue
            return event

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _Exchange:
    """The requests of one device connection, one after another (keep-alive), each relayed to the API."""

    def __init__(self, sock: socket.socket, peer: str, scheme: str, upstream: tuple[str, int]):
        self.sock = sock
        self.peer = peer
        self.scheme = scheme
        self.upstream = upstream
        self.client = h11.Connection(h11.SERVER)

    def _to_client(self, event) -> None:
        data = self.client.send(event)
        if data:
            self.sock.sendall(data)

    def _client_event(self):
        while True:
            event = self.client.next_event()
            if event is h11.NEED_DATA:
                self.client.receive_data(self.sock.recv(CHUNK))
                continue
            return event

    def _answer(self, status: int, text: str, api: bool) -> None:
        body = json.dumps({"detail": text}, ensure_ascii=False).encode("utf-8") if api else text.encode("utf-8")
        kind = b"application/json" if api else b"text/plain; charset=utf-8"
        self._to_client(h11.Response(status_code=status, headers=[
            (b"content-type", kind), (b"content-length", str(len(body)).encode("ascii")), (b"connection", b"close"),
            (b"cache-control", b"no-store"),
        ]))
        self._to_client(h11.Data(data=body))
        self._to_client(h11.EndOfMessage())

    def run(self) -> None:
        try:
            while self._relay_one():
                self.client.start_next_cycle()
        except h11.RemoteProtocolError as error:
            # A malformed request from the device: say so if nothing has been answered yet.
            if self.client.our_state in (h11.IDLE, h11.SEND_RESPONSE):
                try:
                    self._answer(error.error_status_hint, "Неверный запрос", api=False)
                except (OSError, h11.LocalProtocolError):
                    pass

    def _relay_one(self) -> bool:
        request = self._client_event()
        if not isinstance(request, h11.Request):
            return False  # the device closed the connection between requests
        api = request.target.startswith(b"/api/")
        if self.client.they_are_waiting_for_100_continue:
            self._to_client(h11.InformationalResponse(status_code=100, headers=[]))
        try:
            upstream = _Upstream(self.upstream)
        except OSError:
            self._answer(502, UPSTREAM_DOWN, api)
            return False
        try:
            try:
                upstream.send(h11.Request(
                    method=request.method, target=request.target,
                    headers=request_headers(request.headers, self.peer, self.scheme),
                ))
                while True:
                    event = self._client_event()
                    if isinstance(event, h11.Data):
                        upstream.send(h11.Data(data=event.data))
                    elif isinstance(event, h11.EndOfMessage):
                        upstream.send(h11.EndOfMessage())
                        break
                    else:
                        return False
                response = upstream.next_event()
                while isinstance(response, h11.InformationalResponse):
                    response = upstream.next_event()
            except (OSError, h11.ProtocolError):
                if self.client.our_state is h11.SEND_RESPONSE:
                    self._answer(502, UPSTREAM_DOWN, api)
                return False
            if not isinstance(response, h11.Response):
                self._answer(502, UPSTREAM_DOWN, api)
                return False
            self._to_client(h11.Response(
                status_code=response.status_code, headers=response_headers(response.headers), reason=response.reason,
            ))
            while True:
                event = upstream.next_event()  # a failure mid-body just drops the device's connection
                if isinstance(event, h11.Data):
                    self._to_client(h11.Data(data=event.data))
                elif isinstance(event, h11.EndOfMessage):
                    self._to_client(h11.EndOfMessage())
                    break
                else:
                    return False
        finally:
            upstream.close()
        return self.client.our_state is h11.DONE and self.client.their_state is h11.DONE


class Gate:
    def __init__(self, port: int, upstream: tuple[str, int], tls: ssl.SSLContext | None = None, host: str = "0.0.0.0",
                 max_connections: int = MAX_CONNECTIONS):
        self.host = host
        self.port = port
        self.upstream = upstream
        self.tls = tls
        self._slots = threading.BoundedSemaphore(max_connections)
        self._closing = threading.Event()
        self.listener: socket.socket | None = None

    def bind(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other program may take the port over
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(64)
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.5)
        self.listener = sock
        self.port = sock.getsockname()[1]

    def serve_forever(self) -> None:
        assert self.listener is not None
        while not self._closing.is_set():
            try:
                conn, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._closing.is_set():
                    break
                continue
            if not self._slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._handle, args=(conn, address[0]), daemon=True).start()

    def close(self) -> None:
        self._closing.set()
        if self.listener is not None:
            self.listener.close()

    def _handle(self, conn: socket.socket, peer: str) -> None:
        try:
            conn.settimeout(FIRST_BYTE_TIMEOUT)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            first = conn.recv(1, socket.MSG_PEEK)
            if not first:
                return
            scheme = "http"
            if first[0] == TLS_HANDSHAKE:
                if self.tls is None:
                    return  # no certificate: an https:// attempt is closed at once, http:// still works
                conn = self.tls.wrap_socket(conn, server_side=True)
                scheme = "https"
            conn.settimeout(IDLE_TIMEOUT)
            _Exchange(conn, peer, scheme, self.upstream).run()
        except (OSError, ssl.SSLError, h11.ProtocolError, ValueError):
            pass  # a device that went away, a refused certificate, a broken request
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._slots.release()


def tls_context(cert: str | Path, key: str | Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    return context


# ── Certificate (no `cryptography` in the venv: the openssl CLI of Git for Windows does it) ─────────────────────────


class CertificateUnavailable(RuntimeError):
    pass


OPENSSL_FALLBACKS = (
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    r"C:\Program Files\Git\usr\bin\openssl.exe",
)


def find_openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    return next((candidate for candidate in OPENSSL_FALLBACKS if Path(candidate).is_file()), None)


def ensure_certificate(directory: Path, host: str, ips: list[str], openssl: str | None = None) -> tuple[Path, Path]:
    """`directory`/server.crt + server.key for this PC's name and addresses; made again when an address is new or the
    name changed. CertificateUnavailable(reason) when there is no openssl or it failed."""
    cert, key, meta = directory / "server.crt", directory / "server.key", directory / "server.json"
    host = host.lower()
    wanted = sorted({"127.0.0.1", *ips})
    try:
        saved = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    if (cert.is_file() and key.is_file() and isinstance(saved, dict) and saved.get("host") == host
            and set(wanted) <= set(saved.get("ips") or [])):
        return cert, key
    program = openssl or find_openssl()
    if not program:
        raise CertificateUnavailable("openssl не найден (ставится с Git для Windows) — устройства откроют только http://")
    directory.mkdir(parents=True, exist_ok=True)
    names = [f"DNS:{host}", "DNS:localhost", *(f"IP:{ip}" for ip in wanted)]
    new_cert, new_key = directory / "server.crt.new", directory / "server.key.new"
    with tempfile.TemporaryDirectory() as scratch:
        # Git's openssl looks for a config of its own install; an empty one is enough for `req -x509`.
        config = Path(scratch) / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name = dn\n[dn]\n", encoding="ascii")
        command = [
            program, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-sha256",
            "-days", "3650", "-keyout", str(new_key), "-out", str(new_cert), "-subj", f"/CN=ERP stage {host}",
            "-addext", "subjectAltName=" + ",".join(names), "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature", "-addext", "extendedKeyUsage=serverAuth",
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, timeout=60, env={**os.environ, "OPENSSL_CONF": str(config)},
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CertificateUnavailable(f"openssl не запустился: {error}") from error
    if result.returncode != 0 or not new_cert.is_file() or not new_key.is_file():
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["без текста"]
        raise CertificateUnavailable(f"openssl не сделал сертификат: {tail[0]}")
    os.replace(new_key, key)
    os.replace(new_cert, cert)
    meta.write_text(json.dumps({"host": host, "ips": wanted, "made": datetime.now(timezone.utc).isoformat()}),
                    encoding="utf-8")
    return cert, key


# ── Process ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _exit_when_parent_is_gone() -> None:
    try:
        sys.stdin.buffer.read()
    except (OSError, ValueError):
        pass
    os._exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.stage_gate", description="Вход с устройств на стенд ERP")
    parser.add_argument("--port", type=int, default=8031)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--upstream", default="127.0.0.1:8030", help="адрес API, host:port")
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--watch-stdin", action="store_true", help="выйти, когда закроется stdin (родитель завершился)")
    args = parser.parse_args(argv)

    from app.shared.stop_file import watch_stop_file

    watch_stop_file()
    if args.watch_stdin:
        threading.Thread(target=_exit_when_parent_is_gone, name="gate-parent", daemon=True).start()
    upstream_host, _, upstream_port = args.upstream.rpartition(":")
    tls = tls_context(args.cert, args.key) if args.cert and args.key else None
    gate = Gate(args.port, (upstream_host or "127.0.0.1", int(upstream_port)), tls, host=args.host)
    try:
        gate.bind()
    except OSError as error:
        print(f"{datetime.now():%d.%m.%Y %H:%M:%S} порт {args.port} занят другой программой: {error}", flush=True)
        return 2
    print(f"{datetime.now():%d.%m.%Y %H:%M:%S} ворота {args.host}:{gate.port} → {args.upstream}, "
          f"https: {'да' if tls else 'нет'}", flush=True)
    gate.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
