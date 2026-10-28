"""Установка сканового агента на любой ПК (owner 28.10.2026: «везде… универсальность, если вдруг ПК сменять»).

Whoever may scan («Сканирование документов» on edit; was the administrator alone) issues a one-time install token in the screen (`POST /api/stock/scan/agent/install-token` → the
one-line PowerShell command); on the new PC the command downloads `install.ps1` from this server, which

- `POST /api/stock/scan/agent/enroll` `{install_token, machine_id, pc_name}` (no login: the secret IS the credential,
  valid an hour, spendable once) → `{token, server}` — an API token (scopes `labels:print` + `scan:upload`, a year) in
  the name of the administrator who issued the install token; the script stores it DPAPI-protected;
- `GET /api/stock/scan/agent/install.ps1`, `GET /api/stock/scan/agent/files/agent.ps1`, `GET /api/stock/scan/agent/manifest`
  (no login: the same files lie in the public repository) — the installer, the agent and its `{version, sha256}`.

It lives in workflows because it joins two modules: the warehouse owns the registry and the install tokens, identity
issues the API token."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.modules.identity.api import identity_uow_factory
from app.modules.identity.application import IdentityValidationError, issue_api_token
from app.modules.warehouse import agent_release, scan_agents
from app.modules.warehouse.api import warehouse_uow_factory
from app.shared.clock import moscow_now
from app.modules.warehouse.scan_jobs_api import scan_edit_principal
from app.shared.http_auth import Principal
from app.shared.identity import Actor

router = APIRouter(prefix="/api/stock/scan/agent", tags=["warehouse"])
AGENT_SCOPES = ("labels:print", "scan:upload")
AGENT_TOKEN_DAYS = 365
NO_FILES = "Файлы агента не лежат на этом сервере"
Now = Annotated[datetime, Depends(moscow_now)]
WarehouseFactory = Annotated[Callable, Depends(warehouse_uow_factory)]
IdentityFactory = Annotated[Callable, Depends(identity_uow_factory)]
# Owner 28.10.2026 («каждый, кому доступна эта функция, может воспользоваться своим устройством»): whoever may scan
# («Сканирование документов» on edit) sets up the agent on their own PC; a token never passes (no scope named).
Admin = Annotated[Principal, Depends(scan_edit_principal)]


def _base(request: Request) -> str:
    """The server address for the new PC. TLS ends at Tailscale Funnel before Caddy, so the request reads «http»: a
    named host is always served over https (28.10.2026 — the first command said http://… and could not reach the
    server); a bare IP or localhost (the LAN, the preview) keeps what it came with."""
    url = request.base_url
    host = url.hostname or ""
    local = host == "localhost" or host.replace(".", "").isdigit() or ":" in host
    return str(url if local else url.replace(scheme="https")).rstrip("/")


def install_command(base: str, secret: str) -> str:
    """One line for a PowerShell window on the new PC: TLS 1.2, the installer from this server, run with the secret."""
    return (
        'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        f"& ([scriptblock]::Create((Invoke-RestMethod '{base}/api/stock/scan/agent/install.ps1'))) "
        f"-Server '{base}' -InstallToken '{secret}'"
        '"'
    )


@router.post("/install-token")
def new_install_token(request: Request, admin: Admin, now: Now, uow_factory: WarehouseFactory) -> dict:
    secret, expires = scan_agents.issue_install(
        uow_factory(), created_by=admin.actor.id, created_by_name=admin.actor.name, now=now)
    return {"command": install_command(_base(request), secret), "expires_at": expires.isoformat(),
            "valid_minutes": int(scan_agents.INSTALL_TTL.total_seconds() // 60)}


class EnrollInput(BaseModel):
    install_token: str = Field(min_length=1, max_length=200)
    machine_id: str = Field(min_length=1, max_length=scan_agents.MACHINE_ID_MAX)
    pc_name: str = Field(default="", max_length=128)


@router.post("/enroll")
def enroll(payload: EnrollInput, request: Request, now: Now, warehouse_factory: WarehouseFactory,
           identity_factory: IdentityFactory) -> dict:
    try:
        created_by, created_by_name = scan_agents.redeem_install(
            warehouse_factory(), payload.install_token, payload.machine_id, now)
    except scan_agents.InstallRefused as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    pc = " ".join(payload.pc_name.split()) or payload.machine_id[:12]
    try:
        issued = issue_api_token(f"Агент ПК {pc}"[:128], AGENT_SCOPES, AGENT_TOKEN_DAYS,
                                 Actor(id=created_by, name=created_by_name, department=""), now, identity_factory())
    except IdentityValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"token": issued.secret, "server": _base(request)}


def _file(name: str, *, media: str, strip_bom: bool = False) -> Response:
    content = agent_release.read_file(name)
    if content is None:
        raise HTTPException(status_code=404, detail=NO_FILES)
    if strip_bom and content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    return Response(content, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/install.ps1")
def installer() -> Response:
    # Without the BOM: the text is run from memory (`[scriptblock]::Create`), where a BOM would stand before `param`.
    return _file("install.ps1", media="text/plain; charset=utf-8", strip_bom=True)


@router.get("/files/agent.ps1")
def agent_file() -> Response:
    return _file("agent.ps1", media="application/octet-stream")


@router.get("/manifest")
def manifest() -> dict:
    current = agent_release.release()
    if current is None:
        raise HTTPException(status_code=404, detail=NO_FILES)
    return {"version": current.version, "sha256": current.sha256, "size": current.size,
            "path": "/api/stock/scan/agent/files/agent.ps1"}
