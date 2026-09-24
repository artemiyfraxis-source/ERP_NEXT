"""Transaction-friendly audit and integration event records."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .identity import Actor


@dataclass(frozen=True)
class AuditEvent:
    id: UUID
    aggregate_id: UUID
    event_type: str
    actor: Actor
    occurred_at: datetime
    details: dict[str, str]


@dataclass(frozen=True)
class OutboxMessage:
    id: UUID
    topic: str
    idempotency_key: str
    created_at: datetime
    payload: dict[str, str | None]
