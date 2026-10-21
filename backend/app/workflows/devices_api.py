"""Стенд, опция warehouse.tsd: вход с устройств и экран ТСД по HTTP.

- GET /api/devices/entry — без входа: «здесь есть экран ТСД» (экран входа ТСД показывает вход по PIN сотрудника);
- GET /api/devices/status — переключатель, слушают ли ворота, адреса, https; PUT /api/devices/network — администратор;
- GET /api/devices/qr.svg?url= — QR только на адреса этих ворот;
- POST|GET /api/devices/scanner-log — «Проверка сканера» с ТСД → <данные стенда>/scanner-log.txt;
- POST /api/devices/client-error (без входа: белый экран бывает до входа), GET /api/devices/client-errors;
- GET /tsd — короткий адрес для QR, ведёт на /?section=tsd.

Off the stage every route answers 404 «только на стенде», with the option off — 404 «Опция выключена» (checked after the
access gate, so a viewer still gets 403 on a write first)."""

from __future__ import annotations

import io
import threading
import time
from typing import Annotated
from urllib.parse import urlsplit

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from app.shared import device_gate, stage_options
from app.shared.http_auth import Principal, warehouse_edit_principal, warehouse_view_principal
from app.workflows.stage_options_api import admin_principal

router = APIRouter(tags=["devices"])
OPTION = device_gate.OPTION
SCANNER_POSTS_PER_HOUR = 120
CLIENT_ERRORS_PER_HOUR = 20


def _require_tsd() -> None:
    if not stage_options.available():
        raise HTTPException(status_code=404, detail="Вход с устройств есть только на стенде.")
    if not stage_options.enabled(OPTION):
        raise HTTPException(status_code=404, detail="Опция выключена")


def _peer(request: Request) -> str:
    # Through the gate uvicorn has already put the device's address here (X-Forwarded-For from 127.0.0.1).
    return request.client.host if request.client else "unknown"


def _note_port(request: Request) -> None:
    # uvicorn on the stage listens on 127.0.0.1: its socket is the port the gate forwards to.
    server = request.scope.get("server")
    if server and len(server) > 1 and server[0] in {"127.0.0.1", "::1"} and server[1]:
        device_gate.SUPERVISOR.note_api_port(int(server[1]))


class _Limiter:
    """N writes per hour per device address — the log endpoints are open to anyone on the network."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self._seen: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [moment for moment in self._seen.get(key, []) if now - moment < 3600]
            if len(recent) >= self.per_hour:
                self._seen[key] = recent
                return False
            self._seen[key] = [*recent, now]
            if len(self._seen) > 1000:  # forget the quiet ones
                self._seen = {k: v for k, v in self._seen.items() if v and now - v[-1] < 3600}
            return True


SCANNER_LIMIT = _Limiter(SCANNER_POSTS_PER_HOUR)
CLIENT_ERROR_LIMIT = _Limiter(CLIENT_ERRORS_PER_HOUR)


@router.get("/api/devices/entry")
def entry() -> dict:
    """Open before sign-in: the ТСД login screen asks whether the stage has the ТСД screen (option on)."""
    _require_tsd()
    return {"tsd": True}


@router.get("/api/devices/status")
def status(request: Request, _principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    _require_tsd()
    _note_port(request)
    return device_gate.SUPERVISOR.status()


class NetworkPayload(BaseModel):
    enabled: bool


@router.put("/api/devices/network")
def switch_network(
    payload: NetworkPayload, request: Request, _principal: Annotated[Principal, Depends(admin_principal)],
) -> dict:
    """«Пускать устройства из сети»: the gate starts (or stops) right away; the switch survives a restart."""
    _require_tsd()
    _note_port(request)
    device_gate.set_network(payload.enabled)
    device_gate.SUPERVISOR.apply()
    return device_gate.SUPERVISOR.status()


def _own_address(url: str) -> bool:
    """Only addresses of these gates get a QR: the ТСД scans it straight into its browser."""
    if len(url) > 200:
        return False
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.fragment:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    own = {address.ip for address in device_gate.local_addresses()} | {device_gate.host_name()}
    if host not in own or port != device_gate.SUPERVISOR.port:
        return False
    path = parts.path or "/"
    return all(ch.isalnum() or ch in "/-_.?=&" for ch in path + parts.query) and path.startswith("/")


@router.get("/api/devices/qr.svg")
def qr_code(
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    url: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    _require_tsd()
    if not _own_address(url):
        raise HTTPException(status_code=422, detail="QR делается только на адрес этого компьютера для устройств")
    buffer = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buffer, kind="svg", scale=4, border=2, xmldecl=False, light="#fff")
    return Response(buffer.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


class ScannerLogPayload(BaseModel):
    lines: list[Annotated[str, Field(max_length=300)]] = Field(min_length=1, max_length=60)


@router.post("/api/devices/scanner-log")
def post_scanner_log(
    payload: ScannerLogPayload, request: Request,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
) -> dict:
    """«Проверить сканер» on the ТСД: what the scanner really typed (keys, input, paste, timing)."""
    _require_tsd()
    peer = _peer(request)
    if not SCANNER_LIMIT.allow(peer):
        return {"ok": False}
    device_gate.append_scanner_log(peer, principal.actor.name, request.headers.get("user-agent", ""), payload.lines)
    return {"ok": True}


@router.get("/api/devices/scanner-log")
def scanner_log(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    _require_tsd()
    path = device_gate.scanner_log_path()
    return {"lines": device_gate.tail(path, 80) if path else []}


class ClientErrorPayload(BaseModel):
    message: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=500)


@router.post("/api/devices/client-error")
def post_client_error(payload: ClientErrorPayload, request: Request) -> dict:
    """A device browser that failed to start the app (the boot guard of index.html, before any sign-in)."""
    _require_tsd()
    peer = _peer(request)
    if not CLIENT_ERROR_LIMIT.allow(peer):
        return {"ok": False}
    device_gate.append_client_error(peer, payload.url, payload.message, request.headers.get("user-agent", ""))
    return {"ok": True}


@router.get("/api/devices/client-errors")
def client_errors(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    _require_tsd()
    path = device_gate.client_errors_path()
    lines = device_gate.tail(path, 20) if path else []
    keys = ("at", "ip", "url", "message", "browser")
    return {"errors": [dict(zip(keys, line.split("\t"), strict=False)) for line in reversed(lines) if line.strip()]}


@router.get("/tsd", include_in_schema=False)
def tsd_shortcut() -> RedirectResponse:
    """The short address of the QR: …:8031/tsd → the ТСД screen."""
    _require_tsd()
    return RedirectResponse("/?section=tsd", status_code=307)
