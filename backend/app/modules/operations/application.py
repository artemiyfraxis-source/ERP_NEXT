"""Reliable notification consumer and inbox commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID

from app.shared.events import OutboxMessage

from .domain import Notification, NotificationValidationError, normalize_recipient


class PoisonNotificationMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


class NotificationRepository(Protocol):
    def add(self, notification: Notification) -> Notification: ...
    def list_for(self, email: str, limit: int) -> Sequence[Notification]: ...
    def mark_read(self, email: str, ids: tuple[UUID, ...], now: datetime) -> int: ...


class OutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str, *, max_attempts: int = 5) -> None: ...


class UnitOfWork(Protocol):
    notifications: NotificationRepository
    outbox: OutboxRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class MarkReadCommand:
    recipient_email: str
    ids: tuple[UUID, ...]
    now: datetime


def mark_notifications_read(command: MarkReadCommand, uow: UnitOfWork) -> int:
    with uow:
        unread = uow.notifications.mark_read(
            command.recipient_email,
            command.ids,
            command.now,
        )
        uow.commit()
    return unread


def dispatch_next_notification(now: datetime, uow: UnitOfWork) -> int | None:
    with uow:
        message = uow.outbox.next_unpublished("notification.requested", now)
        if message is None:
            return None
        try:
            recipients = tuple(
                email.strip()
                for email in str(message.payload.get("recipients") or "").replace(";", ",").split(",")
                if email.strip()
            )
            if not recipients:
                raise NotificationValidationError("Адресаты уведомления не указаны")
            normalized_recipients = tuple(dict.fromkeys(
                normalize_recipient(recipient) for recipient in recipients
            ))
            created = 0
            for recipient in normalized_recipients:
                notification = Notification.create(
                    recipient_email=recipient,
                    created_at=message.created_at,
                    kind=str(message.payload.get("type") or "info"),
                    reference=str(message.payload.get("reference") or ""),
                    title=str(message.payload.get("title") or ""),
                    body=str(message.payload.get("body") or ""),
                    dedupe_key=f"{message.id}:{recipient}",
                )
                uow.notifications.add(notification)
                created += 1
            uow.outbox.mark_published(message.id, now)
            uow.commit()
            return created
        except NotificationValidationError as error:
            uow.outbox.mark_failed(message.id, now, str(error), max_attempts=1)
            uow.commit()
            raise PoisonNotificationMessage(message.id, str(error)) from error
