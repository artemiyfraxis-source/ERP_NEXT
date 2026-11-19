"""Printer commands from ERP to the print agent (owner 19.11.2026, migration 0203): «Очистить принтер».

The marking printer (Godex EZ2350i of ОБ.101) keeps labels in its own buffer: deleting Windows jobs does not stop
them. A command is queued for one exact queue (machine id + Windows printer name). The agent finds it in its claim
reply, removes that queue's ERP marking jobs from Windows, sends the Godex buffer-clear command and reports.

    pending ──claim──▶ taken ──report──▶ done | failed
       └── not taken in COMMAND_WAIT ──▶ failed       taken past its lease ──▶ failed
At most one open (pending/taken) command per queue (partial UNIQUE uq_printer_command_open). While one is open the
queue hands out no marking jobs (claim_marking), so nothing new is spooled under the clearing. A command is never
re-delivered after its lease: the operator sees the failure and repeats it; clearing twice is harmless anyway.
The peel-off flag of the marking profiles bound to a queue travels in every claim reply (`marking_printers`)."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, Uuid, select, text, update
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.events import AuditEvent
from app.shared.persistence import Base

COMMAND_LEASE = timedelta(seconds=90)
COMMAND_WAIT = timedelta(minutes=2)
# The first agent that executes printer commands and enforces peel-off (tools/print-agent/agent.ps1).
MIN_AGENT_VERSION = (2, 11, 0)
OPEN = ("pending", "taken")
STATUS_LABELS = {"pending": "Ждёт агента", "taken": "Выполняется", "done": "Выполнено", "failed": "Не выполнено"}
NOT_TAKEN = "Агент печати не забрал команду — проверьте, что он запущен на ПК принтера"
NOT_REPORTED = "Агент не сообщил итог очистки — проверьте принтер и повторите"


class PrinterCommandRow(Base):
    __tablename__ = "warehouse_printer_commands"
    __table_args__ = (
        ForeignKeyConstraint(["machine_id", "printer"], ["warehouse_agent_printers.machine_id", "warehouse_agent_printers.name"],
                             name="fk_printer_command_printer", ondelete="RESTRICT"),
        CheckConstraint("kind IN ('clear')", name="ck_printer_command_kind"),
        CheckConstraint("status IN ('done', 'pending', 'taken', 'failed')", name="ck_printer_command_status"),
        CheckConstraint("trim(reason) <> ''", name="ck_printer_command_reason"),
        CheckConstraint("status <> 'pending' OR (taken_at IS NULL AND attempt IS NULL AND lease_until IS NULL AND finished_at IS NULL)",
                        name="ck_printer_command_pending"),
        CheckConstraint("status <> 'taken' OR (taken_at IS NOT NULL AND attempt IS NOT NULL AND claim_token_id IS NOT NULL "
                        "AND lease_until IS NOT NULL AND finished_at IS NULL)", name="ck_printer_command_taken"),
        CheckConstraint("status IN ('pending', 'taken') OR finished_at IS NOT NULL", name="ck_printer_command_final"),
        CheckConstraint("status <> 'failed' OR (message IS NOT NULL AND trim(message) <> '')", name="ck_printer_command_failed"),
        Index("ix_printer_commands_target", "machine_id", "printer", "created_at"),
        Index("uq_printer_command_open", "machine_id", "printer", unique=True,
              sqlite_where=text("status IN ('pending', 'taken')"), postgresql_where=text("status IN ('pending', 'taken')")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(80), nullable=False)
    printer: Mapped[str] = mapped_column(String(255), nullable=False)
    equipment_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    requested_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[str | None] = mapped_column(String(36))
    claim_token_id: Mapped[UUID | None] = mapped_column(Uuid)
    message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)


class CommandBusy(Exception):
    """An open command already waits for this queue, or the agent cannot execute commands."""


class CommandFinished(Exception):
    """The command is not taken by this attempt any more."""


def _session(uow):
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def agent_executes_commands(version: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", version or "")) and tuple(map(int, version.split("."))) >= MIN_AGENT_VERSION


def expire_commands(session, now: datetime) -> None:
    session.execute(update(PrinterCommandRow).where(PrinterCommandRow.status == "pending",
        PrinterCommandRow.created_at < now - COMMAND_WAIT).values(status="failed", finished_at=now, message=NOT_TAKEN)
        .execution_options(synchronize_session=False))
    session.execute(update(PrinterCommandRow).where(PrinterCommandRow.status == "taken",
        PrinterCommandRow.lease_until < now).values(status="failed", finished_at=now, lease_until=None, message=NOT_REPORTED)
        .execution_options(synchronize_session=False))


def open_command_printers(session, machine_id: str) -> set[str]:
    return set(session.scalars(select(PrinterCommandRow.printer).where(PrinterCommandRow.machine_id == machine_id,
        PrinterCommandRow.status.in_(OPEN))))


def request_clear(session, *, equipment_id: UUID, machine_id: str, printer: str, reason: str, actor, now: datetime) -> PrinterCommandRow:
    """Queue «Очистить принтер» in the caller's transaction. CommandBusy — one is already open or the agent is old."""
    from .scan_agents import ScanAgentRow

    reason = " ".join(reason.split())
    if not reason:
        raise ValueError("Укажите причину очистки принтера")
    expire_commands(session, now)
    agent = session.get(ScanAgentRow, machine_id)
    if agent is None or not agent_executes_commands(agent.version):
        version = agent.version if agent else "нет"
        raise CommandBusy(f"Агент печати на ПК принтера — версии {version}: очистку выполняет агент "
                          f"{'.'.join(map(str, MIN_AGENT_VERSION))} и новее, дождитесь его обновления")
    if session.scalar(select(PrinterCommandRow.id).where(PrinterCommandRow.machine_id == machine_id,
            PrinterCommandRow.printer == printer, PrinterCommandRow.status.in_(OPEN)).limit(1)):
        raise CommandBusy("Очистка этого принтера уже выполняется")
    row = PrinterCommandRow(id=uuid4(), machine_id=machine_id, printer=printer, equipment_id=equipment_id, kind="clear",
        status="pending", reason=reason[:1000], requested_by=actor.id, requested_by_name=actor.name[:255], created_at=now)
    session.add(row)
    session.flush()  # uq_printer_command_open arbitrates a concurrent request (IntegrityError → 409)
    return row


