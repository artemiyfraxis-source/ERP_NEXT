"""«Отсканировать УПД» со сканера склада — HTTP contract (owner 23.10.2026; the queue itself — `scan_jobs.py`).

The storekeeper's screen (warehouse edit, as `POST /api/stock/receipts`):
- `POST /api/stock/receipts/scan-jobs` (no body) → `{id, status}` — a pending job for the agent of the warehouse PC;
- `GET /api/stock/receipts/scan-jobs/{id}` → `{id, status, error, result}`; `result` is exactly the answer of
  `POST /api/stock/receipts/scan` (`{scan_file, recognised, lines, note}`) once the job is done, else null. A job
  pending over 120 s or claimed over 180 s reads (and stays) failed «Сканер не ответил — проверьте агент на ПК склада».
- `DELETE /api/stock/receipts/scan-jobs/{id}` → `{id, status}` — an open job becomes failed «Отменено», a finished
  one is answered as it is.

The agent of the warehouse PC calls with `Authorization: Bearer <API token with the scope scan:upload>` (one token
may carry `labels:print` and `scan:upload` together); a person's session is refused (403):
- `POST /api/stock/scan/agent/claim` → `{job: {id} | null}` — the oldest pending job becomes claimed;
- `POST /api/stock/scan/agent/jobs/{id}/result` — multipart field `file` (PDF / JPG / PNG ≤ 20 MB) → the file is
  stored, the job done; or JSON `{"error": "…"}` → the job failed with that reason. Answer `{ok: true, id, status}`;
  404 — no such job, 409 — its result is already in or it was never handed out (a retry: drop the job), 422 — no file
  or a file ERP does not keep (the job is failed with that reason). Recognition (OCR included) runs after the answer,
  so the agent never waits for it; the screen's poll waits for it instead and the answer is stored once."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.file_types import sniffed_type
from app.shared.http_auth import TOKEN_SCOPE_PREFIX, Principal, authenticated_principal, warehouse_edit_principal

from .api import warehouse_uow_factory
from .application import UnitOfWork
from .domain import RECEIPT_SCAN_PATH
from .receipts_api import ScanSources, _checked, multipart_file, scan_answer, scan_sources
from .receipts_application import SCAN_FILE_LIMIT, scan_file, store_job_scan
from .scan_jobs import (
    DONE, FAILED, ScanJob, ScanJobClosed, cancel_job, claim, create_job, fail_job, get_job, recognition_lock,
    save_result, stored_result,
)

SCAN_SCOPE = "scan:upload"
SCAN_AGENT_ONLY = "Очередь сканирования забирает только агент склада (токен API «scan:upload»)."
router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]
log = logging.getLogger(__name__)


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


def job_result(uow_factory: Callable[[], UnitOfWork], sources: ScanSources, job: ScanJob) -> dict | None:
    """The done job's answer: the stored one, else made now (and stored once). Blocking — run it off the event loop."""
    if job.status != DONE or job.file_id is None:
        return None
    if job.result:
        return json.loads(job.result)
    # One recognition per job at a time in every API process: the screen polls while the first answer is being made.
    with recognition_lock(uow_factory(), job.id):
        stored = stored_result(uow_factory(), job.id)  # made by another request while this one waited
        if stored:
            return json.loads(stored)
        found = scan_file(uow_factory(), job.file_id)
        content = found[1] if found else b""
        link = f"{RECEIPT_SCAN_PATH}{job.file_id}"
        answer = scan_answer(sources, link, content, sniffed_type(content))
        save_result(uow_factory(), job.id, json.dumps(answer, ensure_ascii=False))
        return answer


def _recognise_after_upload(uow_factory, sources: ScanSources, job_id: UUID, now: datetime) -> None:
    try:
        job_result(uow_factory, sources, get_job(uow_factory(), job_id, now=now))
    except Exception:  # noqa: BLE001 — the screen's poll makes the answer itself when this one fails
        log.exception("Распознавание скана УПД по заданию %s не удалось", job_id)


@router.post("/receipts/scan-jobs")
def new_scan_job(
    principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    return _brief(create_job(uow_factory(), requested_by=principal.actor.name, now=now))


@router.get("/receipts/scan-jobs/{job_id}")
def scan_job(
    job_id: UUID,
    request: Request,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job = _queue(lambda: get_job(uow_factory(), job_id, now=now))
    result = job_result(uow_factory, scan_sources(request, uow_factory), job)
    return {**_brief(job), "error": job.error, "result": result}


@router.delete("/receipts/scan-jobs/{job_id}")
def cancel_scan_job(
    job_id: UUID,
    _principal: Annotated[Principal, Depends(warehouse_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    return _brief(_queue(lambda: cancel_job(uow_factory(), job_id, now=now)))


@router.post("/scan/agent/claim")
def scan_agent_claim(
    _agent: Annotated[Principal, Depends(scan_agent_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    job_id = claim(uow_factory(), now=now)
    return {"job": {"id": str(job_id)} if job_id else None}


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
                lambda: _queue(lambda: fail_job(uow_factory(), job_id, error="Скан УПД больше 20 МБ", now=now)))
            raise HTTPException(status_code=422, detail="Скан УПД больше 20 МБ")
        upload = multipart_file(content_type, body)
        if upload is None:
            raise HTTPException(status_code=422, detail="Файл не передан: нужен multipart с полем «file»")
        file_name, content = upload
        await run_in_threadpool(
            lambda: _queue(lambda: _checked(lambda: store_job_scan(uow_factory(), job_id, file_name, content, now))))
        background.add_task(
            _recognise_after_upload, uow_factory, scan_sources(request, uow_factory), job_id, now,
        )
        return {"ok": True, "id": str(job_id), "status": DONE}
    error = _error_text(body)
    if error is None:
        raise HTTPException(
            status_code=422, detail="Нужен файл скана (multipart, поле «file») или JSON {\"error\": \"…\"}",
        )
    await run_in_threadpool(lambda: _queue(lambda: fail_job(uow_factory(), job_id, error=error, now=now)))
    return {"ok": True, "id": str(job_id), "status": FAILED}
