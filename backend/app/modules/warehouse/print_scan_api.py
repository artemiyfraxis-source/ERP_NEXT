"""«Принтеры склада» in «Оборудование» (owner 20.11.2026: «по принтерам тоже функционала нет никакого»): the cards of the
label printer and of the МФУ. Migration 0214; queues in `label_jobs.py`, `document_print.py`, `plain_scans.py`.

Label printer (warehouse view; printing goes through the existing `POST /api/stock/labels/test` and
`POST /api/stock/batches/{lot_id}/label`, warehouse edit):
- `GET /api/stock/devices/labels` → `{agent, jobs}`: the print agent's last call and queue (`label_jobs.agent_status`)
  and the last TSPL jobs — what (lot ГРН, article, name / «Пробная этикетка» / «Этикетка СИ»), copies, who, when, state.

МФУ of a PC (`machine_id` — the agent's PC id; «Сканирование документов» on edit, like every scan button):
- `GET /api/stock/devices/mfp/{machine_id}` → `{pc, online, scanners, printers, scans, prints}`.
- `POST /api/stock/devices/mfp/{machine_id}/scan` `{scanner, color: color|gray, dpi: 150|300|600, append_to?}` → the
  plain scan (never read as a УПД); `GET|DELETE /api/stock/devices/scans/{id}`, `GET …/scans/{id}/file` — the PDF.
- `POST /api/stock/devices/mfp/{machine_id}/print?printer=&copies=` multipart `file` (PDF / JPG / PNG, ≤ 20 МБ);
  `POST …/print-test` `{printer}`; `POST /api/stock/devices/scans/{id}/print` `{printer, copies}` (a copy of a scan);
  `GET|DELETE /api/stock/devices/prints/{id}`.

The agent (`labels:print` token, PC-bound, agent 2.12.0+):
- `POST /api/stock/documents/agent/claim` `{machine_id, printers: [queue…]}` → `{job: {id, attempt, printer, title,
  pages, copies, sha256, pdf_b64} | null}`;
- `POST /api/stock/documents/agent/jobs/{id}/result` `{machine_id, attempt, outcome: spooled|failed, message}` →
  `{ok, status}`; 403 — another PC or token, 409 — a stale attempt or an answered job."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.file_types import sniffed_type
from app.shared.http_auth import Principal, warehouse_view_principal
from app.shared.stored_files import file_response

from . import document_print, plain_scans
from .api import warehouse_uow_factory
from .application import UnitOfWork
from .devices import DeviceRow, PrinterRow
from .domain import WarehouseValidationError
from .infrastructure import StockLotRow
from .label_jobs import LabelJobRow, agent_status
from .labels_api import agent_principal
from .receipts_api import multipart_file
from .receipts_application import as_pdf
from .scan_agents import LIVE_FOR, ScanAgentRow
from .scan_jobs import AgentMachineRefused, AgentTokenRow, ScanJobClosed, ScanJobRow, agent_machine
from .scan_jobs_api import scan_edit_principal

router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
ScanPerson = Annotated[Principal, Depends(scan_edit_principal)]
Agent = Annotated[Principal, Depends(agent_principal)]
LABEL_JOBS = 20
UPLOAD_SLACK = 64 * 1024
KIND_TEXT = {"sample": "Пробная этикетка", "instrument": "Этикетка СИ"}


def _answer(operation):
    try:
        return operation()
    except WarehouseValidationError as error:
        raise HTTPException(422, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, error.args[0]) from error
    except (document_print.PrintJobClosed, ScanJobClosed) as error:
        raise HTTPException(409, str(error)) from error
    except (AgentMachineRefused, PermissionError) as error:
        raise HTTPException(403, str(error)) from error


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/devices/labels")
def label_printer(_principal: Annotated[Principal, Depends(warehouse_view_principal)], uow_factory: UowFactory) -> dict:
    with uow_factory() as uow:
        rows = uow.session.execute(select(LabelJobRow.id, LabelJobRow.kind, LabelJobRow.lot_id, LabelJobRow.copies,
            LabelJobRow.created_by, LabelJobRow.created_at, LabelJobRow.status, LabelJobRow.finished_at, LabelJobRow.error)
            .where(LabelJobRow.format == "tspl").order_by(LabelJobRow.created_at.desc(), LabelJobRow.id).limit(LABEL_JOBS)).all()
        lots = {lot.id: lot for lot in uow.session.scalars(select(StockLotRow).where(
            StockLotRow.id.in_([row.lot_id for row in rows if row.lot_id])))} if any(row.lot_id for row in rows) else {}
    jobs = []
    for row in rows:
        lot = lots.get(row.lot_id) if row.lot_id else None
        what = KIND_TEXT.get(row.kind) or (f"ГРН {lot.grn}{'/' + lot.package if lot.package else ''}" if lot else "Партия удалена")
        jobs.append({"id": str(row.id), "kind": row.kind, "what": what, "article": lot.article if lot else "",
                     "name": lot.name if lot else "", "warehouse": lot.warehouse if lot else "",
                     "lot_id": str(row.lot_id) if lot else None, "copies": row.copies, "by": row.created_by,
                     "at": _moment(row.created_at), "status": row.status, "finished_at": _moment(row.finished_at),
                     "error": row.error or ""})
    return {"agent": agent_status(uow_factory()), "jobs": jobs}


def _fresh(moment: datetime | None, now: datetime) -> bool:
    if moment is None:
        return False
    moment = moment.replace(tzinfo=now.tzinfo) if moment.tzinfo is None else moment
    return moment >= now - LIVE_FOR


@router.get("/devices/mfp/{machine_id}")
def mfp(machine_id: str, _principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    with uow_factory() as uow:
        session = uow.session
        agent = session.get(ScanAgentRow, machine_id)
        if agent is None:
            raise HTTPException(404, "ПК не найден")
        online = _fresh(agent.last_seen, now) and not agent.legacy
        scanners = [{"id": row.device_id, "name": row.name, "present": online and row.present and _fresh(row.last_seen, now)}
                    for row in session.scalars(select(DeviceRow).where(DeviceRow.machine_id == machine_id,
                                                                       DeviceRow.kind == "scan").order_by(DeviceRow.name))]
        printers = [{"name": row.name, "driver": row.driver, "present": online and row.present and _fresh(row.last_seen, now)}
                    for row in session.scalars(select(PrinterRow).where(PrinterRow.machine_id == machine_id,
                                                                        PrinterRow.kind == "document").order_by(PrinterRow.name))]
        pc = {"id": machine_id, "name": agent.pc_name or machine_id, "version": agent.version,
              "last_seen": _moment(agent.last_seen)}
    return {"pc": pc, "online": online, "scanners": scanners, "printers": printers,
            "scans": plain_scans.recent(uow_factory(), machine_id, now),
            "prints": document_print.recent(uow_factory(), machine_id, now)}


class ScanInput(BaseModel):
    scanner: str = Field(min_length=1, max_length=300)
    color: Literal["color", "gray"] = "gray"
    dpi: Literal[150, 300, 600] = 300
    append_to: UUID | None = None


@router.post("/devices/mfp/{machine_id}/scan")
def start_scan(machine_id: str, payload: ScanInput, principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    job = _answer(lambda: plain_scans.start(uow_factory, machine_id=machine_id, scanner=payload.scanner,
        color=payload.color, dpi=payload.dpi, append_to=payload.append_to, actor=principal.actor, now=now))
    return plain_scans.get(uow_factory(), job.id, now)


@router.get("/devices/scans/{job_id}")
def scan(job_id: UUID, _principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    return _answer(lambda: plain_scans.get(uow_factory(), job_id, now))


@router.delete("/devices/scans/{job_id}")
def cancel_scan(job_id: UUID, _principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    return _answer(lambda: plain_scans.cancel(uow_factory(), job_id, now))


@router.get("/devices/scans/{job_id}/file")
def scan_file(job_id: UUID, _principal: ScanPerson, uow_factory: UowFactory,
              download: Annotated[bool, Query()] = False) -> Response:
    name, content = _answer(lambda: plain_scans.file_of(uow_factory(), job_id))
    return file_response(content, name, download=download)


@router.post("/devices/mfp/{machine_id}/print")
async def print_upload(
    machine_id: str,
    request: Request,
    principal: ScanPerson,
    now: Now,
    uow_factory: UowFactory,
    printer: Annotated[str, Query(min_length=1, max_length=255)],
    copies: Annotated[int, Query(ge=1, le=document_print.COPIES_MAX)] = 1,
) -> dict:
    body = await request.body()
    if len(body) > document_print.PDF_LIMIT + UPLOAD_SLACK:
        raise HTTPException(422, "Файл для печати — до 20 МБ")
    upload = multipart_file(request.headers.get("content-type", ""), body)
    if upload is None:
        raise HTTPException(422, "Файл не передан: нужен multipart с полем «file»")
    name, content = upload

    def queue() -> dict:
        kind = sniffed_type(content)
        if kind not in ("application/pdf", "image/jpeg", "image/png"):
            raise WarehouseValidationError("Печатать можно PDF, JPG или PNG")
        title, _kind, pdf = as_pdf(str(name or "").strip() or "Документ.pdf", kind, content)
        return document_print.enqueue(uow_factory(), machine_id=machine_id, printer=printer, pdf=pdf, title=title,
                                      source="upload", copies=copies, actor=principal.actor, now=now)

    return await run_in_threadpool(lambda: _answer(queue))


class TestPageInput(BaseModel):
    printer: str = Field(min_length=1, max_length=255)


@router.post("/devices/mfp/{machine_id}/print-test")
def print_test(machine_id: str, payload: TestPageInput, principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    with uow_factory() as uow:
        agent = uow.session.get(ScanAgentRow, machine_id)
        pc = agent.pc_name if agent else ""
    pdf = document_print.test_page(payload.printer, pc, principal.actor.name, now)
    return _answer(lambda: document_print.enqueue(uow_factory(), machine_id=machine_id, printer=payload.printer, pdf=pdf,
        title="Пробная страница", source="test", copies=1, actor=principal.actor, now=now))


class CopyInput(BaseModel):
    printer: str = Field(min_length=1, max_length=255)
    copies: int = Field(default=1, ge=1, le=document_print.COPIES_MAX)


@router.post("/devices/scans/{job_id}/print")
def print_scan(job_id: UUID, payload: CopyInput, principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    def queue() -> dict:
        name, content = plain_scans.file_of(uow_factory(), job_id)
        with uow_factory() as uow:
            machine = uow.session.scalar(select(ScanJobRow.target_machine_id).where(ScanJobRow.id == job_id))
        kind = "application/pdf" if content[:5] == b"%PDF-" else sniffed_type(content) or ""
        title, _kind, pdf = as_pdf(name, kind, content)
        # The copy goes to a document printer of the PC that scanned it (`enqueue` checks the queue is there now).
        return document_print.enqueue(uow_factory(), machine_id=machine or "", printer=payload.printer, pdf=pdf,
                                      title=title, source="scan", copies=payload.copies, actor=principal.actor, now=now)

    return _answer(queue)


@router.get("/devices/prints/{job_id}")
def print_job(job_id: UUID, _principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    return _answer(lambda: document_print.job(uow_factory(), job_id, now))


@router.delete("/devices/prints/{job_id}")
def cancel_print(job_id: UUID, _principal: ScanPerson, now: Now, uow_factory: UowFactory) -> dict:
    return _answer(lambda: document_print.cancel(uow_factory(), job_id, now))


# --- the agent --------------------------------------------------------------------------------------------------


def _bound_machine(uow_factory, token_id: UUID, claimed: str) -> str:
    with uow_factory() as uow:
        token = uow.session.get(AgentTokenRow, token_id)
        bound = token.machine_id if token else ""
    return agent_machine(uow_factory(), token_id, claimed or bound)


class AgentClaimInput(BaseModel):
    machine_id: str = Field(default="", max_length=80)
    printers: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(default_factory=list, max_length=20)


@router.post("/documents/agent/claim")
def agent_claim(payload: AgentClaimInput, agent: Agent, now: Now, uow_factory: UowFactory) -> dict:
    machine = _answer(lambda: _bound_machine(uow_factory, agent.actor.id, payload.machine_id))
    with uow_factory() as uow:
        # Only the queues this PC reports as document printers now.
        reported = set(uow.session.scalars(select(PrinterRow.name).where(PrinterRow.machine_id == machine,
            PrinterRow.kind == "document", PrinterRow.present.is_(True))))
    printers = [name for name in payload.printers if name in reported]
    if not printers:
        return {"job": None}
    return {"job": document_print.claim(uow_factory(), machine_id=machine, token_id=agent.actor.id, printers=printers,
                                        now=now)}


class AgentResultInput(BaseModel):
    machine_id: str = Field(default="", max_length=80)
    attempt: str = Field(min_length=1, max_length=36)
    outcome: Literal["spooled", "failed"]
    message: str = Field(default="", max_length=2000)


@router.post("/documents/agent/jobs/{job_id}/result")
def agent_result(job_id: UUID, payload: AgentResultInput, agent: Agent, now: Now, uow_factory: UowFactory) -> dict:
    def answer() -> dict:
        machine = _bound_machine(uow_factory, agent.actor.id, payload.machine_id)
        status = document_print.report(uow_factory(), job_id, machine_id=machine, token_id=agent.actor.id,
                                       attempt=payload.attempt, outcome=payload.outcome, message=payload.message, now=now)
        return {"ok": True, "status": status}

    return _answer(answer)
