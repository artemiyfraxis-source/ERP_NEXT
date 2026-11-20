"""«Ручная работа» (plan «Станки вне проекта», slice S1; 19.11.2026): work on any machine outside a project, counted by
hand. Migration 0202_manual_jobs; HTTP in app/workflows/manual_jobs_api.py.

A job is started on one machine (`equipment_units`) with a required «Зачем», the wire and / or contact (from the
references or typed in), the length and an optional planned quantity, and is finished with good / defect pieces (defect
needs its reason) and a material decision — or cancelled with a reason. Constraints, not rollbacks:
- one running job per machine (UNIQUE partial index), a sequential number (UNIQUE, «Р-000001» on screen, «R-000001» for
  the PLC later), every status carries exactly its own fields (CHECKs below);
- the эталон a job follows belongs to that same machine (composite FK onto `equipment_setup_references`);
- every change after the start compares and swaps `version`.
The job never writes stock off by itself (plan, decision A): at the finish the person says «Не списывать» (with a reason)
or «Ждёт решения склада»; a finished job still pending is decided later (`decide_material`, slice S3): «Не списывать»
with a reason, or «Списать с партии» (`issued`, rows of `production_manual_job_issues`, each backed by its own
warehouse write-off — app/workflows/manual_job_stock.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric,
                        String, UniqueConstraint, Uuid, func, select, text, update)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

STATUSES = ("running", "done", "cancelled")
COUNT_SOURCES = ("plc", "crimp_log", "manual")
MATERIALS = ("pending", "issued", "not_issued")
# What the finish may say: «Списать с партии» is the warehouse's step after the finish (decide_material, slice S3).
FINISH_MATERIALS = ("pending", "not_issued")
RUNNING_WHERE = "status = 'running'"
NUMBER_PREFIX = "Р"  # Cyrillic Р: what people read
PLC_PREFIX = "R"  # the machine keeps ASCII only
NUMBER_ATTEMPTS = 5
LIMIT = 1_000_000
_SQL = lambda values: ", ".join(repr(value) for value in values)  # noqa: E731

STATUS_TEXTS = {"running": ("Идёт", "info"), "done": ("Завершена", "ok"), "cancelled": ("Отменена", "neutral")}
MATERIAL_TEXTS = {"pending": ("Ждёт решения склада", "warn"), "issued": ("Списано с партии", "ok"),
                  "not_issued": ("Не списывать", "neutral")}
COUNT_TEXTS = {"plc": "Счётчик станка", "crimp_log": "Журнал обжима", "manual": "Вручную"}


class ManualJobInvalid(Exception):
    """The request breaks a rule of the job (422)."""


class ManualJobConflict(Exception):
    """The job or the machine changed meanwhile (409)."""


class ManualJobRow(Base):
    __tablename__ = "production_manual_jobs"
    __table_args__ = (
        UniqueConstraint("number", name="uq_production_manual_jobs_number"),
        ForeignKeyConstraint(["reference_id", "equipment_id"],
                             ["equipment_setup_references.id", "equipment_setup_references.unit_id"],
                             name="fk_production_manual_jobs_reference", ondelete="RESTRICT"),
        CheckConstraint("number >= 1", name="ck_production_manual_jobs_number"),
        CheckConstraint(f"status IN ({_SQL(STATUSES)})", name="ck_production_manual_jobs_status"),
        CheckConstraint("trim(purpose) <> ''", name="ck_production_manual_jobs_purpose"),
        CheckConstraint("(wire = '') = (wire_key = '')", name="ck_production_manual_jobs_wire_key"),
        CheckConstraint("(contact = '') = (contact_key = '')", name="ck_production_manual_jobs_contact_key"),
        CheckConstraint("length_mm IS NULL OR length_mm > 0", name="ck_production_manual_jobs_length"),
        CheckConstraint("quantity_planned IS NULL OR quantity_planned > 0", name="ck_production_manual_jobs_planned"),
        CheckConstraint(f"count_source IN ({_SQL(COUNT_SOURCES)})", name="ck_production_manual_jobs_count_source"),
        CheckConstraint("count_source <> 'plc' OR counter_start IS NOT NULL", name="ck_production_manual_jobs_counter"),
        CheckConstraint("counter_start IS NULL OR counter_start >= 0", name="ck_production_manual_jobs_counter_start"),
        CheckConstraint("(machine_count IS NULL OR machine_count >= 0) AND (good_quantity IS NULL OR good_quantity >= 0)"
                        " AND (defect_quantity IS NULL OR defect_quantity >= 0)", name="ck_production_manual_jobs_counts"),
        CheckConstraint("COALESCE(defect_quantity, 0) = 0 OR trim(defect_reason) <> ''",
                        name="ck_production_manual_jobs_defect_reason"),
        CheckConstraint(f"material IN ({_SQL(MATERIALS)})", name="ck_production_manual_jobs_material"),
        CheckConstraint("material <> 'not_issued' OR trim(material_note) <> ''", name="ck_production_manual_jobs_not_issued"),
        CheckConstraint("status <> 'running' OR material = 'pending'", name="ck_production_manual_jobs_running_material"),
        CheckConstraint("status <> 'running' OR (good_quantity IS NULL AND defect_quantity IS NULL"
                        " AND machine_count IS NULL)", name="ck_production_manual_jobs_running_totals"),
        CheckConstraint("status <> 'done' OR (good_quantity IS NOT NULL AND defect_quantity IS NOT NULL"
                        " AND finished_at IS NOT NULL)", name="ck_production_manual_jobs_done"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="ck_production_manual_jobs_finished"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_production_manual_jobs_order"),
        CheckConstraint("status <> 'cancelled' OR trim(cancel_reason) <> ''", name="ck_production_manual_jobs_cancel"),
        CheckConstraint("trim(operator_name) <> ''", name="ck_production_manual_jobs_operator"),
        CheckConstraint("version >= 1", name="ck_production_manual_jobs_version"),
        # One running job per machine: a second start waits for the first to be finished or cancelled.
        Index("ux_production_manual_jobs_running", "equipment_id", unique=True,
              sqlite_where=text(RUNNING_WHERE), postgresql_where=text(RUNNING_WHERE)),
        Index("ix_production_manual_jobs_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_production_manual_jobs_equipment"),
        nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    wire: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    wire_key: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    contact: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    contact_key: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    length_mm: Mapped[Decimal | None] = mapped_column(Numeric(9, 2))
    # Settings of the job as written or read (S2 fills it from the machine); {} by hand.
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reference_id: Mapped[UUID | None] = mapped_column(Uuid)
    quantity_planned: Mapped[int | None] = mapped_column(Integer)
    count_source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    counter_start: Mapped[int | None] = mapped_column(Integer)
    machine_count: Mapped[int | None] = mapped_column(Integer)
    good_quantity: Mapped[int | None] = mapped_column(Integer)
    defect_quantity: Mapped[int | None] = mapped_column(Integer)
    defect_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    material: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    material_note: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    cancel_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    write_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("production_machine_writes.id", ondelete="SET NULL", name="fk_production_manual_jobs_write"))
    operator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    operator_name: Mapped[str] = mapped_column(String(200), nullable=False)
    finished_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ManualJobIssueRow(Base):
    """«Списать с партии» of a finished job (slice S3): one row per lot, each backed by its own write-off."""
    __tablename__ = "production_manual_job_issues"
    __table_args__ = (
        UniqueConstraint("job_id", "lot_id", name="uq_production_manual_job_issues_lot"),
        UniqueConstraint("writeoff_id", name="uq_production_manual_job_issues_writeoff"),
        CheckConstraint("quantity > 0", name="ck_production_manual_job_issues_quantity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("production_manual_jobs.id", ondelete="RESTRICT", name="fk_production_manual_job_issues_job"),
        nullable=False, index=True)
    lot_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("warehouse_lots.id", ondelete="RESTRICT", name="fk_production_manual_job_issues_lot"),
        nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    writeoff_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("warehouse_writeoffs.id", ondelete="RESTRICT", name="fk_production_manual_job_issues_writeoff"),
        nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --- pure rules ---

def number_text(number: int) -> str:
    return f"{NUMBER_PREFIX}-{number:06d}"


def plc_number(number: int) -> str:
    return f"{PLC_PREFIX}-{number:06d}"


def clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class NewJob:
    equipment_id: UUID
    purpose: str
    wire: str = ""
    wire_key: str = ""
    contact: str = ""
    contact_key: str = ""
    length_mm: Decimal | None = None
    quantity_planned: int | None = None
    reference_id: UUID | None = None
    params: dict | None = None


@dataclass(frozen=True)
class Finish:
    good: int
    defect: int
    defect_reason: str
    material: str
    material_note: str
    machine_count: int | None = None


def check_new(job: NewJob) -> NewJob:
    purpose = clean(job.purpose, 500)
    if not purpose:
        raise ManualJobInvalid("Укажите, зачем эта работа")
    wire, contact = clean(job.wire, 200), clean(job.contact, 200)
    length = job.length_mm
    if length is not None and (not length.is_finite() or length <= 0 or length >= Decimal("10000000")):
        raise ManualJobInvalid("Длина: положительное число, мм")
    if length is not None and length != length.quantize(Decimal("0.01")):
        raise ManualJobInvalid("Длина: с точностью до 0,01 мм")
    if job.quantity_planned is not None and not 0 < job.quantity_planned <= LIMIT:
        raise ManualJobInvalid("Количество: целое число больше нуля")
    wire_key = (clean(job.wire_key, 200) or wire.casefold()) if wire else ""
    contact_key = (clean(job.contact_key, 200) or contact.casefold()) if contact else ""
    return NewJob(job.equipment_id, purpose, wire, wire_key, contact, contact_key, length, job.quantity_planned,
                  job.reference_id, dict(job.params or {}))


def check_finish(finish: Finish) -> Finish:
    for value, label in ((finish.good, "Годные"), (finish.defect, "Брак")):
        if not 0 <= value <= LIMIT:
            raise ManualJobInvalid(f"{label}: целое число от 0")
    if finish.machine_count is not None and not 0 <= finish.machine_count <= LIMIT:
        raise ManualJobInvalid("Счётчик станка: целое число от 0")
    reason = clean(finish.defect_reason, 300)
    if finish.defect and not reason:
        raise ManualJobInvalid("Укажите причину брака")
    if finish.material == "issued":
        raise ManualJobInvalid("С партии списывают после завершения — выберите «Ждёт решения склада»")
    if finish.material not in FINISH_MATERIALS:
        raise ManualJobInvalid("Выберите, что делать с материалом")
    note = clean(finish.material_note, 300)
    if finish.material == "not_issued" and not note:
        raise ManualJobInvalid("Укажите, почему материал не списывается")
    return Finish(finish.good, finish.defect, reason if finish.defect else "", finish.material, note,
                  finish.machine_count)


# --- storage ---

def running(session: Session, equipment_id: UUID) -> ManualJobRow | None:
    return session.scalar(select(ManualJobRow).where(ManualJobRow.equipment_id == equipment_id,
                                                     ManualJobRow.status == "running"))


def _busy(row: ManualJobRow) -> ManualJobConflict:
    return ManualJobConflict(f"На станке уже идёт ручная работа {number_text(row.number)} — завершите или отмените её")


def next_number(session: Session) -> int:
    return (session.scalar(select(func.max(ManualJobRow.number))) or 0) + 1


def start(session: Session, job: NewJob, *, actor_id: UUID, actor_name: str, now: datetime) -> ManualJobRow:
    """A new running job with the next number. The number is max + 1 inside a savepoint: a parallel start that took it
    first makes the insert fail on the UNIQUE number and this one retries with the next — never two equal numbers."""
    job = check_new(job)
    name = clean(actor_name, 200) or "—"
    current = running(session, job.equipment_id)
    if current is not None:
        raise _busy(current)
    for _ in range(NUMBER_ATTEMPTS):
        number = next_number(session)
        row = ManualJobRow(id=uuid4(), number=number, equipment_id=job.equipment_id, status="running",
                           purpose=job.purpose, wire=job.wire, wire_key=job.wire_key, contact=job.contact,
                           contact_key=job.contact_key, length_mm=job.length_mm, params=job.params or {},
                           reference_id=job.reference_id, quantity_planned=job.quantity_planned, count_source="manual",
                           material="pending", operator_id=actor_id, operator_name=name, started_at=now, version=1)
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError as error:
            kind = integrity_kind(error)
            if kind == "number":
                continue
            if kind == "running":
                current = running(session, job.equipment_id)
                raise (_busy(current) if current is not None else ManualJobConflict(
                    "На станке уже идёт ручная работа — обновите список")) from None
            if kind == "foreign":
                raise ManualJobInvalid("Станок или эталон не найден — обновите окно") from None
            raise
        return row
    raise ManualJobConflict("Номер ручной работы заняли одновременно — повторите")


def integrity_kind(error: IntegrityError) -> str:
    """Which rule refused the insert: the number taken by a parallel start (retry), a job already running on the machine,
    a missing machine / эталон, or anything else (a bug — raised as is). PostgreSQL names the constraint, SQLite the
    columns."""
    text = str(getattr(error, "orig", error)).lower()
    if "uq_production_manual_jobs_number" in text or "production_manual_jobs.number" in text:
        return "number"
    if "ux_production_manual_jobs_running" in text or "production_manual_jobs.equipment_id" in text:
        return "running"
    if "foreign key" in text or "fk_production_manual_jobs" in text:
        return "foreign"
    return "other"


def _changed(session: Session, row: ManualJobRow, version: int, values: dict) -> ManualJobRow:
    count = session.execute(update(ManualJobRow).where(ManualJobRow.id == row.id, ManualJobRow.version == version,
                                                       ManualJobRow.status == "running")
                            .values(**values, version=version + 1)).rowcount
    if count != 1:
        raise ManualJobConflict("Ручную работу уже изменили — обновите и проверьте ещё раз")
    session.expire(row)
    session.refresh(row)
    return row


def _open(session: Session, job_id: UUID, version: int) -> ManualJobRow:
    row = session.get(ManualJobRow, job_id)
    if row is None:
        raise KeyError("Ручная работа не найдена")
    if row.status != "running":
        raise ManualJobConflict(f"Ручная работа {number_text(row.number)} уже {STATUS_TEXTS[row.status][0].lower()}")
    if row.version != version:
        raise ManualJobConflict("Ручную работу уже изменили — обновите и проверьте ещё раз")
    return row


def finish(session: Session, job_id: UUID, version: int, result: Finish, *, actor_name: str,
           now: datetime, counter_reset: bool = False, counter_unread: bool = False) -> ManualJobRow:
    """Done with good / defect. Run from the machine card (S2): `counter_reset` — the machine counter went back,
    `counter_unread` — it was not read (no link); the count was typed by hand, so the job is marked «Вручную» and
    `params.counter_reset` / `params.counter_unread`."""
    result = check_finish(result)
    row = _open(session, job_id, version)
    values = {
        "status": "done", "good_quantity": result.good, "defect_quantity": result.defect,
        "defect_reason": result.defect_reason, "machine_count": result.machine_count, "material": result.material,
        "material_note": result.material_note if result.material == "not_issued" else "",
        "finished_at": max(now, aware(row.started_at, now)), "finished_by_name": clean(actor_name, 200)}
    if counter_reset or counter_unread:
        values.update({"count_source": "manual", "machine_count": None,
                       "params": {**(row.params or {}), "counter_reset" if counter_reset else "counter_unread": True}})
    return _changed(session, row, version, values)


def attach_plc(session: Session, job_id: UUID, *, write_id: UUID, counter_start: int, params: dict) -> ManualJobRow | None:
    """The job's settings were written to the machine and read back (S2): from now it is counted by the machine's
    T.Finish from `counter_start`. A job no longer running (finished or cancelled meanwhile) is left as it is → None."""
    row = session.get(ManualJobRow, job_id)
    if row is None or row.status != "running":
        return None
    if row.count_source == "plc" and row.counter_start is not None:
        counter_start = row.counter_start  # written again after a correction: the first start stands
    return _changed(session, row, row.version, {"count_source": "plc", "counter_start": counter_start,
                                                "write_id": write_id, "params": {**(row.params or {}), **params}})


def cancel(session: Session, job_id: UUID, version: int, reason: str, *, actor_name: str,
           now: datetime) -> ManualJobRow:
    reason = clean(reason, 300)
    if not reason:
        raise ManualJobInvalid("Укажите причину отмены")
    row = _open(session, job_id, version)
    return _changed(session, row, version, {
        "status": "cancelled", "cancel_reason": reason, "finished_at": max(now, aware(row.started_at, now)),
        "finished_by_name": clean(actor_name, 200)})


DECISIONS = ("issued", "not_issued")


def decide_material(session: Session, job_id: UUID, version: int, material: str, note: str = "") -> ManualJobRow:
    """The material of a finished job still «Ждёт решения склада» (slice S3): «Не списывать» with a reason, or «Списать
    с партии» — the caller writes the lots off in the same transaction. Compare-and-swap on `version` AND on the job
    being done and still pending: the UPDATE locks the job row, so a parallel decision waits for it and then finds the
    version moved (409) — of two decisions exactly one stands."""
    if material not in DECISIONS:
        raise ManualJobInvalid("Выберите, что делать с материалом")
    note = clean(note, 300)
    if material == "not_issued" and not note:
        raise ManualJobInvalid("Укажите, почему материал не списывается")
    row = session.get(ManualJobRow, job_id)
    if row is None:
        raise KeyError("Ручная работа не найдена")
    if row.status == "running":
        raise ManualJobConflict(f"Ручная работа {number_text(row.number)} ещё идёт — сначала завершите её")
    if row.status != "done":
        raise ManualJobConflict(f"Ручная работа {number_text(row.number)} отменена — материал по ней не списывают")
    if row.material != "pending":
        raise ManualJobConflict(f"Материал по {number_text(row.number)} уже решён: {MATERIAL_TEXTS[row.material][0]}")
    if row.version != version:
        raise ManualJobConflict("Ручную работу уже изменили — обновите и проверьте ещё раз")
    count = session.execute(update(ManualJobRow).where(
        ManualJobRow.id == row.id, ManualJobRow.version == version, ManualJobRow.status == "done",
        ManualJobRow.material == "pending",
    ).values(material=material, material_note=note if material == "not_issued" else "", version=version + 1)).rowcount
    if count != 1:
        raise ManualJobConflict("Ручную работу уже изменили — обновите и проверьте ещё раз")
    session.expire(row)
    session.refresh(row)
    return row


def issues_of(session: Session, job_id: UUID) -> list[ManualJobIssueRow]:
    return list(session.scalars(select(ManualJobIssueRow).where(ManualJobIssueRow.job_id == job_id)
                                .order_by(ManualJobIssueRow.created_at, ManualJobIssueRow.id)))


WAITING_WHERE = (ManualJobRow.status == "done", ManualJobRow.material == "pending")


def waiting(session: Session, limit: int = 1000) -> list[ManualJobRow]:
    """Finished jobs whose material «Ждёт решения склада», whatever their date — the warehouse's queue."""
    return list(session.scalars(select(ManualJobRow).where(*WAITING_WHERE)
                                .order_by(ManualJobRow.finished_at.desc(), ManualJobRow.number.desc()).limit(limit)))


def waiting_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(ManualJobRow).where(*WAITING_WHERE)) or 0)


def aware(value: datetime, now: datetime) -> datetime:
    """SQLite gives timestamps back naive in the shop zone."""
    return value.replace(tzinfo=now.tzinfo) if value.tzinfo is None else value


def in_period(session: Session, start_at: datetime, end_at: datetime, equipment_id: UUID | None = None,
              limit: int = 1000) -> list[ManualJobRow]:
    """Jobs that overlap [start_at, end_at): started before its end and not finished before its start (a running job
    started earlier is still in the period)."""
    query = select(ManualJobRow).where(ManualJobRow.started_at < end_at,
                                       (ManualJobRow.finished_at.is_(None)) | (ManualJobRow.finished_at >= start_at))
    if equipment_id is not None:
        query = query.where(ManualJobRow.equipment_id == equipment_id)
    return list(session.scalars(query.order_by(ManualJobRow.started_at.desc(), ManualJobRow.number.desc()).limit(limit)))
