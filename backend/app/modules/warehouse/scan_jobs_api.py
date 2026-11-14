"""Shared scan transport and authenticated device heartbeats.

Scan creation accepts an explicit device owner/scanner pair, or automatically chooses
one online scanner. Device presence comes only from heartbeats; browser addresses do
not route jobs. Scanner preferences are stored by the shared UI in personal settings.
Device jobs expose status and cancellation only to their requester. Agent tokens are
machine-bound for heartbeat, claim and result, including scan-only requests.
Receipt uploads, recognition and merge retain their existing transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.vna_capture import CaptureSettings
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, require_section, warehouse_edit_principal
from app.workflows.edo_receipt import EdoRejected, file_kind, is_edo_upload, read_delivery

from . import devices, scan_agents, vna_jobs
from .agent_release import update_for
from .api import warehouse_uow_factory
from .application import UnitOfWork
from .domain import RECEIPT_SCAN_PATH
from .receipts_api import _checked, multipart_file
from .receipts_application import SCAN_FILE_LIMIT, merge_scans, store_edo_file, store_job_scan, store_scan
from .scan_jobs import (
    DONE,
    FAILED,
    SCANNER_MAX,
    VNA_FILE_LIMIT,
    AgentMachineRefused,
    ScanJob,
    ScanJobClosed,
    agent_machine,
    cancel_job,
    claim,
    create_job,
    fail_job,
    get_job,
    save_result,
)
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
# «Сканирование документов» (owner 28.10.2026): the scan buttons, their jobs, the agent list and the agent install
# (workflows/scan_agent_install.py) — whoever may scan may also set up their own PC's agent.
scan_edit_principal = require_section("scan", "edit", detail="Сканирование документов вам не открыто.")
ScanPerson = Annotated[Principal, Depends(scan_edit_principal)]


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
    except AgentMachineRefused as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


def _recognise_later(background: BackgroundTasks, uow_factory, sources: ScanSources, job: ScanJob,
                     pages: list[UUID] | None = None) -> None:
    """After the answer is sent: the job's reading (`recognise_job` holds a database lock — one per job in every process)."""
    if job.status == DONE and not job.result:
        background.add_task(recognise_job, uow_factory, sources, job, pages)


class ScanStartInput(BaseModel):
    agent_id: str = Field(default="", max_length=scan_agents.MACHINE_ID_MAX)
    target_scanner: str = Field(default="", max_length=SCANNER_MAX)


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers puts the real address here from Caddy's X-Forwarded-For.
    return request.client.host if request.client else ""


