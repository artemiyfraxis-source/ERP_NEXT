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

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, Text, Uuid, and_, case, delete, false, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.advisory_lock import advisory_lock, lock_key
from app.shared.persistence import Base

from .scan_agents import ScanAgentRow  # noqa: F401  (the FK target is in the metadata)

PENDING_TIMEOUT = timedelta(seconds=120)
CLAIMED_TIMEOUT = timedelta(seconds=180)
KEEP_FINISHED = timedelta(days=30)
ERROR_MAX = 2000
SCANNER_MAX = 300
NO_ANSWER = "Сканер не ответил — проверьте агент на ПК склада"
CANCELLED = "Отменено"
NO_REASON = "Агент сканирования не прислал скан и не сказал почему"
VNA_NO_ANSWER = "ВАЦ не ответил — проверьте агент на выбранном ПК"
VNA_FILE_LIMIT = 2 * 1024 * 1024


class AgentTokenRow(Base):
    """One credential per PC. Enrollment binds immediately; legacy tokens use first-heartbeat TOFU.

    TOFU trusts the first authenticated heartbeat, so legacy credentials must be kept private.
    A binding is never updated or transferred by heartbeat, claim or result.
    """

    __tablename__ = "warehouse_agent_tokens"
    __table_args__ = (
        CheckConstraint("trim(machine_id) <> ''", name="ck_warehouse_agent_tokens_machine"),
        Index("uq_warehouse_agent_tokens_machine", "machine_id", unique=True,
              sqlite_where=text("retired = 0"), postgresql_where=text("retired = false")),
    )

    token_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("identity_api_tokens.id", ondelete="CASCADE"), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(80), nullable=False)
    retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())


class AgentMachineRefused(Exception):
    """The authenticated token cannot act for the supplied PC (403)."""


def _agent_machine_id(machine_id: str) -> str:
    machine_id = " ".join(machine_id.split())
    if not machine_id or len(machine_id) > 80 or machine_id.startswith("legacy:"):
        raise AgentMachineRefused("Нужен идентификатор ПК, привязанный к токену агента")
    return machine_id


def agent_machine(uow, token_id: UUID, machine_id: str, *, bind: bool = False) -> str:
    machine_id = _agent_machine_id(machine_id)
    with uow:
        session = _session(uow)
        lock = advisory_lock(session.get_bind(), lock_key("warehouse-agent-enroll", machine_id)) if bind else nullcontext()
        with lock:
            row = session.get(AgentTokenRow, token_id)
            if row is None and bind:
                try:
                    # PK/UNIQUE arbitrate simultaneous first heartbeats for either token or PC.
                    with session.begin_nested():
                        session.add(AgentTokenRow(token_id=token_id, machine_id=machine_id))
                        session.flush()
                except IntegrityError:
                    row = session.get(AgentTokenRow, token_id)
                    if row is None:
                        raise AgentMachineRefused("Этот ПК уже привязан к другому токену агента") from None
                else:
                    row = session.get(AgentTokenRow, token_id)
            if row is None:
                raise AgentMachineRefused("Сначала отправьте сведения агента о ПК")
            if row.retired or row.machine_id != machine_id:
                raise AgentMachineRefused("Токен агента отозван или привязан к другому ПК")
            uow.commit()
            return row.machine_id


def enroll_agent(uow, token_id: UUID, machine_id: str, revoke: Callable[[UUID], None]) -> None:
    """Only the redeemed install workflow may replace a PC's credential.

    Revoke before replacement; keep old bindings so even an in-flight old heartbeat cannot use TOFU again.
    If replacement fails, the old token stays revoked and a fresh authorized install can recover the PC.
    """
    machine_id = _agent_machine_id(machine_id)
    with uow:
        session = _session(uow)
        with advisory_lock(session.get_bind(), lock_key("warehouse-agent-enroll", machine_id)):
            old = session.scalar(select(AgentTokenRow).where(
                AgentTokenRow.machine_id == machine_id, AgentTokenRow.retired.is_(False)).with_for_update())
            if old is not None:
                revoke(old.token_id)
                old.retired = True
                session.flush()
            session.add(AgentTokenRow(token_id=token_id, machine_id=machine_id))
            uow.commit()


PENDING, CLAIMED, DONE, FAILED = "pending", "claimed", "done", "failed"
OPEN_STATUSES = (PENDING, CLAIMED)
FINISHED_STATUSES = (DONE, FAILED)


class ScanJobRow(Base):
    """One press of «Отсканировать УПД» and where it is in the queue (migration 0145)."""

    __tablename__ = "warehouse_scan_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'claimed', 'done', 'failed')", name="ck_warehouse_scan_jobs_status"),
        CheckConstraint("file_id IS NULL OR status = 'done'", name="ck_warehouse_scan_jobs_file_only_done"),
        CheckConstraint("status <> 'done' OR (kind = 'scan' AND file_id IS NOT NULL) OR "
                        "(kind = 'vna' AND capture_content IS NOT NULL)", name="ck_warehouse_scan_jobs_done_file"),
        CheckConstraint("kind IN ('scan', 'vna')", name="ck_warehouse_scan_jobs_kind"),
        CheckConstraint("source IN ('s2vna', 'arinst')", name="ck_warehouse_scan_jobs_source"),
        CheckConstraint("(kind = 'scan' AND capture_content IS NULL AND capture_settings IS NULL AND instrument_id IS NULL AND vna_name = '' AND capture_file_name = '' AND source = 's2vna') OR "
                        "(kind = 'vna' AND file_id IS NULL AND result = '' AND target_machine_id IS NOT NULL "
                        "AND target_machine_id <> '' AND target_scanner IS NULL)", name="ck_warehouse_scan_jobs_kind_payload"),
        CheckConstraint("capture_content IS NULL OR (status = 'done' AND length(capture_content) BETWEEN 1 AND 2097152)",
                        name="ck_warehouse_scan_jobs_capture_size"),
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
        CheckConstraint(
            "target_scanner IS NULL OR (target_scanner <> '' AND target_machine_id IS NOT NULL)",
            name="ck_warehouse_scan_jobs_target_scanner",
        ),
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
    # The selected PC (migration 0157); NULL belongs to pre-strict jobs, which now expire without being claimed.
    target_machine_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=True,
    )
    # «Своё устройство» (migration 0159): the scanner the person chose (WIA DeviceID or name, as the agent reported
    # it); NULL belongs to pre-strict jobs and no longer permits a first-scanner claim.
    target_scanner: Mapped[str | None] = mapped_column(String(SCANNER_MAX), nullable=True)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    result: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="scan", server_default="scan")
    capture_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    vna_name: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="s2vna", server_default="s2vna")
    capture_file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    # Production owns instrument identity; the capture workflow validates it before enqueueing.
    instrument_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    capture_settings: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)


