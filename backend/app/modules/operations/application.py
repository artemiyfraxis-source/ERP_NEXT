"""Reliable notification consumer, inbox commands and closing a project task by hand."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor

from .domain import Notification, NotificationValidationError, ProjectTask, normalize_recipient

logger = logging.getLogger(__name__)


class PoisonNotificationMessage(RuntimeError):
    def __init__(self, message_id: UUID, reason: str):
        super().__init__(reason)
        self.message_id = message_id


class NotificationRepository(Protocol):
    def add(self, notification: Notification) -> Notification: ...
    def list_for(self, email: str, limit: int) -> Sequence[Notification]: ...
    def unread_count(self, email: str) -> int: ...
    def mark_read(self, email: str, ids: tuple[UUID, ...], now: datetime) -> int: ...


class RecipientDirectory(Protocol):
    """Active identity users behind the ids and role keys a producer addressed."""
    def emails_for_users(self, user_ids: tuple[UUID, ...]) -> Sequence[str]: ...
    def emails_for_roles(self, roles: tuple[str, ...]) -> Sequence[str]: ...


class OutboxRepository(Protocol):
    def next_unpublished(self, topic: str, now: datetime) -> OutboxMessage | None: ...
    def mark_published(self, message_id: UUID, published_at: datetime) -> None: ...
    def mark_failed(self, message_id: UUID, failed_at: datetime, error: str, *, max_attempts: int = 5) -> None: ...


class UnitOfWork(Protocol):
    notifications: NotificationRepository
    recipients: RecipientDirectory
    outbox: OutboxRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


class ProjectTaskRepository(Protocol):
    def get(self, task_id: UUID) -> ProjectTask | None: ...
    def save(self, task: ProjectTask) -> None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class ProjectTasksUnitOfWork(Protocol):
    project_tasks: ProjectTaskRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class CloseProjectTaskCommand:
    task_id: UUID
    actor: Actor
    now: datetime


def close_project_task(command: CloseProjectTaskCommand, uow: ProjectTasksUnitOfWork) -> ProjectTask:
    """Закрыть задачу руками (то, что арбитр не видит сам). Права на раздел задачи проверяет API."""
    with uow:
        task = uow.project_tasks.get(command.task_id)
        if task is None:
            raise KeyError(f"Project task not found: {command.task_id}")
        closed = task.close(command.now, command.actor.name)
        uow.project_tasks.save(closed)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=closed.id, event_type="operations.project_task_closed",
            actor=command.actor, occurred_at=command.now,
            details={"kind": closed.kind, "project": closed.project, "item_index": closed.item_index},
        ))
        uow.commit()
        return closed


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


def _split(value: object) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )


def _recipient_emails(payload: dict, uow: UnitOfWork) -> tuple[str, ...] | None:
    """`notifyRecipients_` (Notify.gs:10-19) over explicit e-mails, user ids and identity roles.

    None: the event names nobody at all (a broken producer, poison). An empty tuple: it names users
    that are gone, inactive or hold no such role; SUPPLYMAP drops that silently (`if (!list.length)`).
    """
    emails = _split(payload.get("recipients"))
    raw_ids = _split(payload.get("recipient_user_ids"))
    roles = _split(payload.get("recipient_roles"))
    if not emails and not raw_ids and not roles:
        return None
    try:
        user_ids = tuple(UUID(value) for value in raw_ids)
    except ValueError as error:
        raise NotificationValidationError("Некорректный идентификатор адресата") from error
    resolved = list(emails)
    if user_ids:
        resolved.extend(uow.recipients.emails_for_users(user_ids))
    if roles:
        resolved.extend(uow.recipients.emails_for_roles(roles))
    return tuple(dict.fromkeys(normalize_recipient(email) for email in resolved))


def dispatch_next_notification(now: datetime, uow: UnitOfWork) -> int | None:
    with uow:
        message = uow.outbox.next_unpublished("notification.requested", now)
        if message is None:
            return None
        try:
            normalized_recipients = _recipient_emails(message.payload, uow)
            if normalized_recipients is None:
                raise NotificationValidationError("Адресаты уведомления не указаны")
            if not normalized_recipients:
                logger.info("Notification %s has no active recipient; dropped", message.id)
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
