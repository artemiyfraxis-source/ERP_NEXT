"""Document printing on the МФУ of the warehouse (owner 20.11.2026: «я хочу и печатать на нём»), migration 0214.

The МФУ hangs on the warehouse Windows PC like the TSC; the agent of that PC reports its Windows queue as a printer of
kind 'document' (agent 2.12.0) and prints a PDF there: it renders each page with the Windows PDF engine and sends the
pages to the queue `copies` times, collated. A job names the exact PC and queue (FK to the printer row).

Status machine, held by CHECK constraints and the conditional UPDATEs below:
    pending ──claim──▶ taken ──receipt──▶ spooled | failed
       │                 └── lease ran out ──▶ unknown (the operator checks the tray; never printed twice)
       └── «Отменить» ──▶ cancelled
A late receipt of an `unknown` job is still taken: it says what really happened. Finished jobs older than 30 days are
purged on claim (their PDFs with them)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, LargeBinary, String, Text, Uuid, delete, select, update,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

from .domain import WarehouseValidationError
from .scan_agents import LIVE_FOR, ScanAgentRow
from .scan_jobs import _session

PDF_LIMIT = 20 * 1024 * 1024
PAGES_MAX = 200
COPIES_MAX = 50
SHEETS_MAX = 500
LEASE = timedelta(minutes=5)  # rendering and spooling a long document on the PC
KEEP_FINISHED = timedelta(days=30)
ERROR_MAX = 2000
RECENT = 20
NO_ANSWER = "Агент не подтвердил печать — проверьте лоток МФУ, прежде чем печатать снова"
NO_REASON = "Агент не напечатал документ и не сказал почему"
CANCELLED_TEXT = "Отменено"
OPEN = ("pending", "taken")
FINISHED = ("spooled", "failed", "unknown", "cancelled")
_FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"


class DocumentPrintJobRow(Base):
    __tablename__ = "warehouse_document_print_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["target_machine_id", "target_printer"], ["warehouse_agent_printers.machine_id",
                             "warehouse_agent_printers.name"], name="fk_document_print_job_printer", ondelete="RESTRICT"),
        CheckConstraint("status IN ('pending', 'taken', 'spooled', 'failed', 'unknown', 'cancelled')",
                        name="ck_document_print_job_status"),
        CheckConstraint("source IN ('upload', 'test', 'scan')", name="ck_document_print_job_source"),
        CheckConstraint("trim(title) <> ''", name="ck_document_print_job_title"),
        CheckConstraint("length(pdf) BETWEEN 1 AND 20971520", name="ck_document_print_job_pdf"),
        CheckConstraint("length(sha256) = 64", name="ck_document_print_job_sha256"),
        CheckConstraint("pages BETWEEN 1 AND 200 AND copies BETWEEN 1 AND 50 AND pages * copies <= 500",
                        name="ck_document_print_job_amount"),
        CheckConstraint("trim(target_printer) <> ''", name="ck_document_print_job_printer"),
        CheckConstraint("(status = 'pending' AND taken_at IS NULL AND attempt IS NULL AND claim_token_id IS NULL) "
                        "OR (status = 'cancelled' AND taken_at IS NULL AND attempt IS NULL) "
                        "OR (status NOT IN ('pending', 'cancelled') AND taken_at IS NOT NULL AND attempt IS NOT NULL)",
                        name="ck_document_print_job_taken"),
        CheckConstraint("(status = 'taken') = (lease_until IS NOT NULL)", name="ck_document_print_job_lease"),
        CheckConstraint("(status IN ('pending', 'taken')) = (finished_at IS NULL)", name="ck_document_print_job_finished"),
        CheckConstraint("status NOT IN ('failed', 'unknown', 'cancelled') OR trim(error) <> ''",
                        name="ck_document_print_job_error"),
        Index("ix_document_print_jobs_target", "target_machine_id", "target_printer", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    copies: Mapped[int] = mapped_column(Integer, nullable=False)
    target_machine_id: Mapped[str] = mapped_column(String(80), nullable=False)
    target_printer: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_token_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class PrintJobClosed(Exception):
    """The job is not waiting for this answer (409)."""


def pdf_pages(content: bytes) -> int:
    """Pages of a PDF ERP sends to the printer; WarehouseValidationError — not a PDF it can print."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if not content.lstrip()[:5].startswith(b"%PDF-"):
        raise WarehouseValidationError("Печатать можно PDF, JPG или PNG")
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise WarehouseValidationError("PDF защищён паролем — снимите защиту и загрузите снова")
        return len(reader.pages)
    except (PyPdfError, ValueError, OSError):
        raise WarehouseValidationError("Файл не читается как PDF") from None


