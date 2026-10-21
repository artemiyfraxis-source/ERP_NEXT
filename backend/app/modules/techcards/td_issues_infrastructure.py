"""Выпуски комплекта ТД (migrations 0129, 0130): строка таблицы `td_issues` и её репозиторий."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from app.shared.persistence import Base

from .td_issues import TdIssue

_ACTIVE = text("status = 'active'")


class TdIssueRow(Base):
    __tablename__ = "td_issues"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'signing', 'active', 'annulled')", name="ck_td_issues_status"),
        CheckConstraint(
            "litera IN ('', 'П', 'О', 'О1', 'О2', 'О3', 'О4', 'О5', 'А', 'Б', 'И')", name="ck_td_issues_litera",
        ),
        CheckConstraint("method_code IN ('85', '88', '80', '81', '02', '03')", name="ck_td_issues_method_code"),
        CheckConstraint("change_no >= 0", name="ck_td_issues_change_no"),
        CheckConstraint("sequence_no > 0", name="ck_td_issues_sequence_no"),
        CheckConstraint("change_no = 0 OR (notice_no <> '' AND reason <> '')", name="ck_td_issues_notice"),
        CheckConstraint("site IN ('', 'ЦАР', 'ЖУК')", name="ck_td_issues_site"),
        UniqueConstraint("project_card_id", "change_no", name="uq_td_issues_card_change"),
        UniqueConstraint("sequence_no", "change_no", name="uq_td_issues_sequence_change"),
        Index(
            "ux_td_issues_active_card", "project_card_id", unique=True, sqlite_where=_ACTIVE, postgresql_where=_ACTIVE,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project_card_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("techcard_projects.id", ondelete="RESTRICT"), nullable=False,
    )
    change_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    org_code: Mapped[str] = mapped_column(String(32), nullable=False, server_default="ФРАК")
    method_code: Mapped[str] = mapped_column(String(8), nullable=False)
    litera: Mapped[str] = mapped_column(String(8), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    etk_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    signatories: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    notice_no: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    reason: Mapped[str] = mapped_column(Text(), nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    annulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workshop: Mapped[str] = mapped_column(Text(), nullable=False, server_default="")
    site: Mapped[str] = mapped_column(Text(), nullable=False, server_default="")

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


_FIELDS = (
    "project_card_id", "change_no", "sequence_no", "org_code", "method_code", "litera", "status", "etk_revision",
    "snapshot", "signatories", "notice_no", "reason", "created_at", "created_by", "approved_at", "annulled_at",
    "workshop", "site",
)


def _domain(row: TdIssueRow) -> TdIssue:
    return TdIssue(id=row.id, version=row.version, **{field: getattr(row, field) for field in _FIELDS})


class SqlAlchemyTdIssueRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, issue_id: UUID) -> TdIssue | None:
        row = self.session.get(TdIssueRow, issue_id)
        return _domain(row) if row is not None else None

    def for_card(self, card_id: UUID) -> list[TdIssue]:
        """Выпуски карты, новые сверху."""
        rows = self.session.scalars(
            select(TdIssueRow).where(TdIssueRow.project_card_id == card_id).order_by(TdIssueRow.change_no.desc())
        )
        return [_domain(row) for row in rows]

    def active_for_card(self, card_id: UUID) -> TdIssue | None:
        row = self.session.scalar(
            select(TdIssueRow).where(TdIssueRow.project_card_id == card_id, TdIssueRow.status == "active")
        )
        return _domain(row) if row is not None else None

    def latest(self) -> TdIssue | None:
        """Последний по времени неаннулированный выпуск любой карты — образец полей автоматического выпуска."""
        row = self.session.scalar(
            select(TdIssueRow).where(TdIssueRow.status != "annulled")
            .order_by(TdIssueRow.created_at.desc(), TdIssueRow.sequence_no.desc(), TdIssueRow.change_no.desc()).limit(1)
        )
        return _domain(row) if row is not None else None

    def next_sequence(self) -> int:
        return int(self.session.scalar(select(func.max(TdIssueRow.sequence_no))) or 0) + 1

    def add(self, issue: TdIssue) -> None:
        self.session.add(TdIssueRow(id=issue.id, version=issue.version, **{field: getattr(issue, field) for field in _FIELDS}))

    def save(self, issue: TdIssue, expected_version: int) -> None:
        row = self.session.get(TdIssueRow, issue.id)
        if row is None or row.version != expected_version:
            raise StaleDataError("td issue changed concurrently")
        row.version = issue.version
        for field in _FIELDS:
            setattr(row, field, getattr(issue, field))

    def flush(self) -> None:
        self.session.flush()
