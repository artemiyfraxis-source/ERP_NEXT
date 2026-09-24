from datetime import datetime

import pytest

from app.modules.operations.domain import (
    Notification,
    NotificationValidationError,
    normalize_recipient,
)


def test_notification_normalizes_recipient_and_limits_legacy_body_size():
    notification = Notification.create(
        recipient_email=" Staff@Example.RU ",
        created_at=datetime(2026, 9, 22, 12, 0),
        kind="status",
        reference="SM-1",
        title="Статус изменён",
        body="x" * 2000,
        dedupe_key="event:staff",
    )
    assert notification.recipient_email == "staff@example.ru"
    assert len(notification.body) == 1500


def test_invalid_recipient_is_rejected():
    with pytest.raises(NotificationValidationError):
        normalize_recipient("not-an-email")
