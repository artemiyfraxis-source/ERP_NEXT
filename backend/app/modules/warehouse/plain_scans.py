"""Plain scans of the МФУ card in «Оборудование» (owner 20.11.2026: «это не только сканер»), migration 0214.

The same scan queue and agent op as «Отсканировать УПД» (`scan_jobs.py`), only as transport: a job with purpose 'plain'
carries the options the person chose (`capture_settings`: colour «color» / «gray», dpi 150 / 300 / 600) and is never
read by the document model — a CHECK keeps its `result` empty and the УПД routes answer 404 for it. The file is kept in
`warehouse_receipt_files` like every scan, but no delivery points at it, so it never reaches the УПД lists.

«Несколько страниц в один PDF» on a flatbed: the next sheet is a new job that names the scan it continues
(`capture_settings.append_to`); when its sheet arrives, the earlier PDF and the new pages become one new file of this job.
The earlier scan is then hidden from the card's list (its pages live on in the longer one)."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from uuid import UUID, uuid4

from sqlalchemy import select

from .application import UnitOfWork
from .domain import WarehouseValidationError
from .infrastructure import WarehouseReceiptFileRow
from .receipts_application import SCAN_FILE_LIMIT, as_pdf, checked_scan
from .scan_jobs import (
    CANCELLED, DONE, FAILED, OPEN_STATUSES, ScanJob, ScanJobRow, _expire, _session, create_job, fail_job,
    finish_with_file, open_for_result,
)
from .scan_agents import LIVE_FOR, ScanAgentRow

COLORS = ("color", "gray")
DPIS = (150, 300, 600)
PAGES_MAX = 50
RECENT = 20
TOO_BIG = "Скан больше 20 МБ — выберите меньшее разрешение или ч/б"


def settings_of(color: str, dpi: int, append_to: UUID | None) -> dict:
    if color not in COLORS:
        raise WarehouseValidationError("Цвет скана — цветной или чёрно-белый")
    if dpi not in DPIS:
        raise WarehouseValidationError("Разрешение скана — 150, 300 или 600 dpi")
    return {"color": color, "dpi": dpi, **({"append_to": str(append_to)} if append_to else {})}


def start(uow_factory, *, machine_id: str, scanner: str, color: str, dpi: int, append_to: UUID | None,
          actor, now: datetime) -> ScanJob:
    """A plain scan on that PC's scanner. The scanner must be reported by the PC now; a continued scan must be a done
    plain scan of the same scanner (its pages go first)."""
    from .devices import DeviceRow

    settings = settings_of(color, dpi, append_to)
    with uow_factory() as uow:
        session = _session(uow)
        live = session.scalar(select(DeviceRow).join(ScanAgentRow).where(
            DeviceRow.machine_id == machine_id, DeviceRow.kind == "scan", DeviceRow.device_id == scanner,
            DeviceRow.present.is_(True), DeviceRow.last_seen >= now - LIVE_FOR, ScanAgentRow.legacy.is_(False)))
        if live is None:
            raise WarehouseValidationError("Сканер МФУ сейчас не на связи — проверьте, что МФУ и ПК включены")
        if append_to is not None:
            first = session.get(ScanJobRow, append_to)
            if (first is None or first.purpose != "plain" or first.status != DONE or first.target_machine_id != machine_id
                    or first.target_scanner != scanner):
                raise WarehouseValidationError("Скан, к которому добавляется страница, не найден — начните новый")
            if int((first.capture_settings or {}).get("pages") or 1) >= PAGES_MAX:
                raise WarehouseValidationError(f"В одном PDF — не больше {PAGES_MAX} страниц")
    return create_job(uow_factory(), requested_by=actor.name, requester_id=actor.id, now=now, target=machine_id,
                      scanner=scanner, settings=settings)


def _pdf_pages(content: bytes) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        return len(PdfReader(BytesIO(content)).pages)
    except (PyPdfError, ValueError, OSError):
        return 1


def _joined(first: bytes, second: bytes) -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PyPdfError

    writer = PdfWriter()
    try:
        for content in (first, second):
            for page in PdfReader(BytesIO(content)).pages:
                writer.add_page(page)
    except (PyPdfError, ValueError, OSError):
        raise WarehouseValidationError("Предыдущие страницы скана не читаются — начните новый скан") from None
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def store_plain_scan(uow: UnitOfWork, job_id: UUID, file_name: str, content: bytes, now: datetime) -> str:
    """The agent's file for a claimed plain scan: kept as PDF (after the pages of the scan it continues), the job done
    with its page count; never read. A file ERP does not keep fails the job with the reason (422)."""
    try:
        _name, kind = checked_scan(file_name, content)
    except WarehouseValidationError as error:
        fail_job(uow, job_id, error=str(error), now=now)
        raise
    _name, kind, content = as_pdf(_name, kind, content)
    with uow:
        session = uow.session
        job = open_for_result(session, job_id, now)
        if job.purpose != "plain":
            raise WarehouseValidationError("Это задание — скан УПД")
        settings = dict(job.capture_settings or {})
        if settings.get("append_to") and kind == "application/pdf":
            first = session.get(ScanJobRow, UUID(settings["append_to"]))
            stored = session.get(WarehouseReceiptFileRow, first.file_id) if first is not None and first.file_id else None
            if stored is not None:
                content = _joined(stored.content, content)
        if len(content) > SCAN_FILE_LIMIT:
            raise WarehouseValidationError(TOO_BIG)
        file_id = uuid4()
        uow.receipts.add_file(file_id, f"Скан {now:%d.%m.%Y %H-%M}.pdf" if kind == "application/pdf" else _name, kind,
                              content, job.requested_by, now)
        session.flush()
        finish_with_file(session, job_id, file_id, now)
        row = session.get(ScanJobRow, job_id)
        row.capture_settings = {**settings, "pages": _pdf_pages(content) if kind == "application/pdf" else 1}
        uow.commit()
    return str(file_id)


def _view(row: ScanJobRow) -> dict:
    settings = row.capture_settings or {}
    return {
        "id": str(row.id), "status": row.status, "error": row.error or "", "by": row.requested_by,
        "at": row.created_at.isoformat(), "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "color": settings.get("color", ""), "dpi": settings.get("dpi"), "pages": settings.get("pages"),
        "append_to": settings.get("append_to"), "scanner": row.target_scanner or "",
        "file": f"/api/stock/devices/scans/{row.id}/file" if row.status == DONE and row.file_id else "",
    }


def get(uow, job_id: UUID, now: datetime) -> dict:
    with uow:
        session = _session(uow)
        _expire(session, now)
        row = session.get(ScanJobRow, job_id, populate_existing=True)
        if row is None or row.purpose != "plain":
            raise KeyError("Скан не найден")
        view = _view(row)
        uow.commit()
    return view


def requester(uow, job_id: UUID) -> UUID | None:
    with uow:
        row = _session(uow).get(ScanJobRow, job_id)
        if row is None or row.purpose != "plain":
            raise KeyError("Скан не найден")
        return row.requester_id


def cancel(uow, job_id: UUID, now: datetime) -> dict:
    with uow:
        session = _session(uow)
        _expire(session, now)
        row = session.get(ScanJobRow, job_id, populate_existing=True)
        if row is None or row.purpose != "plain":
            raise KeyError("Скан не найден")
        if row.status in OPEN_STATUSES:
            row.status, row.finished_at, row.error = FAILED, now, CANCELLED
        view = _view(row)
        uow.commit()
    return view


def recent(uow, machine_id: str, now: datetime, limit: int = RECENT) -> list[dict]:
    """The PC's plain scans, newest first; a scan continued by a done later one is left out (its pages are in it)."""
    with uow:
        session = _session(uow)
        _expire(session, now)
        rows = list(session.scalars(select(ScanJobRow).where(
            ScanJobRow.purpose == "plain", ScanJobRow.target_machine_id == machine_id)
            .order_by(ScanJobRow.created_at.desc(), ScanJobRow.id).limit(limit * 3)))
        uow.commit()
    continued = {(row.capture_settings or {}).get("append_to") for row in rows if row.status == DONE}
    return [_view(row) for row in rows if str(row.id) not in continued][:limit]


def file_of(uow, job_id: UUID) -> tuple[str, bytes]:
    with uow:
        session = _session(uow)
        row = session.get(ScanJobRow, job_id)
        if row is None or row.purpose != "plain" or row.status != DONE or row.file_id is None:
            raise KeyError("Скан не найден")
        stored = session.get(WarehouseReceiptFileRow, row.file_id)
        if stored is None:
            raise KeyError("Файл скана не найден")
        return stored.file_name, stored.content
