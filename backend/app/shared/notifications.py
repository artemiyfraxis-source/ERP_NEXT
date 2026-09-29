"""Producer side of the portal inbox (SUPPLYMAP `notifyDispatch_`, Notify.gs).

A module never resolves e-mails itself: it publishes `notification.requested` with recipient USER IDS
and/or identity ROLE keys, and Operations resolves them to active users when it delivers the message.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from .events import OutboxMessage

NOTIFICATION_TOPIC = "notification.requested"


class NotificationOutbox(Protocol):
    def add(self, message: OutboxMessage) -> None: ...
    def by_idempotency_key(self, idempotency_key: str) -> OutboxMessage | None: ...


# QUAR_TECH_EMAILS / QUAR_SUPPLY_EMAILS (Config.gs:229-230) named people; ERP names identity roles.
# «Технолог» is the label of role `admin` (PORTAL_ROLE_LABELS_, Portal.gs:73; the technologist in
# QUAR_TECH_EMAILS has role admin in STAFF_BY_EMAIL) — `engineer` is «Инженер». Решено агентом — проверить.
TECHNOLOGIST_ROLE = "admin"
SUPPLY_ROLE = "buyer"
PROJECT_MANAGER_ROLE = "pm"
STOREKEEPER_ROLE = "receiver"


def notification_requested(
    *,
    key: str,
    now: datetime,
    title: str,
    body: str,
    kind: str,
    reference: str = "",
    user_ids: Iterable[UUID | None] = (),
    roles: Iterable[str] = (),
) -> OutboxMessage | None:
    """One inbox event; None when nobody could be addressed (the caller then simply skips it)."""
    ids = tuple(dict.fromkeys(str(user_id) for user_id in user_ids if user_id is not None))
    role_keys = tuple(dict.fromkeys(str(role).strip() for role in roles if str(role or "").strip()))
    if not ids and not role_keys:
        return None
    return OutboxMessage(
        id=uuid4(),
        topic=NOTIFICATION_TOPIC,
        idempotency_key=f"notify:{key}",
        created_at=now,
        payload={
            "recipient_user_ids": ",".join(ids),
            "recipient_roles": ",".join(role_keys),
            "type": kind,
            "reference": reference,
            "title": title,
            "body": body,
        },
    )


def publish_notification(outbox: NotificationOutbox, message: OutboxMessage | None) -> bool:
    """Adds the event unless it is empty or was already published under the same key."""
    if message is None or outbox.by_idempotency_key(message.idempotency_key) is not None:
        return False
    outbox.add(message)
    return True
