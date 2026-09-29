"""SQL persistence for the portal inbox and operational read models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, Uuid, case, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.modules.identity.infrastructure import IdentityUserRow
from app.shared.persistence import AuditEventRow, Base, OutboxMessageRow, SqlAlchemyOutboxRepository

from .domain import Notification


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text(), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationsJobMarkRow(Base):
    """ScriptProperties of the SUPPLYMAP owner tick: day gates and dedup marks of the daily jobs."""

    __tablename__ = "operations_job_marks"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyJobMarks:
    def __init__(self, session: Session):
        self.session = session

    def get(self, key: str) -> str | None:
        row = self.session.get(OperationsJobMarkRow, key)
        return row.value if row is not None else None

    def put(self, key: str, value: str, now: datetime) -> None:
        row = self.session.get(OperationsJobMarkRow, key)
        if row is None:
            self.session.add(OperationsJobMarkRow(key=key, value=value, marked_at=now))
            return
        row.value = value
        row.marked_at = now


class SqlAlchemyNotificationRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: NotificationRow) -> Notification:
        return Notification(
            id=row.id,
            recipient_email=row.recipient_email,
            created_at=row.created_at,
            kind=row.kind,
            reference=row.reference,
            title=row.title,
            body=row.body,
            dedupe_key=row.dedupe_key,
            read_at=row.read_at,
        )

    def add(self, notification: Notification) -> Notification:
        existing = self.session.scalar(
            select(NotificationRow).where(NotificationRow.dedupe_key == notification.dedupe_key)
        )
        if existing is not None:
            return self._domain(existing)
        self.session.add(NotificationRow(**notification.__dict__))
        return notification

    def list_for(self, email: str, limit: int) -> list[Notification]:
        rows = self.session.scalars(
            select(NotificationRow)
            .where(NotificationRow.recipient_email == email.casefold())
            .order_by(NotificationRow.created_at.desc(), NotificationRow.id.desc())
            .limit(limit)
        )
        return [self._domain(row) for row in rows]

    def unread_count(self, email: str) -> int:
        return int(self.session.scalar(
            select(func.count()).select_from(NotificationRow).where(
                NotificationRow.recipient_email == email.casefold(),
                NotificationRow.read_at.is_(None),
            )
        ) or 0)

    def mark_read(self, email: str, ids: tuple[UUID, ...], now: datetime) -> int:
        statement = select(NotificationRow).where(
            NotificationRow.recipient_email == email.casefold(),
            NotificationRow.read_at.is_(None),
        )
        if ids:
            statement = statement.where(NotificationRow.id.in_(ids))
        for row in self.session.scalars(statement):
            row.read_at = now
        return self.unread_count(email)


class SqlAlchemyRecipientDirectory:
    """Reads identity users to resolve addressed ids/roles; a deactivated user gets nothing."""

    def __init__(self, session: Session):
        self.session = session

    def emails_for_users(self, user_ids: tuple[UUID, ...]) -> list[str]:
        if not user_ids:
            return []
        return sorted(self.session.scalars(
            select(IdentityUserRow.email).where(
                IdentityUserRow.id.in_(user_ids),
                IdentityUserRow.active.is_(True),
            )
        ))

    def emails_for_roles(self, roles: tuple[str, ...]) -> list[str]:
        if not roles:
            return []
        return sorted(self.session.scalars(
            select(IdentityUserRow.email).where(
                IdentityUserRow.role.in_(roles),
                IdentityUserRow.active.is_(True),
            )
        ))


class SqlAlchemyOperationsRepository:
    def __init__(self, session: Session):
        self.session = session

    def audit(self, *, event_type: str, aggregate_id: UUID | None, limit: int) -> list[dict]:
        statement = select(AuditEventRow)
        if event_type:
            statement = statement.where(AuditEventRow.event_type.contains(event_type))
        if aggregate_id is not None:
            statement = statement.where(AuditEventRow.aggregate_id == aggregate_id)
        rows = list(self.session.scalars(
            statement.order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc()).limit(limit)
        ))
        return [{
            "id": row.id,
            "aggregate_id": row.aggregate_id,
            "event_type": row.event_type,
            "actor": row.actor_name,
            "department": row.actor_department,
            "occurred_at": row.occurred_at,
            "details": row.details,
        } for row in rows]

    def dead_letter_signatures(self) -> list[tuple[str, str, int]]:
        """Dead-lettered outbox messages grouped by topic and error — the «Ошибки» rows with ×N."""
        rows = self.session.execute(
            select(OutboxMessageRow.topic, OutboxMessageRow.last_error, func.count())
            .where(OutboxMessageRow.dead_lettered_at.is_not(None))
            .group_by(OutboxMessageRow.topic, OutboxMessageRow.last_error)
            .order_by(OutboxMessageRow.topic, OutboxMessageRow.last_error)
        )
        return [(str(topic), str(error or ""), int(count)) for topic, error, count in rows]

    def outbox_health(self) -> dict:
        counts = self.session.execute(select(
            func.count().label("total"),
            func.sum(case((OutboxMessageRow.published_at.is_not(None), 1), else_=0)).label("published"),
            func.sum(case((OutboxMessageRow.dead_lettered_at.is_not(None), 1), else_=0)).label("dead"),
            func.sum(case((
                OutboxMessageRow.published_at.is_(None) & OutboxMessageRow.dead_lettered_at.is_(None), 1
            ), else_=0)).label("pending"),
        )).one()
        failures = list(self.session.scalars(
            select(OutboxMessageRow)
            .where(OutboxMessageRow.last_error.is_not(None))
            .order_by(OutboxMessageRow.created_at.desc())
            .limit(20)
        ))
        return {
            "total": int(counts.total or 0),
            "published": int(counts.published or 0),
            "pending": int(counts.pending or 0),
            "dead": int(counts.dead or 0),
            "failures": [{
                "id": row.id,
                "topic": row.topic,
                "attempts": row.attempt_count,
                "error": row.last_error,
                "dead": row.dead_lettered_at is not None,
            } for row in failures],
        }


class SqlAlchemyOperationsUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.notifications = SqlAlchemyNotificationRepository(self.session)
        self.recipients = SqlAlchemyRecipientDirectory(self.session)
        self.operations = SqlAlchemyOperationsRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        self.marks = SqlAlchemyJobMarks(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self.committed = True
