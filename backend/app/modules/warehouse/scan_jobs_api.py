"""«Отсканировать УПД» — HTTP contract (owner 23.10.2026; the queue itself — `scan_jobs.py`, the reading —
`scan_recognition.py`).

The storekeeper's screen (warehouse edit, as `POST /api/stock/receipts`):
- `POST /api/stock/receipts/scan-jobs` (body `{agent_id?}` — the agent this browser remembered) → `{id, status}` — a
  pending job for the agent of the PC the browser sits at (`scan_agents.route`: by address, by the remembered id), or
  `{status: "choose", agents: [{id, pc, scanners, version}]}` — pick one and press again with its `agent_id`, or
  `{status: "no_agent", idle: [PC…]}` — no PC with an agent and a scanner is alive (the screen shows the install guide);
- `GET /api/stock/receipts/scan-jobs/{id}` → `{id, status, error, result, recognising, scan_file}`; `result` — the
  answer `{scan_file, recognised, lines, note}` (`scan_recognition.scan_answer`) once it is made, else null;
  `recognising` — the scan is in, its answer is being made in the background (a model reads 8–100 s, never inside
  this GET: a poll that finds nothing running restarts it as a background task). A job pending over 120 s or claimed
  over 180 s reads (and stays) failed «Сканер не ответил — проверьте агент на ПК склада».
- `DELETE /api/stock/receipts/scan-jobs/{id}` → `{id, status}` — an open job becomes failed «Отменено», a finished
  one is answered as it is.
- `POST /api/stock/receipts/scan` (multipart `file`: PDF, JPG, PNG ≤ 20 MB, «Файл…») → the job view: the file is
  stored with a job that is done from the start, its answer is made in the background; the screen polls the job.
  An ЭДО УПД (Diadoc XML or a ZIP of them, owner 26.10.2026) is kept as it came and read exactly — the job view comes
  with `result` already in; not a УПД / счёт-фактура — 422 «Это не УПД в формате ФНС — …».
- `POST /api/stock/receipts/scan-merge` `{files: [2..20 scan links]}` («+ Страница») → the job view of ONE merged PDF;
  its answer combines the readings kept with the sheets — made at once when every sheet is read, else in the
  background.

The agent of the warehouse PC calls with `Authorization: Bearer <API token with the scope scan:upload>` (one token
may carry `labels:print` and `scan:upload` together); a person's session is refused (403):
- `POST /api/stock/scan/agent/claim` `{host, machine_id?}` → `{job: {id} | null}` — the oldest pending job targeted to
  this agent (or untargeted) becomes claimed; an old agent without `machine_id` takes untargeted jobs only;
- `POST /api/stock/scan/agent/heartbeat` `{machine_id, pc_name, version, lan_ip, scanners: [{id, name}]}` (at start,
  then every ~2 min) → `{ok, update: {version, sha256, size, path} | null}` — the registry row of the PC; `update` — a
  newer agent.ps1 to download (hash-checked by the agent);
- `POST /api/stock/scan/agent/jobs/{id}/result` — multipart field `file` (PDF / JPG / PNG ≤ 20 MB) → the file is
  stored, the job done; or JSON `{"error": "…"}` → the job failed with that reason. Answer `{ok: true, id, status}`;
  404 — no such job, 409 — its result is already in or it was never handed out (a retry: drop the job), 422 — no file
  or a file ERP does not keep (the job is failed with that reason). Recognition runs after the answer, so the agent
  never waits for it; the screen's poll waits for it instead and the answer is stored once."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, warehouse_edit_principal
from app.workflows.edo_receipt import EdoRejected, file_kind, is_edo_upload, read_delivery

from . import scan_agents
from .agent_release import update_for
from .api import warehouse_uow_factory
from .application import UnitOfWork
from .domain import RECEIPT_SCAN_PATH
from .receipts_api import _checked, multipart_file
from .receipts_application import SCAN_FILE_LIMIT, merge_scans, store_edo_file, store_job_scan, store_scan
from .scan_jobs import DONE, FAILED, ScanJob, ScanJobClosed, cancel_job, claim, create_job, fail_job, get_job, save_result
from .scan_recognition import (
    ScanSources,
    edo_answer,
    edo_names,
    job_view,
    readings_kept,
    recognise_job,
    scan_sources,
)

SCAN_SCOPE = "scan:upload"
SCAN_AGENT_ONLY = "Очередь сканирования забирает только агент склада (токен API «scan:upload»)."
TOO_BIG = "Скан УПД больше 20 МБ"
NO_FILE = "Файл не передан: нужен multipart с полем «file»"
router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]


def scan_agent_principal(
    principal: Annotated[Principal, Security(authenticated_principal, scopes=[TOKEN_SCOPE_PREFIX + SCAN_SCOPE])],
) -> Principal:
    """The agent only: an API token with `scan:upload` (a token without it is refused by the security scope), never a
    person's session — a browser must not take jobs off the queue."""
    if not principal.service:
        raise HTTPException(status_code=403, detail=SCAN_AGENT_ONLY)
    return principal