@router.get("/scan/agents")
def scan_agent_list(
    _principal: ScanPerson,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    return {"agents": devices.scanner_agents(uow_factory(), now),
            "offline_devices": devices.offline_scanners(uow_factory(), now)}


@router.post("/receipts/scan-jobs")
def new_scan_job(
    request: Request,
    principal: ScanPerson,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: ScanStartInput | None = None,
) -> dict:
    wish = payload or ScanStartInput()
    agents = devices.scanner_agents(uow_factory(), now)
    choices = [(pc, scanner) for pc in agents for scanner in pc["scanners"]]
    if not choices:
        return {"status": "no_agent", "idle": [],
                "message": "Нет сканеров в сети. Попросите администратора проверить агент на ПК со сканером."}
    selected = next(((pc, scanner) for pc, scanner in choices
                     if pc["id"] == wish.agent_id and scanner["id"] == wish.target_scanner), None)
    if selected is None and wish.agent_id and wish.target_scanner:
        by_name = [(pc, scanner) for pc, scanner in choices
                   if pc["id"] == wish.agent_id and scanner["name"] == wish.target_scanner]
        if len(by_name) == 1:
            selected = by_name[0]
    if not wish.agent_id and not wish.target_scanner and len(choices) == 1:
        selected = choices[0]
    if selected is None:
        return {"status": "choose", "agents": agents}
    pc, scanner = selected
    return {**_brief(create_job(uow_factory(), requested_by=principal.actor.name, requester_id=principal.actor.id, now=now,
                               target=pc["id"], scanner=scanner["id"])),
            "device": {"agent_id": pc["id"], "scanner_id": scanner["id"], "name": scanner["name"], "pc": pc["pc"]}}


@router.get("/receipts/scan-jobs/{job_id}")
def scan_job(
    job_id: UUID,
    request: Request,
    background: BackgroundTasks,
    _principal: ScanPerson,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    if job.kind != "scan" or (job.target_machine_id and job.requester_id != _principal.actor.id):
        raise HTTPException(404, "Задание сканирования не найдено")
    _recognise_later(background, uow_factory, scan_sources(request, uow_factory), job)
    return job_view(job)


@router.delete("/receipts/scan-jobs/{job_id}")
def cancel_scan_job(
    job_id: UUID,
    _principal: ScanPerson,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    if job.kind != "scan" or (job.target_machine_id and job.requester_id != _principal.actor.id):
        raise HTTPException(404, "Задание сканирования не найдено")
    return _brief(_queue(lambda: cancel_job(uow_factory(), job_id, now=now)))


@router.post("/receipts/scan")
async def scan_receipt_document(
    request: Request,
    background: BackgroundTasks,
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],  # «Файл…» is no scanning: the section's right
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
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],  # pages of a scan or of files
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    """A УПД of several sheets: the stored scans (links, page order) are glued into one PDF and stored with a done job;
    answers the job view, its `result` already in when every sheet was read before."""
    return await run_in_threadpool(_merge, request, background, payload.files, principal, now, uow_factory)


class AgentClaimInput(BaseModel):
    host: str = Field(default="", max_length=128)
    machine_id: str = Field(default="", max_length=scan_agents.MACHINE_ID_MAX)
    kinds: list[Literal["scan", "vna"]] = Field(default_factory=list, max_length=2)


class Scanner(BaseModel):
    id: str = Field(default="", max_length=300)
    name: str = Field(max_length=200)


class VnaCapability(BaseModel):
    name: str = Field(max_length=500)
    ready: bool
    reason: Literal["", "s2vna_not_running", "device_not_connected", "probe_failed"] = ""


class AgentHeartbeatInput(BaseModel):
    machine_id: str = Field(min_length=1, max_length=scan_agents.MACHINE_ID_MAX)
    pc_name: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=32)
    lan_ip: str = Field(default="", max_length=64)
    scanners: list[Scanner] = Field(default_factory=list, max_length=20)
    vna: VnaCapability | None = None
    vna_sources: list[Literal["s2vna", "arinst"]] | None = Field(default=None, max_length=2)


@router.post("/scan/agent/claim")
def scan_agent_claim(
    request: Request,
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
    payload: AgentClaimInput | None = None,
) -> dict:
    who = payload or AgentClaimInput()
    machine_id = _queue(lambda: agent_machine(uow_factory(), _agent.actor.id, who.machine_id))
    machine_id = scan_agents.touch(uow_factory(), machine_id=machine_id, host=who.host, ip_seen=_client_ip(request),
                                   now=now)
    job = claim(uow_factory(), now=now, machine_id=machine_id, vna="vna" in who.kinds)
    return {"job": {"id": str(job.id), "target_scanner": job.target_scanner,
                    **({"kind": job.kind, "source": job.source} if who.kinds else {})} if job else None}


@router.post("/scan/agent/heartbeat")
def scan_agent_heartbeat(
    payload: AgentHeartbeatInput,
    request: Request,
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    # Shared heartbeat: protect the registry even when a caller omits VNA capabilities.
    machine_id = _queue(lambda: agent_machine(uow_factory(), _agent.actor.id, payload.machine_id, bind=True))
    try:
        scan_agents.heartbeat(
            uow_factory(), machine_id=machine_id, pc_name=payload.pc_name, version=payload.version,
            ip_seen=_client_ip(request), ip_lan=payload.lan_ip,
            scanners=[item.model_dump() for item in payload.scanners], now=now,
            vna=payload.vna.model_dump() if payload.vna else None, vna_sources=payload.vna_sources)
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
    job = await run_in_threadpool(lambda: _queue(lambda: get_job(uow_factory(), job_id, now=now)))
    machine_id = await run_in_threadpool(lambda: _queue(lambda: agent_machine(
        uow_factory(), _agent.actor.id, request.headers.get("x-agent-machine-id", ""))))
    if job.target_machine_id != machine_id:
        raise HTTPException(403, "Задание выдано другому ПК")
    if job.kind == "vna":
        return await _vna_result(job, request, now, uow_factory, machine_id)
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


async def _vna_result(job: ScanJob, request: Request, now: datetime, uow_factory, machine_id: str) -> dict:
    if not machine_id or machine_id != job.target_machine_id:
        raise HTTPException(403, "Задание ВАЦ выдано другому ПК")
    if job.status != "claimed":
        raise HTTPException(409, "Задание ВАЦ не ожидает результат")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > VNA_FILE_LIMIT + 64 * 1024:
            await run_in_threadpool(lambda: _queue(lambda: fail_job(
                uow_factory(), job.id, error="Файл ВАЦ больше 2 МиБ", now=now)))
            raise HTTPException(422, "Файл ВАЦ больше 2 МиБ")
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type.lower():
        upload = multipart_file(content_type, bytes(body))
        if upload is None:
            error = NO_FILE
        else:
            name, data = upload
            try:
                settings_part = multipart_file(content_type, bytes(body), field="capture_settings")
                settings = (CaptureSettings.model_validate_json(settings_part[1]).model_dump(exclude_none=True)
                            if settings_part else None)
                await run_in_threadpool(lambda: _queue(lambda: vna_jobs.complete(
                    uow_factory(), job.id, data, name, now, machine_id, settings)))
                return {"ok": True, "id": str(job.id), "status": DONE}
            except ValidationError:
                error = "Некорректные настройки измерения ВАЦ"
            except ValueError as invalid:
                error = str(invalid)
        await run_in_threadpool(lambda: _queue(lambda: fail_job(uow_factory(), job.id, error=error, now=now)))
        raise HTTPException(422, error)
    error = _error_text(bytes(body))
    if error is None:
        raise HTTPException(422, "Нужен файл .s1p или JSON с причиной ошибки")
    await run_in_threadpool(lambda: _queue(lambda: fail_job(uow_factory(), job.id, error=error, now=now)))
    return {"ok": True, "id": str(job.id), "status": FAILED}