@dataclass(frozen=True)
class ScanJob:
    id: UUID
    status: str
    error: str
    file_id: UUID | None
    result: str  # the recognition answer as JSON text, '' — not made yet (or the job is not done)
    requested_by: str
    kind: str = "scan"
    vna_name: str = ""
    target_machine_id: str | None = None
    source: str = "s2vna"
    capture_file_name: str = ""
    instrument_id: UUID | None = None
    capture_settings: dict | None = None


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
                   requested_by=row.requested_by, kind=row.kind, vna_name=row.vna_name,
                   target_machine_id=row.target_machine_id, source=row.source, capture_file_name=row.capture_file_name,
                   instrument_id=row.instrument_id, capture_settings=row.capture_settings)


def _expire(session: Session, now: datetime) -> None:
    """Jobs nobody answered in time become failed «Сканер не ответил…» (persisted, so every reader sees the same)."""
    session.execute(
        update(ScanJobRow).where(or_(
            and_(ScanJobRow.status == PENDING, ScanJobRow.created_at < now - PENDING_TIMEOUT),
            and_(ScanJobRow.status == CLAIMED, ScanJobRow.claimed_at < now - CLAIMED_TIMEOUT),
        ))
        .values(status=FAILED, finished_at=now, error=case((ScanJobRow.kind == "vna", VNA_NO_ANSWER), else_=NO_ANSWER))
        .execution_options(synchronize_session=False)
    )


def _read(session: Session, job_id: UUID) -> ScanJob:
    row = session.get(ScanJobRow, job_id, populate_existing=True)
    if row is None:
        raise KeyError("Задание сканирования не найдено")
    return _job(row)


def create_job(uow, *, requested_by: str, now: datetime, target: str | None = None,
               scanner: str | None = None, kind: str = "scan", vna_name: str = "", source: str = "s2vna",
               instrument_id: UUID | None = None) -> ScanJob:
    """Create a pending job. Only a full `target` / `scanner` pair can be claimed; incomplete legacy rows expire."""
    if source not in ("s2vna", "arinst"):
        raise ValueError("Неизвестный источник ВАЦ")
    scanner = (scanner or "").strip()[:SCANNER_MAX] or None
    with uow:
        session = _session(uow)
        row = ScanJobRow(id=uuid4(), status=PENDING, requested_by=requested_by[:255] or "—", created_at=now,
                         target_machine_id=target, target_scanner=scanner if target else None, error="", result="",
                         kind=kind, vna_name=vna_name, source=source, instrument_id=instrument_id)
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


@dataclass(frozen=True)
class Claimed:
    id: UUID
    target_scanner: str | None  # the scanner the person chose; claims now require a nonempty scanner
    kind: str = "scan"
    source: str = "s2vna"


def claim(uow, *, now: datetime, machine_id: str | None = None, vna: bool = False) -> Claimed | None:
    """Claim the oldest complete PC/scanner pair targeted to this agent. Incomplete pre-strict jobs expire normally;
    no agent, including an old one without an id, may scan them. Finished jobs past KEEP_FINISHED go."""
    with uow:
        session = _session(uow)
        _expire(session, now)
        session.execute(delete(ScanJobRow).where(
            ScanJobRow.status.in_(FINISHED_STATUSES), ScanJobRow.finished_at < now - KEEP_FINISHED,
        ))
        claimed = None
        supported = and_(ScanJobRow.kind == "scan", ScanJobRow.target_scanner.is_not(None), ScanJobRow.target_scanner != "")
        if vna:
            supported = or_(supported, ScanJobRow.kind == "vna")
        mine = and_(ScanJobRow.target_machine_id.is_not(None), ScanJobRow.target_machine_id != "", supported,
                    ScanJobRow.target_machine_id == machine_id)
        for _attempt in range(5):  # another agent may take the same row between the read and the write
            candidate = session.execute(
                select(ScanJobRow.id, ScanJobRow.target_scanner, ScanJobRow.kind, ScanJobRow.source).where(ScanJobRow.status == PENDING, mine)
                .order_by(ScanJobRow.created_at, ScanJobRow.id).limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if candidate is None:
                break
            taken = session.execute(
                update(ScanJobRow).where(ScanJobRow.id == candidate.id, ScanJobRow.status == PENDING)
                .values(status=CLAIMED, claimed_at=now)
                .execution_options(synchronize_session=False)
            )
            if taken.rowcount == 1:
                claimed = Claimed(candidate.id, candidate.target_scanner, candidate.kind, candidate.source)
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
