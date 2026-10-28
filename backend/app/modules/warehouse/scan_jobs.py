"""The scan queue of «Отсканировать УПД» (owner 23.10.2026): the button drives the shop scanner.

The Pantum BM2300W (Wi-Fi MFP, Mopria Scan = eSCL) is reached from the warehouse Windows PC, the ERP runs in Docker
on Linux. The button queues a job here; the agent of that PC (an API token with the scope `scan:upload`, the same
program that prints the labels) claims the oldest pending job, scans the paper and uploads the file, or reports why
it could not. The file goes into `warehouse_receipt_files` like a hand-uploaded scan.

Status machine, held by CHECK constraints (migration 0145) and by the conditional UPDATEs below:
    pending ──claim──▶ claimed ──file──▶ done
       │                  └──error──▶ failed
       └──────── cancel / no answer ──▶ failed
A pending job older than 120 s or a claimed one older than 180 s is failed «Сканер не ответил…» the first time
anyone looks at the queue (the screen's poll, the agent's claim): nobody waits for a scan the storekeeper gave up on.
done and failed are final. A hand-uploaded or merged scan gets a job that is done from the start (`add_done_job`),
the handle the screen polls while the file is read. `result` keeps the recognition answer of a done job once it is
made in the background (a model takes 8–100 s);
finished jobs older than 30 days are purged on claim (their files stay: the delivery may link them)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, and_, delete, or_, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.advisory_lock import advisory_lock, lock_key
from app.shared.persistence import Base

from .scan_agents import ScanAgentRow  # noqa: F401  (the FK target is in the metadata)

PENDING_TIMEOUT = timedelta(seconds=120)
CLAIMED_TIMEOUT = timedelta(seconds=180)
KEEP_FINISHED = timedelta(days=30)
ERROR_MAX = 2000
NO_ANSWER = "Сканер не ответил — проверьте агент на ПК склада"
CANCELLED = "Отменено"
NO_REASON = "Агент сканирования не прислал скан и не сказал почему"

PENDING, CLAIMED, DONE, FAILED = "pending", "claimed", "done", "failed"
OPEN_STATUSES = (PENDING, CLAIMED)
FINISHED_STATUSES = (DONE, FAILED)


class ScanJobRow(Base):
    """One press of «Отсканировать УПД» and where it is in the queue (migration 0145)."""

    __tablename__ = "warehouse_scan_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'claimed', 'done', 'failed')", name="ck_warehouse_scan_jobs_status"),
        CheckConstraint("file_id IS NULL OR status = 'done'", name="ck_warehouse_scan_jobs_file_only_done"),
        CheckConstraint("status <> 'done' OR file_id IS NOT NULL", name="ck_warehouse_scan_jobs_done_file"),
        CheckConstraint("claimed_at IS NULL OR status <> 'pending'", name="ck_warehouse_scan_jobs_pending_unclaimed"),
        CheckConstraint(
            "(status <> 'claimed' AND status <> 'done') OR claimed_at IS NOT NULL",
            name="ck_warehouse_scan_jobs_claimed_at",
        ),
        CheckConstraint(
            "finished_at IS NULL OR status IN ('done', 'failed')", name="ck_warehouse_scan_jobs_finished_only_final",
        ),
        CheckConstraint(
            "(status <> 'done' AND status <> 'failed') OR finished_at IS NOT NULL",
            name="ck_warehouse_scan_jobs_final_finished",
        ),
        CheckConstraint("status <> 'failed' OR error <> ''", name="ck_warehouse_scan_jobs_failed_error"),
        CheckConstraint("result = '' OR status = 'done'", name="ck_warehouse_scan_jobs_result_only_done"),
        Index("ix_warehouse_scan_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("warehouse_receipt_files.id", ondelete="RESTRICT"), nullable=True,
    )
    # «Сканер с любого ПК» (migration 0157): the agent the job was created for; NULL — any agent may claim it (legacy).
    target_machine_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=True,
    )
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


@dataclass(frozen=True)
class ScanJob:
    id: UUID
    status: str
    error: str
    file_id: UUID | None
    result: str  # the recognition answer as JSON text, '' — not made yet (or the job is not done)
    requested_by: str


class ScanJobClosed(Exception):
    """The job is not waiting for this agent's answer: already done / failed (a retry, a timeout, a cancel), or never
    handed out (409)."""


def _session(uow) -> Session:
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def _job(row: ScanJobRow) -> ScanJob:
    return ScanJob(id=row.id, status=row.status, error=row.error or "", file_id=row.file_id, result=row.result or "",
                   requested_by=row.requested_by)


def _expire(session: Session, now: datetime) -> None:
    """Jobs nobody answered in time become failed «Сканер не ответил…» (persisted, so every reader sees the same)."""
    session.execute(
        update(ScanJobRow).where(or_(
            and_(ScanJobRow.status == PENDING, ScanJobRow.created_at < now - PENDING_TIMEOUT),
            and_(ScanJobRow.status == CLAIMED, ScanJobRow.claimed_at < now - CLAIMED_TIMEOUT),
        ))
        .values(status=FAILED, finished_at=now, error=NO_ANSWER)
        .execution_options(synchronize_session=False)
    )


def _read(session: Session, job_id: UUID) -> ScanJob:
    row = session.get(ScanJobRow, job_id, populate_existing=True)
    if row is None:
        raise KeyError("Задание сканирования не найдено")
    return _job(row)


def create_job(uow, *, requested_by: str, now: datetime, target: str | None = None) -> ScanJob:
    """«Отсканировать УПД»: one pending job for the agent `target` (None — any agent, an old one without an id)."""
    with uow:
        session = _session(uow)
        row = ScanJobRow(id=uuid4(), status=PENDING, requested_by=requested_by[:255] or "—", created_at=now,
                         target_machine_id=target, error="", result="")
        session.add(row)
        uow.commit()
    return _job(row)


def add_done_job(session: Session, *, file_id: UUID, requested_by: str, now: datetime) -> ScanJob:
    """Inside the transaction that stored a hand-uploaded (or merged) scan: a job that is done from the start — no
    agent takes it; the screen polls it for the reading like a scanner job (26.10: a model reads for 8–100 s)."""
    row = ScanJobRow(id=uuid4(), status=DONE, requested_by=requested_by[:255] or "—", created_at=now, claimed_at=now,
                     finished_at=now, file_id=file_id, error="", result="")
    session.add(row)
    session.flush()
    return _job(row)


def get_job(uow, job_id: UUID, *, now: datetime) -> ScanJob:
    """The job as the screen polls it; an overdue one is failed on the way. KeyError — no such job."""
    with uow:
        session = _session(uow)
        _expire(session, now)
        job = _read(session, job_id)
        uow.commit()
    return job


def cancel_job(uow, job_id: UUID, *, now: datetime) -> ScanJob:
    """The storekeeper closed the window or pressed «Отмена»: an open job is failed «Отменено»; a finished one is
    returned as it is. KeyError — no such job."""
    with uow:
        session = _session(uow)
        _expire(session, now)
        session.execute(
            update(ScanJobRow).where(ScanJobRow.id == job_id, ScanJobRow.status.in_(OPEN_STATUSES))
            .values(status=FAILED, finished_at=now, error=CANCELLED)
            .execution_options(synchronize_session=False)
        )
        job = _read(session, job_id)
        uow.commit()
    return job


def claim(uow, *, now: datetime, machine_id: str | None = None) -> UUID | None:
    """The oldest pending job (not overdue) for this agent — targeted to `machine_id` or untargeted — becomes claimed;
    an old agent without an id (None) takes untargeted jobs only. Finished jobs past KEEP_FINISHED go."""
    with uow:
        session = _session(uow)
        _expire(session, now)
        session.execute(delete(ScanJobRow).where(
            ScanJobRow.status.in_(FINISHED_STATUSES), ScanJobRow.finished_at < now - KEEP_FINISHED,
        ))
        claimed = None
        mine = ScanJobRow.target_machine_id.is_(None) if machine_id is None else or_(
            ScanJobRow.target_machine_id.is_(None), ScanJobRow.target_machine_id == machine_id)
        for _attempt in range(5):  # another agent may take the same row between the read and the write
            candidate = session.scalar(
                select(ScanJobRow.id).where(ScanJobRow.status == PENDING, mine)
                .order_by(ScanJobRow.created_at, ScanJobRow.id).limit(1)
                .with_for_update(skip_locked=True)
            )
            if candidate is None:
                break
            taken = session.execute(
                update(ScanJobRow).where(ScanJobRow.id == candidate, ScanJobRow.status == PENDING)
                .values(status=CLAIMED, claimed_at=now)
                .execution_options(synchronize_session=False)
            )
            if taken.rowcount == 1:
                claimed = candidate
                break
        uow.commit()
    return claimed


def _closed(session: Session, job_id: UUID) -> ScanJobClosed:
    status = session.scalar(select(ScanJobRow.status).where(ScanJobRow.id == job_id))
    if status is None:
        raise KeyError("Задание сканирования не найдено")
    if status == PENDING:
        return ScanJobClosed("Задание сканирования не выдано агенту")
    return ScanJobClosed("Задание сканирования уже закрыто")


def open_for_result(session: Session, job_id: UUID, now: datetime) -> ScanJob:
    """Inside the caller's transaction: the claimed job the agent answers for. KeyError / ScanJobClosed otherwise."""
    _expire(session, now)
    job = _read(session, job_id)
    if job.status != CLAIMED:
        raise _closed(session, job_id)
    return job


