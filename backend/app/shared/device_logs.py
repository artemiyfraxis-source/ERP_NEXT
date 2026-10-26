"""Журналы с устройств: «Проверка сканера» с ТСД и ошибки браузера устройства (белый экран до входа).

Папка — ERP_DEVICE_LOG_DIR (в Docker это /app/data/import/devices); не задана — папка во временном каталоге системы.
Файлы: scanner-log.txt, client-errors.log. Старые журналы стенда не переносятся.
Пишут все процессы API (ERP_API_WORKERS > 1): дозапись и обрезку держит замок на файл (flock на <журнал>.lock),
иначе обрезка в одном процессе стирала бы строку, дописанную другим. На Windows (локальная ERP, один процесс) — замок
процесса."""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the local ERP runs one API process
    fcntl = None  # type: ignore[assignment]

LOG_DIR_ENV = "ERP_DEVICE_LOG_DIR"
SCANNER_LOG_LIMIT = 512 * 1024
CLIENT_ERRORS_LIMIT = 256 * 1024
_write_lock = threading.Lock()


def log_dir() -> Path:
    raw = os.getenv(LOG_DIR_ENV, "").strip()
    return Path(raw) if raw else Path(tempfile.gettempdir()) / "erp-devices"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """This process's threads and, on Linux, every other API process writing the same journal."""
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is None:
            yield
            return
        with open(path.with_name(path.name + ".lock"), "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def _append(path: Path, text: str, limit: int) -> None:
    with _locked(path):
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


def scanner_log_path() -> Path:
    return log_dir() / "scanner-log.txt"


def client_errors_path() -> Path:
    return log_dir() / "client-errors.log"


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def append_scanner_log(peer: str, user: str, user_agent: str, lines: list[str]) -> None:
    path = scanner_log_path()
    head = f"--- {datetime.now():%d.%m.%Y %H:%M:%S} {peer} {_clean(user, 80)} · {_clean(user_agent, 200)}"
    body = [line.replace("\r", " ").replace("\n", " ")[:300] for line in lines]
    _append(path, "\n".join([head, *body]) + "\n", SCANNER_LOG_LIMIT)


def append_client_error(peer: str, url: str, message: str, user_agent: str) -> None:
    path = client_errors_path()
    line = "\t".join([f"{datetime.now():%d.%m.%Y %H:%M:%S}", peer, _clean(url, 300), _clean(message, 1000),
                      _clean(user_agent, 300)])
    _append(path, line + "\n", CLIENT_ERRORS_LIMIT)