def test_page(printer: str, pc: str, who: str, now: datetime) -> bytes:
    """«Пробная страница»: A4 with the queue, the PC, who and when — and a frame 10 mm in from the sheet's edges."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_creator("Fraxis ERP Next")
    pdf.set_title("Пробная страница")
    pdf.add_font("DejaVu", "", str(_FONTS / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(_FONTS / "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_line_width(0.4)
    pdf.rect(10, 10, 190, 277)
    pdf.set_xy(20, 24)
    pdf.set_font("DejaVu", "B", 20)
    pdf.cell(0, 10, "Пробная страница ERP", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 12)
    for line in (f"Принтер: {printer}", f"ПК: {pc or '—'}", f"Отправил: {who or '—'}", f"Время: {now:%d.%m.%Y %H:%M}",
                 "Рамка — 10 мм от края листа: если её видно целиком, поля в порядке."):
        pdf.set_x(20)
        pdf.multi_cell(170, 7, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _printer(session, machine_id: str, printer: str, now: datetime):
    from .devices import PrinterRow

    return session.scalar(select(PrinterRow).join(ScanAgentRow).where(
        PrinterRow.machine_id == machine_id, PrinterRow.name == printer, PrinterRow.kind == "document",
        PrinterRow.present.is_(True), PrinterRow.last_seen >= now - LIVE_FOR, ScanAgentRow.legacy.is_(False)))


def enqueue(uow, *, machine_id: str, printer: str, pdf: bytes, title: str, source: str, copies: int, actor,
            now: datetime) -> dict:
    """One PDF to the МФУ's queue. WarehouseValidationError — the printer is not reported now, too many sheets, not a
    printable PDF."""
    if not pdf or len(pdf) > PDF_LIMIT:
        raise WarehouseValidationError("Файл для печати — до 20 МБ")
    if not 1 <= copies <= COPIES_MAX:
        raise WarehouseValidationError(f"Копий — от 1 до {COPIES_MAX}")
    pages = pdf_pages(pdf)
    if not 1 <= pages <= PAGES_MAX:
        raise WarehouseValidationError(f"В документе {pages} стр. — печатаем до {PAGES_MAX}")
    if pages * copies > SHEETS_MAX:
        raise WarehouseValidationError(f"{pages} стр. × {copies} копий — больше {SHEETS_MAX} листов за раз")
    title = " ".join(str(title or "").split())[:255] or "Документ.pdf"
    with uow:
        session = _session(uow)
        if _printer(session, machine_id, printer, now) is None:
            raise WarehouseValidationError("МФУ сейчас не на связи — проверьте, что МФУ и ПК включены")
        row = DocumentPrintJobRow(id=uuid4(), created_at=now, created_by=actor.name[:255] or "—", requester_id=actor.id,
                                  title=title, source=source, pdf=pdf, sha256=hashlib.sha256(pdf).hexdigest(),
                                  pages=pages, copies=copies, target_machine_id=machine_id, target_printer=printer,
                                  status="pending", error="")
        session.add(row)
        session.flush()
        view = _view(row)
        uow.commit()
    return view


def _expire(session, now: datetime) -> None:
    session.execute(update(DocumentPrintJobRow).where(DocumentPrintJobRow.status == "taken",
        DocumentPrintJobRow.lease_until < now).values(status="unknown", lease_until=None, finished_at=now, error=NO_ANSWER)
        .execution_options(synchronize_session=False))


def claim(uow, *, machine_id: str, token_id: UUID, printers: list[str], now: datetime) -> dict | None:
    """The oldest pending job for one of this PC's document queues it asks for; it is taken for LEASE with a new
    attempt id. A taken job is never handed out again."""
    import base64

    with uow:
        session = _session(uow)
        _expire(session, now)
        session.execute(delete(DocumentPrintJobRow).where(DocumentPrintJobRow.status.in_(FINISHED),
                                                          DocumentPrintJobRow.finished_at < now - KEEP_FINISHED))
        result = None
        for _attempt in range(5):  # another poll may take the same row between the read and the write
            candidate = session.scalar(select(DocumentPrintJobRow.id).where(
                DocumentPrintJobRow.status == "pending", DocumentPrintJobRow.target_machine_id == machine_id,
                DocumentPrintJobRow.target_printer.in_(printers or [""]))
                .order_by(DocumentPrintJobRow.created_at, DocumentPrintJobRow.id).limit(1).with_for_update(skip_locked=True))
            if candidate is None:
                break
            attempt = str(uuid4())
            taken = session.execute(update(DocumentPrintJobRow).where(DocumentPrintJobRow.id == candidate,
                DocumentPrintJobRow.status == "pending").values(status="taken", taken_at=now, lease_until=now + LEASE,
                attempt=attempt, claim_token_id=token_id).execution_options(synchronize_session=False))
            if taken.rowcount == 1:
                row = session.get(DocumentPrintJobRow, candidate, populate_existing=True)
                result = {"id": str(row.id), "attempt": attempt, "printer": row.target_printer, "title": row.title,
                          "pages": row.pages, "copies": row.copies, "sha256": row.sha256,
                          "pdf_b64": base64.b64encode(row.pdf).decode("ascii")}
                break
        uow.commit()
    return result


def report(uow, job_id: UUID, *, machine_id: str, token_id: UUID, attempt: str, outcome: str, message: str,
           now: datetime) -> str:
    """The agent's receipt: spooled (Windows took every page) or failed. Idempotent; a late receipt of an `unknown` job
    replaces the guess with what happened. KeyError / PermissionError / PrintJobClosed."""
    if outcome not in ("spooled", "failed"):
        raise ValueError("Неизвестный результат печати")
    text = " ".join(str(message or "").split())[:ERROR_MAX]
    with uow:
        session = _session(uow)
        row = session.scalar(select(DocumentPrintJobRow).where(DocumentPrintJobRow.id == job_id).with_for_update())
        if row is None:
            raise KeyError("Задание печати не найдено")
        if row.target_machine_id != machine_id or row.claim_token_id != token_id:
            raise PermissionError("Задание выдано другому агенту")
        if row.attempt != attempt:
            raise PrintJobClosed("Попытка печати устарела")
        if row.status == outcome:
            return row.status
        if row.status not in ("taken", "unknown"):
            raise PrintJobClosed("Результат уже записан")
        row.status, row.lease_until, row.finished_at = outcome, None, now
        row.error = "" if outcome == "spooled" else (text or NO_REASON)
        uow.commit()
        return outcome


def cancel(uow, job_id: UUID, now: datetime) -> dict:
    with uow:
        session = _session(uow)
        row = session.get(DocumentPrintJobRow, job_id)
        if row is None:
            raise KeyError("Задание печати не найдено")
        session.execute(update(DocumentPrintJobRow).where(DocumentPrintJobRow.id == job_id,
            DocumentPrintJobRow.status == "pending").values(status="cancelled", finished_at=now, error=CANCELLED_TEXT)
            .execution_options(synchronize_session=False))
        row = session.get(DocumentPrintJobRow, job_id, populate_existing=True)
        view = _view(row)
        uow.commit()
    return view


def _view(row: DocumentPrintJobRow) -> dict:
    return {"id": str(row.id), "title": row.title, "source": row.source, "pages": row.pages, "copies": row.copies,
            "printer": row.target_printer, "status": row.status, "error": row.error or "", "by": row.created_by,
            "at": row.created_at.isoformat(), "finished_at": row.finished_at.isoformat() if row.finished_at else None}


def recent(uow, machine_id: str, now: datetime, limit: int = RECENT) -> list[dict]:
    with uow:
        session = _session(uow)
        _expire(session, now)
        rows = list(session.scalars(select(DocumentPrintJobRow).where(DocumentPrintJobRow.target_machine_id == machine_id)
                                    .order_by(DocumentPrintJobRow.created_at.desc(), DocumentPrintJobRow.id).limit(limit)))
        views = [_view(row) for row in rows]
        uow.commit()
    return views


def job(uow, job_id: UUID, now: datetime) -> dict:
    with uow:
        session = _session(uow)
        _expire(session, now)
        row = session.get(DocumentPrintJobRow, job_id, populate_existing=True)
        if row is None:
            raise KeyError("Задание печати не найдено")
        view = _view(row)
        uow.commit()
    return view
