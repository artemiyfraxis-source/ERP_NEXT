"""Durable session/receipt/evidence storage; callers own the transaction."""

from datetime import datetime, timedelta
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        String, Text, UniqueConstraint, Uuid, select, text, update)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from . import vna_bench as bench
from . import vna_sessions as domain
from .vna_bench_protocol import OPS_V2, command_args, validate_result_v2
from .vna_screen_infrastructure import VnaScreenRepository

ACTIVE_SQL = "state NOT IN ('recorded','failed','aborted')"
COMMAND_TIMEOUT = timedelta(seconds=180)
BENCH_IDLE = timedelta(hours=12)   # a bench session idle in `ready` this long is closed and frees the instrument
# Bench sessions (0205_vna_bench_sessions): purpose, nullable task fields and an all-or-nothing calibration.
SESSION_CHECKS = {
    "ck_vna_sessions_purpose": "purpose IN ('assembly','bench')",
    "ck_vna_sessions_target": "(purpose = 'assembly' AND task_id IS NOT NULL AND operation_id IS NOT NULL AND "
                              "product_position IS NOT NULL) OR (purpose = 'bench' AND task_id IS NULL AND "
                              "operation_id IS NULL AND product_position IS NULL)",
    "ck_vna_sessions_bench_state": "purpose = 'bench' OR state NOT IN ('ready','releasing')",
    "ck_vna_sessions_bench_only": "purpose = 'bench' OR (calibrated_at IS NULL AND calibration_temps IS NULL AND "
                                  "profile_hash IS NULL AND calibration_check_id IS NULL)",
    "ck_vna_sessions_calibrated": "(calibrated_at IS NULL) = (calibration_temps IS NULL) AND "
                                  "(calibrated_at IS NULL) = (profile_hash IS NULL) AND "
                                  "(calibrated_at IS NULL) = (calibration_check_id IS NULL)",
    "ck_vna_sessions_profile_hash": "profile_hash IS NULL OR length(profile_hash) = 64",
}
ITEM_CHECKS = {
    "ck_vna_bench_items_kind": "kind IN (" + ",".join(repr(k) for k in bench.ITEM_KINDS) + ")",
    "ck_vna_bench_items_target": "target IN (" + ",".join(repr(t) for t in bench.TARGETS) + ")",
    "ck_vna_bench_items_kind_target": "(kind IN ('instrument','assembly','control_standard') AND target = 'dut') OR "
                                      "(kind = 'load' AND target IN ('port1','port2')) OR "
                                      "(kind = 'flex' AND target IN ('port1','port2','thru','thru_bent')) OR "
                                      "(kind = 'kit_standards' AND target IN ('port1','port2','thru'))",
    "ck_vna_bench_items_standard": "(kind = 'kit_standards' AND standard IN ('OPEN','SHORT','LOAD','THRU') AND "
                                   "(standard = 'THRU') = (target = 'thru')) OR (kind <> 'kit_standards' AND standard IS NULL)",
    "ck_vna_bench_items_connection": "connection_no BETWEEN 1 AND 3",
    "ck_vna_bench_items_verdict": "verdict IS NULL OR verdict IN ('pass','border','fail','not_judged')",
    "ck_vna_bench_items_object": "(kind IN ('instrument','control_standard','kit_standards') AND instrument_id IS NOT NULL) OR "
                                 "(kind NOT IN ('instrument','control_standard','kit_standards') AND instrument_id IS NULL)",
    "ck_vna_bench_items_assembly": "(kind = 'assembly' AND task_id IS NOT NULL AND product_position >= 1) OR "
                                   "(kind <> 'assembly' AND task_id IS NULL AND product_position IS NULL)",
    "ck_vna_bench_items_repeat": "connection_no = 1 OR kind IN ('instrument','assembly','kit_standards')",
    "ck_vna_bench_items_recorded_file": "recorded_file_id IS NULL OR kind = 'instrument'",
    "ck_vna_bench_items_vswr": "vswr_measurement_id IS NULL OR kind = 'assembly'",
    "ck_vna_bench_items_recorded": "(recorded_at IS NULL) = (recorded_file_id IS NULL AND vswr_measurement_id IS NULL)",
}


