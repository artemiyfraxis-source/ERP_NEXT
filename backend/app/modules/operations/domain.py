"""Pure notification rules extracted from the SUPPLYMAP portal inbox."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4


class NotificationValidationError(ValueError):
    pass


def normalize_recipient(value: str) -> str:
    email = str(value or "").strip().casefold()
    local, separator, domain = email.partition("@")
    if not separator or not local or "." not in domain:
        raise NotificationValidationError("Некорректный адрес уведомления")
    return email


@dataclass(frozen=True)
class Notification:
    id: UUID
    recipient_email: str
    created_at: datetime
    kind: str
    reference: str
    title: str
    body: str
    dedupe_key: str
    read_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        recipient_email: str,
        created_at: datetime,
        kind: str,
        reference: str,
        title: str,
        body: str,
        dedupe_key: str,
    ) -> Notification:
        clean_title = str(title or "").strip()[:200]
        clean_key = str(dedupe_key or "").strip()
        if not clean_title:
            raise NotificationValidationError("Заголовок уведомления обязателен")
        if not clean_key:
            raise NotificationValidationError("Ключ уведомления обязателен")
        return cls(
            id=uuid4(),
            recipient_email=normalize_recipient(recipient_email),
            created_at=created_at,
            kind=str(kind or "info").strip()[:64] or "info",
            reference=str(reference or "").strip()[:255],
            title=clean_title,
            body=str(body or "").strip()[:1500],
            dedupe_key=clean_key[:255],
        )

    def mark_read(self, now: datetime) -> Notification:
        return self if self.read_at is not None else replace(self, read_at=now)
