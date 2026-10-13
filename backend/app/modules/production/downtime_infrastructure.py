"""Простои с причинами (migration 0117): строка ORM и хранилище; правила — в downtime.py."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, or_, select, text, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from .downtime import COMMENT_LIMIT, Downtime, DowntimeConflict, DowntimeReason

TABLE = "production_downtimes"
REASONS = tuple(reason.value for reason in DowntimeReason)
OPEN_INDEX = "uq_production_downtimes_open_task"


class ProductionDowntimeRow(Base):
    __tablename__ = TABLE
    __table_args__ = (
        CheckConstraint(f"reason IN ({', '.join(repr(item) for item in REASONS)})", name="ck_production_downtimes_reason"),
        CheckConstraint("reason <> 'other' OR length(trim(comment)) > 0", name="ck_production_downtimes_other_comment"),
        CheckConstraint(f"length(comment) <= {COMMENT_LIMIT}", name="ck_production_downtimes_comment_length"),
        CheckConstraint(
            "ended_at IS NULL OR (ended_at >= started_at AND ended_by_id IS NOT NULL)", name="ck_production_downtimes_end",
        ),
        CheckConstraint("ended_by_id IS NULL OR ended_at IS NOT NULL", name="ck_production_downtimes_ended_by"),
        Index(
            OPEN_INDEX, "task_id", unique=True,
            sqlite_where=text("ended_at IS NULL"), postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_production_downtimes_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("production_tasks.id", ondelete="SET NULL", name="fk_production_downtimes_task_id"),
    )
    operation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("production_operations.id", ondelete="SET NULL", name="fk_production_downtimes_operation_id"),
    )
    operator_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_production_downtimes_operator_id"),
    )
    operator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(String(COMMENT_LIMIT), nullable=False, default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_production_downtimes_started_by_id"),
        nullable=False,
    )
    started_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    task_number: Mapped[str] = mapped_column(String(64), nullable=False)
    item: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_production_downtimes_ended_by_id"),
    )
    ended_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


_FIELDS = tuple(Downtime.__dataclass_fields__)


def _domain(row: ProductionDowntimeRow) -> Downtime:
    values = {name: getattr(row, name) for name in _FIELDS}
    return Downtime(**{**values, "reason": DowntimeReason(row.reason)})


class SqlAlchemyDowntimeRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, downtime: Downtime) -> None:
        self.session.add(ProductionDowntimeRow(**{**downtime.__dict__, "reason": downtime.reason.value}))
        self.session.flush()

    def get(self, downtime_id: UUID) -> Downtime | None:
        row = self.session.get(ProductionDowntimeRow, downtime_id)
        return _domain(row) if row is not None else None

    def open_for_task(self, task_id: UUID) -> Downtime | None:
        row = self.session.scalar(select(ProductionDowntimeRow).where(
            ProductionDowntimeRow.task_id == task_id, ProductionDowntimeRow.ended_at.is_(None),
        ))
        return _domain(row) if row is not None else None

    def open_all(self, limit: int = 1_000) -> list[Downtime]:
        rows = self.session.scalars(select(ProductionDowntimeRow).where(ProductionDowntimeRow.ended_at.is_(None))
                                    .order_by(ProductionDowntimeRow.started_at).limit(limit))
        return [_domain(row) for row in rows]

    def overlapping(self, since: datetime, until: datetime, limit: int = 5_000) -> list[Downtime]:
        """Простои, задевшие [since, until): начаты до конца периода и не закончены до его начала."""
        row = ProductionDowntimeRow
        rows = self.session.scalars(select(row).where(
            row.started_at < until, or_(row.ended_at.is_(None), row.ended_at > since),
        ).order_by(row.started_at, row.id).limit(limit))
        return [_domain(item) for item in rows]

    def close(self, downtime: Downtime) -> None:
        """Пишет конец, только если простой ещё открыт: второе одновременное «Закончить» получает отказ."""
        result = self.session.execute(update(ProductionDowntimeRow).where(
            ProductionDowntimeRow.id == downtime.id, ProductionDowntimeRow.ended_at.is_(None),
        ).values(ended_at=downtime.ended_at, ended_by_id=downtime.ended_by_id, ended_by_name=downtime.ended_by_name))
        if result.rowcount != 1:
            raise DowntimeConflict("Простой уже закончен")