def finish_with_file(session: Session, job_id: UUID, file_id: UUID, now: datetime) -> None:
    """Inside the transaction that stored the file: the claimed job becomes done. ScanJobClosed — it is not claimed
    any more (the caller's transaction is rolled back with the file)."""
    done = session.execute(
        update(ScanJobRow).where(ScanJobRow.id == job_id, ScanJobRow.status == CLAIMED)
        .values(status=DONE, finished_at=now, file_id=file_id, error="")
        .execution_options(synchronize_session=False)
    )
    if done.rowcount != 1:
        raise _closed(session, job_id)


def fail_job(uow, job_id: UUID, *, error: str, now: datetime) -> None:
    """The agent could not scan (no paper, scanner off, eSCL error) or sent a file ERP does not keep: failed with the
    reason. KeyError — no such job; ScanJobClosed — not claimed."""
    reason = " ".join(str(error or "").split())[:ERROR_MAX] or NO_REASON
    with uow:
        session = _session(uow)
        open_for_result(session, job_id, now)
        session.execute(
            update(ScanJobRow).where(ScanJobRow.id == job_id, ScanJobRow.status == CLAIMED)
            .values(status=FAILED, finished_at=now, error=reason)
            .execution_options(synchronize_session=False)
        )
        uow.commit()