def command_view(row: PrinterCommandRow | None) -> dict | None:
    if row is None:
        return None
    moment = lambda value: value.isoformat() if value is not None else None
    return {"id": str(row.id), "kind": row.kind, "status": row.status, "status_label": STATUS_LABELS[row.status],
            "reason": row.reason, "requested_by": row.requested_by_name, "created_at": moment(row.created_at),
            "finished_at": moment(row.finished_at), "message": row.message or "", "result": row.result or {}}


def latest_command(session, machine_id: str, printer: str) -> PrinterCommandRow | None:
    return session.scalar(select(PrinterCommandRow).where(PrinterCommandRow.machine_id == machine_id,
        PrinterCommandRow.printer == printer).order_by(PrinterCommandRow.created_at.desc(), PrinterCommandRow.id.desc()).limit(1))


def marking_printers(session, machine_id: str) -> list[dict]:
    """Each marking queue bound on this PC and whether a profile of its equipment runs the peel-off dispenser."""
    from .marking_profiles import MarkingBindingRow, MarkingProfileRow

    result: dict[str, bool] = {}
    for binding in session.scalars(select(MarkingBindingRow).where(MarkingBindingRow.machine_id == machine_id)):
        profiles = session.scalars(select(MarkingProfileRow.geometry).where(MarkingProfileRow.equipment_id == binding.equipment_id))
        peel = any(bool((geometry or {}).get("peel")) for geometry in profiles)
        result[binding.printer] = result.get(binding.printer, False) or peel
    return [{"name": name, "peel": peel} for name, peel in sorted(result.items())]


def claim_command(uow, *, machine_id: str, token_id: UUID, now: datetime) -> dict | None:
    """The oldest pending command for this PC becomes taken for COMMAND_LEASE."""
    with uow:
        session = _session(uow)
        expire_commands(session, now)
        candidate = session.scalar(select(PrinterCommandRow).where(PrinterCommandRow.machine_id == machine_id,
            PrinterCommandRow.status == "pending").order_by(PrinterCommandRow.created_at, PrinterCommandRow.id).limit(1).with_for_update())
        if candidate is None:
            uow.commit()
            return None
        attempt = str(uuid4())
        taken = session.execute(update(PrinterCommandRow).where(PrinterCommandRow.id == candidate.id, PrinterCommandRow.status == "pending")
            .values(status="taken", taken_at=now, lease_until=now + COMMAND_LEASE, attempt=attempt, claim_token_id=token_id)
            .execution_options(synchronize_session=False))
        result = None
        if taken.rowcount == 1:
            result = {"id": str(candidate.id), "attempt": attempt, "kind": candidate.kind, "printer": candidate.printer}
        uow.commit()
        return result


def report_command(uow, command_id: UUID, *, machine_id: str, token_id: UUID, attempt: str, outcome: str, message: str,
                   result: dict, now: datetime, actor=None) -> str:
    """The agent's result for one attempt, journaled. Idempotent for the same attempt and outcome; KeyError — no such command."""
    if outcome not in ("done", "failed"):
        raise ValueError("Неизвестный итог команды")
    message = " ".join((message or "").split())[:2000]
    if outcome == "failed" and not message:
        message = "Агент не сообщил причину"
    with uow:
        session = _session(uow)
        row = session.scalar(select(PrinterCommandRow).where(PrinterCommandRow.id == command_id).with_for_update())
        if row is None:
            raise KeyError("Команда принтера не найдена")
        if row.machine_id != machine_id or row.claim_token_id != token_id:
            raise PermissionError("Команда выдана другому агенту")
        if row.attempt != attempt:
            raise CommandFinished("Попытка выполнения устарела")
        if row.status == outcome:
            return row.status
        changed = session.execute(update(PrinterCommandRow).where(PrinterCommandRow.id == command_id,
            PrinterCommandRow.status == "taken", PrinterCommandRow.attempt == attempt)
            .values(status=outcome, finished_at=now, lease_until=None, message=message or None, result=result or {})
            .execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise CommandFinished("Итог команды уже записан")
        if actor is not None:
            uow.audit.add(AuditEvent(id=uuid4(), aggregate_id=row.equipment_id, event_type="production.marking_printer_cleared",
                actor=actor, occurred_at=now, details={"command_id": str(row.id), "machine_id": row.machine_id,
                    "printer": row.printer, "outcome": outcome, "message": message, **(result or {})}))
        uow.commit()
        return outcome
