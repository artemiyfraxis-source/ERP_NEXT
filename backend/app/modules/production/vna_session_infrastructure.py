"""Durable session/receipt/evidence storage; callers own the transaction."""

from datetime import datetime, timedelta
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        String, Text, UniqueConstraint, Uuid, select, text, update)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from . import vna_sessions as domain

ACTIVE_SQL = "state NOT IN ('recorded','failed','aborted')"
COMMAND_TIMEOUT = timedelta(seconds=180)


class VnaSessionRow(Base):
    __tablename__ = "production_vna_sessions"
    __table_args__ = (
        CheckConstraint("state IN (" + ",".join(repr(s) for s in domain.STATES) + ")", name="ck_vna_sessions_state"),
        CheckConstraint("version >= 1 AND step >= 0 AND product_position >= 1", name="ck_vna_sessions_counters"),
        CheckConstraint("trim(instrument_serial) <> ''", name="ck_vna_sessions_serial"),
        CheckConstraint("trim(machine_id) <> ''", name="ck_vna_sessions_machine"),
        Index("uq_vna_sessions_instrument_active", "instrument_id", unique=True,
              sqlite_where=text(ACTIVE_SQL), postgresql_where=text(ACTIVE_SQL)),
        Index("uq_vna_sessions_serial_active", "instrument_serial", unique=True,
              sqlite_where=text(ACTIVE_SQL), postgresql_where=text(ACTIVE_SQL)),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    # Warehouse owns PC identity; the workflow validates it without coupling module-subset schemas.
    machine_id: Mapped[str] = mapped_column(String(80), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False)
    instrument_serial: Mapped[str] = mapped_column(String(300), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False)
    product_position: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    recorded_reference: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VnaCommandRow(Base):
    __tablename__ = "production_vna_commands"
    __table_args__ = (
        CheckConstraint("op IN (" + ",".join(repr(s) for s in domain.OPS) + ")", name="ck_vna_commands_op"),
        CheckConstraint("status IN ('pending','claimed','done','failed','cancelled','uncertain')", name="ck_vna_commands_status"),
        CheckConstraint("expected_version >= 1 AND expected_step >= 0", name="ck_vna_commands_counters"),
        CheckConstraint("status <> 'claimed' OR claimed_at IS NOT NULL", name="ck_vna_commands_claimed"),
        UniqueConstraint("session_id", "expected_version", name="uq_vna_commands_version"),
        Index("uq_vna_commands_active", "session_id", unique=True,
              sqlite_where=text("status IN ('pending','claimed')"), postgresql_where=text("status IN ('pending','claimed')")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_sessions.id", ondelete="RESTRICT"), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_step: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[str] = mapped_column(String(32), nullable=False)
    args: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VnaArtifactRow(Base):
    __tablename__ = "production_vna_artifacts"
    __table_args__ = (
        CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_vna_artifacts_size"),
        UniqueConstraint("command_id", "file_name", name="uq_vna_artifacts_command_file"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_sessions.id", ondelete="RESTRICT"), nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_commands.id", ondelete="RESTRICT"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(110), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VnaSessionRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, session_id: UUID) -> VnaSessionRow:
        row = self.session.get(VnaSessionRow, session_id, populate_existing=True)
        if row is None:
            raise KeyError("Сессия ВАЦ не найдена")
        return row

    def active(self, instrument_id: UUID | None, serial: str = "") -> bool:
        match = VnaSessionRow.instrument_id == instrument_id
        if serial:
            match = match | (VnaSessionRow.instrument_serial == serial)
        return self.session.scalar(select(VnaSessionRow.id).where(
            match, VnaSessionRow.state.not_in(domain.TERMINAL)).limit(1)) is not None

    def create(self, *, now: datetime, **values) -> VnaSessionRow:
        row = VnaSessionRow(id=uuid4(), state="draft", version=1, step=0,
                            created_at=now, updated_at=now, **values)
        self.session.add(row)
        self.session.flush()  # DB unique indexes arbitrate simultaneous sessions, including duplicate serials.
        return row

    @staticmethod
    def expect(row, version, step):
        if (row.version, row.step) != (version, step):
            raise domain.SessionConflict("Сессия изменилась — обновите шаг")

    def _change(self, row, now, **values):
        changed = self.session.execute(update(VnaSessionRow).where(
            VnaSessionRow.id == row.id, VnaSessionRow.version == row.version,
            VnaSessionRow.step == row.step, VnaSessionRow.state == row.state,
        ).values(version=row.version + 1, updated_at=now, **values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise domain.SessionConflict("Сессия изменилась — обновите шаг")
        self.session.refresh(row)

    def _enqueue(self, row, op, args, now):
        args = domain.ARGS[op].model_validate(args).model_dump(mode="json")
        command = VnaCommandRow(id=uuid4(), session_id=row.id, expected_version=row.version,
                                expected_step=row.step, op=op, args=args, status="pending", created_at=now)
        self.session.add(command)
        self.session.flush()
        return command

    def _next(self, row, step, now):
        if step == len(row.steps):
            self._change(row, now, step=step, state="review", error="")
            return
        next_step = row.steps[step]
        state = domain.waiting_state(next_step) if next_step["human"] else domain.executing_state(next_step["op"])
        self._change(row, now, step=step, state=state, error="")
        if not next_step["human"]:
            self._enqueue(row, next_step["op"], next_step["args"], now)

    def act(self, row, action, version, step, now):
        self.expect(row, version, step)
        if action == "plan" and row.state == "draft":
            self._change(row, now, state="planned")
        elif action == "start" and row.state == "planned":
            self._next(row, 0, now)
        elif action == "confirm" and row.state in ("awaiting_standard", "awaiting_dut"):
            current = row.steps[row.step]
            self._change(row, now, state=domain.executing_state(current["op"]))
            self._enqueue(row, current["op"], current["args"], now)
        elif action == "abort" and row.state not in (*domain.TERMINAL, "aborting"):
            self._abort(row, now)
        elif action == "recover" and row.state == "recovery_required":
            self._abort(row, now)
        else:
            raise domain.SessionConflict("Действие недоступно на текущем шаге")

    def _abort(self, row, now):
        untouched = row.state in ("draft", "planned")
        self._change(row, now, state="aborted" if untouched else "aborting", error="")
        self.session.execute(update(VnaCommandRow).where(
            VnaCommandRow.session_id == row.id, VnaCommandRow.status.in_(("pending", "claimed")),
        ).values(status="cancelled", finished_at=now))
        if not untouched:
            self._enqueue(row, "abort", {}, now)

    def expire(self, now, *, machine_id=None, session_id=None):
        query = select(VnaCommandRow).join(VnaSessionRow).where(
            VnaCommandRow.status == "claimed", VnaCommandRow.claimed_at < now - COMMAND_TIMEOUT,
            VnaSessionRow.state.not_in(domain.TERMINAL))
        if machine_id is not None:
            query = query.where(VnaSessionRow.machine_id == machine_id)
        if session_id is not None:
            query = query.where(VnaSessionRow.id == session_id)
        for command in list(self.session.scalars(query)):
            row = self.get(command.session_id)
            self.expect(row, command.expected_version, command.expected_step)
            self._change(row, now, state="recovery_required", error="Ответ ВАЦ потерян; требуется остановка и восстановление")
            command.status, command.finished_at = "uncertain", now
        self.session.flush()

    def pending(self, machine_id):
        return self.session.scalar(select(VnaCommandRow).join(VnaSessionRow).where(
            VnaSessionRow.machine_id == machine_id, VnaCommandRow.status.in_(("pending", "claimed")),
            VnaSessionRow.state.not_in((*domain.TERMINAL, "recovery_required")),
        ).order_by(VnaCommandRow.created_at, VnaCommandRow.id).limit(1))

    def claim(self, command, now):
        row = self.get(command.session_id)
        self.expect(row, command.expected_version, command.expected_step)
        # Serialize claim with abort/result using the same session row. Claim is not a new logical step.
        locked = self.session.execute(update(VnaSessionRow).where(
            VnaSessionRow.id == row.id, VnaSessionRow.version == row.version,
        ).values(updated_at=row.updated_at))
        if locked.rowcount != 1:
            raise domain.SessionConflict("Сессия изменилась")
        self.session.execute(update(VnaCommandRow).where(
            VnaCommandRow.id == command.id, VnaCommandRow.status == "pending",
        ).values(status="claimed", claimed_at=now))
        self.session.refresh(command)
        if command.status != "claimed":
            raise domain.SessionConflict("Команда уже закрыта")
        return self.command_view(command, row)

    def complete(self, row, command_id, version, step, result, error, now):
        command = self.session.get(VnaCommandRow, command_id, populate_existing=True)
        if command is None or command.session_id != row.id:
            raise KeyError("Команда ВАЦ не найдена")
        if (command.expected_version, command.expected_step) != (version, step):
            raise domain.SessionConflict("Ответ относится к другой версии или шагу")
        digest = domain.fingerprint(dict(result=result, error=error))
        if command.status in ("done", "failed"):
            if command.result_hash != digest:
                raise domain.SessionConflict("Повторный ответ отличается от сохранённого")
            return  # Lost HTTP acknowledgement: no state mutation, no second artifact or execution.
        if command.status != "claimed" or (row.state == "aborting" and command.op not in ("abort", "restore")):
            raise domain.SessionConflict("Команда не ожидает результат")
        self.expect(row, version, step)
        if error:
            saved, artifacts = {"error": error}, []
        else:
            saved, artifacts = domain.validate_result(command.op, command.args, row.instrument_serial, result)
        # CAS the session before writing a receipt/evidence. A competing abort rolls everything back.
        self._change(row, now, state="recovery_required" if error else row.state,
                     error=error or "")
        command.status, command.finished_at = ("failed" if error else "done"), now
        command.result, command.result_hash = saved, digest
        self.session.flush()
        for artifact, content in artifacts:
            self.session.add(VnaArtifactRow(id=uuid4(), session_id=row.id, command_id=command.id,
                file_name=artifact.file_name, content=content, sha256=hashlib.sha256(content).hexdigest(),
                evidence=dict(serial=row.instrument_serial, traces=artifact.traces,
                              orientation=command.args["orientation"], profile=command.args["profile"],
                              port_to_end=command.args["port_to_end"],
                              correction=saved["correction"]), created_at=now))
        self.session.flush()
        if error:
            return
        if row.state == "aborting":
            if command.op == "abort":
                self._enqueue(row, "restore", {}, now)
            else:
                self._change(row, now, state="aborted", error="")
        else:
            self._next(row, row.step + 1, now)

    def completed(self, session_id: UUID) -> dict:
        """P4 integration port: immutable server evidence; no browser calibration claims."""
        row = self.get(session_id)
        if row.state not in ("review", "recorded"):
            raise domain.SessionConflict("Сессия ещё не готова к записи замера")
        return self.view(row)

    def mark_recorded(self, session_id, *, expected_version, reference: str, now):
        """Call inside P4's attempt transaction, after norm/band validation and attempt persistence."""
        row = self.get(session_id)
        if not reference or len(reference) > 128:
            raise ValueError("Нужен идентификатор записанной попытки")
        if row.state == "recorded" and row.recorded_reference == reference:
            return
        if row.state != "review" or row.version != expected_version:
            raise domain.SessionConflict("Сессия изменилась или уже записана")
        self._change(row, now, state="recorded", recorded_reference=reference)

    @staticmethod
    def command_view(command, row):
        return dict(id=str(command.id), command_id=str(command.id), kind="vna_control", protocol_version=1,
                    session_id=str(row.id), expected_version=command.expected_version,
                    expected_step=command.expected_step, instrument_id=str(row.instrument_id),
                    instrument_serial=row.instrument_serial, op=command.op, args=command.args)

    def view(self, row):
        commands = list(self.session.scalars(select(VnaCommandRow).where(
            VnaCommandRow.session_id == row.id).order_by(VnaCommandRow.created_at, VnaCommandRow.expected_version)))
        artifacts = list(self.session.scalars(select(VnaArtifactRow).where(VnaArtifactRow.session_id == row.id)))
        return dict(id=str(row.id), state=row.state, version=row.version, step=row.step,
                    operator_id=str(row.operator_id), machine_id=row.machine_id, instrument_id=str(row.instrument_id),
                    instrument_serial=row.instrument_serial, task_id=str(row.task_id), operation_id=str(row.operation_id),
                    product_position=row.product_position, snapshot=row.snapshot, steps=row.steps, error=row.error,
                    recorded_reference=row.recorded_reference,
                    commands=[dict(**self.command_view(c, row), status=c.status, result=c.result) for c in commands],
                    artifacts=[dict(id=str(a.id), command_id=str(a.command_id), file_name=a.file_name,
                                    sha256=a.sha256, metadata=a.evidence) for a in artifacts])
