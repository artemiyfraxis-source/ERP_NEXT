"""Стенд, опция warehouse.tsd: вход с устройств — переключатель «Пускать устройства из сети», адреса этого
компьютера, сертификат и процесс ворот (app/stage_gate.py), журналы «Проверки сканера» и ошибок браузера ТСД.

Everything lives next to the stage options file (ERP_STAGE_OPTIONS → its folder, the stage's `data\\`):
devices.json (the switch), tls\\ (certificate), device-gate.log, scanner-log.txt, client-errors.log. Without
ERP_STAGE_OPTIONS (production, CI) nothing here runs: no folder, no gate, the routes answer 404.

The API process owns the gate: a supervisor thread (started from app.main) keeps it running while the option AND the
switch are on and stops it otherwise — on start, on «Пускать» / «Не пускать», when the option is switched off, when
the gate died. The gate's stdin is a pipe from here, so it also exits when this process is gone."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import stage_gate
from app.shared import stage_options

OPTION = "warehouse.tsd"
DEFAULT_GATE_PORT = 8031
DEFAULT_API_PORT = 8030  # the stage's port (stage.ps1 -Port)
GATE_PORT_ENV = "ERP_DEVICE_GATE_PORT"
API_PORT_ENV = "ERP_API_PORT"
BACKEND_DIR = Path(__file__).resolve().parents[2]
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
SCANNER_LOG_LIMIT = 512 * 1024
CLIENT_ERRORS_LIMIT = 256 * 1024
GATE_LOG_LIMIT = 1024 * 1024
CHECK_EVERY = 3.0
_write_lock = threading.Lock()


def data_dir() -> Path | None:
    raw = os.getenv(stage_options.ENV, "").strip()
    return Path(raw).resolve().parent if raw else None


def _port_from_env(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() and 0 < int(raw) < 65536 else None


def gate_port() -> int:
    return _port_from_env(GATE_PORT_ENV) or DEFAULT_GATE_PORT


def api_port(argv: list[str] | None = None) -> int:
    """The API's own port: ERP_API_PORT, else uvicorn's `--port N` on the command line, else the stage's 8030."""
    if (port := _port_from_env(API_PORT_ENV)) is not None:
        return port
    args = sys.argv if argv is None else argv
    for index, arg in enumerate(args):
        value = args[index + 1] if arg == "--port" and index + 1 < len(args) else arg[7:] if arg.startswith("--port=") else ""
        if value.isdigit() and 0 < int(value) < 65536:
            return int(value)
    return DEFAULT_API_PORT


# ── The switch ─────────────────────────────────────────────────────────────────────────────────────────────────────


def network_enabled() -> bool:
    folder = data_dir()
    if folder is None:
        return False
    try:
        data = json.loads((folder / "devices.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("network") is True


def set_network(enabled: bool) -> None:
    folder = data_dir()
    if folder is None:
        raise stage_options.StageOptionsUnavailable("Вход с устройств настраивается только на стенде")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "devices.json"
    with _write_lock:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"network": bool(enabled)}), encoding="utf-8")
        os.replace(temporary, target)


# ── Addresses ──────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Address:
    ip: str
    label: str


