"""The label print queue (owner 22.10.2026): the ERP server never prints by itself any more.

The TSC MB341 hangs on USB at the warehouse Windows PC, the ERP runs in Docker on Linux. Every print — a lot's
«Этикетка…», the labels after receiving, the ТСД «Этикетка», «Пробная этикетка» — queues a ready TSPL job here; the
print agent on the warehouse PC (an API token with the scope `labels:print`) claims the oldest job, sends the bytes to
the printer once (the copies are inside the job: `PRINT <copies>,1`) and reports the result.

TSPL status machine, held by CHECK constraints and the conditional UPDATEs below:
    pending ──claim──▶ taken ──result ok──▶ printed
                         │  └──result not ok──▶ failed
                         └─ lease expired ─▶ claimed again (a duplicate label beats a lost one)
A job never goes back to pending. Finished TSPL jobs older than 30 days are purged on claim.
Godex marking (migration 0190) uses an exact machine/queue, immutable PNG and attempt token;
spool acceptance is `spooled`, while expiration becomes `unknown` and pauses that printer until the operator resolves
it in ERP. A late agent receipt is stored on the `unknown` job; re-sending it answers the job's current status.
«Отменить оставшиеся» (0203) moves only never-handed-out marking jobs `pending → cancelled` with the reason.
A failed peel-off check (`halt` receipt) stops the rest of its batch until the operator acts (marking_halts).
Marking history is retained. `warehouse_label_agent` remains the warehouse TSPL heartbeat only."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, Uuid, and_, delete, func, or_, select, update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

COPIES_MAX = 50
LEASE = timedelta(seconds=120)
KEEP_FINISHED = timedelta(days=30)
ERROR_MAX = 2000
NO_REASON = "Агент печати не напечатал этикетку и не сказал почему"
QUEUED = "В очереди принтера."

PENDING, TAKEN, PRINTED, FAILED, CANCELLED = "pending", "taken", "printed", "failed", "cancelled"
OPEN_STATUSES = (PENDING, TAKEN)
FINISHED_STATUSES = (PRINTED, FAILED)


class LabelJobRow(Base):
    """One label print job: the TSPL bytes and where it is in the queue (migration 0141)."""

    __tablename__ = "warehouse_label_jobs"
    __table_args__ = (
        # 'sample' first: the CI seeder takes the first value of an IN list, and a 'lot' job needs its lot.
        CheckConstraint("kind IN ('sample', 'lot', 'instrument', 'marking')", name="ck_warehouse_label_jobs_kind"),
        CheckConstraint("lot_id IS NULL OR kind = 'lot'", name="ck_warehouse_label_jobs_sample_no_lot"),
        CheckConstraint("kind <> 'lot' OR lot_id IS NOT NULL", name="ck_warehouse_label_jobs_lot"),
        CheckConstraint("copies >= 1 AND copies <= 50", name="ck_warehouse_label_jobs_copies"),
        CheckConstraint("(format = 'tspl' AND length(tspl) > 0) OR (format = 'gdi_png' AND length(png) > 0)", name="ck_warehouse_label_jobs_tspl"),
        CheckConstraint("status IN ('pending', 'taken', 'printed', 'failed', 'spooled', 'unknown', 'cancelled')", name="ck_warehouse_label_jobs_status"),
        CheckConstraint("taken_at IS NULL OR status <> 'pending'", name="ck_warehouse_label_jobs_pending_untaken"),
        CheckConstraint("status IN ('pending', 'cancelled') OR taken_at IS NOT NULL", name="ck_warehouse_label_jobs_taken_at"),
        CheckConstraint("lease_until IS NULL OR status = 'taken'", name="ck_warehouse_label_jobs_lease_only_taken"),
        CheckConstraint("status <> 'taken' OR lease_until IS NOT NULL", name="ck_warehouse_label_jobs_taken_lease"),
        CheckConstraint("lease_until IS NULL OR lease_until > taken_at", name="ck_warehouse_label_jobs_lease_after"),
        CheckConstraint(
            "finished_at IS NULL OR status IN ('printed', 'failed', 'spooled', 'unknown', 'cancelled')",
            name="ck_warehouse_label_jobs_finished_only_final",
        ),
        CheckConstraint(
            "status = 'pending' OR status = 'taken' OR finished_at IS NOT NULL",
            name="ck_warehouse_label_jobs_final_finished",
        ),
        CheckConstraint("error IS NULL OR status IN ('failed', 'unknown', 'cancelled')", name="ck_warehouse_label_jobs_error_only_failed"),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="ck_warehouse_label_jobs_failed_error"),
        CheckConstraint("(format = 'tspl' AND kind <> 'marking' AND batch_id IS NULL AND png IS NULL AND status NOT IN ('spooled', 'unknown')) OR "
                        "(format = 'gdi_png' AND kind = 'marking' AND copies = 1 AND png IS NOT NULL AND length(png) BETWEEN 1 AND 2097152 "
                        "AND target_machine_id IS NOT NULL AND target_printer IS NOT NULL AND trim(target_printer) <> '' "
                        "AND batch_id IS NOT NULL AND sequence IS NOT NULL AND sequence >= 1 "
                        "AND width_mm IS NOT NULL AND width_mm BETWEEN 5 AND 150 AND height_mm IS NOT NULL AND height_mm BETWEEN 5 AND 150 "
                        "AND orientation IS NOT NULL AND orientation IN ('portrait', 'landscape') AND status <> 'printed')", name="ck_label_job_payload"),
        CheckConstraint("format <> 'gdi_png' OR status IN ('pending', 'cancelled') OR (attempt IS NOT NULL AND claim_token_id IS NOT NULL)", name="ck_label_job_attempt"),
        # 0203: «Отменить оставшиеся» — only a never-handed-out marking job, with the operator's reason.
        CheckConstraint("status <> 'cancelled' OR (format = 'gdi_png' AND taken_at IS NULL AND attempt IS NULL "
                        "AND claim_token_id IS NULL AND finished_at IS NOT NULL AND error IS NOT NULL AND trim(error) <> '')", name="ck_label_job_cancelled"),
        UniqueConstraint("batch_id", "sequence", name="uq_label_job_sequence"),
        ForeignKeyConstraint(["target_machine_id", "target_printer"], ["warehouse_agent_printers.machine_id", "warehouse_agent_printers.name"],
                             name="fk_label_job_printer", ondelete="RESTRICT"),
        Index("ix_warehouse_label_jobs_status_created", "status", "created_at"),
        Index("ix_label_jobs_target", "target_machine_id", "target_printer", "status"),
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
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="tspl", server_default="tspl")
    png: Mapped[bytes | None] = mapped_column(LargeBinary)
    target_machine_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"))
    target_printer: Mapped[str | None] = mapped_column(String(255))
    width_mm: Mapped[float | None] = mapped_column(Float)
    height_mm: Mapped[float | None] = mapped_column(Float)
    orientation: Mapped[str | None] = mapped_column(String(16))
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("warehouse_marking_batches.id", ondelete="RESTRICT"))
    sequence: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[str | None] = mapped_column(String(36))
    claim_token_id: Mapped[UUID | None] = mapped_column(Uuid)
    receipt: Mapped[dict | None] = mapped_column(JSON)


class LabelAgentRow(Base):
    """The print agent's heartbeat, one row per installation (id = 1); no row — the agent has never called."""

    __tablename__ = "warehouse_label_agent"
    __table_args__ = (CheckConstraint("id = 1", name="ck_warehouse_label_agent_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    printer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    token_id: Mapped[UUID | None] = mapped_column(Uuid)


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


def add_job(session: Session, *, tspl: bytes, copies: int, kind: str, lot_id: UUID | None, created_by: str,
            now: datetime) -> UUID:
    """One job into the caller's open transaction (the «Принять» of a delivery line queues its labels together with
    the lot). ValueError — copies out of 1..50 or an empty job."""
    if not 1 <= copies <= COPIES_MAX:
        raise ValueError(f"Копий — от 1 до {COPIES_MAX}")
    if not tspl:
        raise ValueError("Пустое задание печати")
    job_id = uuid4()
    session.add(LabelJobRow(
        id=job_id, created_at=now, created_by=created_by[:255], lot_id=lot_id if kind == "lot" else None,
        kind=kind, copies=copies, tspl=tspl, status=PENDING,
    ))
    return job_id


def enqueue(uow, *, tspl: bytes, copies: int, kind: str, lot_id: UUID | None, created_by: str, now: datetime) -> UUID:
    """Queue one job; the agent picks it up. ValueError — copies out of 1..50 or an empty job."""
    if not 1 <= copies <= COPIES_MAX:
        raise ValueError(f"Копий — от 1 до {COPIES_MAX}")
    if not tspl:
        raise ValueError("Пустое задание печати")
    with uow:
        job_id = add_job(_session(uow), tspl=tspl, copies=copies, kind=kind, lot_id=lot_id, created_by=created_by,
                         now=now)
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


def claim(uow, *, printer: str, host: str, now: datetime, token_id: UUID | None = None) -> ClaimedJob | None:
    """The oldest pending job, or a taken one whose lease ran out, becomes taken for LEASE. The heartbeat is written
    whether a job is there or not; finished jobs past KEEP_FINISHED are purged on the way."""
    with uow:
        session = _session(uow)
        agent = session.scalar(select(LabelAgentRow).where(LabelAgentRow.id == 1).with_for_update())
        if token_id is not None:
            from .devices import PrinterRow
            from .scan_jobs import AgentTokenRow

            enrolled = session.get(AgentTokenRow, token_id)
            device = session.get(PrinterRow, (enrolled.machine_id, printer.strip())) if enrolled else None
            if enrolled and (enrolled.retired or (device is not None and device.kind != "tspl")):
                return None
            if agent and agent.token_id not in (None, token_id):
                old, new = session.get(AgentTokenRow, agent.token_id), session.get(AgentTokenRow, token_id)
                if old and new and old.retired and not new.retired and old.machine_id == new.machine_id:
                    agent.token_id = token_id
            # Bound by the enrolled token (and so by its machine) only: a renamed Windows queue («TSC … (копия 1)»)
            # or a changed config printer must not silently stop warehouse printing; the heartbeat below records them.
            if agent and agent.token_id not in (None, token_id):
                return None
            if not printer.strip():
                return None
        _touch_agent(session, now, host=host.strip(), printer=printer.strip())
        if token_id is not None:
            session.flush()
            pinned = session.execute(update(LabelAgentRow).where(LabelAgentRow.id == 1,
                or_(LabelAgentRow.token_id.is_(None), LabelAgentRow.token_id == token_id)).values(token_id=token_id))
            if pinned.rowcount != 1:
                return None
        session.execute(delete(LabelJobRow).where(
            LabelJobRow.format == "tspl", LabelJobRow.status.in_(FINISHED_STATUSES), LabelJobRow.finished_at < now - KEEP_FINISHED,
        ))
        claimed = None
        for _attempt in range(5):  # another agent may take the same row between the read and the write
            candidate = session.scalar(
                select(LabelJobRow.id).where(_claimable(now), LabelJobRow.format == "tspl")
                .order_by(LabelJobRow.created_at, LabelJobRow.id).limit(1)
                .with_for_update(skip_locked=True)
            )
            if candidate is None:
                break
            taken = session.execute(
                update(LabelJobRow).where(LabelJobRow.id == candidate, _claimable(now))
                .values(status=TAKEN, taken_at=now, lease_until=now + LEASE, claim_token_id=token_id)
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


def report(uow, job_id: UUID, *, ok: bool, error: str, now: datetime, token_id: UUID | None = None) -> None:
    """A taken job's result: printed, or failed with the agent's reason. KeyError — no such job; JobFinished — its
    result is already in (the job is final)."""
    reason = None if ok else (" ".join(error.split())[:ERROR_MAX] or NO_REASON)
    with uow:
        session = _session(uow)
        row = session.get(LabelJobRow, job_id)
        if row is not None and (row.format != "tspl" or (token_id is not None and row.claim_token_id not in (None, token_id))):
            raise JobFinished("Задание выдано другому агенту или требует результата с номером попытки")
        done = session.execute(
            update(LabelJobRow).where(LabelJobRow.id == job_id, LabelJobRow.status == TAKEN, LabelJobRow.format == "tspl")
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


def add_marking_job(session, *, batch_id, sequence, png, geometry, machine_id, printer, actor, now):
    identifier = uuid4()
    session.add(LabelJobRow(id=identifier, created_at=now, created_by=actor.name[:255], kind="marking", copies=1,
        tspl=b"", format="gdi_png", png=png, status=PENDING, batch_id=batch_id, sequence=sequence,
        target_machine_id=machine_id, target_printer=printer, width_mm=geometry["width_mm"],
        height_mm=geometry["height_mm"], orientation=geometry["orientation"]))
    return identifier


def expire_marking(session, now):
    session.execute(update(LabelJobRow).where(LabelJobRow.format == "gdi_png", LabelJobRow.status == TAKEN,
        LabelJobRow.lease_until < now).values(status="unknown", lease_until=None, finished_at=now,
            error="Агент не подтвердил результат — проверьте принтер перед продолжением"))


def cancel_pending_marking(session, batch_id, *, reason, now) -> list[int]:
    """«Отменить оставшиеся»: every never-handed-out job of the batch becomes `cancelled` with the reason; a job the
    agent already took (or spooled) is not touched. Returns the cancelled sequence numbers (the caller journals them)."""
    reason = " ".join(reason.split())
    if not reason:
        raise ValueError("Укажите причину отмены")
    text = f"Отменено: {reason}"[:ERROR_MAX]
    sequences = list(session.scalars(select(LabelJobRow.sequence).where(LabelJobRow.batch_id == batch_id,
        LabelJobRow.format == "gdi_png", LabelJobRow.status == PENDING).order_by(LabelJobRow.sequence).with_for_update()))
    if not sequences:
        return []
    changed = session.execute(update(LabelJobRow).where(LabelJobRow.batch_id == batch_id, LabelJobRow.format == "gdi_png",
        LabelJobRow.status == PENDING, LabelJobRow.sequence.in_(sequences)).values(status=CANCELLED, finished_at=now, error=text)
        .execution_options(synchronize_session=False))
    if changed.rowcount != len(sequences):
        # The agent took one between the read and the write (engines without FOR UPDATE): the caller retries.
        raise JobFinished("Очередь изменилась — повторите отмену")
    return sequences


def pending_marking_by_printer(session, machine_id, printer) -> dict:
    """Never-handed-out marking jobs on this queue: batch id → sequences."""
    result: dict = {}
    for batch_id, sequence in session.execute(select(LabelJobRow.batch_id, LabelJobRow.sequence).where(LabelJobRow.format == "gdi_png",
            LabelJobRow.target_machine_id == machine_id, LabelJobRow.target_printer == printer, LabelJobRow.status == PENDING)
            .order_by(LabelJobRow.batch_id, LabelJobRow.sequence)):
        result.setdefault(batch_id, []).append(sequence)
    return result


def marking_halts(session, batch_ids) -> dict:
    """Peel-off check failures nobody has dealt with yet: batch id → sequences. The agent (2.11.0+) marks such a failed
    receipt `halt`: nothing was printed, so the rest of the batch must not print past the gap. Dealt with:
    «Продолжить без неё» (`halt_released`, workflows.marking_labels.resolve) or a «Перепечатать номера…» batch of this
    one has spooled that label. «Отменить оставшиеся» leaves nothing to hold."""
    from .marking_profiles import MarkingBatchRow

    batch_ids = list(batch_ids)
    if not batch_ids:
        return {}
    failed = [(batch_id, sequence) for batch_id, sequence, receipt in session.execute(select(LabelJobRow.batch_id,
        LabelJobRow.sequence, LabelJobRow.receipt).where(LabelJobRow.batch_id.in_(batch_ids), LabelJobRow.status == FAILED))
        if (receipt or {}).get("halt") and not (receipt or {}).get("halt_released")]
    if not failed:
        return {}
    covered = set()
    for reprint in session.scalars(select(MarkingBatchRow).where(MarkingBatchRow.reprint_of.in_({batch_id for batch_id, _ in failed}))):
        sources = ((reprint.manifest or {}).get("reprint") or {}).get("sequences") or []
        spooled = set(session.scalars(select(LabelJobRow.sequence).where(LabelJobRow.batch_id == reprint.id,
            LabelJobRow.status == "spooled")))
        covered |= {(reprint.reprint_of, source) for index, source in enumerate(sources, 1) if index in spooled}
    result: dict = {}
    for batch_id, sequence in failed:
        if (batch_id, sequence) not in covered:
            result.setdefault(batch_id, []).append(sequence)
    return result


def claim_marking(uow, *, machine_id, token_id, now):
    from .devices import PrinterRow
    from .marking_profiles import MarkingBatchRow
    from .printer_commands import agent_executes_commands, marking_printers, open_command_printers
    from .scan_agents import LIVE_FOR, ScanAgentRow

    with uow:
        session = _session(uow)
        expire_marking(session, now)
        printers = session.scalars(select(PrinterRow).where(PrinterRow.machine_id == machine_id,
            PrinterRow.kind == "gdi_png", PrinterRow.present.is_(True), PrinterRow.last_seen >= now - LIVE_FOR)
            .order_by(PrinterRow.name).with_for_update()).all()
        # «Очистить принтер» in progress: nothing new is spooled under the clearing.
        clearing = open_command_printers(session, machine_id)
        # Peel-off now (the bound profiles) as well as when the batch was queued: a batch from before 0203 or before the
        # flag was switched on carries no snapshot flag. Only an agent that checks ^O1 (2.11.0+) gets a peel-off job.
        peel_now = {item["name"]: item["peel"] for item in marking_printers(session, machine_id)}
        agent = session.get(ScanAgentRow, machine_id)
        checks_peel = agent is not None and agent_executes_commands(agent.version)
        for printer in printers:
            if printer.name in clearing:
                continue
            # Row locking handles PostgreSQL. CAS also arbitrates engines without FOR UPDATE.
            version = printer.queue_version
            changed = session.execute(update(PrinterRow).where(PrinterRow.machine_id == machine_id,
                PrinterRow.name == printer.name, PrinterRow.queue_version == version)
                .values(queue_version=version + 1).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                continue
            target = (LabelJobRow.format == "gdi_png", LabelJobRow.target_machine_id == machine_id,
                      LabelJobRow.target_printer == printer.name)
            if session.scalar(select(LabelJobRow.id).where(*target, LabelJobRow.status.in_((TAKEN, "unknown"))).limit(1)):
                continue
            started_batches = select(LabelJobRow.batch_id).where(*target, LabelJobRow.status != PENDING)
            # A batch stopped by a failed peel-off check waits for the operator; other batches of the queue go on.
            halted = list(marking_halts(session, session.scalars(select(LabelJobRow.batch_id).where(*target,
                LabelJobRow.status == PENDING).distinct())))
            waiting = (LabelJobRow.batch_id.not_in(halted),) if halted else ()
            job = session.scalar(select(LabelJobRow).where(*target, LabelJobRow.status == PENDING, *waiting)
                .order_by(LabelJobRow.batch_id.in_(started_batches).desc(), LabelJobRow.created_at,
                          LabelJobRow.batch_id, LabelJobRow.sequence).limit(1).with_for_update())
            if job is None:
                continue
            # The profile's peel-off dispenser (0203): the agent refuses the job unless the driver emits ^O1.
            batch = session.get(MarkingBatchRow, job.batch_id)
            geometry = ((batch.manifest if batch else {}).get("profile_snapshot") or {}).get("geometry") or {}
            peel = bool(geometry.get("peel")) or bool(peel_now.get(printer.name))
            if peel and not checks_peel:
                continue  # stays pending; the print window says the agent needs an update (marking_labels.machines)
            attempt = str(uuid4())
            taken = session.execute(update(LabelJobRow).where(LabelJobRow.id == job.id, LabelJobRow.status == PENDING)
                .values(status=TAKEN, taken_at=now, lease_until=now + LEASE, attempt=attempt, claim_token_id=token_id))
            if taken.rowcount != 1:
                continue
            result = {"id": str(job.id), "attempt": attempt, "format": "gdi_png", "printer": printer.name,
                      "width_mm": job.width_mm, "height_mm": job.height_mm, "orientation": job.orientation,
                      "dpi": 300, "copies": 1, "png_b64": base64.b64encode(job.png).decode("ascii"),
                      "sha256": hashlib.sha256(job.png).hexdigest(), "peel": peel}
            uow.commit()
            return result
        uow.commit()
    return None


def report_marking(uow, job_id, *, machine_id, token_id, attempt, outcome, message, spool_job_id, now, tracked=False,
                   halt=False) -> str:
    """The agent's receipt for one attempt → the job's status after it. Idempotent: the same receipt again answers
    the current status, so an agent holding an `unknown` receipt learns when the operator resolved the job.
    A receipt after the lease ran out (the job is `unknown` by then) is stored for the operator, the status stays.
    `halt` (a failed peel-off check, agent 2.11.0+) stops the rest of the batch until the operator acts (marking_halts)."""
    if outcome not in ("spooled", "failed", "unknown"):
        raise ValueError("Неизвестный результат печати")
    with uow:
        session = _session(uow)
        row = session.scalar(select(LabelJobRow).where(LabelJobRow.id == job_id).with_for_update())
        if row is None:
            raise KeyError("Задание печати не найдено")
        if row.target_machine_id != machine_id or row.claim_token_id != token_id:
            raise PermissionError("Задание выдано другому агенту")
        if row.format != "gdi_png" or row.attempt != attempt:
            raise JobFinished("Попытка печати устарела")
        receipt = {"outcome": outcome, "message": message[:ERROR_MAX], "spool_job_id": spool_job_id}
        if tracked:
            # The agent keeps watching the Windows spool job and reports its end via report_print_state.
            receipt["tracked"] = True
        if halt and outcome == FAILED:
            receipt["halt"] = True
        if row.receipt and {key: value for key, value in row.receipt.items() if not key.startswith("print_")} == receipt:
            return row.status
        if row.status == TAKEN:
            lease = row.lease_until.replace(tzinfo=now.tzinfo) if row.lease_until.tzinfo is None else row.lease_until
            if lease >= now:
                changed = session.execute(update(LabelJobRow).where(LabelJobRow.id == job_id,
                    LabelJobRow.status == TAKEN, LabelJobRow.attempt == attempt, LabelJobRow.claim_token_id == token_id)
                    .values(status=outcome, lease_until=None, finished_at=now, receipt=receipt,
                            error=(message[:ERROR_MAX] or NO_REASON) if outcome in ("failed", "unknown") else None)
                    .execution_options(synchronize_session=False))
                if changed.rowcount != 1:
                    raise JobFinished("Результат уже записан")
                uow.commit()
                return outcome
            expire_marking(session, now)
            session.flush()
            session.refresh(row)
        if row.receipt is not None:
            raise JobFinished("Результат уже записан")
        # Late receipt: the job became `unknown` (or the operator already resolved it). Keep the operator's decision.
        row.receipt = receipt
        status = row.status
        uow.commit()
        return status


PRINT_STATES = ("printed", "error", "cancelled")


def report_print_state(uow, job_id, *, machine_id, token_id, attempt, state, message, now) -> str:
    """The agent's word on how a spooled job ended in the Windows queue: printed / error (still watched, may recover)
    / cancelled. The job's own status stays `spooled`; the state lives in the receipt. A final state is never
    downgraded by a later `error` (a re-sent receipt after an answer lost on the way)."""
    if state not in PRINT_STATES:
        raise ValueError("Неизвестное состояние печати")
    with uow:
        session = _session(uow)
        row = session.scalar(select(LabelJobRow).where(LabelJobRow.id == job_id).with_for_update())
        if row is None:
            raise KeyError("Задание печати не найдено")
        if row.target_machine_id != machine_id or row.claim_token_id != token_id:
            raise PermissionError("Задание выдано другому агенту")
        if row.format != "gdi_png" or row.attempt != attempt or row.status != "spooled" or not (row.receipt or {}).get("tracked"):
            raise JobFinished("Задание не ждёт итога печати")
        current = (row.receipt or {}).get("print_state")
        if current in ("printed", "cancelled") and state == "error":
            return current
        row.receipt = {**row.receipt, "print_state": state, "print_message": message[:ERROR_MAX],
                       "print_state_at": now.isoformat()}
        uow.commit()
        return state


def agent_status(uow) -> dict:
    """What «Оборудование» shows about the printer: the agent's last call, the queue and how the last job ended."""
    with uow:
        session = _session(uow)
        agent = session.execute(
            select(LabelAgentRow.last_seen, LabelAgentRow.host, LabelAgentRow.printer, LabelAgentRow.last_error)
            .where(LabelAgentRow.id == 1)
        ).first()
        queue = session.scalar(
            select(func.count()).select_from(LabelJobRow).where(LabelJobRow.status.in_(OPEN_STATUSES), LabelJobRow.format == "tspl")
        )
        last = session.execute(
            select(LabelJobRow.status, LabelJobRow.finished_at, LabelJobRow.error)
            .where(LabelJobRow.status.in_(FINISHED_STATUSES), LabelJobRow.format == "tspl")
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