scan_agent_principal.token_scopes = frozenset({SCAN_SCOPE})  # type: ignore[attr-defined]  (the token contract test)


def _brief(job: ScanJob) -> dict:
    return {"id": str(job.id), "status": job.status}


def _queue(operation):
    try:
        return operation()
    except KeyError as error:
        raise HTTPException(status_code=404, detail=error.args[0]) from error
    except ScanJobClosed as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _recognise_later(background: BackgroundTasks, uow_factory, sources: ScanSources, job: ScanJob,
                     pages: list[UUID] | None = None) -> None:
    """After the answer is sent: the job's reading (`recognise_job` holds a database lock — one per job in every process)."""
    if job.status == DONE and not job.result:
        background.add_task(recognise_job, uow_factory, sources, job, pages)


class ScanStartInput(BaseModel):
    agent_id: str = Field(default="", max_length=scan_agents.MACHINE_ID_MAX)


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers puts the real address here from Caddy's X-Forwarded-For.
    return request.client.host if request.client else ""


@router.post("/receipts/scan-jobs")
def new_scan_job(
    request: Request,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: ScanStartInput | None = None,
) -> dict:
    routing = scan_agents.route(uow_factory(), browser_ip=_client_ip(request),
                                remembered=payload.agent_id if payload else "", now=now)
    if routing.kind == scan_agents.CHOOSE:
        return {"status": "choose", "agents": [agent.view() for agent in routing.agents]}
    if routing.kind == scan_agents.NO_AGENT:
        return {"status": "no_agent", "idle": routing.idle}
    target = routing.agent_id if routing.kind == scan_agents.AGENT else None
    return _brief(create_job(uow_factory(), requested_by=principal.actor.name, now=now, target=target))


