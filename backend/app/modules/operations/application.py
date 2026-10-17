"""Reliable notification consumer, inbox commands and closing a project task by hand."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.clock import as_utc
from app.shared.events import AuditEvent, OutboxMessage
from app.shared.identity import Actor
from app.shared.notifications import LEGACY_ROLE_AUDIENCES

from .background import BackgroundHealth, Heartbeat, PendingTopic, assess
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
    def delete(self, email: str, notification_id: UUID) -> bool: ...
    def delete_many(self, email: str, *, ids: tuple[UUID, ...] = (), only_read: bool = False) -> int: ...


class RecipientDirectory(Protocol):
    """Active identity users behind the ids and audiences a producer addressed (identity's read port)."""
    def emails_for_users(self, user_ids: tuple[UUID, ...]) -> Sequence[str]: ...
    def emails_for_audiences(self, audiences: tuple[str, ...]) -> Sequence[str]: ...


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


class BackgroundReadModel(Protocol):
    def heartbeats(self) -> Mapping[str, Heartbeat]: ...
    def pending_topics(self) -> Sequence[PendingTopic]: ...
    def outbox_health(self) -> dict: ...


class DeadLetterStore(Protocol):
    def dead_letter_for_update(self, message_id: UUID) -> dict | None: ...
    def requeue_dead_letter(self, message_id: UUID, now: datetime) -> None: ...


class OperationsUnitOfWork(Protocol):
    operations: BackgroundReadModel
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


def background_health(now: datetime, uow: OperationsUnitOfWork) -> tuple[BackgroundHealth, dict]:
    """Audit U08: the worker's verdict (heartbeats + the age of the queue) and the outbox counters, one read."""
    with uow:
        heartbeats = uow.operations.heartbeats()
        pending = uow.operations.pending_topics()
        counts = uow.operations.outbox_health()
    return assess(heartbeats, pending, as_utc(now)), counts


class DeadLetterNotFound(LookupError):
    pass


class DeadLetterRetryRefused(ValueError):
    pass


@dataclass(frozen=True)
class RetryDeadLetterCommand:
    message_id: UUID
    actor: Actor
    now: datetime


class RetryUnitOfWork(Protocol):
    operations: DeadLetterStore
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


def retry_dead_letter(command: RetryDeadLetterCommand, uow: RetryUnitOfWork) -> bool:
    """Put a dead-lettered outbox message back into the queue, unchanged (audit U08: «повторить» is a command, never a
    payload edit in the database). Idempotent: a message that is already back in the queue is left alone and nothing is
    journalled — a double click, or two administrators at once, retry it once. Returns True when it was requeued."""
    with uow:
        state = uow.operations.dead_letter_for_update(command.message_id)
        if state is None:
            raise DeadLetterNotFound("Фоновое задание не найдено.")
        if state["published"]:
            raise DeadLetterRetryRefused("Задание уже выполнено — повторять нечего.")
        if not state["dead"]:
            return False
        uow.operations.requeue_dead_letter(command.message_id, command.now)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=command.message_id, event_type="operations.outbox_retried",
            actor=command.actor, occurred_at=command.now,
            details={"topic": state["topic"], "attempts": str(state["attempts"]), "error": state["error"][:300]},
        ))
        uow.commit()
        return True


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


def delete_notification(recipient_email: str, notification_id: UUID, uow: UnitOfWork) -> int:
    """Delete one item of the recipient's own inbox; the unread count after it. KeyError: not his or not there."""
    with uow:
        if not uow.notifications.delete(recipient_email, notification_id):
            raise KeyError(f"Notification not found: {notification_id}")
        uow.commit()
        return uow.notifications.unread_count(recipient_email)


def delete_notifications(
    recipient_email: str, *, ids: tuple[UUID, ...] = (), only_read: bool = False, uow: UnitOfWork,
) -> tuple[int, int]:
    """Clear the recipient's own inbox: the chosen ids, only what he has read, or everything.

    Returns (deleted, unread left). Ids that are not his are skipped, never touched.
    """
    with uow:
        deleted = uow.notifications.delete_many(recipient_email, ids=ids, only_read=only_read)
        uow.commit()
        return deleted, uow.notifications.unread_count(recipient_email)


def _split(value: object) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )


def _recipient_emails(payload: dict, uow: UnitOfWork) -> tuple[str, ...] | None:
    """`notifyRecipients_` (Notify.gs:10-19) over explicit e-mails, user ids and audiences.

    None: the event names nobody at all (a broken producer, poison). An empty tuple: it names users
    that are gone, inactive or in no such audience; SUPPLYMAP drops that silently (`if (!list.length)`).
    A message published before audiences carries role keys (`recipient_roles`): the four that producers used
    resolve to the same audiences; any other key names nobody.
    """
    emails = _split(payload.get("recipients"))
    raw_ids = _split(payload.get("recipient_user_ids"))
    audiences = _split(payload.get("recipient_audiences"))
    legacy_roles = _split(payload.get("recipient_roles"))
    if not emails and not raw_ids and not audiences and not legacy_roles:
        return None
    try:
        user_ids = tuple(UUID(value) for value in raw_ids)
    except ValueError as error:
        raise NotificationValidationError("Некорректный идентификатор адресата") from error
    resolved = list(emails)
    if user_ids:
        resolved.extend(uow.recipients.emails_for_users(user_ids))
    for role in legacy_roles:
        if role not in LEGACY_ROLE_AUDIENCES:
            logger.warning("Notification names role %r, which has no audience; nobody is addressed by it", role)
    audiences = tuple(dict.fromkeys((
        *audiences, *(LEGACY_ROLE_AUDIENCES[role] for role in legacy_roles if role in LEGACY_ROLE_AUDIENCES),
    )))
    if audiences:
        resolved.extend(uow.recipients.emails_for_audiences(audiences))
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
