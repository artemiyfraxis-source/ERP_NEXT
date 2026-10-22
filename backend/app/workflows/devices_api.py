"""Вход с устройств и экран ТСД по HTTP (ТСД открывает <адрес ERP>/tsd и входит по PIN сотрудника).

- GET /api/devices/entry — без входа: «здесь есть экран ТСД» (экран входа ТСД показывает вход по PIN сотрудника);
- GET /api/devices/qr.svg?url= — QR только на адреса того же хоста, по которому открыт запрос;
- POST|GET /api/devices/scanner-log — «Проверка сканера» с ТСД → <ERP_DEVICE_LOG_DIR>/scanner-log.txt;
- POST /api/devices/client-error (без входа: белый экран бывает до входа), GET /api/devices/client-errors;
- GET /tsd — короткий адрес для QR, ведёт на /?section=tsd."""

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

from app.shared import device_logs
from app.shared.http_auth import Principal, warehouse_edit_principal, warehouse_view_principal

router = APIRouter(tags=["devices"])
SCANNER_POSTS_PER_HOUR = 120
CLIENT_ERRORS_PER_HOUR = 20


def _peer(request: Request) -> str:
    # uvicorn puts the real address here from X-Forwarded-For (proxy headers from Caddy).
    return request.client.host if request.client else "unknown"


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
    """Open before sign-in: the ТСД login screen asks whether the server has the ТСД screen."""
    return {"tsd": True}


def _own_address(url: str, request: Request) -> bool:
    """Only the host this request came to gets a QR (behind Caddy: X-Forwarded-Host): the ТСД scans it into its browser."""
    if len(url) > 200:
        return False
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.fragment:
        return False
    own = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
    if not parts.netloc or parts.netloc.lower() != own:
        return False
    path = parts.path or "/"
    return all(ch.isalnum() or ch in "/-_.?=&" for ch in path + parts.query) and path.startswith("/")


@router.get("/api/devices/qr.svg")
def qr_code(
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_view_principal)],
    url: Annotated[str, Query(max_length=200)] = "",
) -> Response:
    if not _own_address(url, request):
        raise HTTPException(status_code=422, detail="QR делается только на адрес, по которому открыт ERP")
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
    peer = _peer(request)
    if not SCANNER_LIMIT.allow(peer):
        return {"ok": False}
    device_logs.append_scanner_log(peer, principal.actor.name, request.headers.get("user-agent", ""), payload.lines)
    return {"ok": True}


@router.get("/api/devices/scanner-log")
def scanner_log(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    return {"lines": device_logs.tail(device_logs.scanner_log_path(), 80)}


class ClientErrorPayload(BaseModel):
    message: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=500)


@router.post("/api/devices/client-error")
def post_client_error(payload: ClientErrorPayload, request: Request) -> dict:
    """A device browser that failed to start the app (the boot guard of index.html, before any sign-in)."""
    peer = _peer(request)
    if not CLIENT_ERROR_LIMIT.allow(peer):
        return {"ok": False}
    device_logs.append_client_error(peer, payload.url, payload.message, request.headers.get("user-agent", ""))
    return {"ok": True}


@router.get("/api/devices/client-errors")
def client_errors(_principal: Annotated[Principal, Depends(warehouse_view_principal)]) -> dict:
    path = device_logs.client_errors_path()
    lines = device_logs.tail(path, 20)
    keys = ("at", "ip", "url", "message", "browser")
    return {"errors": [dict(zip(keys, line.split("\t"), strict=False)) for line in reversed(lines) if line.strip()]}


@router.get("/tsd", include_in_schema=False)
def tsd_shortcut() -> RedirectResponse:
    """The short address of the QR: …:8031/tsd → the ТСД screen."""
    return RedirectResponse("/?section=tsd", status_code=307)
