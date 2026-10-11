"""Shared SQLAlchemy metadata and append-only persistence adapters."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid, event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, SessionTransaction, mapped_column
from sqlalchemy.orm.exc import StaleDataError

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


class StockVersionConflict(StaleDataError):
    """A stock row (procurement line, «Со склада» cover, warehouse lot) changed after this transaction read it.

    The compare-and-swap of `add()` lost: nothing of the command may be written, the caller rolls back and the API
    answers 409 (after its single retry). It subclasses StaleDataError only so the existing 409 / retry / outbox
    handlers keep catching it; callers name this type, never the SQLAlchemy one."""


_READ_VERSIONS = "erp_read_versions"


def track_read_versions(row_class: type) -> None:
    """Remember, per database transaction, the version each `row_class` row had when the session first read it.

    `get()` hands out a domain snapshot and lets the ORM row go; `add()` fetches the row again. Without this record a
    commit by someone else in between would be the version `add()` checks against, and the stale snapshot would
    overwrite it (audit F01). The record outlives the row in the identity map and is dropped when the transaction
    ends."""

    def remember(row: Any, session: Session | None) -> None:
        version = inspect(row).dict.get("version")
        if session is not None and version is not None:
            session.info.setdefault(_READ_VERSIONS, {}).setdefault((type(row), row.id), version)

    @event.listens_for(row_class, "load")
    def _loaded(row, context):
        remember(row, context.session)

    @event.listens_for(row_class, "refresh")
    def _refreshed(row, context, attrs):
        if attrs is None or "version" in attrs:
            remember(row, context.session)


def check_read_version(session: Session, stored: Any, incoming_version: int) -> None:
    """Compare-and-swap before `add()` copies a snapshot onto its stored row: the row must still have the version this
    transaction read it at, and the snapshot may not carry an older version than the row (a stale snapshot written
    after a newer one). A concurrent commit that lands after this check is refused by the ORM's version-guarded
    UPDATE at flush."""
    read = session.info.get(_READ_VERSIONS, {}).get((type(stored), stored.id))
    if (read is not None and stored.version != read) or incoming_version < stored.version:
        raise StockVersionConflict(
            f"{stored.__tablename__} {stored.id}: прочитана версия {read}, сейчас {stored.version}, "
            f"записывается {incoming_version}"
        )


def note_written_version(session: Session, stored: Any) -> None:
    """After `add()`: the transaction's own write is the version its later reads and writes compare against."""
    session.info.setdefault(_READ_VERSIONS, {})[(type(stored), stored.id)] = stored.version


@event.listens_for(Session, "after_transaction_end")
def _forget_read_versions(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(_READ_VERSIONS, None)


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

    def is_open(self, topic: str, idempotency_key: str) -> bool:
        """Whether a message can still be delivered (unpublished and not dead-lettered)."""
        row_id = self.session.scalar(
            select(OutboxMessageRow.id).where(
                OutboxMessageRow.topic == topic,
                OutboxMessageRow.idempotency_key == idempotency_key,
                OutboxMessageRow.published_at.is_(None),
                OutboxMessageRow.dead_lettered_at.is_(None),
            )
        )
        return row_id is not None

    def defer(self, message_id: UUID, retry_at: datetime) -> None:
        """Delay an open message without counting a failed delivery attempt."""
        row = self.session.get(OutboxMessageRow, message_id)
        if row is None:
            raise KeyError(f"Outbox message not found: {message_id}")
        if row.published_at is not None or row.dead_lettered_at is not None:
            raise ValueError(f"Outbox message is no longer open: {message_id}")
        row.next_attempt_at = retry_at

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
    from app.modules.techcards import assembly_drafts_infrastructure as _assembly_drafts  # noqa: F401

    Base.metadata.create_all(engine)