class VnaSessionRow(Base):
    __tablename__ = "production_vna_sessions"
    __table_args__ = (
        CheckConstraint("state IN (" + ",".join(repr(s) for s in domain.STATES) + ")", name="ck_vna_sessions_state"),
        CheckConstraint("version >= 1 AND step >= 0 AND product_position >= 1", name="ck_vna_sessions_counters"),
        CheckConstraint("trim(instrument_serial) <> ''", name="ck_vna_sessions_serial"),
        CheckConstraint("trim(machine_id) <> ''", name="ck_vna_sessions_machine"),
        *(CheckConstraint(sql, name=name) for name, sql in SESSION_CHECKS.items()),
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
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="assembly", server_default="assembly")
    task_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_tasks.id", ondelete="RESTRICT"))
    operation_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_operations.id", ondelete="RESTRICT"))
    product_position: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    recorded_reference: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    # Bench only: the valid calibration (СТО ФРАК-002-2026 п. 11.5) — all set together with its passed journal row.
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calibration_temps: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))   # None is SQL NULL for the CHECK
    profile_hash: Mapped[str | None] = mapped_column(String(64))
    calibration_check_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("production_vna_checks.id", ondelete="RESTRICT", name="fk_vna_sessions_calibration_check"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VnaCommandRow(Base):
    __tablename__ = "production_vna_commands"
    __table_args__ = (
        CheckConstraint("op IN (" + ",".join(repr(s) for s in OPS_V2) + ")", name="ck_vna_commands_op"),
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


class VnaBenchItemRow(Base):
    """One accepted bench measurement: what was connected, its connection 1..3 in a judged series, its verdict."""

    __tablename__ = "production_vna_bench_items"
    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in ITEM_CHECKS.items()),
        UniqueConstraint("command_id", name="uq_vna_bench_items_command"),
        UniqueConstraint("artifact_id", name="uq_vna_bench_items_artifact"),
        UniqueConstraint("group_id", "connection_no", name="uq_vna_bench_items_connection"),
        UniqueConstraint("recorded_file_id", name="uq_vna_bench_items_recorded_file"),
        UniqueConstraint("vswr_measurement_id", name="uq_vna_bench_items_vswr"),
        Index("ix_vna_bench_items_session", "session_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_sessions.id", ondelete="RESTRICT"), nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_commands.id", ondelete="RESTRICT"), nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_vna_artifacts.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    group_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    connection_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(String(12), nullable=False)
    standard: Mapped[str | None] = mapped_column(String(8))
    instrument_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_instruments.id", ondelete="RESTRICT"))
    task_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_tasks.id", ondelete="RESTRICT"))
    product_position: Mapped[int | None] = mapped_column(Integer)
    verdict: Mapped[str | None] = mapped_column(String(12))
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_file_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("production_instrument_files.id", ondelete="RESTRICT"))
    vswr_measurement_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("production_vswr_measurements.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def protocol(row) -> int:
    """Bench sessions speak protocol v2; the one-position ОТК session stays on v1."""
    return 2 if row.purpose == "bench" else 1


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

    def lock(self, row):
        """Serialize claim, operator actions and recording before reading command state."""
        locked = self.session.execute(update(VnaSessionRow).where(
            VnaSessionRow.id == row.id, VnaSessionRow.version == row.version,
            VnaSessionRow.step == row.step, VnaSessionRow.state == row.state,
        ).values(updated_at=VnaSessionRow.updated_at).execution_options(synchronize_session=False))
        if locked.rowcount != 1:
            raise domain.SessionConflict("Сессия изменилась — обновите шаг")
        self.session.refresh(row)

    def _change(self, row, now, **values):
        changed = self.session.execute(update(VnaSessionRow).where(
            VnaSessionRow.id == row.id, VnaSessionRow.version == row.version,
            VnaSessionRow.step == row.step, VnaSessionRow.state == row.state,
        ).values(version=row.version + 1, updated_at=now, **values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise domain.SessionConflict("Сессия изменилась — обновите шаг")
        self.session.refresh(row)

    def _enqueue(self, row, op, args, now):
        args = command_args(protocol(row), op, args)
        command = VnaCommandRow(id=uuid4(), session_id=row.id, expected_version=row.version,
                                expected_step=row.step, op=op, args=args, status="pending", created_at=now)
        self.session.add(command)
        self.session.flush()
        return command

    def _next(self, row, step, now):
        if step == len(row.steps):
            # A bench session waits for the operator's next item; the ОТК session goes to review.
            self._change(row, now, step=step, state="ready" if row.purpose == "bench" else "review", error="")
            return
        next_step = row.steps[step]
        state = domain.waiting_state(next_step) if next_step["human"] else self._executing(row, next_step["op"])
        self._change(row, now, step=step, state=state, error="")
        if not next_step["human"]:
            self._enqueue(row, next_step["op"], next_step["args"], now)

    @staticmethod
    def _executing(row, op):
        return bench.executing_state(op) if row.purpose == "bench" else domain.executing_state(op)

    def act(self, row, action, version, step, now):
        self.lock(row)
        self.expect(row, version, step)
        is_bench = row.purpose == "bench"
        if action == "plan" and row.state == "draft":
            self._change(row, now, state="planned")
        elif action == "start" and row.state == "planned":
            self._next(row, 0, now)
        elif action == "confirm" and row.state in ("awaiting_standard", "awaiting_dut"):
            current = row.steps[row.step]
            self._change(row, now, state=self._executing(row, current["op"]))
            self._enqueue(row, current["op"], current["args"], now)
        elif is_bench and action == "recalibrate" and row.state == "ready":
            # СТО ФРАК-002-2026 п. 11.5: a lost calibration is redone in the same session; nothing of the old one stays.
            self.append(row, bench.calibration_steps(row.snapshot, load=VnaScreenRepository(self.session).default("load")),
                        now, **bench.UNCALIBRATED)
        elif is_bench and action == "close" and row.state == "ready":
            self.append(row, [bench.release_step(bench.last_screen_hash(row.steps))], now)
        elif is_bench and action == "cancel_item" and row.state in ("awaiting_standard", "awaiting_dut"):
            # The human step was never sent to the agent: drop it and the rest of its item.
            self._change(row, now, steps=row.steps[:row.step])
            self._next(row, row.step, now)
        elif action == "abort" and row.state not in (*domain.TERMINAL, "aborting"):
            self._abort(row, now)
        elif action == "recover" and row.state == "recovery_required":
            self._abort(row, now)
        elif (action == "release_unrestored" and row.state == "recovery_required" and row.purpose == "bench"
              and protocol(row) == 2):
            # Admin escape when restoring keeps failing: the agent stops controlling the VNA without loading the
            # operator's backup (its settings are checked by hand); the session ends `aborted` on the receipt.
            # It runs as the bench's own closing step (`releasing`): every agent refuses `release` while `aborting`
            # (Assert-VnaLease accepts only abort/restore there), 2.12.0 included.
            self.session.execute(update(VnaCommandRow).where(
                VnaCommandRow.session_id == row.id, VnaCommandRow.status.in_(("pending", "claimed")),
            ).values(status="cancelled", finished_at=now))
            closing = bench.release_step(None)
            closing["item"] = {**closing["item"], "unrestored": True}
            self._change(row, now, steps=row.steps[:row.step] + [closing], state=bench.executing_state("release"),
                         error="")
            self._enqueue(row, "release", closing["args"], now)
        else:
            raise domain.SessionConflict("Действие недоступно на текущем шаге")

    def _abort(self, row, now):
        # act() holds the same row lock as claim(): pending cannot turn into claimed here.
        prepare_unclaimed = row.step == 0 and row.state == "preparing" and self.session.scalar(
            select(VnaCommandRow.id).where(
                VnaCommandRow.session_id == row.id, VnaCommandRow.expected_version == row.version,
                VnaCommandRow.op == "prepare", VnaCommandRow.status == "pending",
                VnaCommandRow.claimed_at.is_(None),
            )) is not None
        # Review is reached only after the final restore receipt has been accepted.
        local_abort = row.state in ("draft", "planned", "review") or prepare_unclaimed
        self._change(row, now, state="aborted" if local_abort else "aborting", error="")
        self.session.execute(update(VnaCommandRow).where(
            VnaCommandRow.session_id == row.id, VnaCommandRow.status.in_(("pending", "claimed")),
        ).values(status="cancelled", finished_at=now))
        if not local_abort:
            self._enqueue(row, "abort", {}, now)

    def expire(self, now, *, machine_id=None, session_id=None):
        self._expire_idle_bench(now, machine_id=machine_id, session_id=session_id)
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

    def _expire_idle_bench(self, now, *, machine_id=None, session_id=None):
        """A forgotten workplace session must not hold the instrument for ever: idle in `ready` for 12 h → aborted.
        Nothing is queued in `ready`, so no device work is left; the operator's S2VNA state is not restored."""
        query = select(VnaSessionRow).where(VnaSessionRow.purpose == "bench", VnaSessionRow.state == "ready",
                                            VnaSessionRow.updated_at < now - BENCH_IDLE)
        if machine_id is not None:
            query = query.where(VnaSessionRow.machine_id == machine_id)
        if session_id is not None:
            query = query.where(VnaSessionRow.id == session_id)
        for row in list(self.session.scalars(query)):
            self._change(row, now, state="aborted", error="Сеанс рабочего места простоял больше 12 ч и закрыт; "
                                                          "настройки оператора на ВАЦ не восстанавливались")
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
        self.lock(row)
        self.session.execute(update(VnaCommandRow).where(
            VnaCommandRow.id == command.id, VnaCommandRow.status == "pending",
        ).values(status="claimed", claimed_at=now))
        self.session.refresh(command)
        if command.status != "claimed":
            raise domain.SessionConflict("Команда уже закрыта")
        return self.command_view(command, row)

    def complete(self, row, command_id, version, step, result, error, now, *, backup_saved: bool | None = None,
                 advance: bool = True):
        """Store one receipt. With `advance=False` (bench) an accepted success is returned for the workflow to judge
        and continue (`proceed`); a replay, an error or an abort path returns None."""
        command = self.session.get(VnaCommandRow, command_id, populate_existing=True)
        if command is None or command.session_id != row.id:
            raise KeyError("Команда ВАЦ не найдена")
        if (command.expected_version, command.expected_step) != (version, step):
            raise domain.SessionConflict("Ответ относится к другой версии или шагу")
        if backup_saved is not None and (type(backup_saved) is not bool or command.op != "prepare" or not error):
            raise ValueError("Признак резервной копии допустим только для ошибки подготовки")
        receipt = dict(result=result, error=error)
        if backup_saved is not None:
            receipt["backup_saved"] = backup_saved
        digest = domain.fingerprint(receipt)  # Preserve the original digest for receipts without the new field.
        if command.status in ("done", "failed"):
            if command.result_hash != digest:
                raise domain.SessionConflict("Повторный ответ отличается от сохранённого")
            return None  # Lost HTTP acknowledgement: no state mutation, no second artifact or execution.
        if command.status != "claimed" or (row.state == "aborting" and command.op not in ("abort", "restore")):
            raise domain.SessionConflict("Команда не ожидает результат")
        self.expect(row, version, step)
        state = row.state
        if error:
            saved, artifacts = {"error": error}, []
            if backup_saved is not None:
                saved["backup_saved"] = backup_saved
            # Only a completed prepare failure explicitly preceding backup can release the physical lease.
            # Missing information, timeouts, cancelled commands and later operations remain conservative.
            state = "failed" if command.op == "prepare" and backup_saved is False else "recovery_required"
        elif protocol(row) == 2:
            saved, artifacts = validate_result_v2(command.op, command.args, row.instrument_serial, result)
        else:
            saved, artifacts = domain.validate_result(command.op, command.args, row.instrument_serial, result)
        # CAS the session before writing a receipt/evidence. A competing abort rolls everything back.
        self._change(row, now, state=state, error=error or "")
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
            return None
        if row.state == "aborting":
            if command.op == "abort":
                self._enqueue(row, "restore", {}, now)
            else:
                self._change(row, now, state="aborted", error="")
        elif advance:
            self._next(row, row.step + 1, now)
        else:
            return command
        return None

    # ---- bench sessions: the workflow judges each receipt, then continues ----------------------------------------

    def append(self, row, steps, now, **values):
        """Bench, from `ready`: add an item's (or the calibration's / closing) steps and start the first one."""
        if row.purpose != "bench" or row.state != "ready":
            raise domain.SessionConflict("Сессия рабочего места не ожидает новый шаг")
        start = len(row.steps)
        self._change(row, now, steps=row.steps + steps, **values)
        self._next(row, start, now)

    def proceed(self, row, now, *, tail: list | None = None, error: str = "", **values):
        """Bench, after a judged receipt: optionally replace every step after the current one, then run the next.
        `error` is the operator-facing reason when an item stopped (kept until the next action)."""
        if tail is not None or values:
            steps = row.steps if tail is None else row.steps[:row.step + 1] + tail
            self._change(row, now, steps=steps, **values)
        self._next(row, row.step + 1, now)
        if error:
            self._change(row, now, error=error[:2000])

    def abandon(self, row, now):
        """Bench: the admin's release without restoring was accepted — control ended, nothing is recorded."""
        self._change(row, now, state="aborted", step=len(row.steps))

    def finish(self, row, now):
        """Bench: `release` was accepted — the agent keeps the ERP screen and calibration for viewing only."""
        self._change(row, now, state="recorded", step=len(row.steps))

    def add_item(self, row, command, artifact_id, item: dict, verdict, result, now, *,
                 vswr_measurement_id=None) -> VnaBenchItemRow:
        record = VnaBenchItemRow(
            id=uuid4(), session_id=row.id, command_id=command.id, artifact_id=artifact_id, run_id=UUID(item["run_id"]),
            group_id=UUID(item["group_id"]), connection_no=item["connection_no"], kind=item["kind"], target=item["target"],
            standard=item.get("standard"), instrument_id=UUID(item["instrument_id"]) if item.get("instrument_id") else None,
            task_id=UUID(item["task_id"]) if item.get("task_id") else None, product_position=item.get("product_position"),
            verdict=verdict, result=result, created_at=now, vswr_measurement_id=vswr_measurement_id,
            recorded_at=now if vswr_measurement_id is not None else None)
        self.session.add(record)
        self.session.flush()
        return record

    def record_file(self, item: VnaBenchItemRow, file_id, now):
        """«Записать в карточку»: the connection's file is on the card — once (UNIQUE file, CHECK on kind)."""
        if item.recorded_file_id is None:
            item.recorded_file_id, item.recorded_at = file_id, now
            self.session.flush()

    def items(self, session_id, run_id=None) -> list[VnaBenchItemRow]:
        query = select(VnaBenchItemRow).where(VnaBenchItemRow.session_id == session_id)
        if run_id is not None:
            query = query.where(VnaBenchItemRow.run_id == UUID(str(run_id)))
        # In the order the sweeps were commanded: the command version grows with every session change.
        query = query.join(VnaCommandRow, VnaCommandRow.id == VnaBenchItemRow.command_id)
        return list(self.session.scalars(query.order_by(VnaCommandRow.expected_version)))

    def artifact(self, command_id) -> VnaArtifactRow:
        artifact = self.session.scalar(select(VnaArtifactRow).where(VnaArtifactRow.command_id == command_id))
        if artifact is None:
            raise ValueError("Замер не сохранён")
        if hashlib.sha256(bytes(artifact.content)).hexdigest() != artifact.sha256:
            raise ValueError("Контрольная сумма замера не совпадает")
        return artifact

    def receipts(self, row, op: str) -> list[VnaCommandRow]:
        return list(self.session.scalars(select(VnaCommandRow).where(
            VnaCommandRow.session_id == row.id, VnaCommandRow.op == op, VnaCommandRow.status == "done",
        ).order_by(VnaCommandRow.created_at, VnaCommandRow.expected_version)))

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
        return dict(id=str(command.id), command_id=str(command.id), kind="vna_control", protocol_version=protocol(row),
                    session_id=str(row.id), expected_version=command.expected_version,
                    expected_step=command.expected_step, instrument_id=str(row.instrument_id),
                    instrument_serial=row.instrument_serial, op=command.op, args=command.args)

    def view(self, row):
        commands = list(self.session.scalars(select(VnaCommandRow).where(
            VnaCommandRow.session_id == row.id).order_by(VnaCommandRow.created_at, VnaCommandRow.expected_version)))
        artifacts = list(self.session.scalars(select(VnaArtifactRow).where(VnaArtifactRow.session_id == row.id)))
        bench_view = {}
        if row.purpose == "bench":
            bench_view = dict(calibrated_at=row.calibrated_at, calibration_temps=row.calibration_temps,
                              profile_hash=row.profile_hash,
                              calibration_check_id=str(row.calibration_check_id) if row.calibration_check_id else None,
                              items=[self.item_view(item) for item in self.items(row.id)])
        return dict(id=str(row.id), purpose=row.purpose, state=row.state, version=row.version, step=row.step,
                    operator_id=str(row.operator_id), machine_id=row.machine_id, instrument_id=str(row.instrument_id),
                    instrument_serial=row.instrument_serial, task_id=str(row.task_id) if row.task_id else None,
                    operation_id=str(row.operation_id) if row.operation_id else None,
                    product_position=row.product_position, snapshot=row.snapshot, steps=row.steps, error=row.error,
                    recorded_reference=row.recorded_reference, **bench_view,
                    commands=[dict(**self.command_view(c, row), status=c.status, result=c.result) for c in commands],
                    artifacts=[dict(id=str(a.id), command_id=str(a.command_id), file_name=a.file_name,
                                    sha256=a.sha256, metadata=a.evidence) for a in artifacts])

    @staticmethod
    def item_view(item: VnaBenchItemRow) -> dict:
        return dict(id=str(item.id), command_id=str(item.command_id), artifact_id=str(item.artifact_id),
                    run_id=str(item.run_id), group_id=str(item.group_id), connection_no=item.connection_no, kind=item.kind,
                    target=item.target, standard=item.standard,
                    instrument_id=str(item.instrument_id) if item.instrument_id else None,
                    task_id=str(item.task_id) if item.task_id else None, product_position=item.product_position,
                    verdict=item.verdict, result=item.result, created_at=item.created_at, recorded_at=item.recorded_at,
                    recorded_file_id=str(item.recorded_file_id) if item.recorded_file_id else None,
                    vswr_measurement_id=str(item.vswr_measurement_id) if item.vswr_measurement_id else None)
