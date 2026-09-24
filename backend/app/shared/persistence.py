"""Shared SQLAlchemy metadata and append-only persistence adapters."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .events import AuditEvent, OutboxMessage


class Base(DeclarativeBase):
    pass


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_department: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)


class OutboxMessageRow(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    published_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dead_lettered_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))


class SqlAlchemyAuditRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, event: AuditEvent) -> None:
        self.session.add(
            AuditEventRow(
                id=event.id,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                actor_id=event.actor.id,
                actor_name=event.actor.name,
                actor_department=event.actor.department,
                occurred_at=event.occurred_at,
                details=event.details,
            )
        )


class SqlAlchemyOutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, message: OutboxMessage) -> None:
        self.session.add(
            OutboxMessageRow(
                id=message.id,
                topic=message.topic,
                idempotency_key=message.idempotency_key,
                created_at=message.created_at,
                payload=message.payload,
            )
        )

    def by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None:
        row = self.session.scalar(
            select(OutboxMessageRow).where(
                OutboxMessageRow.idempotency_key == idempotency_key,
            )
        )
        return _outbox_domain(row) if row is not None else None

    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None:
        statement = (
            select(OutboxMessageRow)
            .where(
                OutboxMessageRow.topic == topic,
                OutboxMessageRow.published_at.is_(None),
                OutboxMessageRow.dead_lettered_at.is_(None),
                (
                    OutboxMessageRow.next_attempt_at.is_(None)
                    | (OutboxMessageRow.next_attempt_at <= now)
                ),
            )
            .order_by(OutboxMessageRow.created_at, OutboxMessageRow.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = self.session.scalar(statement)
        return _outbox_domain(row) if row is not None else None

    def mark_published(self, message_id: UUID, published_at: datetime) -> None:
        row = self.session.get(OutboxMessageRow, message_id)
        if row is None:
            raise KeyError(f"Outbox message not found: {message_id}")
        row.published_at = published_at
        row.last_error = None
        row.next_attempt_at = None

    def mark_failed(
        self,
        message_id: UUID,
        failed_at: datetime,
        error: str,
        *,
        max_attempts: int = 5,
    ) -> None:
        row = self.session.get(OutboxMessageRow, message_id)
        if row is None:
            raise KeyError(f"Outbox message not found: {message_id}")
        row.attempt_count += 1
        row.last_error = error[:1000]
        if row.attempt_count >= max_attempts:
            row.dead_lettered_at = failed_at
            row.next_attempt_at = None
            return
        delay_seconds = min(2 ** row.attempt_count, 300)
        row.next_attempt_at = failed_at + timedelta(seconds=delay_seconds)


def _outbox_domain(row: OutboxMessageRow) -> OutboxMessage:
    return OutboxMessage(
        id=row.id,
        topic=row.topic,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        payload=row.payload,
    )


def create_schema(engine: Engine) -> None:
    # Import table owners before materializing the shared metadata.
    from app.modules.identity import infrastructure as _identity  # noqa: F401
    from app.modules.procurement import infrastructure as _procurement  # noqa: F401
    from app.modules.requests import infrastructure as _requests  # noqa: F401
    from app.modules.receiving import infrastructure as _receiving  # noqa: F401
    from app.modules.warehouse import infrastructure as _warehouse  # noqa: F401
    from app.modules.fulfillment import infrastructure as _fulfillment  # noqa: F401
    from app.modules.production import infrastructure as _production  # noqa: F401
    from app.modules.shipping import infrastructure as _shipping  # noqa: F401
    from app.modules.references import infrastructure as _references  # noqa: F401
    from app.modules.operations import infrastructure as _operations  # noqa: F401

    Base.metadata.create_all(engine)
