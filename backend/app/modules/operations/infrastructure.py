"""SQL persistence for the portal inbox and operational read models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import logging

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    case,
    delete,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.modules.identity.audience_directory import audience_emails, user_emails
from app.shared.clock import as_utc
from app.shared.persistence import (
    AuditEventRow,
    Base,
    OutboxMessageRow,
    SqlAlchemyAuditRepository,
    SqlAlchemyOutboxRepository,
)

from .background import Heartbeat, PendingTopic
from .domain import Notification, ProjectTask, ProjectTaskStatus


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


class WorkerHeartbeatRow(Base):
    """One row per job group of the background worker (audit U08): its last start, success, failure and run time."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="ck_worker_heartbeats_duration"),
        CheckConstraint("failures >= 0", name="ck_worker_heartbeats_failures"),
    )

    job_group: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


def _aware(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None


class SqlAlchemyWorkerHeartbeats:
    """The worker's stamp. Each call is its own short transaction, so a stamp never rides on (or rolls back with) the
    job's own work; a stamp that cannot be written is logged and never stops the worker."""

    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def record(self, group: str, started_at: datetime, duration_ms: int, error: str | None) -> bool:
        try:
            with self.sessions() as session:
                row = session.get(WorkerHeartbeatRow, group)
                if row is None:
                    row = WorkerHeartbeatRow(job_group=group, duration_ms=0, failures=0)
                    session.add(row)
                row.started_at = started_at
                row.duration_ms = max(int(duration_ms), 0)
                if error is None:
                    row.success_at = started_at
                    row.failures = 0
                else:
                    row.failure_at = started_at
                    row.error = error[:1000]
                    row.failures = (row.failures or 0) + 1
                session.commit()
            return True
        except Exception:  # noqa: BLE001 — the stamp is diagnostics; the job itself already ran
            logging.warning("Worker heartbeat %s was not recorded", group, exc_info=True)
            return False


class ProjectTaskRow(Base):
    """Задачи модулям по проекту (ARCH-TRACK-3, C2); одно событие журнала даёт не больше одной задачи вида."""

    __tablename__ = "project_tasks"
    __table_args__ = (
        UniqueConstraint("kind", "source_event_id", name="ux_project_tasks_kind_source_event"),
        CheckConstraint("status IN ('open', 'done', 'cancelled')", name="ck_project_tasks_status"),
        Index("ix_project_tasks_project", "project"),
        Index("ix_project_tasks_module_status", "module", "status"),
        Index("ix_project_tasks_status_kind", "status", "kind"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


class SqlAlchemyProjectTaskRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProjectTaskRow) -> ProjectTask:
        return ProjectTask(
            id=row.id, project=row.project, item_index=row.item_index, module=row.module, kind=row.kind,
            title=row.title, source_event_id=row.source_event_id, source_type=row.source_type,
            subject_id=row.subject_id, status=ProjectTaskStatus(row.status), created_at=row.created_at,
            closed_at=row.closed_at, closed_by=row.closed_by,
        )

    def add(self, task: ProjectTask) -> None:
        self.session.add(ProjectTaskRow(**{**task.__dict__, "status": task.status.value}))

    def save(self, task: ProjectTask) -> None:
        row = self.session.get(ProjectTaskRow, task.id)
        if row is None:
            raise KeyError(f"Project task not found: {task.id}")
        row.status = task.status.value
        row.closed_at = task.closed_at
        row.closed_by = task.closed_by

    def get(self, task_id: UUID) -> ProjectTask | None:
        row = self.session.get(ProjectTaskRow, task_id)
        return self._domain(row) if row is not None else None

    def by_source(self, kind: str, source_event_id: UUID) -> ProjectTask | None:
        row = self.session.scalar(select(ProjectTaskRow).where(
            ProjectTaskRow.kind == kind, ProjectTaskRow.source_event_id == source_event_id,
        ))
        return self._domain(row) if row is not None else None

    def open_for(self, kind: str, project: str, item_index: str) -> ProjectTask | None:
        row = self.session.scalar(select(ProjectTaskRow).where(
            ProjectTaskRow.kind == kind, ProjectTaskRow.project == project,
            ProjectTaskRow.item_index == item_index, ProjectTaskRow.status == ProjectTaskStatus.OPEN.value,
        ).limit(1))
        return self._domain(row) if row is not None else None

    def open_of_kinds(self, kinds: tuple[str, ...]) -> list[ProjectTask]:
        if not kinds:
            return []
        rows = self.session.scalars(select(ProjectTaskRow).where(
            ProjectTaskRow.status == ProjectTaskStatus.OPEN.value, ProjectTaskRow.kind.in_(kinds),
        ).order_by(ProjectTaskRow.created_at, ProjectTaskRow.id))
        return [self._domain(row) for row in rows]

    def list(
        self, *, project: str = "", module: str = "", status: str = "", limit: int = 500,
    ) -> list[ProjectTask]:
        """Задачи по возрастанию времени — очередь: старая сверху. Пустой фильтр — без условия."""
        statement = select(ProjectTaskRow)
        if project:
            statement = statement.where(ProjectTaskRow.project == project)
        if module:
            statement = statement.where(ProjectTaskRow.module == module)
        if status:
            statement = statement.where(ProjectTaskRow.status == status)
        rows = self.session.scalars(
            statement.order_by(ProjectTaskRow.created_at, ProjectTaskRow.id).limit(limit)
        )
        return [self._domain(row) for row in rows]


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

    def delete(self, email: str, notification_id: UUID) -> bool:
        """Remove one inbox item of this recipient; another user's id is simply not found."""
        row = self.session.scalar(select(NotificationRow).where(
            NotificationRow.id == notification_id,
            NotificationRow.recipient_email == email.casefold(),
        ))
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def delete_many(self, email: str, *, ids: tuple[UUID, ...] = (), only_read: bool = False) -> int:
        """Remove the recipient's own items: the given ids, and/or only the read ones; nothing given = his whole inbox."""
        statement = delete(NotificationRow).where(NotificationRow.recipient_email == email.casefold())
        if ids:
            statement = statement.where(NotificationRow.id.in_(ids))
        if only_read:
            statement = statement.where(NotificationRow.read_at.is_not(None))
        return int(self.session.execute(statement).rowcount or 0)


class SqlAlchemyRecipientDirectory:
    """Resolves addressed ids/audiences through identity's read port; a deactivated user gets nothing."""

    def __init__(self, session: Session):
        self.session = session

    def emails_for_users(self, user_ids: tuple[UUID, ...]) -> list[str]:
        return user_emails(self.session, user_ids)

    def emails_for_audiences(self, audiences: tuple[str, ...]) -> list[str]:
        return audience_emails(self.session, audiences)


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


    def heartbeats(self) -> dict[str, Heartbeat]:
        return {row.job_group: Heartbeat(
            group=row.job_group,
            started_at=_aware(row.started_at),
            success_at=_aware(row.success_at),
            failure_at=_aware(row.failure_at),
            error=row.error,
            duration_ms=row.duration_ms,
            failures=row.failures,
        ) for row in self.session.scalars(select(WorkerHeartbeatRow))}

    def pending_topics(self) -> list[PendingTopic]:
        """Topics with messages neither published nor dead-lettered: how many and since when (the oldest)."""
        rows = self.session.execute(
            select(OutboxMessageRow.topic, func.count(), func.min(OutboxMessageRow.created_at))
            .where(OutboxMessageRow.published_at.is_(None), OutboxMessageRow.dead_lettered_at.is_(None))
            .group_by(OutboxMessageRow.topic)
        )
        return [PendingTopic(topic=str(topic), count=int(count), oldest_at=as_utc(oldest)) for topic, count, oldest in rows]

    def dead_letter_for_update(self, message_id: UUID) -> dict | None:
        row = self.session.get(OutboxMessageRow, message_id, with_for_update=True)
        if row is None:
            return None
        return {
            "id": row.id,
            "topic": row.topic,
            "attempts": row.attempt_count,
            "error": row.last_error or "",
            "published": row.published_at is not None,
            "dead": row.dead_lettered_at is not None,
        }

    def requeue_dead_letter(self, message_id: UUID, now: datetime) -> None:
        """Back into the queue as it is — the payload is never touched; a fresh set of attempts, due at once."""
        row = self.session.get(OutboxMessageRow, message_id, with_for_update=True)
        if row is None or row.dead_lettered_at is None or row.published_at is not None:
            raise KeyError(f"Outbox message is not dead-lettered: {message_id}")
        row.dead_lettered_at = None
        row.attempt_count = 0
        row.next_attempt_at = now


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
        self.project_tasks = SqlAlchemyProjectTaskRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
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