def _default_route_ip() -> str | None:
    """The address the PC reaches the network with (no packet is sent): the office Wi-Fi / cable one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
    except OSError:
        return None


def local_addresses() -> list[Address]:
    """IPv4 addresses a device can reach this PC at: the office network first, Tailscale (100.64.0.0/10) last."""
    found: set[str] = set()
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    primary = _default_route_ip()
    if primary:
        found.add(primary)
    ranked = []
    for ip in found:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            continue
        tailscale = address in TAILSCALE
        rank = 2 if tailscale else 0 if ip == primary else 1
        ranked.append((rank, tuple(int(part) for part in ip.split(".")), Address(ip, "Tailscale" if tailscale else "сеть офиса")))
    return [address for _rank, _key, address in sorted(ranked)]


def host_name() -> str:
    return socket.gethostname().lower()


# ── The gate process ───────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Launch:
    port: int
    upstream_port: int
    cert: str | None
    key: str | None
    cert_stamp: int  # a new certificate (a new address) restarts the gate


def launch_process(launch: Launch, log: Path) -> subprocess.Popen:
    command = [sys.executable, "-m", "app.stage_gate", "--port", str(launch.port),
               "--upstream", f"127.0.0.1:{launch.upstream_port}", "--watch-stdin"]
    if launch.cert and launch.key:
        command += ["--cert", launch.cert, "--key", launch.key]
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if log.stat().st_size > GATE_LOG_LIMIT:
            log.unlink()
    except OSError:
        pass
    with open(log, "ab") as output:
        return subprocess.Popen(
            command, cwd=BACKEND_DIR, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
            creationflags=stage_gate.NO_WINDOW, env={**os.environ, "PYTHONUTF8": "1"},
        )


def port_answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _last_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


class GateSupervisor:
    def __init__(self, launch=launch_process, answers=port_answers, certificate=stage_gate.ensure_certificate):
        self._launch = launch
        self._answers = answers
        self._certificate = certificate
        self._lock = threading.RLock()
        self._process = None
        self._current: Launch | None = None
        self.upstream_port = api_port()
        self.reason = ""
        self.https_reason = ""
        self._thread: threading.Thread | None = None

    @staticmethod
    def wanted() -> bool:
        return stage_options.available() and stage_options.enabled(OPTION) and network_enabled()

    def note_api_port(self, port: int | None) -> None:
        """A request told the API its real port (scope["server"]): the gate forwards there."""
        if port and port != self.upstream_port:
            self.upstream_port = port

    @property
    def port(self) -> int:
        current = self._current
        return current.port if current else gate_port()

    def _running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _stop(self) -> None:
        process, self._process, self._current = self._process, None, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()  # the gate exits on EOF even if terminate() reached only the venv launcher
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()

    def apply(self) -> None:
        """Bring the gate to what the option and the switch say."""
        with self._lock:
            if not self.wanted():
                self._stop()
                self.reason = ""
                return
            folder = data_dir()
            assert folder is not None
            cert = key = None
            stamp = 0
            try:
                cert_path, key_path = self._certificate(folder / "tls", host_name(), [a.ip for a in local_addresses()])
                cert, key, stamp = str(cert_path), str(key_path), cert_path.stat().st_mtime_ns
                self.https_reason = ""
            except (stage_gate.CertificateUnavailable, OSError) as error:
                self.https_reason = str(error)
            launch = Launch(gate_port(), self.upstream_port, cert, key, stamp)
            if self._running() and self._current == launch:
                return
            self._stop()
            log = folder / "device-gate.log"
            try:
                self._process = self._launch(launch, log)
            except OSError as error:
                self.reason = f"Ворота не запустились: {error}"
                return
            self._current = launch
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self._running() and not self._answers(launch.port):
                time.sleep(0.1)
            if not self._running():
                self.reason = _last_line(log) or "Ворота сразу завершились"
                self._process = None
                self._current = None
            else:
                self.reason = ""

    def listening(self) -> bool:
        with self._lock:
            return self._running() and self._current is not None and self._answers(self._current.port)

    def status(self) -> dict:
        with self._lock:
            enabled = network_enabled()
            listening = self.listening() if enabled else False
            port = self.port
            https = bool(listening and self._current and self._current.cert)
            addresses = [
                {"url": f"http://{address.ip}:{port}", "label": address.label} for address in local_addresses()
            ] if listening else []
            reason = ""
            if enabled and not listening:
                reason = self.reason or "Ворота запускаются…"
            elif listening and not https:
                reason = self.https_reason
            return {
                "enabled": enabled, "listening": listening, "port": port, "api_port": self.upstream_port,
                "addresses": addresses, "https": https, "reason": reason, "host": host_name(),
            }

    def start(self) -> None:
        """The supervisor thread: only on the stage (ERP_STAGE_OPTIONS), once per process."""
        if not stage_options.available() or self._thread is not None:
            return

        def loop() -> None:
            while True:
                try:
                    self.apply()
                except Exception as error:  # noqa: BLE001 — the loop must outlive any one failure
                    self.reason = f"Сбой ворот: {error}"
                time.sleep(CHECK_EVERY)

        self._thread = threading.Thread(target=loop, name="erp-device-gate", daemon=True)
        self._thread.start()


SUPERVISOR = GateSupervisor()


# ── Logs from the devices ──────────────────────────────────────────────────────────────────────────────────────────


def _append(path: Path, text: str, limit: int) -> None:
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size + len(text.encode("utf-8")) > limit:
            # Keep the newer half: the last checks are what the owner reads.
            kept = path.read_bytes()[-(limit // 2):]
            cut = kept.find(b"\n")
            path.write_bytes(kept[cut + 1:] if cut >= 0 else b"")
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def tail(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def scanner_log_path() -> Path | None:
    folder = data_dir()
    return folder / "scanner-log.txt" if folder else None


def client_errors_path() -> Path | None:
    folder = data_dir()
    return folder / "client-errors.log" if folder else None


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def append_scanner_log(peer: str, user: str, user_agent: str, lines: list[str]) -> None:
    path = scanner_log_path()
    if path is None:
        return
    head = f"--- {datetime.now():%d.%m.%Y %H:%M:%S} {peer} {_clean(user, 80)} · {_clean(user_agent, 200)}"
    body = [line.replace("\r", " ").replace("\n", " ")[:300] for line in lines]
    _append(path, "\n".join([head, *body]) + "\n", SCANNER_LOG_LIMIT)


def append_client_error(peer: str, url: str, message: str, user_agent: str) -> None:
    path = client_errors_path()
    if path is None:
        return
    line = "\t".join([f"{datetime.now():%d.%m.%Y %H:%M:%S}", peer, _clean(url, 300), _clean(message, 1000),
                      _clean(user_agent, 300)])
    _append(path, line + "\n", CLIENT_ERRORS_LIMIT)