@contextmanager
def recognition_lock(uow, job_id: UUID) -> Iterator[None]:
    """One recognition of the job at a time across every API process (ERP_API_WORKERS > 1): the upload's background
    task and the screen's poll may land in different processes, and OCR takes seconds. The second one waits, then
    finds the stored answer (`stored_result`)."""
    with uow:
        bind = _session(uow).get_bind()
    with advisory_lock(bind, lock_key("warehouse-scan-recognition", job_id)):
        yield


@contextmanager
def recognition_try_lock(uow, job_id: UUID) -> Iterator[bool]:
    """The same lock as `recognition_lock` without waiting: False — another process is reading this job now."""
    with uow:
        bind = _session(uow).get_bind()
    with advisory_lock(bind, lock_key("warehouse-scan-recognition", job_id), wait=False) as got:
        yield got


def stored_result(uow, job_id: UUID) -> str:
    """The recognition answer stored for the job ('' — none yet)."""
    with uow:
        return _session(uow).scalar(select(ScanJobRow.result).where(ScanJobRow.id == job_id)) or ""


def save_result(uow, job_id: UUID, result: str) -> None:
    """The recognition answer of a done job, stored once (a second writer changes nothing)."""
    with uow:
        session = _session(uow)
        session.execute(
            update(ScanJobRow).where(ScanJobRow.id == job_id, ScanJobRow.status == DONE, ScanJobRow.result == "")
            .values(result=result)
            .execution_options(synchronize_session=False)
        )
        uow.commit()
