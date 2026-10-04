"""Producer side of the portal inbox (SUPPLYMAP `notifyDispatch_`, Notify.gs).

A module never resolves e-mails itself: it publishes `notification.requested` with recipient USER IDS
and/or AUDIENCES (a group of people by what they do in the system, not by a role key), and Operations resolves
them to active users when it delivers the message — see `app/modules/identity/audiences.py` for who is in which.
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


# QUAR_TECH_EMAILS / QUAR_SUPPLY_EMAILS (Config.gs:229-230) named people; ERP names AUDIENCES (access-levels plan,
# «Уведомления — аудитории по разделам», owner 04.10.2026): technologist, owner and security alerts go to the
# superadministrators and administrators; supply = people who edit «Лист закупки»; the project manager = «Отгрузка»
# editors; the storekeeper = «Отгрузка — склад» editors. The rule itself is in `app/modules/identity/audiences.py`.
TECHNOLOGIST_AUDIENCE = "technologist"
SUPPLY_AUDIENCE = "supply"
PROJECT_MANAGER_AUDIENCE = "project_manager"
STOREKEEPER_AUDIENCE = "storekeeper"
AUDIENCES = frozenset({TECHNOLOGIST_AUDIENCE, SUPPLY_AUDIENCE, PROJECT_MANAGER_AUDIENCE, STOREKEEPER_AUDIENCE})

# The role keys modules addressed before audiences (`recipient_roles`); a message already sitting in the outbox keeps
# them and resolves to the same audience. «Технолог» was role `admin` (PORTAL_ROLE_LABELS_, Portal.gs:73).
LEGACY_ROLE_AUDIENCES: dict[str, str] = {
    "admin": TECHNOLOGIST_AUDIENCE,
    "buyer": SUPPLY_AUDIENCE,
    "pm": PROJECT_MANAGER_AUDIENCE,
    "receiver": STOREKEEPER_AUDIENCE,
}


def notification_requested(
    *,
    key: str,
    now: datetime,
    title: str,
    body: str,
    kind: str,
    reference: str = "",
    user_ids: Iterable[UUID | None] = (),
    audiences: Iterable[str] = (),
) -> OutboxMessage | None:
    """One inbox event; None when nobody could be addressed (the caller then simply skips it)."""
    ids = tuple(dict.fromkeys(str(user_id) for user_id in user_ids if user_id is not None))
    audience_keys = tuple(dict.fromkeys(str(audience).strip() for audience in audiences if str(audience or "").strip()))
    unknown = [audience for audience in audience_keys if audience not in AUDIENCES]
    if unknown:
        raise ValueError(f"Неизвестная аудитория уведомления: {', '.join(unknown)}")
    if not ids and not audience_keys:
        return None
    return OutboxMessage(
        id=uuid4(),
        topic=NOTIFICATION_TOPIC,
        idempotency_key=f"notify:{key}",
        created_at=now,
        payload={
            "recipient_user_ids": ",".join(ids),
            "recipient_audiences": ",".join(audience_keys),
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