@router.get("/receipts/scan-jobs/{job_id}")
def scan_job(
    job_id: UUID,
    request: Request,
    background: BackgroundTasks,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    _recognise_later(background, uow_factory, scan_sources(request, uow_factory), job)
    return job_view(job)


@router.delete("/receipts/scan-jobs/{job_id}")
def cancel_scan_job(
    job_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    return _brief(_queue(lambda: cancel_job(uow_factory(), job_id, now=now)))


@router.post("/receipts/scan")
async def scan_receipt_document(
    request: Request,
    background: BackgroundTasks,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """«Файл…»: the scan is stored (as PDF) with a done job; its answer is made after this one is sent."""
    body = await request.body()
    if len(body) > SCAN_FILE_LIMIT + 64 * 1024:
        raise HTTPException(status_code=422, detail=TOO_BIG)
    upload = multipart_file(request.headers.get("content-type", ""), body)
    if upload is None:
        raise HTTPException(status_code=422, detail=NO_FILE)
    file_name, content = upload
    if is_edo_upload(file_name, content):
        return await run_in_threadpool(_edo, request, file_name, content, principal, now, uow_factory)
    job = await run_in_threadpool(
        lambda: _checked(lambda: store_scan(uow_factory(), file_name, content, principal.actor, now)))
    _recognise_later(background, uow_factory, scan_sources(request, uow_factory), job)
    return job_view(job)


def _edo(request: Request, file_name: str, content: bytes, principal: Principal, now: datetime, uow_factory) -> dict:
    """An ЭДО УПД: read exactly from the file, kept as it came, the answer stored with its job at once."""
    try:
        delivery = read_delivery(file_name, content)
    except EdoRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    job = _checked(lambda: store_edo_file(uow_factory(), file_name, file_kind(content), content, principal.actor, now))
    answer = edo_answer(scan_sources(request, uow_factory), f"{RECEIPT_SCAN_PATH}{job.file_id}", delivery,
                        edo_names(uow_factory))
    save_result(uow_factory(), job.id, json.dumps(answer, ensure_ascii=False))
    return job_view(_queue(lambda: get_job(uow_factory(), job.id, now=now)))


class ScanMergeInput(BaseModel):
    files: list[str]


def _merge(request: Request, background: BackgroundTasks, links: list[str], principal: Principal, now: datetime,
           uow_factory) -> dict:
    merged = _checked(lambda: merge_scans(uow_factory(), links, principal.actor, now))
    sources = scan_sources(request, uow_factory)
    if readings_kept(uow_factory, merged.pages):
        recognise_job(uow_factory, sources, merged.job, merged.pages)  # no file is read: the sheets' readings combined
        return job_view(_queue(lambda: get_job(uow_factory(), merged.job.id, now=now)))
    _recognise_later(background, uow_factory, sources, merged.job, merged.pages)
    return job_view(merged.job)


@router.post("/receipts/scan-merge")
async def merge_receipt_scans(
    payload: ScanMergeInput,
    request: Request,
    background: BackgroundTasks,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """A УПД of several sheets: the stored scans (links, page order) are glued into one PDF and stored with a done job;
    answers the job view, its `result` already in when every sheet was read before."""
    return await run_in_threadpool(_merge, request, background, payload.files, principal, now, uow_factory)


class AgentClaimInput(BaseModel):
    host: str = Field(default="", max_length=128)
    machine_id: str = Field(default="", max_length=scan_agents.MACHINE_ID_MAX)


class Scanner(BaseModel):
    id: str = Field(default="", max_length=300)
    name: str = Field(max_length=200)


class AgentHeartbeatInput(BaseModel):
    machine_id: str = Field(min_length=1, max_length=scan_agents.MACHINE_ID_MAX)
    pc_name: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=32)
    lan_ip: str = Field(default="", max_length=64)
    scanners: list[Scanner] = Field(default_factory=list, max_length=20)


@router.post("/scan/agent/claim")
def scan_agent_claim(
    request: Request,
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: AgentClaimInput | None = None,
) -> dict:
    who = payload or AgentClaimInput()
    machine_id = scan_agents.touch(uow_factory(), machine_id=who.machine_id, host=who.host, ip_seen=_client_ip(request),
                                   now=now)
    job_id = claim(uow_factory(), now=now, machine_id=machine_id)
    return {"job": {"id": str(job_id)} if job_id else None}


@router.post("/scan/agent/heartbeat")
def scan_agent_heartbeat(
    payload: AgentHeartbeatInput,
    request: Request,
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    try:
        scan_agents.heartbeat(
            uow_factory(), machine_id=payload.machine_id, pc_name=payload.pc_name, version=payload.version,
            ip_seen=_client_ip(request), ip_lan=payload.lan_ip,
            scanners=[item.model_dump() for item in payload.scanners], now=now)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"ok": True, "update": update_for(payload.version)}


def _error_text(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or "error" not in payload:
        return None
    return str(payload["error"] or "")


@router.post("/scan/agent/jobs/{job_id}/result")
async def scan_agent_result(
    job_id: UUID,
    request: Request,
    background: BackgroundTasks,
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if "multipart/form-data" in content_type.lower():
        if len(body) > SCAN_FILE_LIMIT + 64 * 1024:
            await run_in_threadpool(
                lambda: _queue(lambda: fail_job(uow_factory(), job_id, error=TOO_BIG, now=now)))
            raise HTTPException(status_code=422, detail=TOO_BIG)
        upload = multipart_file(content_type, body)
        if upload is None:
            raise HTTPException(status_code=422, detail=NO_FILE)
        file_name, content = upload
        await run_in_threadpool(
            lambda: _queue(lambda: _checked(lambda: store_job_scan(uow_factory(), job_id, file_name, content, now))))
        job = await run_in_threadpool(lambda: get_job(uow_factory(), job_id, now=now))
        _recognise_later(background, uow_factory, scan_sources(request, uow_factory), job)
        return {"ok": True, "id": str(job_id), "status": DONE}
    error = _error_text(body)
    if error is None:
        raise HTTPException(
            status_code=422, detail="Нужен файл скана (multipart, поле «file») или JSON {\"error\": \"…\"}",
        )
    await run_in_threadpool(lambda: _queue(lambda: fail_job(uow_factory(), job_id, error=error, now=now)))
    return {"ok": True, "id": str(job_id), "status": FAILED}
