"""The label print queue (owner 22.10.2026): the ERP server never prints by itself any more.

The TSC MB341 hangs on USB at the warehouse Windows PC, the ERP runs in Docker on Linux. Every print — a lot's
«Этикетка…», the labels after receiving, the ТСД «Этикетка», «Пробная этикетка» — queues a ready TSPL job here; the
print agent on the warehouse PC (an API token with the scope `labels:print`) claims the oldest job, sends the bytes to
the printer once (the copies are inside the job: `PRINT <copies>,1`) and reports the result.

Status machine, held by CHECK constraints (migration 0141) and by the conditional UPDATEs below:
    pending ──claim──▶ taken ──result ok──▶ printed
                         │  └──result not ok──▶ failed
                         └─ lease expired ─▶ claimed again (a duplicate label beats a lost one)
A job never goes back to pending; printed and failed are final. Finished jobs older than 30 days are purged on claim.
`warehouse_label_agent` is the agent's single-row heartbeat: when it was last seen, from which PC, which printer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, Index, Integer, LargeBinary, String, Text, Uuid, and_, delete, func, or_, select, update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

COPIES_MAX = 50
LEASE = timedelta(seconds=120)
KEEP_FINISHED = timedelta(days=30)
ERROR_MAX = 2000
NO_REASON = "Агент печати не напечатал этикетку и не сказал почему"
QUEUED = "В очереди принтера."

PENDING, TAKEN, PRINTED, FAILED = "pending", "taken", "printed", "failed"
OPEN_STATUSES = (PENDING, TAKEN)
FINISHED_STATUSES = (PRINTED, FAILED)


class LabelJobRow(Base):
    """One label print job: the TSPL bytes and where it is in the queue (migration 0141)."""

    __tablename__ = "warehouse_label_jobs"
    __table_args__ = (
        # 'sample' first: the CI seeder takes the first value of an IN list, and a 'lot' job needs its lot.
        CheckConstraint("kind IN ('sample', 'lot')", name="ck_warehouse_label_jobs_kind"),
        CheckConstraint("lot_id IS NULL OR kind = 'lot'", name="ck_warehouse_label_jobs_sample_no_lot"),
        CheckConstraint("kind <> 'lot' OR lot_id IS NOT NULL", name="ck_warehouse_label_jobs_lot"),
        CheckConstraint("copies >= 1 AND copies <= 50", name="ck_warehouse_label_jobs_copies"),
        CheckConstraint("length(tspl) > 0", name="ck_warehouse_label_jobs_tspl"),
        CheckConstraint("status IN ('pending', 'taken', 'printed', 'failed')", name="ck_warehouse_label_jobs_status"),
        CheckConstraint("taken_at IS NULL OR status <> 'pending'", name="ck_warehouse_label_jobs_pending_untaken"),
        CheckConstraint("status = 'pending' OR taken_at IS NOT NULL", name="ck_warehouse_label_jobs_taken_at"),
        CheckConstraint("lease_until IS NULL OR status = 'taken'", name="ck_warehouse_label_jobs_lease_only_taken"),
        CheckConstraint("status <> 'taken' OR lease_until IS NOT NULL", name="ck_warehouse_label_jobs_taken_lease"),
        CheckConstraint("lease_until IS NULL OR lease_until > taken_at", name="ck_warehouse_label_jobs_lease_after"),
        CheckConstraint(
            "finished_at IS NULL OR status = 'printed' OR status = 'failed'",
            name="ck_warehouse_label_jobs_finished_only_final",
        ),
        CheckConstraint(
            "status = 'pending' OR status = 'taken' OR finished_at IS NOT NULL",
            name="ck_warehouse_label_jobs_final_finished",
        ),
        CheckConstraint("error IS NULL OR status = 'failed'", name="ck_warehouse_label_jobs_error_only_failed"),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="ck_warehouse_label_jobs_failed_error"),
        Index("ix_warehouse_label_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    lot_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    copies: Mapped[int] = mapped_column(Integer, nullable=False)
    tspl: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LabelAgentRow(Base):
    """The print agent's heartbeat, one row per installation (id = 1); no row — the agent has never called."""

    __tablename__ = "warehouse_label_agent"
    __table_args__ = (CheckConstraint("id = 1", name="ck_warehouse_label_agent_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    printer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    copies: int
    tspl: bytes


class JobFinished(Exception):
    """The job was already printed or failed: its result came before (an agent's retry, or a lease taken over)."""


def _session(uow) -> Session:
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def enqueue(uow, *, tspl: bytes, copies: int, kind: str, lot_id: UUID | None, created_by: str, now: datetime) -> UUID:
    """Queue one job; the agent picks it up. ValueError — copies out of 1..50 or an empty job."""
    if not 1 <= copies <= COPIES_MAX:
        raise ValueError(f"Копий — от 1 до {COPIES_MAX}")
    if not tspl:
        raise ValueError("Пустое задание печати")
    job_id = uuid4()
    with uow:
        _session(uow).add(LabelJobRow(
            id=job_id, created_at=now, created_by=created_by[:255], lot_id=lot_id if kind == "lot" else None,
            kind=kind, copies=copies, tspl=tspl, status=PENDING,
        ))
        uow.commit()
    return job_id


def _claimable(now: datetime):
    return or_(
        LabelJobRow.status == PENDING,
        and_(LabelJobRow.status == TAKEN, LabelJobRow.lease_until < now),
    )


def _touch_agent(session: Session, now: datetime, *, host: str | None = None, printer: str | None = None,
                 last_error: str | None = None) -> None:
    row = session.get(LabelAgentRow, 1)
    if row is None:
        row = LabelAgentRow(id=1, host="", printer="", last_error="", last_seen=now)
        session.add(row)
    row.last_seen = now
    if host is not None:
        row.host = host[:255]
    if printer is not None:
        row.printer = printer[:255]
    if last_error is not None:
        row.last_error = last_error


def claim(uow, *, printer: str, host: str, now: datetime) -> ClaimedJob | None:
    """The oldest pending job, or a taken one whose lease ran out, becomes taken for LEASE. The heartbeat is written
    whether a job is there or not; finished jobs past KEEP_FINISHED are purged on the way."""
    with uow:
        session = _session(uow)
        _touch_agent(session, now, host=host.strip(), printer=printer.strip())
        session.execute(delete(LabelJobRow).where(
            LabelJobRow.status.in_(FINISHED_STATUSES), LabelJobRow.finished_at < now - KEEP_FINISHED,
        ))
        claimed = None
        for _attempt in range(5):  # another agent may take the same row between the read and the write
            candidate = session.scalar(
                select(LabelJobRow.id).where(_claimable(now))
                .order_by(LabelJobRow.created_at, LabelJobRow.id).limit(1)
                .with_for_update(skip_locked=True)
            )
            if candidate is None:
                break
            taken = session.execute(
                update(LabelJobRow).where(LabelJobRow.id == candidate, _claimable(now))
                .values(status=TAKEN, taken_at=now, lease_until=now + LEASE)
                .execution_options(synchronize_session=False)
            )
            if taken.rowcount == 1:
                row = session.execute(
                    select(LabelJobRow.copies, LabelJobRow.tspl).where(LabelJobRow.id == candidate)
                ).one()
                claimed = ClaimedJob(id=candidate, copies=row.copies, tspl=bytes(row.tspl))
                break
        uow.commit()
    return claimed


def report(uow, job_id: UUID, *, ok: bool, error: str, now: datetime) -> None:
    """A taken job's result: printed, or failed with the agent's reason. KeyError — no such job; JobFinished — its
    result is already in (the job is final)."""
    reason = None if ok else (" ".join(error.split())[:ERROR_MAX] or NO_REASON)
    with uow:
        session = _session(uow)
        done = session.execute(
            update(LabelJobRow).where(LabelJobRow.id == job_id, LabelJobRow.status == TAKEN)
            .values(status=PRINTED if ok else FAILED, finished_at=now, lease_until=None, error=reason)
            .execution_options(synchronize_session=False)
        )
        if done.rowcount != 1:
            status = session.scalar(select(LabelJobRow.status).where(LabelJobRow.id == job_id))
            if status is None:
                raise KeyError("Задание печати не найдено")
            # A pending job has never been handed out: a result for it is as wrong as for a final one.
            raise JobFinished("Задание печати уже закрыто" if status in FINISHED_STATUSES else "Задание печати не выдано агенту")
        _touch_agent(session, now, last_error=reason or "")
        uow.commit()


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def agent_status(uow) -> dict:
    """What «Оборудование» shows about the printer: the agent's last call, the queue and how the last job ended."""
    with uow:
        session = _session(uow)
        agent = session.execute(
            select(LabelAgentRow.last_seen, LabelAgentRow.host, LabelAgentRow.printer, LabelAgentRow.last_error)
            .where(LabelAgentRow.id == 1)
        ).first()
        queue = session.scalar(
            select(func.count()).select_from(LabelJobRow).where(LabelJobRow.status.in_(OPEN_STATUSES))
        )
        last = session.execute(
            select(LabelJobRow.status, LabelJobRow.finished_at, LabelJobRow.error)
            .where(LabelJobRow.status.in_(FINISHED_STATUSES))
            .order_by(LabelJobRow.finished_at.desc(), LabelJobRow.id.desc()).limit(1)
        ).first()
    return {
        "last_seen": _moment(agent.last_seen) if agent else None,
        "host": agent.host if agent else "",
        "printer": agent.printer if agent else "",
        "queue": int(queue or 0),
        "last_error": (agent.last_error or None) if agent else None,
        "last_job": {"status": last.status, "finished_at": _moment(last.finished_at), "error": last.error} if last else None,
    }
