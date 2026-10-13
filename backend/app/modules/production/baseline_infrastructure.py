"""Хранение базового плана (миграция 0118): снимок и его строки только добавляются — UPDATE запрещён триггером
в базе, и у репозитория нет ни одного пути изменения."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from .baseline import BaselineLine, PlanBaseline, TaskFact, first_start, moment
from .domain import ProductionTask
from .infrastructure import ProductionOutputRow, ProductionTaskDeliveryRow, ProductionWorkTimeLogRow, SqlAlchemyOutputRepository
from .worklog import WorkEvent


class ProductionPlanBaselineRow(Base):
    __tablename__ = "production_plan_baselines"
    __table_args__ = (
        CheckConstraint("number >= 1", name="ck_production_plan_baselines_number"),
        CheckConstraint("skipped >= 0", name="ck_production_plan_baselines_skipped"),
        CheckConstraint("created_by <> ''", name="ck_production_plan_baselines_created_by"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Без FK: фиксировать может и API-ключ (его id — не сотрудник), как в журнале действий.
    created_by_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductionPlanBaselineLineRow(Base):
    __tablename__ = "production_plan_baseline_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_production_plan_baseline_lines_quantity"),
        CheckConstraint("planned_finish >= planned_start", name="ck_production_plan_baseline_lines_window"),
    )
    baseline_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_plan_baselines.id", ondelete="RESTRICT"), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="CASCADE"), primary_key=True, index=True)
    task_number: Mapped[str] = mapped_column(String(64), nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item: Mapped[str] = mapped_column(String(255), nullable=False)
    planned_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_finish: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)


class SqlAlchemyBaselineRepository:
    """Базовые планы: add и чтение. Строки не меняются (нет метода, и триггер в базе)."""

    def __init__(self, session: Session):
        self.session = session

    def next_number(self) -> int:
        value = self.session.scalar(select(func.max(ProductionPlanBaselineRow.number)))
        return 1 if value is None else int(value) + 1

    def add(self, baseline: PlanBaseline) -> None:
        self.session.add(ProductionPlanBaselineRow(
            id=baseline.id, number=baseline.number, created_at=baseline.created_at,
            created_by_id=baseline.created_by_id, created_by=baseline.created_by, skipped=baseline.skipped,
        ))
        self.session.flush()  # UNIQUE номера — в этой транзакции: две одновременные фиксации не получат один номер
        self.session.add_all(ProductionPlanBaselineLineRow(baseline_id=baseline.id, **line.__dict__) for line in baseline.lines)
        self.session.flush()

    def latest(self) -> PlanBaseline | None:
        row = self.session.scalar(select(ProductionPlanBaselineRow).order_by(ProductionPlanBaselineRow.number.desc()).limit(1))
        if row is None:
            return None
        lines = self.session.scalars(
            select(ProductionPlanBaselineLineRow).where(ProductionPlanBaselineLineRow.baseline_id == row.id)
            .order_by(ProductionPlanBaselineLineRow.task_number, ProductionPlanBaselineLineRow.task_id)
        )
        return PlanBaseline(
            id=row.id, number=row.number, created_at=row.created_at, created_by_id=row.created_by_id,
            created_by=row.created_by, skipped=row.skipped,
            lines=tuple(BaselineLine(
                task_id=line.task_id, task_number=line.task_number, project=line.project, item=line.item,
                planned_start=line.planned_start, planned_finish=line.planned_finish, quantity=line.quantity,
            ) for line in lines),
        )

    def facts(self, tasks: list[ProductionTask]) -> dict[UUID, TaskFact]:
        """Факт заданий одним проходом: журнал времени, выработка (активная, без исправленных) и «Сдано N шт»."""
        ids = [task.id for task in tasks]
        if not ids:
            return {}
        log = ProductionWorkTimeLogRow
        starts: dict[UUID, list[tuple[UUID, datetime]]] = defaultdict(list)
        cancelled: set[UUID] = set()
        for task_id, session_id, event, occurred_at in self.session.execute(
            select(log.task_id, log.session_id, log.event, log.occurred_at)
            .where(log.task_id.in_(ids), log.event.in_((WorkEvent.START.value, WorkEvent.CANCEL.value)))
        ):
            if event == WorkEvent.CANCEL.value:
                cancelled.add(session_id)
            else:
                starts[task_id].append((session_id, occurred_at))
        outputs: dict[UUID, list] = defaultdict(list)
        for output in self.session.scalars(
            select(ProductionOutputRow).where(ProductionOutputRow.task_id.in_(ids), SqlAlchemyOutputRepository._active())
        ):
            outputs[output.task_id].append(output)
        deliveries: dict[UUID, list] = defaultdict(list)
        for delivery in self.session.scalars(
            select(ProductionTaskDeliveryRow).where(
                ProductionTaskDeliveryRow.task_id.in_(ids), ProductionTaskDeliveryRow.cancelled_at.is_(None),
            )
        ):
            deliveries[delivery.task_id].append(delivery)
        return {task.id: _fact(task, starts[task.id], cancelled, outputs[task.id], deliveries[task.id]) for task in tasks}


def _fact(task: ProductionTask, starts, cancelled, outputs, deliveries) -> TaskFact:
    # Начало: журнал времени; работа до журнала (0105) — начало сданной выработки; площадка — первая сдача.
    moments = [at for at in (first_start(starts, cancelled),) if at is not None]
    moments += [output.started_at for output in outputs]
    moments += [delivery.delivered_at for delivery in deliveries]
    started_at = min(moments, key=moment) if moments else None
    if task.handed_to:
        done = Decimal(sum(delivery.quantity for delivery in deliveries))
        finishes = [delivery.delivered_at for delivery in deliveries]
    else:
        final = task.final_operation
        good = sum((output.good_quantity for output in outputs if output.operation_id == final.id), Decimal(0))
        per_product = task.operation_units(final.id, 1)
        done = good / per_product if per_product else good
        finishes = [output.finished_at for output in outputs if output.operation_id == final.id]
    return TaskFact(
        status=task.status, started_at=started_at,
        finished_at=max(finishes, key=moment) if finishes else None, done_quantity=done,
    )

