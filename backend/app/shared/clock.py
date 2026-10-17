"""Server clock used by HTTP and background entrypoints."""

from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends


MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW)


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC value, treating legacy/test naive values as UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def moscow_today(now: Annotated[datetime, Depends(moscow_now)]) -> date:
    return now.date()
