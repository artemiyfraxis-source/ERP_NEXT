"""SQLAlchemy persistence for production resource planning."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, Uuid, and_, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, aliased, mapped_column, sessionmaker, validates

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.shipping.infrastructure import FinishedGoodRow, SqlAlchemyFinishedGoodsRepository
from app.shared.persistence import AuditEventRow, Base, SqlAlchemyAuditRepository

from .application import Absence, DuplicateLaunch, Operator
from .domain import DEFAULT_SHIFT, MARKS_ISSUED_EVENT, MARKS_RELEASED_EVENT, RESTORED_EVENT, EtkOperation, FlowMode, MarkEvent, OperationInput, OperationShare, OperatorSkill, ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError, TaskDelivery, WorkShift
from .plan import (
    KIT_STAGES, PLAN_BLOCKERS, PLAN_KINDS, PLAN_STAGE_AUTO, PROJECT_TERMINALS, PlanEvent, PlanProjectSnapshot,
    blocker_alias,
    normalize_project_number,
    stage_alias,
    terminal_alias,
)
from .genealogy import (
    BoxConsumptionSnapshot,
    BoxSnapshot,
    FinishedReceiptSnapshot,
    MaterialConsumptionSnapshot,
    SerialSnapshot,
    build_genealogy,
)
from .traceability import (
    BoxStatus,
    ConsumptionEdge,
    DefectDecision,
    DefectDisposition,
    DefectRecord,
    DefectStatus,
    FinishedGoodsReceipt,
    MaterialConsumption,
    MaterialInput,
    MaterialSource,
    InstrumentStatus,
    MeasurementInstrument,
    ProductionBox,
    ProductionOutput,
    QcDecision,
    QcInspection,
    SerialStatus,
    SerialUnit,
    WorkInput,
    WorkSession,
    WorkStatus,
    box_code_key,
    box_code_prefix,
    issue_box_number,
    issue_defect_number,
    next_box_code,
    operator_short_code,
)
from .vswr import VswrMeasurement, VswrVerdict
from .worklog import SUMMARY_KEYS, WorkEvent, WorkTimeEntry


class ProductionTaskRow(Base):
    __tablename__ = "production_tasks"
    __table_args__ = (
        Index("ix_production_status_created_id", "status", "created_at", "id"),
        # Цель 2 (миграция 0089): план задания — ограничениями базы, не только кодом.
        CheckConstraint("flow_mode IN ('sequential', 'parallel', 'mixed')", name="ck_production_tasks_flow_mode"),
        CheckConstraint(
            "transfer_batch IS NULL OR (transfer_batch >= 1 AND transfer_batch <= quantity)",
            name="ck_production_tasks_transfer_batch",
        ),
        CheckConstraint(
            "early_quantity IS NULL OR (early_quantity >= 1 AND early_quantity < quantity)",
            name="ck_production_tasks_early_quantity",
        ),
        CheckConstraint("techcard_revision IS NULL OR techcard_revision >= 1", name="ck_production_tasks_techcard_revision"),
        # Миграция 0113: один номер запуска на КБ — два запуска одной КБ из одного состояния не создадут два задания.
        Index("ux_production_tasks_techcard_launch_seq", "techcard_id", "launch_seq", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    source_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    item: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fulfillment_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    launched_by: Mapped[str] = mapped_column(String(255), nullable=False)
    handed_to: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    # План задания (Цель 2): техкарта и ревизия норм (NULL — задание до 0089), режим передачи, передаточная партия
    # (NULL — вся партия), «первые k раньше» (NULL — нет). Без FK на техкарту: задание хранит происхождение и
    # после удаления карты вместе с проектом.
    techcard_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    techcard_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flow_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="mixed", server_default="mixed")
    transfer_batch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    early_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # № проекта для сравнения (`normalize_project_number`, миграция project_keys): трек проекта читает по индексу.
    # Ставится сам при записи `project`; NULL — строка вставлена мимо ORM, такие читаются и сверяются в Python.
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Номер запуска КБ (1, 2, … по техкарте; launch_task); NULL — задание без техкарты.
    launch_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    @validates("project")
    def _keep_project_key(self, _name: str, value: str) -> str:
        self.project_key = normalize_project_number(value)
        return value


class ProductionTaskTeamRow(Base):
    """Команда задания: люди из справочника сотрудников по id, в порядке выбора."""
    __tablename__ = "production_task_team"
    __table_args__ = (
        UniqueConstraint("task_id", "position", name="ux_production_task_team_position"),
        CheckConstraint("position >= 0", name="ck_production_task_team_position"),
    )

    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), primary_key=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductionTaskDeliveryRow(Base):
    """«Сдано N шт» площадки, которой задание отдано целиком (ЖУК). Отмена — отметка, строка остаётся."""
    __tablename__ = "production_task_deliveries"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_production_task_deliveries_quantity"),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by IS NULL) OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_production_task_deliveries_cancel",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ProductionOperationRow(Base):
    __tablename__ = "production_operations"
    __table_args__ = (
        Index("ux_production_operation_position", "task_id", "position", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    seconds_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    setup_seconds: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    norm_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_finish: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Блок схемы полуфабрикатов ЭТК (ARCH-TRACK-2); NULL — не известен (задания до схемы).
    stage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Задание по ПФ (0120): операции ЭТК полуфабриката [{position, code, stage}] в порядке ПФ; NULL — операция
    # задания и есть одна операция ЭТК (запуск без схемы, задания до 0120).
    etk_ops: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    # Версии карточек операций ЭТК на первом «Начать» (0126): [{position, source, changed_at}]; NULL — ещё не начинали.
    # Пишет только SqlAlchemyWorkRepository.record_card_versions (условно, один раз); add() задания её не трогает.
    # deferred: чтение задания её не выбирает (читается отдельным запросом card_versions).
    card_versions: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True, deferred=True)


def _etk_ops_json(operation: ProductionOperation) -> list[dict] | None:
    return [{"position": item.position, "code": item.code, "stage": item.stage} for item in operation.etk_ops] or None


def _etk_ops_domain(value: object) -> tuple[EtkOperation, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        EtkOperation(int(item["position"]), str(item.get("code") or ""), str(item.get("stage") or ""))
        for item in value if isinstance(item, dict) and item.get("position")
    )


class ProductionOperationInputRow(Base):
    """Ребро сетевого графика задания: операция берёт полуфабрикат операции input_operation_id."""
    __tablename__ = "production_operation_inputs"
    __table_args__ = (UniqueConstraint("operation_id", "input_operation_id", name="ux_production_operation_inputs_edge"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="CASCADE"), nullable=False)
    input_operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="CASCADE"), nullable=False, index=True)
    per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, default=Decimal(1), server_default="1")
    # Передаточная партия ребра; NULL — партия задания (решение 4).
    transfer_batch: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)


class ProductionOperationOperatorRow(Base):
    __tablename__ = "production_operation_operators"

    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="CASCADE"), primary_key=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Доля монтажника: штуки (NULL — вся операция бригадой), своё плановое окно и факт «взял»/«закончил».
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_finish: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductionHolidayRow(Base):
    __tablename__ = "production_holidays"
    day: Mapped[date] = mapped_column(Date(), primary_key=True)


class ProductionAbsenceRow(Base):
    __tablename__ = "production_absences"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="CASCADE"), nullable=False, index=True)
    from_date: Mapped[date] = mapped_column(Date(), nullable=False)
    to_date: Mapped[date] = mapped_column(Date(), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # A registered leave request (0089, identity/leave.py → workflows/leave_calendar.py); NULL — the master's own row.
    # The master's calendar save never touches these.
    leave_request_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_leave_requests.id", ondelete="CASCADE", name="fk_production_absences_leave_request_id"),
        nullable=True,
    )

    __table_args__ = (Index("ux_production_absences_leave_request_id", "leave_request_id", unique=True),)


class ProductionShiftRow(Base):
    __tablename__ = "production_shift"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    weekdays: Mapped[str] = mapped_column(String(32), nullable=False)
    start_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    finish_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    breaks_json: Mapped[str] = mapped_column(Text(), nullable=False)
    effective_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductionOperatorSkillRow(Base):
    __tablename__ = "production_operator_skills"
    __table_args__ = (
        Index("ix_production_operator_skills_code_allowed", "operation_code", "allowed"),
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="CASCADE"), primary_key=True,
    )
    operation_code: Mapped[str] = mapped_column(String(255), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)


class ProductionSequenceRow(Base):
    __tablename__ = "production_sequences"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductionOperatorCodeRow(Base):
    """Короткий код сотрудника в коде коробки («Б07», 0119): выдаётся при первой сдаче и больше не меняется,
    UNIQUE среди сотрудников."""
    __tablename__ = "production_operator_codes"
    __table_args__ = (
        UniqueConstraint("code", name="uq_production_operator_codes_code"),
        CheckConstraint("code <> ''", name="ck_production_operator_codes_code"),
    )
    operator_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="CASCADE"), primary_key=True,
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False)


class ProductionPlanProjectRow(Base):
    __tablename__ = "production_plan_projects"

    project: Mapped[str] = mapped_column(String(255), primary_key=True)
    customer: Mapped[str] = mapped_column(String(512), nullable=False)
    opened_on: Mapped[date | None] = mapped_column(Date(), nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    terminal: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(chr(39) + value.replace(chr(39), chr(39) * 2) + chr(39) for value in values)})"


# Migration 0101_production_stage_history added these checks (and a PostgreSQL/SQLite trigger refusing UPDATE:
# the journal only grows; DELETE stays for a deleted project); 0110_stage_warehouse_accepted re-points them at the
# 8-stage ladder and the one closed-project word «Отменён» (old rows stay as written — NOT VALID — and read as the
# new words via `stage_alias` / `terminal_alias`); 0161_ladder_markers_blockers re-points the stage checks at the
# short ladder (owner 29.10) and adds the blocker check (СТОП · ОЖИД ИНФ · ОЖИД КОМПЛ; old «Этап КБ» marker words and
# ХОЛД / ПАУЗА stay as written and read via `summarize_kit_marks` / `blocker_alias`).
# The value column comes first in each check: the head-1 seeder (tests/migrations/seed_any_revision.py) fills a
# checked column with the first literal.
PLAN_EVENT_CHECKS = {
    "ck_production_plan_events_kind": _sql_in("kind", tuple(PLAN_KINDS.values())),
    "ck_production_plan_events_stage_value": (
        f"{_sql_in('value', (*KIT_STAGES, PLAN_STAGE_AUTO))} OR kind <> '{PLAN_KINDS['stage']}'"
    ),
    "ck_production_plan_events_status_value": (
        f"{_sql_in('value', (*PROJECT_TERMINALS, PLAN_STAGE_AUTO))} OR kind <> '{PLAN_KINDS['status']}'"
    ),
    "ck_production_plan_events_from_value": (
        f"from_value = '' OR (kind = '{PLAN_KINDS['stage']}' AND {_sql_in('from_value', KIT_STAGES)})"
        f" OR (kind = '{PLAN_KINDS['status']}' AND {_sql_in('from_value', PROJECT_TERMINALS)})"
    ),
    "ck_production_plan_events_blocker": f"{_sql_in('blocker', PLAN_BLOCKERS)} OR kind <> '{PLAN_KINDS['block']}'",
}


class ProductionPlanEventRow(Base):
    __tablename__ = "production_plan_events"
    __table_args__ = (
        Index("ix_production_plan_events_project_time", "project", "occurred_at", "id"),
        Index("ux_production_plan_events_project_sequence", "project", "sequence", unique=True),
        *(CheckConstraint(sql, name=name) for name, sql in PLAN_EVENT_CHECKS.items()),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    who: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    blocker: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    value: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    field: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    from_value: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    reason: Mapped[str] = mapped_column(Text(), nullable=False, default="", server_default="")


class ProductionBoxRow(Base):
    __tablename__ = "production_boxes"
    __table_args__ = (
        Index("ix_production_boxes_task_status", "task_id", "status"),
        Index(
            "ux_production_open_box_operator", "task_id", "operation_id", "operator_id",
            unique=True, sqlite_where=text("status = 'Открыта'"),
            postgresql_where=text("status = 'Открыта'"),
        ),
        Index("ux_production_boxes_code", "code", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    item: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_position: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    consumed: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    return_reason: Mapped[str] = mapped_column(Text(), nullable=False)
    rejected: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False, server_default="0")
    # Код для подписи маркером (0119): проект·КБ·дата·код сотрудника·номер, UNIQUE; NULL — коробка до 0119.
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ProductionWorkSessionRow(Base):
    __tablename__ = "production_work_sessions"
    __table_args__ = (
        Index(
            "ux_production_active_work_operator", "operator_id", unique=True,
            sqlite_where=text("status IN ('В работе', 'Пауза')"),
            postgresql_where=text("status IN ('В работе', 'Пауза')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    pause_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductionWorkInputRow(Base):
    __tablename__ = "production_work_inputs"
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_work_sessions.id", ondelete="CASCADE"), primary_key=True)
    box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ProductionWorkMaterialInputRow(Base):
    __tablename__ = "production_work_material_inputs"
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_work_sessions.id", ondelete="CASCADE"), primary_key=True)
    fulfillment_line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("fulfillment_lines.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)


class ProductionOutputRow(Base):
    __tablename__ = "production_outputs"
    # An output is replaced at most once: a correction always targets the head of its chain.
    __table_args__ = (Index("ux_production_outputs_correction_of_id", "correction_of_id", unique=True),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), nullable=True, unique=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    good_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    defect_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    output_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, index=True)
    correction_of_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_outputs.id", ondelete="RESTRICT"), nullable=True)


WORK_TIME_EVENTS = tuple(item.value for item in WorkEvent)


class ProductionWorkTimeLogRow(Base):
    """Журнал рабочего времени (миграция 0105): строка не меняется после записи (триггер в миграции)."""
    __tablename__ = "production_work_time_log"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_production_work_time_log_idempotency_key"),
        UniqueConstraint("session_id", "sequence", name="uq_production_work_time_log_session_sequence"),
        CheckConstraint(f"event IN ({', '.join(repr(item) for item in WORK_TIME_EVENTS)})", name="ck_production_work_time_log_event"),
        CheckConstraint("sequence >= 0", name="ck_production_work_time_log_sequence"),
        CheckConstraint("seconds_per_unit >= 0", name="ck_production_work_time_log_unit"),
        CheckConstraint("setup_seconds >= 0", name="ck_production_work_time_log_setup"),
        CheckConstraint("planned_norm_seconds >= 0", name="ck_production_work_time_log_planned"),
        CheckConstraint("good_quantity IS NULL OR good_quantity >= 0", name="ck_production_work_time_log_good"),
        CheckConstraint("defect_quantity IS NULL OR defect_quantity >= 0", name="ck_production_work_time_log_defect"),
        CheckConstraint("running_seconds IS NULL OR running_seconds >= 0", name="ck_production_work_time_log_running"),
        CheckConstraint("earned_norm_seconds IS NULL OR earned_norm_seconds >= 0", name="ck_production_work_time_log_earned"),
        CheckConstraint(
            "event <> 'finish' OR (good_quantity IS NOT NULL AND defect_quantity IS NOT NULL"
            " AND running_seconds IS NOT NULL AND earned_norm_seconds IS NOT NULL)",
            name="ck_production_work_time_log_finish",
        ),
        Index("ix_production_work_time_log_occurred", "event", "occurred_at"),
        Index("ix_production_work_time_log_operator", "operator_id", "operation_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item: Mapped[str] = mapped_column(String(255), nullable=False)
    task_number: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_code: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    seconds_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    setup_seconds: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    planned_norm_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    good_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    defect_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    running_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    earned_norm_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ProductionConsumptionRow(Base):
    __tablename__ = "production_consumption"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, index=True)
    target_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    output_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_outputs.id", ondelete="RESTRICT"), nullable=False, index=True)


class ProductionMaterialConsumptionRow(Base):
    __tablename__ = "production_material_consumption"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    fulfillment_line_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("fulfillment_lines.id", ondelete="RESTRICT"), nullable=False, index=True)
    target_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    output_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_outputs.id", ondelete="RESTRICT"), nullable=False, index=True)
    grn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    package: Mapped[str] = mapped_column(String(32), nullable=False)


class ProductionFinishedGoodsReceiptRow(Base):
    __tablename__ = "production_finished_goods_receipts"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, unique=True)
    finished_good_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("finished_goods.id", ondelete="RESTRICT"), nullable=False, index=True)
    grn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductionDefectRow(Base):
    __tablename__ = "production_defects"
    __table_args__ = (Index("ux_production_defects_replaced_by_output_id", "replaced_by_output_id", unique=True),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    output_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_outputs.id", ondelete="RESTRICT"), nullable=False, unique=True)
    source_box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    operator_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    character: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    replaced_by_output_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_outputs.id", ondelete="RESTRICT"), nullable=True)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ProductionDefectDecisionRow(Base):
    __tablename__ = "production_defect_decisions"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    defect_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_defects.id", ondelete="RESTRICT"), nullable=False, unique=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    act_reference: Mapped[str] = mapped_column(Text(), nullable=False)


class ProductionSerialRow(Base):
    __tablename__ = "production_serials"
    __table_args__ = (Index("ux_production_serial_task_position", "task_id", "position", unique=True),)
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_box_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=True, index=True)
    finished_goods_grn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class ProductionInstrumentRow(Base):
    __tablename__ = "production_instruments"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer(), nullable=False)
    inventory_number: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(255), nullable=False)
    verification_number: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_due: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ProductionQcInspectionRow(Base):
    __tablename__ = "production_qc_inspections"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    box_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_boxes.id", ondelete="RESTRICT"), nullable=False, unique=True)
    inspector_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted: Mapped[int] = mapped_column(Integer(), nullable=False)
    rejected: Mapped[int] = mapped_column(Integer(), nullable=False)
    defect_number: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
    defect_character: Mapped[str] = mapped_column(String(255), nullable=False)
    defect_description: Mapped[str] = mapped_column(Text(), nullable=False)


class ProductionQcInstrumentRow(Base):
    __tablename__ = "production_qc_instruments"
    inspection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_qc_inspections.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True)


class ProductionVswrMeasurementRow(Base):
    """Замер КСВН изделия (0142): каждый замер хранится, действует последняя попытка по серийному номеру."""
    __tablename__ = "production_vswr_measurements"
    __table_args__ = (
        UniqueConstraint("serial_id", "attempt", name="uq_production_vswr_serial_attempt"),
        CheckConstraint("attempt >= 1", name="ck_production_vswr_attempt"),
        CheckConstraint("band_from_mhz > 0 AND band_from_mhz < band_to_mhz", name="ck_production_vswr_band"),
        CheckConstraint("max_vswr >= 1", name="ck_production_vswr_max"),
        CheckConstraint(
            "max_vswr_mhz >= band_from_mhz AND max_vswr_mhz <= band_to_mhz", name="ck_production_vswr_peak_in_band",
        ),
        CheckConstraint("norm_vswr_max IS NULL OR norm_vswr_max >= 1", name="ck_production_vswr_norm"),
        CheckConstraint("verdict IN ('pass', 'fail')", name="ck_production_vswr_verdict"),
        CheckConstraint(
            "verdict_manual OR (norm_vswr_max IS NOT NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
            " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)))",
            name="ck_production_vswr_auto_verdict",
        ),
        CheckConstraint("NOT verdict_manual OR length(trim(verdict_reason)) > 0", name="ck_production_vswr_manual_reason"),
        CheckConstraint("length(trim(file_name)) > 0", name="ck_production_vswr_file_name"),
        CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_production_vswr_file_size"),
        CheckConstraint("points >= 1", name="ck_production_vswr_points"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    serial_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_serials.id", ondelete="RESTRICT"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer(), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False, index=True)
    band_from_mhz: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    band_to_mhz: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    max_vswr: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    max_vswr_mhz: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    norm: Mapped[dict] = mapped_column(JSON, nullable=False)
    norm_vswr_max: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)
    verdict_manual: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    verdict_reason: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    inspector_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False, index=True)
    inspector_name: Mapped[str] = mapped_column(String(255), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False, deferred=True)
    points: Mapped[int] = mapped_column(Integer(), nullable=False)
    file_meta: Mapped[dict] = mapped_column(JSON, nullable=False)
    trace: Mapped[list] = mapped_column(JSON, nullable=False)


class ProductionQcDecisionRow(Base):
    __tablename__ = "production_qc_decisions"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    inspection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_qc_inspections.id", ondelete="RESTRICT"), nullable=False, unique=True)
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    act_reference: Mapped[str] = mapped_column(Text(), nullable=False)


class SqlAlchemyProductionRepository:
    def __init__(self, session: Session):
        self.session = session

    def _domain(self, row: ProductionTaskRow) -> ProductionTask:
        operation_rows = list(self.session.scalars(
            select(ProductionOperationRow).where(ProductionOperationRow.task_id == row.id).order_by(ProductionOperationRow.position)
        ))
        # Колонки, а не строки ORM: add() удаляет и заново вставляет доли с теми же ключами.
        share_row = ProductionOperationOperatorRow
        operators = list(self.session.execute(
            select(
                share_row.operation_id, share_row.operator_id, share_row.quantity, share_row.planned_start,
                share_row.planned_finish, share_row.started_at, share_row.finished_at,
            )
            .join(ProductionOperationRow, ProductionOperationRow.id == ProductionOperationOperatorRow.operation_id)
            .where(ProductionOperationRow.task_id == row.id)
            .order_by(ProductionOperationOperatorRow.operation_id, ProductionOperationOperatorRow.position)
        ))
        by_operation: dict[UUID, list[OperationShare]] = {}
        for share in operators:
            by_operation.setdefault(share.operation_id, []).append(OperationShare(
                share.operator_id, share.quantity, share.planned_start, share.planned_finish,
                share.started_at, share.finished_at,
            ))
        # Входы — тоже колонками (add() переписывает рёбра), по возрастанию номера операции-входа.
        edge, consumer, source = ProductionOperationInputRow, ProductionOperationRow, aliased(ProductionOperationRow)
        inputs: dict[UUID, list[OperationInput]] = {}
        for item in self.session.execute(
            select(edge.operation_id, edge.input_operation_id, edge.per_unit, edge.transfer_batch)
            .join(consumer, consumer.id == edge.operation_id)
            .join(source, source.id == edge.input_operation_id)
            .where(consumer.task_id == row.id)
            .order_by(edge.operation_id, source.position)
        ):
            inputs.setdefault(item.operation_id, []).append(
                OperationInput(item.input_operation_id, item.per_unit, item.transfer_batch)
            )
        team = tuple(self.session.scalars(
            select(ProductionTaskTeamRow.operator_id).where(ProductionTaskTeamRow.task_id == row.id)
            .order_by(ProductionTaskTeamRow.position)
        ))
        return ProductionTask(
            id=row.id, number=row.number, source_idempotency_key=row.source_idempotency_key,
            source_fingerprint=row.source_fingerprint, version=row.version, created_at=row.created_at,
            project=row.project, item=row.item, name=row.name, quantity=row.quantity,
            fulfillment_request_id=row.fulfillment_request_id, deadline=row.deadline,
            status=ProductionStatus(row.status), launched_by=row.launched_by, handed_to=row.handed_to or "",
            operations=tuple(ProductionOperation(
                id=operation.id, position=operation.position, code=operation.code,
                stage=operation.stage, description=operation.description,
                seconds_per_unit=operation.seconds_per_unit, setup_seconds=operation.setup_seconds,
                norm_seconds=operation.norm_seconds,
                assigned_by=operation.assigned_by,
                shares=tuple(by_operation.get(operation.id, ())),
                stage_key=operation.stage_key,
                inputs=tuple(inputs.get(operation.id, ())),
                etk_ops=_etk_ops_domain(operation.etk_ops),
            ) for operation in operation_rows),
            techcard_id=row.techcard_id, techcard_revision=row.techcard_revision,
            flow_mode=FlowMode(row.flow_mode or FlowMode.MIXED.value),
            transfer_batch=row.transfer_batch, early_quantity=row.early_quantity, team=team,
        )

    def get(self, task_id: UUID) -> ProductionTask | None:
        row = self.session.get(ProductionTaskRow, task_id)
        return self._domain(row) if row is not None else None

    def by_idempotency_key(self, key: str) -> ProductionTask | None:
        row = self.session.scalar(select(ProductionTaskRow).where(ProductionTaskRow.source_idempotency_key == key))
        return self._domain(row) if row is not None else None

    def list(self, limit: int = 200, after_id: UUID | None = None) -> list[ProductionTask]:
        statement = select(ProductionTaskRow)
        if after_id is not None:
            cursor = self.session.get(ProductionTaskRow, after_id)
            if cursor is None:
                return []
            statement = statement.where(or_(
                ProductionTaskRow.created_at < cursor.created_at,
                and_(ProductionTaskRow.created_at == cursor.created_at, ProductionTaskRow.id < cursor.id),
            ))
        rows = self.session.scalars(
            statement.order_by(ProductionTaskRow.created_at.desc(), ProductionTaskRow.id.desc()).limit(limit)
        )
        return [self._domain(row) for row in rows]

    def for_project(self, project: str) -> list[ProductionTask]:
        """Все задания одного проекта (точно по `normalize_project_number`, по индексу ключа), старые первыми."""
        key = normalize_project_number(project)
        if not key:
            return []
        rows = self.session.scalars(
            select(ProductionTaskRow)
            .where(or_(ProductionTaskRow.project_key == key, ProductionTaskRow.project_key.is_(None)))
            .order_by(ProductionTaskRow.created_at, ProductionTaskRow.id)
        )
        return [self._domain(row) for row in rows if normalize_project_number(row.project) == key]

    def with_status(self, status: ProductionStatus) -> list[ProductionTask]:
        """Все задания в статусе (по индексу статуса), старые первыми — без общего лимита ленты."""
        rows = self.session.scalars(
            select(ProductionTaskRow).where(ProductionTaskRow.status == status.value)
            .order_by(ProductionTaskRow.created_at, ProductionTaskRow.id)
        )
        return [self._domain(row) for row in rows]

    def for_operator(self, operator_id: UUID, limit: int = 300) -> list[ProductionTask]:
        """Задания, где оператор назначен хотя бы на одну операцию (экран «Мои задания»), свежие первыми."""
        ids = select(ProductionOperationRow.task_id).join(
            ProductionOperationOperatorRow, ProductionOperationOperatorRow.operation_id == ProductionOperationRow.id,
        ).where(ProductionOperationOperatorRow.operator_id == operator_id)
        rows = self.session.scalars(select(ProductionTaskRow).where(ProductionTaskRow.id.in_(ids)).order_by(
            ProductionTaskRow.created_at.desc(), ProductionTaskRow.id.desc(),
        ).limit(limit))
        return [self._domain(row) for row in rows]

    def planned(self, since: datetime) -> list[ProductionTask]:
        """Живые задания, где у кого-то есть окно, кончающееся не раньше since — только их двигает план по факту."""
        ids = select(ProductionOperationRow.task_id).join(
            ProductionOperationOperatorRow, ProductionOperationOperatorRow.operation_id == ProductionOperationRow.id,
        ).where(ProductionOperationOperatorRow.planned_finish >= since)
        rows = self.session.scalars(select(ProductionTaskRow).where(
            ProductionTaskRow.id.in_(ids),
            ProductionTaskRow.status == ProductionStatus.RUNNING.value,
            ProductionTaskRow.handed_to == "",
        ).order_by(ProductionTaskRow.created_at, ProductionTaskRow.id))
        return [self._domain(row) for row in rows]

    def lock(self, task_id: UUID) -> None:
        """Строка задания под замок до конца транзакции и свежие значения: факты двух монтажников по одному
        заданию идут по очереди (PostgreSQL; SQLite и так пишет по одному)."""
        self.session.scalar(
            select(ProductionTaskRow).where(ProductionTaskRow.id == task_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        self.session.scalars(
            select(ProductionOperationRow).where(ProductionOperationRow.task_id == task_id)
            .execution_options(populate_existing=True)
        ).all()

    def reserve_number(self) -> str:
        row = self.session.scalar(select(ProductionSequenceRow).where(ProductionSequenceRow.name == "task").with_for_update())
        if row is None:
            row = ProductionSequenceRow(name="task", value=0)
            self.session.add(row)
            self.session.flush()
        row.value += 1
        return f"ПЗ-{row.value:04d}"

    def last_launch_seqs(self, techcard_ids: Sequence[UUID]) -> dict[UUID, int]:
        """Последний номер запуска каждой из техкарт (нет заданий — техкарты нет в ответе)."""
        if not techcard_ids:
            return {}
        return {
            techcard_id: int(seq) for techcard_id, seq in self.session.execute(
                select(ProductionTaskRow.techcard_id, func.max(ProductionTaskRow.launch_seq))
                .where(ProductionTaskRow.techcard_id.in_(set(techcard_ids)))
                .group_by(ProductionTaskRow.techcard_id)
            ) if seq is not None
        }

    def add(self, task: ProductionTask, launch_seq: int | None = None) -> None:
        row = self.session.get(ProductionTaskRow, task.id)
        if row is None:
            # Номер запуска пишется только при создании задания; потом он не меняется.
            row = ProductionTaskRow(id=task.id, launch_seq=launch_seq)
            self.session.add(row)
        row.number = task.number
        row.source_idempotency_key = task.source_idempotency_key
        row.source_fingerprint = task.source_fingerprint
        row.version = task.version
        row.created_at = task.created_at
        row.project = task.project
        row.item = task.item
        row.name = task.name
        row.quantity = task.quantity
        row.fulfillment_request_id = task.fulfillment_request_id
        row.deadline = task.deadline
        row.status = task.status.value
        row.launched_by = task.launched_by
        row.handed_to = task.handed_to
        row.techcard_id = task.techcard_id
        row.techcard_revision = task.techcard_revision
        row.flow_mode = task.flow_mode.value
        row.transfer_batch = task.transfer_batch
        row.early_quantity = task.early_quantity
        try:
            self.session.flush()
        except IntegrityError as error:
            # Тот же номер запуска этой КБ уже занят другим запуском из того же состояния.
            if launch_seq is not None and "launch_seq" in str(error.orig):
                raise DuplicateLaunch() from error
            raise
        stored_team = tuple(self.session.scalars(
            select(ProductionTaskTeamRow.operator_id).where(ProductionTaskTeamRow.task_id == task.id)
            .order_by(ProductionTaskTeamRow.position)
        ))
        if stored_team != task.team:
            self.session.execute(delete(ProductionTaskTeamRow).where(ProductionTaskTeamRow.task_id == task.id))
            self.session.flush()
            self.session.add_all(
                ProductionTaskTeamRow(task_id=task.id, operator_id=operator_id, position=position)
                for position, operator_id in enumerate(task.team)
            )
        existing = {item.id: item for item in self.session.scalars(select(ProductionOperationRow).where(ProductionOperationRow.task_id == task.id))}
        # Рёбра графа переписываются целиком, и только когда граф изменился (назначения его не трогают).
        edge = ProductionOperationInputRow
        owners = edge.operation_id.in_([*existing, *(operation.id for operation in task.operations)])
        stored_edges = {
            (item.operation_id, item.input_operation_id): (item.per_unit, item.transfer_batch)
            for item in self.session.execute(
                select(edge.operation_id, edge.input_operation_id, edge.per_unit, edge.transfer_batch).where(owners)
            )
        }
        wanted_edges = {
            (operation.id, item.input_operation_id): (item.per_unit, item.transfer_batch)
            for operation in task.operations for item in operation.inputs
        }
        rewrite_edges = stored_edges != wanted_edges
        if rewrite_edges:
            self.session.execute(delete(edge).where(owners))
        for operation in task.operations:
            stored = existing.pop(operation.id, None)
            if stored is None:
                stored = ProductionOperationRow(id=operation.id, task_id=task.id)
                self.session.add(stored)
            stored.position = operation.position
            stored.code = operation.code
            stored.stage = operation.stage
            stored.description = operation.description
            stored.seconds_per_unit = operation.seconds_per_unit
            stored.setup_seconds = operation.setup_seconds
            stored.norm_seconds = operation.norm_seconds
            stored.planned_start = operation.planned_start
            stored.planned_finish = operation.planned_finish
            stored.assigned_by = operation.assigned_by
            stored.stage_key = operation.stage_key
            if stored.etk_ops != _etk_ops_json(operation):
                stored.etk_ops = _etk_ops_json(operation)
            for item in operation.inputs if rewrite_edges else ():
                self.session.add(ProductionOperationInputRow(
                    id=uuid4(), operation_id=operation.id, input_operation_id=item.input_operation_id,
                    per_unit=item.per_unit, transfer_batch=item.transfer_batch,
                ))
            self.session.execute(delete(ProductionOperationOperatorRow).where(ProductionOperationOperatorRow.operation_id == operation.id))
            for position, share in enumerate(operation.shares):
                self.session.add(ProductionOperationOperatorRow(
                    operation_id=operation.id, operator_id=share.operator_id, position=position,
                    quantity=share.quantity, planned_start=share.planned_start, planned_finish=share.planned_finish,
                    started_at=share.started_at, finished_at=share.finished_at,
                ))
        for stale in existing.values():
            self.session.delete(stale)

    def worked(self, task_ids) -> set[UUID]:
        """Задания со следом работы: короб, сессия, выработка, брак или «взял/закончил» в доле монтажника."""
        ids = list(task_ids)
        if not ids:
            return set()
        found: set[UUID] = set()
        for column in (ProductionBoxRow.task_id, ProductionWorkSessionRow.task_id,
                       ProductionOutputRow.task_id, ProductionDefectRow.task_id, ProductionTaskDeliveryRow.task_id):
            found.update(self.session.scalars(select(column).where(column.in_(ids)).distinct()))
        share = ProductionOperationOperatorRow
        found.update(self.session.scalars(
            select(ProductionOperationRow.task_id)
            .join(share, share.operation_id == ProductionOperationRow.id)
            .where(ProductionOperationRow.task_id.in_(ids), or_(share.started_at.is_not(None), share.finished_at.is_not(None)))
            .distinct()
        ))
        return found

    def marks_issued(self, task_ids) -> set[UUID]:
        """Задания, номера меток которых выданы на экран ЭТК (MARKS_ISSUED_EVENT): надписи могли напечатать."""
        ids = list(task_ids)
        if not ids:
            return set()
        return set(self.session.scalars(
            select(AuditEventRow.aggregate_id)
            .where(AuditEventRow.aggregate_id.in_(ids), AuditEventRow.event_type == MARKS_ISSUED_EVENT)
            .distinct()
        ))

    def mark_events(self, task_ids) -> list[MarkEvent]:
        """Освобождения номеров меток (отменено без работы) и возвраты в работу заданий — для mark_serial_range."""
        ids = list(task_ids)
        if not ids:
            return []
        rows = self.session.execute(
            select(AuditEventRow.aggregate_id, AuditEventRow.event_type, AuditEventRow.occurred_at, AuditEventRow.details)
            .where(AuditEventRow.aggregate_id.in_(ids),
                   AuditEventRow.event_type.in_((MARKS_RELEASED_EVENT, RESTORED_EVENT)))
        )
        return [
            MarkEvent(task_id, at, kind == MARKS_RELEASED_EVENT, str((details or {}).get("after") or ""))
            for task_id, kind, at, details in rows
        ]

    def delete(self, task_id: UUID) -> None:
        """Задание без следа работы (см. `worked`) уходит с долями, операциями и своими серийными номерами."""
        operations = select(ProductionOperationRow.id).where(ProductionOperationRow.task_id == task_id)
        self.session.execute(delete(ProductionOperationInputRow).where(ProductionOperationInputRow.operation_id.in_(operations)))
        self.session.execute(delete(ProductionOperationOperatorRow).where(ProductionOperationOperatorRow.operation_id.in_(operations)))
        self.session.execute(delete(ProductionSerialRow).where(ProductionSerialRow.task_id == task_id))
        self.session.execute(delete(ProductionTaskTeamRow).where(ProductionTaskTeamRow.task_id == task_id))
        self.session.execute(delete(ProductionOperationRow).where(ProductionOperationRow.task_id == task_id))
        self.session.execute(delete(ProductionTaskRow).where(ProductionTaskRow.id == task_id))


class SqlAlchemyDeliveryRepository:
    """«Сдано N шт» площадки (ЖУК) по заданиям."""

    def __init__(self, session: Session): self.session = session

    @staticmethod
    def _domain(row: ProductionTaskDeliveryRow) -> TaskDelivery:
        return TaskDelivery(
            id=row.id, task_id=row.task_id, site=row.site, quantity=row.quantity,
            delivered_at=row.delivered_at, delivered_by=row.delivered_by,
            cancelled_at=row.cancelled_at, cancelled_by=row.cancelled_by,
        )

    def for_task(self, task_id: UUID) -> list[TaskDelivery]:
        rows = self.session.scalars(
            select(ProductionTaskDeliveryRow).where(ProductionTaskDeliveryRow.task_id == task_id)
            .order_by(ProductionTaskDeliveryRow.delivered_at, ProductionTaskDeliveryRow.id)
        )
        return [self._domain(row) for row in rows]

    def totals(self, task_ids) -> dict[UUID, int]:
        """Сдано (без отменённых) по заданиям — одним запросом для списка заданий."""
        ids = list(task_ids)
        if not ids:
            return {}
        rows = self.session.execute(
            select(ProductionTaskDeliveryRow.task_id, func.sum(ProductionTaskDeliveryRow.quantity))
            .where(ProductionTaskDeliveryRow.task_id.in_(ids), ProductionTaskDeliveryRow.cancelled_at.is_(None))
            .group_by(ProductionTaskDeliveryRow.task_id)
        )
        return {task_id: int(total or 0) for task_id, total in rows}

    def save(self, delivery: TaskDelivery) -> None:
        row = self.session.get(ProductionTaskDeliveryRow, delivery.id)
        if row is None:
            row = ProductionTaskDeliveryRow(id=delivery.id)
            self.session.add(row)
        row.task_id = delivery.task_id
        row.site = delivery.site
        row.quantity = delivery.quantity
        row.delivered_at = delivery.delivered_at
        row.delivered_by = delivery.delivered_by
        row.cancelled_at = delivery.cancelled_at
        row.cancelled_by = delivery.cancelled_by


class SqlAlchemyOperatorRepository:
    def __init__(self, session: Session): self.session = session

    @staticmethod
    def _domain(row: IdentityUserRow) -> Operator:
        return Operator(
            row.id, row.name, row.site or "", row.active, row.department or "",
            shop_worker=bool(row.shop_worker), site_master=bool(row.site_master),
        )

    def get(self, operator_id: UUID) -> Operator | None:
        row = self.session.get(IdentityUserRow, operator_id)
        return self._domain(row) if row is not None else None

    def list(self) -> list[Operator]:
        rows = self.session.scalars(select(IdentityUserRow).where(IdentityUserRow.active.is_(True)).order_by(IdentityUserRow.name))
        return [self._domain(row) for row in rows]


class SqlAlchemyCalendarRepository:
    def __init__(self, session: Session): self.session = session

    def holidays(self) -> frozenset[date]:
        return frozenset(self.session.scalars(select(ProductionHolidayRow.day)))

    def absences(self) -> list[Absence]:
        rows = self.session.scalars(select(ProductionAbsenceRow).order_by(ProductionAbsenceRow.from_date, ProductionAbsenceRow.id))
        return [
            Absence(row.id, row.operator_id, row.from_date, row.to_date, row.kind, row.leave_request_id) for row in rows
        ]

    def shift(self) -> WorkShift:
        row = self.session.get(ProductionShiftRow, 1)
        if row is None:
            return DEFAULT_SHIFT
        def clock(minutes: int) -> time:
            return time(minutes // 60, minutes % 60)
        return WorkShift(
            weekdays=tuple(int(value) for value in row.weekdays.split(",") if value != ""),
            start=clock(row.start_minutes), finish=clock(row.finish_minutes),
            breaks=tuple((clock(start), clock(finish)) for start, finish in json.loads(row.breaks_json)),
            effective_seconds=row.effective_seconds,
        )

    def replace_leave_absences(self, absences: tuple[Absence, ...]) -> int:
        """The absences of registered leave requests become exactly `absences` (one row per request); the master's rows
        are untouched. Returns how many rows were added, changed or removed."""
        wanted = {item.leave_request_id: item for item in absences if item.leave_request_id is not None}
        rows = {
            row.leave_request_id: row for row in self.session.scalars(
                select(ProductionAbsenceRow).where(ProductionAbsenceRow.leave_request_id.is_not(None))
            )
        }
        changed = 0
        for request_id, row in rows.items():
            item = wanted.get(request_id)
            if item is None:
                self.session.delete(row)
                changed += 1
            elif (row.operator_id, row.from_date, row.to_date, row.kind) != (
                item.operator_id, item.from_date, item.to_date, item.kind,
            ):
                row.operator_id, row.from_date, row.to_date, row.kind = (
                    item.operator_id, item.from_date, item.to_date, item.kind,
                )
                changed += 1
        for request_id, item in wanted.items():
            if request_id not in rows:
                self.session.add(ProductionAbsenceRow(
                    id=item.id, operator_id=item.operator_id, from_date=item.from_date, to_date=item.to_date,
                    kind=item.kind, leave_request_id=request_id,
                ))
                changed += 1
        self.session.flush()
        return changed

    def replace(self, holidays: frozenset[date], absences: tuple[Absence, ...], shift: WorkShift) -> None:
        self.session.execute(delete(ProductionHolidayRow))
        # The master's rows only: the absences of registered leave requests stay as they are.
        self.session.execute(delete(ProductionAbsenceRow).where(ProductionAbsenceRow.leave_request_id.is_(None)))
        self.session.add_all(ProductionHolidayRow(day=day) for day in sorted(holidays))
        self.session.add_all(ProductionAbsenceRow(
            id=item.id, operator_id=item.operator_id, from_date=item.from_date,
            to_date=item.to_date, kind=item.kind,
        ) for item in absences if item.leave_request_id is None)
        def minutes(value: time) -> int:
            return value.hour * 60 + value.minute
        row = self.session.get(ProductionShiftRow, 1)
        values = {
            "weekdays": ",".join(str(day) for day in shift.weekdays),
            "start_minutes": minutes(shift.start), "finish_minutes": minutes(shift.finish),
            "breaks_json": json.dumps([
                [minutes(start), minutes(finish)] for start, finish in shift.breaks
            ], separators=(",", ":")),
            "effective_seconds": shift.effective_seconds,
        }
        if row is None:
            self.session.add(ProductionShiftRow(id=1, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)


class SqlAlchemySkillRepository:
    def __init__(self, session: Session): self.session = session

    def list(self) -> list[OperatorSkill]:
        rows = self.session.scalars(select(ProductionOperatorSkillRow).order_by(
            ProductionOperatorSkillRow.operation_code, ProductionOperatorSkillRow.operator_id,
        ))
        return [OperatorSkill(
            row.operator_id, row.operation_code, row.allowed, row.stars, row.coefficient,
        ) for row in rows]

    def replace_for_operator(self, operator_id: UUID, skills: tuple[OperatorSkill, ...]) -> None:
        self.session.execute(delete(ProductionOperatorSkillRow).where(
            ProductionOperatorSkillRow.operator_id == operator_id,
        ))
        self.session.add_all(ProductionOperatorSkillRow(
            operator_id=item.operator_id, operation_code=item.operation_code,
            allowed=item.allowed, stars=item.stars, coefficient=item.coefficient,
        ) for item in skills)


class SqlAlchemyPlanProjectRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[PlanProjectSnapshot]:
        rows = self.session.scalars(select(ProductionPlanProjectRow).order_by(ProductionPlanProjectRow.project))
        return [PlanProjectSnapshot(
            project=row.project, customer=row.customer, opened_on=row.opened_on,
            deadline=row.deadline, status=row.status, terminal=row.terminal,
            imported_at=row.imported_at,
        ) for row in rows]

    def replace(self, projects: tuple[PlanProjectSnapshot, ...]) -> None:
        self.session.execute(delete(ProductionPlanProjectRow))
        self.session.add_all(ProductionPlanProjectRow(
            project=item.project, customer=item.customer, opened_on=item.opened_on,
            deadline=item.deadline, status=item.status, terminal=item.terminal,
            imported_at=item.imported_at,
        ) for item in projects)


def _read_word(kind: str, value: str) -> str:
    """An old stage / closed-project word of an immutable journal row, as the current lists read it."""
    if kind == PLAN_KINDS["stage"]:
        return stage_alias(value)
    return terminal_alias(value) if kind == PLAN_KINDS["status"] else value


class SqlAlchemyPlanEventRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionPlanEventRow) -> PlanEvent:
        return PlanEvent(
            id=row.id, project=row.project, occurred_at=row.occurred_at,
            who=row.who, kind=row.kind, blocker=blocker_alias(row.blocker),
            text=row.text, deadline=row.deadline, sequence=row.sequence,
            item_index=row.item_index, field=row.field, reason=row.reason,
            # Old «Этап КБ» rows keep their word (the journal is immutable); it reads as the current ladder stage.
            value=_read_word(row.kind, row.value),
            from_value=_read_word(row.kind, row.from_value),
        )

    def last_sequence(self, project: str) -> int:
        """The newest sequence of the project's journal (0 — empty): the version a stage change is checked against."""
        return self.session.scalar(select(func.max(ProductionPlanEventRow.sequence)).where(
            ProductionPlanEventRow.project == project,
        )) or 0

    def list(self, project: str | None = None, limit: int = 10_000) -> list[PlanEvent]:
        statement = select(ProductionPlanEventRow)
        if project is not None:
            statement = statement.where(ProductionPlanEventRow.project == project)
        rows = self.session.scalars(
            statement.order_by(ProductionPlanEventRow.project, ProductionPlanEventRow.sequence).limit(limit)
        )
        return [self._domain(row) for row in rows]

    def _names(self, project: str) -> list[str]:
        """Names under which the journal holds the project: the rows keep the number as it was typed, readers
        match by `normalize_project_number`, so the delete matches the same way."""
        canon = normalize_project_number(project)
        names = self.session.scalars(select(ProductionPlanEventRow.project).distinct())
        return [name for name in names if normalize_project_number(name) == canon] if canon else []

    def of_project(self, project: str) -> list[PlanEvent]:
        names = self._names(project)
        if not names:
            return []
        rows = self.session.scalars(
            select(ProductionPlanEventRow).where(ProductionPlanEventRow.project.in_(names))
            .order_by(ProductionPlanEventRow.occurred_at, ProductionPlanEventRow.project, ProductionPlanEventRow.sequence)
        )
        return [self._domain(row) for row in rows]

    def delete_project(self, project: str) -> int:
        names = self._names(project)
        if not names:
            return 0
        return self.session.execute(
            delete(ProductionPlanEventRow).where(ProductionPlanEventRow.project.in_(names))
        ).rowcount or 0

    def add(self, event: PlanEvent) -> PlanEvent:
        stored = replace(event, sequence=self.last_sequence(event.project) + 1)
        self.session.add(ProductionPlanEventRow(
            id=stored.id, project=stored.project, sequence=stored.sequence,
            occurred_at=stored.occurred_at, who=stored.who, kind=stored.kind,
            blocker=stored.blocker, text=stored.text, deadline=stored.deadline,
            item_index=stored.item_index, value=stored.value, field=stored.field,
            from_value=stored.from_value, reason=stored.reason,
        ))
        return stored


class SqlAlchemyBoxRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionBoxRow) -> ProductionBox:
        return ProductionBox(
            id=row.id, number=row.number, version=row.version,
            task_id=row.task_id, operation_id=row.operation_id, item=row.item,
            operation_position=row.operation_position, operator_id=row.operator_id,
            quantity=row.quantity, consumed=row.consumed, reserved=row.reserved,
            status=BoxStatus(row.status), created_at=row.created_at,
            closed_at=row.closed_at, closed_by=row.closed_by,
            confirmed_at=row.confirmed_at, confirmed_by=row.confirmed_by,
            return_reason=row.return_reason,
            rejected=row.rejected,
            code=row.code,
        )

    def get(self, box_id: UUID) -> ProductionBox | None:
        row = self.session.get(ProductionBoxRow, box_id)
        return self._domain(row) if row is not None else None

    def by_number(self, number: str) -> ProductionBox | None:
        row = self.session.scalar(select(ProductionBoxRow).where(ProductionBoxRow.number == number).limit(1))
        return self._domain(row) if row is not None else None

    def list(self, *, task_id: UUID | None = None, operator_id: UUID | None = None, limit: int = 500) -> list[ProductionBox]:
        statement = select(ProductionBoxRow)
        if task_id is not None:
            statement = statement.where(ProductionBoxRow.task_id == task_id)
        if operator_id is not None:
            statement = statement.where(ProductionBoxRow.operator_id == operator_id)
        rows = self.session.scalars(statement.order_by(ProductionBoxRow.created_at.desc(), ProductionBoxRow.id.desc()).limit(limit))
        return [self._domain(row) for row in rows]

    def reserve_number(self) -> str:
        row = self.session.scalar(select(ProductionSequenceRow).where(ProductionSequenceRow.name == "box").with_for_update())
        if row is None:
            row = ProductionSequenceRow(name="box", value=0)
            self.session.add(row)
            self.session.flush()
        row.value += 1
        return issue_box_number(row.value)

    def operator_code(self, operator_id: UUID) -> str:
        """Код сотрудника для коробки: выданный раньше, иначе новый (буква фамилии + табельный или свободный номер)."""
        held = self.session.get(ProductionOperatorCodeRow, operator_id)
        if held is not None:
            return held.code
        user = self.session.get(IdentityUserRow, operator_id)
        taken = self.session.scalars(select(ProductionOperatorCodeRow.code)).all()
        code = operator_short_code(user.name if user else "", user.personnel_number if user else "", taken)
        self.session.add(ProductionOperatorCodeRow(operator_id=operator_id, code=code))
        self.session.flush()
        return code

    def reserve_code(self, *, project: str, item: str, operator_id: UUID, day: date) -> str:
        """Код новой коробки. Зовётся после reserve_number: замок строки серии номеров (FOR UPDATE) держит
        выдачу кодов по очереди, а UNIQUE на коде не даст двум коробкам один код и без него."""
        prefix = box_code_prefix(project, item, day, self.operator_code(operator_id))
        existing = self.session.scalars(
            select(ProductionBoxRow.code).where(ProductionBoxRow.code.startswith(f"{prefix}·", autoescape=True))
        ).all()
        return next_box_code(prefix, (code for code in existing if code))

    def by_code(self, text: str) -> list[ProductionBox]:
        """Коробки, чей код совпадает с введённым без учёта регистра, пробелов и разделителей."""
        key = box_code_key(text)
        if not key:
            return []
        exact = self.session.scalar(select(ProductionBoxRow).where(ProductionBoxRow.code == str(text).strip()).limit(1))
        if exact is not None:
            return [self._domain(exact)]
        rows = self.session.scalars(select(ProductionBoxRow).where(ProductionBoxRow.code.is_not(None)))
        return [self._domain(row) for row in rows if box_code_key(row.code) == key]

    def search(self, text: str, limit: int = 200) -> list[ProductionBox]:
        """Поиск коробки: часть кода (без учёта разделителей) или номера."""
        key = box_code_key(text)
        if not key:
            return []
        rows = self.session.scalars(
            select(ProductionBoxRow).order_by(ProductionBoxRow.created_at.desc(), ProductionBoxRow.id.desc())
        )
        found = []
        for row in rows:
            if key in box_code_key(row.code or "") or key in box_code_key(row.number or ""):
                found.append(self._domain(row))
                if len(found) >= limit:
                    break
        return found

    def save(self, box: ProductionBox) -> None:
        row = self.session.get(ProductionBoxRow, box.id)
        if row is None:
            row = ProductionBoxRow(id=box.id)
            self.session.add(row)
        for name in (
            "number", "version", "task_id", "operation_id", "item",
            "operation_position", "operator_id", "quantity", "consumed",
            "reserved", "created_at", "closed_at", "closed_by",
            "confirmed_at", "confirmed_by", "return_reason",
            "rejected", "code",
        ):
            setattr(row, name, getattr(box, name))
        row.status = box.status.value


class SqlAlchemyWorkRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionWorkSessionRow) -> WorkSession:
        return WorkSession(
            id=row.id, task_id=row.task_id, operation_id=row.operation_id,
            operator_id=row.operator_id, started_at=row.started_at,
            status=WorkStatus(row.status), pause_started_at=row.pause_started_at,
            pause_seconds=row.pause_seconds,
        )

    def get(self, session_id: UUID) -> WorkSession | None:
        row = self.session.get(ProductionWorkSessionRow, session_id)
        return self._domain(row) if row is not None else None

    def current(self, operator_id: UUID) -> WorkSession | None:
        row = self.session.scalar(select(ProductionWorkSessionRow).where(
            ProductionWorkSessionRow.operator_id == operator_id,
            ProductionWorkSessionRow.status.in_((WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value)),
        ).order_by(ProductionWorkSessionRow.started_at.desc()).limit(1))
        return self._domain(row) if row is not None else None

    def inputs(self, session_id: UUID) -> list[WorkInput]:
        rows = self.session.scalars(select(ProductionWorkInputRow).where(
            ProductionWorkInputRow.session_id == session_id,
        ).order_by(ProductionWorkInputRow.position, ProductionWorkInputRow.box_id))
        return [WorkInput(row.box_id, row.quantity) for row in rows]

    def material_inputs(self, session_id: UUID) -> list[MaterialInput]:
        rows = self.session.scalars(select(ProductionWorkMaterialInputRow).where(
            ProductionWorkMaterialInputRow.session_id == session_id,
        ).order_by(ProductionWorkMaterialInputRow.fulfillment_line_id))
        return [MaterialInput(row.fulfillment_line_id, row.quantity) for row in rows]

    def save(self, session: WorkSession) -> None:
        row = self.session.get(ProductionWorkSessionRow, session.id)
        if row is None:
            row = ProductionWorkSessionRow(id=session.id)
            self.session.add(row)
        row.task_id = session.task_id
        row.operation_id = session.operation_id
        row.operator_id = session.operator_id
        row.started_at = session.started_at
        row.status = session.status.value
        row.pause_started_at = session.pause_started_at
        row.pause_seconds = session.pause_seconds

    def add_inputs(self, session_id: UUID, inputs: tuple[WorkInput, ...]) -> None:
        self.session.add_all(ProductionWorkInputRow(
            session_id=session_id, box_id=item.box_id, quantity=item.quantity, position=index,
        ) for index, item in enumerate(inputs))

    def add_material_inputs(self, session_id: UUID, inputs: tuple[MaterialInput, ...]) -> None:
        self.session.add_all(ProductionWorkMaterialInputRow(
            session_id=session_id, fulfillment_line_id=item.fulfillment_line_id,
            quantity=item.quantity,
        ) for item in inputs)

    def record_card_versions(self, operation_id: UUID, versions: Sequence[dict]) -> bool:
        """Версии карточек ЭТК операции на первом начале: пишутся, только пока их нет (условный UPDATE), — повторное
        начало, продолжение и гонка двух начал ничего не перезаписывают. True — записано сейчас."""
        result = self.session.execute(
            update(ProductionOperationRow)
            .where(ProductionOperationRow.id == operation_id, ProductionOperationRow.card_versions.is_(None))
            .values(card_versions=[dict(item) for item in versions])
        )
        return result.rowcount == 1

    def card_versions(self, operation_ids: Sequence[UUID]) -> dict[UUID, list[dict]]:
        """Записанные на первом начале версии карточек ЭТК по операциям (нет записи — нет ключа)."""
        ids = list(operation_ids)
        if not ids:
            return {}
        rows = self.session.execute(
            select(ProductionOperationRow.id, ProductionOperationRow.card_versions)
            .where(ProductionOperationRow.id.in_(ids), ProductionOperationRow.card_versions.is_not(None))
        )
        return {row.id: list(row.card_versions) for row in rows if isinstance(row.card_versions, list)}


class SqlAlchemyOutputRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, output: ProductionOutput) -> None:
        self.session.add(ProductionOutputRow(**output.__dict__))

    @staticmethod
    def _domain(row: ProductionOutputRow) -> ProductionOutput:
        return ProductionOutput(**{
            name: getattr(row, name) for name in ProductionOutput.__dataclass_fields__
        })

    @staticmethod
    def _active():
        # SUPPLYMAP `prodWorkFacts_`: an output named by another output's correction_of_id drops out of every sum.
        replacement = aliased(ProductionOutputRow)
        return ~select(replacement.id).where(replacement.correction_of_id == ProductionOutputRow.id).exists()

    def get(self, output_id: UUID) -> ProductionOutput | None:
        row = self.session.get(ProductionOutputRow, output_id)
        return self._domain(row) if row is not None else None

    def for_session(self, session_id: UUID) -> ProductionOutput | None:
        row = self.session.scalar(select(ProductionOutputRow).where(ProductionOutputRow.session_id == session_id))
        return self._domain(row) if row is not None else None

    def is_superseded(self, output_id: UUID) -> bool:
        return self.session.scalar(select(ProductionOutputRow.id).where(
            ProductionOutputRow.correction_of_id == output_id,
        ).limit(1)) is not None

    def lineage(self, output_id: UUID) -> tuple[UUID, ...]:
        chain: list[UUID] = []
        current: UUID | None = output_id
        while current is not None and current not in chain:
            chain.append(current)
            current = self.session.scalar(select(ProductionOutputRow.correction_of_id).where(
                ProductionOutputRow.id == current,
            ))
        return tuple(chain)

    def active_for_box(self, box_id: UUID) -> list[ProductionOutput]:
        rows = self.session.scalars(select(ProductionOutputRow).where(
            ProductionOutputRow.output_box_id == box_id, self._active(),
        ).order_by(ProductionOutputRow.finished_at, ProductionOutputRow.id))
        return [self._domain(row) for row in rows]

    def list_for_task(self, task_id: UUID) -> list[ProductionOutput]:
        rows = self.session.scalars(select(ProductionOutputRow).where(
            ProductionOutputRow.task_id == task_id,
        ).order_by(ProductionOutputRow.finished_at, ProductionOutputRow.id))
        return [self._domain(row) for row in rows]

    def active_for_tasks(self, task_ids: tuple[UUID, ...]) -> list[ProductionOutput]:
        if not task_ids:
            return []
        rows = self.session.scalars(select(ProductionOutputRow).where(
            ProductionOutputRow.task_id.in_(task_ids), self._active(),
        ))
        return [self._domain(row) for row in rows]

    def total_for_operation(self, operation_id: UUID) -> Decimal:
        value = self.session.scalar(select(func.coalesce(
            func.sum(ProductionOutputRow.good_quantity + ProductionOutputRow.defect_quantity), 0,
        )).where(ProductionOutputRow.operation_id == operation_id, self._active()))
        return Decimal(value or 0)


class SqlAlchemyWorkLogRepository:
    """Журнал рабочего времени: add и чтение; строки не меняются и не удаляются."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionWorkTimeLogRow) -> WorkTimeEntry:
        values = {name: getattr(row, name) for name in WorkTimeEntry.__dataclass_fields__}
        return WorkTimeEntry(**{**values, "event": WorkEvent(row.event)})

    def add(self, entry: WorkTimeEntry) -> None:
        self.session.add(ProductionWorkTimeLogRow(**{**entry.__dict__, "event": entry.event.value}))
        # Сразу в базу: UNIQUE ключа повтора и номера записи срабатывает в этой транзакции, а не на commit.
        self.session.flush()

    def by_key(self, idempotency_key: str) -> WorkTimeEntry | None:
        row = self.session.scalar(select(ProductionWorkTimeLogRow).where(
            ProductionWorkTimeLogRow.idempotency_key == idempotency_key,
        ))
        return self._domain(row) if row is not None else None

    def next_sequence(self, session_id: UUID) -> int:
        value = self.session.scalar(select(func.max(ProductionWorkTimeLogRow.sequence)).where(
            ProductionWorkTimeLogRow.session_id == session_id,
        ))
        return 0 if value is None else int(value) + 1

    def entries(
        self, *, since: datetime | None = None, until: datetime | None = None,
        operator_id: UUID | None = None, operation_ids: tuple[UUID, ...] | None = None, limit: int = 5_000,
    ) -> list[WorkTimeEntry]:
        query = select(ProductionWorkTimeLogRow)
        if since is not None:
            query = query.where(ProductionWorkTimeLogRow.occurred_at >= since)
        if until is not None:
            query = query.where(ProductionWorkTimeLogRow.occurred_at < until)
        if operator_id is not None:
            query = query.where(ProductionWorkTimeLogRow.operator_id == operator_id)
        if operation_ids is not None:
            if not operation_ids:
                return []
            query = query.where(ProductionWorkTimeLogRow.operation_id.in_(operation_ids))
        rows = self.session.scalars(query.order_by(
            ProductionWorkTimeLogRow.occurred_at, ProductionWorkTimeLogRow.session_id, ProductionWorkTimeLogRow.sequence,
        ).limit(limit))
        return [self._domain(row) for row in rows]

    def summary(self, by: str, *, since: datetime, until: datetime) -> list[dict]:
        """Сдачи за период [since, until), сгруппированные по коду операции, исполнителю или проекту: факт, норма на
        сданные штуки, штуки, число сдач. Код операции подписан названием этапа (последнее по алфавиту, если их
        несколько)."""
        if by not in SUMMARY_KEYS:
            raise ProductionValidationError("Сводка группируется по коду операции, исполнителю или проекту")
        row = ProductionWorkTimeLogRow
        key = {"operation_code": row.operation_code, "operator": row.operator_id, "project": row.project}[by]
        label = {"operation_code": func.max(row.stage), "operator": func.max(row.operator_name), "project": func.max(row.project)}[by]
        query = select(
            key.label("key"), label.label("label"),
            func.count(row.id).label("finishes"),
            func.coalesce(func.sum(row.good_quantity + row.defect_quantity), 0).label("quantity"),
            func.coalesce(func.sum(row.running_seconds), 0).label("actual_seconds"),
            func.coalesce(func.sum(row.earned_norm_seconds), 0).label("norm_seconds"),
        ).where(
            row.event == WorkEvent.FINISH.value, row.occurred_at >= since, row.occurred_at < until,
        ).group_by(key).order_by(key)
        return [dict(item._mapping) for item in self.session.execute(query)]


class SqlAlchemyConsumptionRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, edge: ConsumptionEdge) -> None:
        self.session.add(ProductionConsumptionRow(**edge.__dict__))

    def net_into(self, target_box_id: UUID, output_ids: tuple[UUID, ...]) -> list[WorkInput]:
        """Net pieces the given outputs took into a box, per source box, oldest source first.

        A source everything was given back to stays in the list with 0: SUPPLYMAP still tops up from it.
        """
        if not output_ids:
            return []
        rows = self.session.execute(select(
            ProductionConsumptionRow.source_box_id, func.sum(ProductionConsumptionRow.quantity),
        ).join(
            ProductionBoxRow, ProductionBoxRow.id == ProductionConsumptionRow.source_box_id,
        ).where(
            ProductionConsumptionRow.target_box_id == target_box_id,
            ProductionConsumptionRow.output_id.in_(output_ids),
        ).group_by(
            ProductionConsumptionRow.source_box_id, ProductionBoxRow.created_at, ProductionBoxRow.id,
        ).order_by(ProductionBoxRow.created_at, ProductionBoxRow.id))
        return [WorkInput(source_box_id, Decimal(quantity)) for source_box_id, quantity in rows]


class SqlAlchemyMaterialRepository:
    def __init__(self, session: Session):
        self.session = session

    def request_status(self, request_id: UUID) -> FulfillmentStatus | None:
        value = self.session.scalar(select(FulfillmentRequestRow.status).where(FulfillmentRequestRow.id == request_id))
        return FulfillmentStatus(value) if value is not None else None

    def list_sources(self, request_id: UUID) -> list[MaterialSource]:
        rows = self.session.scalars(select(FulfillmentLineRow).where(
            FulfillmentLineRow.request_id == request_id,
            FulfillmentLineRow.issued > 0,
        ).order_by(FulfillmentLineRow.position))
        result = []
        for row in rows:
            consumed = self.session.scalar(select(func.coalesce(func.sum(ProductionMaterialConsumptionRow.quantity), 0)).where(
                ProductionMaterialConsumptionRow.fulfillment_line_id == row.id,
            ))
            reserved = self.session.scalar(select(func.coalesce(func.sum(ProductionWorkMaterialInputRow.quantity), 0)).join(
                ProductionWorkSessionRow,
                ProductionWorkSessionRow.id == ProductionWorkMaterialInputRow.session_id,
            ).where(
                ProductionWorkMaterialInputRow.fulfillment_line_id == row.id,
                ProductionWorkSessionRow.status.in_((WorkStatus.ACTIVE.value, WorkStatus.PAUSED.value)),
            ))
            result.append(MaterialSource(
                fulfillment_line_id=row.id, article=row.article, name=row.name, unit=row.unit,
                grn=row.lot_grn or "", package=row.lot_package or "",
                issued=row.issued, consumed=Decimal(consumed or 0), reserved=Decimal(reserved or 0),
            ))
        return result

    def source(self, request_id: UUID, line_id: UUID) -> MaterialSource | None:
        self.session.scalar(select(FulfillmentLineRow.id).where(
            FulfillmentLineRow.request_id == request_id,
            FulfillmentLineRow.id == line_id,
        ).with_for_update())
        return next((item for item in self.list_sources(request_id) if item.fulfillment_line_id == line_id), None)

    def add_consumption(self, item: MaterialConsumption) -> None:
        self.session.add(ProductionMaterialConsumptionRow(**item.__dict__))


class SqlAlchemyFinishedGoodsReceiptRepository:
    def __init__(self, session: Session):
        self.session = session

    def by_box(self, box_id: UUID) -> FinishedGoodsReceipt | None:
        row = self.session.scalar(select(ProductionFinishedGoodsReceiptRow).where(
            ProductionFinishedGoodsReceiptRow.source_box_id == box_id,
        ))
        return FinishedGoodsReceipt(
            id=row.id, source_box_id=row.source_box_id,
            finished_good_id=row.finished_good_id, grn=row.grn,
            quantity=row.quantity, received_at=row.received_at,
        ) if row is not None else None

    def add(self, receipt: FinishedGoodsReceipt) -> None:
        self.session.add(ProductionFinishedGoodsReceiptRow(**receipt.__dict__))


class SqlAlchemyDefectRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionDefectRow) -> DefectRecord:
        return DefectRecord(
            id=row.id, number=row.number, version=row.version,
            created_at=row.created_at, output_id=row.output_id,
            source_box_id=row.source_box_id, task_id=row.task_id,
            operation_id=row.operation_id, operator_id=row.operator_id,
            quantity=row.quantity, character=row.character,
            description=row.description, status=DefectStatus(row.status),
            replaced_by_output_id=row.replaced_by_output_id,
        )

    def get(self, defect_id: UUID) -> DefectRecord | None:
        row = self.session.get(ProductionDefectRow, defect_id)
        return self._domain(row) if row is not None else None

    def active_for_outputs(self, output_ids: tuple[UUID, ...]) -> DefectRecord | None:
        if not output_ids:
            return None
        row = self.session.scalar(select(ProductionDefectRow).where(
            ProductionDefectRow.output_id.in_(output_ids),
            ProductionDefectRow.replaced_by_output_id.is_(None),
        ).limit(1))
        return self._domain(row) if row is not None else None

    def list(self, limit: int = 500) -> list[DefectRecord]:
        rows = self.session.scalars(select(ProductionDefectRow).order_by(
            ProductionDefectRow.created_at.desc(), ProductionDefectRow.id.desc(),
        ).limit(limit))
        return [self._domain(row) for row in rows]

    def reserve_number(self) -> str:
        row = self.session.scalar(select(ProductionSequenceRow).where(
            ProductionSequenceRow.name == "defect",
        ).with_for_update())
        if row is None:
            row = ProductionSequenceRow(name="defect", value=0)
            self.session.add(row)
            self.session.flush()
        row.value += 1
        return issue_defect_number(row.value)

    def save(self, defect: DefectRecord) -> None:
        row = self.session.get(ProductionDefectRow, defect.id)
        if row is None:
            row = ProductionDefectRow(id=defect.id)
            self.session.add(row)
        for field in (
            "number", "version", "created_at", "output_id", "source_box_id",
            "task_id", "operation_id", "operator_id", "quantity", "character", "description",
            "replaced_by_output_id",
        ):
            setattr(row, field, getattr(defect, field))
        row.status = defect.status.value

    def add_decision(self, decision: DefectDecision) -> None:
        self.session.add(ProductionDefectDecisionRow(
            id=decision.id, defect_id=decision.defect_id,
            disposition=decision.disposition.value,
            decided_by=decision.decided_by, decided_at=decision.decided_at,
            act_reference=decision.act_reference,
        ))


class SqlAlchemyInstrumentRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionInstrumentRow) -> MeasurementInstrument:
        return MeasurementInstrument(
            id=row.id, version=row.version, inventory_number=row.inventory_number,
            name=row.name, instrument_type=row.instrument_type,
            verification_number=row.verification_number,
            verified_at=row.verified_at, verification_due=row.verification_due,
            status=InstrumentStatus(row.status),
        )

    def get(self, instrument_id: UUID) -> MeasurementInstrument | None:
        row = self.session.get(ProductionInstrumentRow, instrument_id)
        return self._domain(row) if row is not None else None

    def list(self) -> list[MeasurementInstrument]:
        rows = self.session.scalars(select(ProductionInstrumentRow).order_by(ProductionInstrumentRow.inventory_number))
        return [self._domain(row) for row in rows]

    def by_inventory_number(self, number: str) -> MeasurementInstrument | None:
        row = self.session.scalar(select(ProductionInstrumentRow).where(
            ProductionInstrumentRow.inventory_number == number,
        ))
        return self._domain(row) if row is not None else None

    def save(self, instrument: MeasurementInstrument) -> None:
        row = self.session.get(ProductionInstrumentRow, instrument.id)
        if row is None:
            row = ProductionInstrumentRow(id=instrument.id)
            self.session.add(row)
        for field in (
            "version", "inventory_number", "name", "instrument_type",
            "verification_number", "verified_at", "verification_due",
        ):
            setattr(row, field, getattr(instrument, field))
        row.status = instrument.status.value


class SqlAlchemySerialRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionSerialRow) -> SerialUnit:
        return SerialUnit(
            id=row.id, serial_number=row.serial_number, task_id=row.task_id,
            position=row.position, status=SerialStatus(row.status),
            current_box_id=row.current_box_id, finished_goods_grn=row.finished_goods_grn,
        )

    def ensure_task(self, task_id: UUID, task_number: str, quantity: int) -> bool:
        existing = set(self.session.scalars(select(ProductionSerialRow.position).where(
            ProductionSerialRow.task_id == task_id,
        )))
        missing = [position for position in range(1, quantity + 1) if position not in existing]
        self.session.add_all(ProductionSerialRow(
            id=uuid4(), serial_number=f"{task_number}-{position:04d}",
            task_id=task_id, position=position, status=SerialStatus.IN_PROGRESS.value,
            current_box_id=None, finished_goods_grn=None,
        ) for position in missing)
        return bool(missing)

    def list_for_task(self, task_id: UUID) -> list[SerialUnit]:
        rows = self.session.scalars(select(ProductionSerialRow).where(
            ProductionSerialRow.task_id == task_id,
        ).order_by(ProductionSerialRow.position))
        return [self._domain(row) for row in rows]

    def by_position(self, task_id: UUID, position: int) -> SerialUnit | None:
        row = self.session.scalar(select(ProductionSerialRow).where(
            ProductionSerialRow.task_id == task_id, ProductionSerialRow.position == position,
        ))
        return self._domain(row) if row is not None else None

    def assign_qc(self, task_id: UUID, box_id: UUID, accepted: int, rejected: int) -> list[SerialUnit]:
        """Свободные серийные номера в коробку ОТК. Брак берёт сначала изделия, чей последний замер КСВН «не годно»;
        годные — сначала прошедшие замер, затем не мерянные; не прошедшие замер годными не принимаются.
        Без замеров — как раньше: годные — первые по порядку номера, брак — следующие."""
        rows = list(self.session.scalars(select(ProductionSerialRow).where(
            ProductionSerialRow.task_id == task_id,
            ProductionSerialRow.current_box_id.is_(None),
            ProductionSerialRow.status == SerialStatus.IN_PROGRESS.value,
        ).order_by(ProductionSerialRow.position).with_for_update()))
        if len(rows) < accepted + rejected:
            raise ProductionValidationError("Недостаточно свободных серийных номеров задания")
        verdicts = SqlAlchemyVswrRepository(self.session).latest_verdicts([row.id for row in rows])
        failed = [row for row in rows if verdicts.get(row.id) is VswrVerdict.FAIL]
        passed = [row for row in rows if verdicts.get(row.id) is VswrVerdict.PASS]
        unmeasured = [row for row in rows if row.id not in verdicts]
        candidates = passed + unmeasured
        if len(candidates) < accepted:
            raise ProductionValidationError(
                f"Годными принимаются {accepted} шт, а свободных изделий без брака по КСВН {len(candidates)}: "
                "не прошедшие замер КСВН принимаются только в брак",
            )
        accepted_rows = candidates[:accepted]
        rejected_rows = failed[:rejected]
        rest = sorted(candidates[accepted:], key=lambda row: row.position)
        rejected_rows += rest[:rejected - len(rejected_rows)]
        for row in accepted_rows:
            row.current_box_id = box_id
            row.status = SerialStatus.QC_ACCEPTED.value
        for row in rejected_rows:
            row.current_box_id = box_id
            row.status = SerialStatus.DEFECT.value
        chosen = sorted(accepted_rows + rejected_rows, key=lambda row: row.position)
        return [self._domain(row) for row in chosen]

    def mark_handed(self, box_id: UUID, grn: str) -> None:
        rows = self.session.scalars(select(ProductionSerialRow).where(
            ProductionSerialRow.current_box_id == box_id,
            ProductionSerialRow.status == SerialStatus.QC_ACCEPTED.value,
        ).with_for_update())
        for row in rows:
            row.status = SerialStatus.FINISHED_GOODS.value
            row.finished_goods_grn = grn


class SqlAlchemyVswrRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProductionVswrMeasurementRow, serial_number: str) -> VswrMeasurement:
        return VswrMeasurement(
            id=row.id, serial_id=row.serial_id, attempt=row.attempt, instrument_id=row.instrument_id,
            band_from_mhz=row.band_from_mhz, band_to_mhz=row.band_to_mhz,
            max_vswr=row.max_vswr, max_vswr_mhz=row.max_vswr_mhz,
            norm=dict(row.norm or {}), norm_vswr_max=row.norm_vswr_max,
            verdict=VswrVerdict(row.verdict), verdict_manual=row.verdict_manual,
            verdict_reason=row.verdict_reason, inspector_id=row.inspector_id,
            inspector_name=row.inspector_name, measured_at=row.measured_at,
            file_name=row.file_name, points=row.points, file_meta=dict(row.file_meta or {}),
            trace=tuple((float(point[0]), float(point[1])) for point in row.trace or ()),
            serial_number=serial_number,
        )

    def _select(self):
        return select(ProductionVswrMeasurementRow, ProductionSerialRow.serial_number).join(
            ProductionSerialRow, ProductionSerialRow.id == ProductionVswrMeasurementRow.serial_id,
        )

    def next_attempt(self, serial_id: UUID) -> int:
        current = self.session.scalar(select(func.max(ProductionVswrMeasurementRow.attempt)).where(
            ProductionVswrMeasurementRow.serial_id == serial_id,
        ))
        return int(current or 0) + 1

    def add(self, measurement: VswrMeasurement, content: bytes) -> None:
        self.session.add(ProductionVswrMeasurementRow(
            id=measurement.id, serial_id=measurement.serial_id, attempt=measurement.attempt,
            instrument_id=measurement.instrument_id,
            band_from_mhz=measurement.band_from_mhz, band_to_mhz=measurement.band_to_mhz,
            max_vswr=measurement.max_vswr, max_vswr_mhz=measurement.max_vswr_mhz,
            norm=measurement.norm, norm_vswr_max=measurement.norm_vswr_max,
            verdict=measurement.verdict.value, verdict_manual=measurement.verdict_manual,
            verdict_reason=measurement.verdict_reason, inspector_id=measurement.inspector_id,
            inspector_name=measurement.inspector_name, measured_at=measurement.measured_at,
            file_name=measurement.file_name, content=content, points=measurement.points,
            file_meta=measurement.file_meta,
            trace=[list(point) for point in measurement.trace],
        ))

    def list_for_task(self, task_id: UUID) -> list[VswrMeasurement]:
        rows = self.session.execute(self._select().where(ProductionSerialRow.task_id == task_id).order_by(
            ProductionSerialRow.position, ProductionVswrMeasurementRow.attempt,
        ))
        return [self._domain(row, number) for row, number in rows]

    def list_for_serial(self, serial_id: UUID) -> list[VswrMeasurement]:
        rows = self.session.execute(self._select().where(
            ProductionVswrMeasurementRow.serial_id == serial_id,
        ).order_by(ProductionVswrMeasurementRow.attempt))
        return [self._domain(row, number) for row, number in rows]

    def latest_for_serial(self, serial_id: UUID) -> VswrMeasurement | None:
        found = self.session.execute(self._select().where(
            ProductionVswrMeasurementRow.serial_id == serial_id,
        ).order_by(ProductionVswrMeasurementRow.attempt.desc()).limit(1)).first()
        return self._domain(found[0], found[1]) if found is not None else None

    def latest_verdicts(self, serial_ids: Sequence[UUID]) -> dict[UUID, VswrVerdict]:
        """serial_id → вердикт последнего замера; не мерянных изделий в ответе нет."""
        if not serial_ids:
            return {}
        rows = self.session.execute(select(
            ProductionVswrMeasurementRow.serial_id, ProductionVswrMeasurementRow.attempt,
            ProductionVswrMeasurementRow.verdict,
        ).where(ProductionVswrMeasurementRow.serial_id.in_(list(serial_ids))))
        latest: dict[UUID, tuple[int, str]] = {}
        for serial_id, attempt, verdict in rows:
            if serial_id not in latest or attempt > latest[serial_id][0]:
                latest[serial_id] = (attempt, verdict)
        return {serial_id: VswrVerdict(verdict) for serial_id, (_attempt, verdict) in latest.items()}

    def file(self, measurement_id: UUID) -> tuple[str, bytes] | None:
        found = self.session.execute(select(
            ProductionVswrMeasurementRow.file_name, ProductionVswrMeasurementRow.content,
        ).where(ProductionVswrMeasurementRow.id == measurement_id)).first()
        return (found[0], bytes(found[1])) if found is not None else None


class SqlAlchemyQcRepository:
    def __init__(self, session: Session):
        self.session = session

    def _domain(self, row: ProductionQcInspectionRow) -> QcInspection:
        instrument_ids = tuple(self.session.scalars(select(ProductionQcInstrumentRow.instrument_id).where(
            ProductionQcInstrumentRow.inspection_id == row.id,
        )))
        return QcInspection(
            id=row.id, box_id=row.box_id, inspector_id=row.inspector_id,
            inspected_at=row.inspected_at, accepted=row.accepted,
            rejected=row.rejected, defect_number=row.defect_number,
            defect_character=row.defect_character,
            defect_description=row.defect_description,
            instrument_ids=instrument_ids,
        )

    def by_box(self, box_id: UUID) -> QcInspection | None:
        row = self.session.scalar(select(ProductionQcInspectionRow).where(
            ProductionQcInspectionRow.box_id == box_id,
        ))
        return self._domain(row) if row is not None else None

    def get(self, inspection_id: UUID) -> QcInspection | None:
        row = self.session.get(ProductionQcInspectionRow, inspection_id)
        return self._domain(row) if row is not None else None

    def inspected_box_ids(self) -> set[UUID]:
        """Boxes ОТК has inspected (the stage ladder: a final-operation box inspected → «ОТК»)."""
        return set(self.session.scalars(select(ProductionQcInspectionRow.box_id)))

    def list_rejections(self) -> list[QcInspection]:
        rows = self.session.scalars(select(ProductionQcInspectionRow).where(
            ProductionQcInspectionRow.rejected > 0,
        ).order_by(ProductionQcInspectionRow.inspected_at.desc(), ProductionQcInspectionRow.id.desc()))
        return [self._domain(row) for row in rows]

    def decision_for(self, inspection_id: UUID) -> QcDecision | None:
        row = self.session.scalar(select(ProductionQcDecisionRow).where(
            ProductionQcDecisionRow.inspection_id == inspection_id,
        ))
        if row is None:
            return None
        return QcDecision(
            id=row.id, inspection_id=row.inspection_id,
            disposition=DefectDisposition(row.disposition), decided_by=row.decided_by,
            decided_at=row.decided_at, act_reference=row.act_reference,
        )

    def add(self, inspection: QcInspection) -> None:
        self.session.add(ProductionQcInspectionRow(
            id=inspection.id, box_id=inspection.box_id,
            inspector_id=inspection.inspector_id, inspected_at=inspection.inspected_at,
            accepted=inspection.accepted, rejected=inspection.rejected,
            defect_number=inspection.defect_number,
            defect_character=inspection.defect_character,
            defect_description=inspection.defect_description,
        ))
        self.session.add_all(ProductionQcInstrumentRow(
            inspection_id=inspection.id, instrument_id=instrument_id,
        ) for instrument_id in inspection.instrument_ids)

    def add_decision(self, decision: QcDecision) -> None:
        self.session.add(ProductionQcDecisionRow(
            id=decision.id, inspection_id=decision.inspection_id,
            disposition=decision.disposition.value, decided_by=decision.decided_by,
            decided_at=decision.decided_at, act_reference=decision.act_reference,
        ))


class SqlAlchemyGenealogyRepository:
    def __init__(self, session: Session):
        self.session = session

    def search(self, query: str):
        boxes = tuple(BoxSnapshot(
            id=row.id, number=row.number, item=row.item,
            operation_position=row.operation_position, status=row.status,
        ) for row in self.session.scalars(select(ProductionBoxRow)))
        box_consumption = tuple(BoxConsumptionSnapshot(
            source_box_id=row.source_box_id, target_box_id=row.target_box_id,
            quantity=row.quantity,
        ) for row in self.session.scalars(select(ProductionConsumptionRow)))
        material_consumption = tuple(MaterialConsumptionSnapshot(
            fulfillment_line_id=row.fulfillment_line_id,
            target_box_id=row.target_box_id, grn=row.grn,
            package=row.package, quantity=row.quantity,
        ) for row in self.session.scalars(select(ProductionMaterialConsumptionRow)))
        finished_receipts = tuple(FinishedReceiptSnapshot(
            source_box_id=row.source_box_id, finished_good_id=row.finished_good_id,
            grn=row.grn, quantity=row.quantity,
        ) for row in self.session.scalars(select(ProductionFinishedGoodsReceiptRow)))
        serials = tuple(SerialSnapshot(
            serial_number=row.serial_number, current_box_id=row.current_box_id,
            finished_goods_grn=row.finished_goods_grn, status=row.status,
        ) for row in self.session.scalars(select(ProductionSerialRow)))
        return build_genealogy(
            query, boxes=boxes, box_consumption=box_consumption,
            material_consumption=material_consumption,
            finished_receipts=finished_receipts, serials=serials,
        )


class SqlAlchemyProductionUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.production = SqlAlchemyProductionRepository(self.session)
        self.operators = SqlAlchemyOperatorRepository(self.session)
        self.calendar = SqlAlchemyCalendarRepository(self.session)
        self.skills = SqlAlchemySkillRepository(self.session)
        self.plan_projects = SqlAlchemyPlanProjectRepository(self.session)
        self.plan_events = SqlAlchemyPlanEventRepository(self.session)
        self.boxes = SqlAlchemyBoxRepository(self.session)
        self.work = SqlAlchemyWorkRepository(self.session)
        self.outputs = SqlAlchemyOutputRepository(self.session)
        self.work_log = SqlAlchemyWorkLogRepository(self.session)
        self.consumption = SqlAlchemyConsumptionRepository(self.session)
        self.materials = SqlAlchemyMaterialRepository(self.session)
        self.goods = SqlAlchemyFinishedGoodsRepository(self.session)
        self.finished_goods_receipts = SqlAlchemyFinishedGoodsReceiptRepository(self.session)
        self.defects = SqlAlchemyDefectRepository(self.session)
        self.instruments = SqlAlchemyInstrumentRepository(self.session)
        self.serials = SqlAlchemySerialRepository(self.session)
        self.deliveries = SqlAlchemyDeliveryRepository(self.session)
        self.qc = SqlAlchemyQcRepository(self.session)
        self.vswr = SqlAlchemyVswrRepository(self.session)
        self.genealogy = SqlAlchemyGenealogyRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self.committed = True
