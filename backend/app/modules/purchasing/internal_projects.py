"""«Внутренние проекты» (stage option projects.internal): projects of the company itself, numbered «ВП-001».

A request or a procurement line refers to one through `project_reference`, like to a commercial «591К.3»; the project's
purpose decides the GRN prefix. Counts of lines and requests come from the workflows port, never from here."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.requests.domain import PURPOSE_ALIASES, STATIC_PURPOSES

from app.shared import stage_options

from . import settings as purchasing_settings
from .infrastructure import InternalProjectRow

PREFIX = "ВП-"
_NUMBER = re.compile(r"ВП-(\d+)")


class InternalProjectError(ValueError):
    """A refusal with an HTTP status: 404 not found, 409 conflict, 422 invalid data."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def allowed_purposes(session: Session) -> tuple[str, ...]:
    """With the option requests.form on the purposes directory decides; off, the fixed list as before."""
    if not stage_options.enabled("requests.form"):
        return STATIC_PURPOSES
    return tuple(purchasing_settings.load(session)["purposes"])


def normalize_purpose(value: Any, allowed: tuple[str, ...] = STATIC_PURPOSES) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise InternalProjectError("Назначение обязательно")
    text = PURPOSE_ALIASES.get(text.casefold(), text)
    by_key = {item.casefold(): item for item in allowed}
    if text.casefold() not in by_key:
        raise InternalProjectError(f"Назначение «{text}» не из списка")
    return by_key[text.casefold()]


def _name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise InternalProjectError("Название обязательно")
    return name


def next_number(session: Session) -> str:
    highest = 0
    for (number,) in session.execute(select(InternalProjectRow.number)):
        match = _NUMBER.fullmatch(number or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{PREFIX}{highest + 1:03d}"


def serialize(row: InternalProjectRow) -> dict[str, Any]:
    return {
        "id": str(row.id), "number": row.number, "name": row.name, "purpose": row.purpose,
        "responsible_id": str(row.responsible_id) if row.responsible_id else None, "note": row.note,
        "closed": row.closed_at is not None, "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "version": row.version,
    }


def get(session: Session, project_id: UUID) -> InternalProjectRow:
    row = session.get(InternalProjectRow, project_id)
    if row is None:
        raise InternalProjectError("Внутренний проект не найден", 404)
    return row


def list_rows(session: Session) -> list[InternalProjectRow]:
    """Open first, then closed; by number inside each group."""
    rows = list(session.scalars(select(InternalProjectRow)))
    return sorted(rows, key=lambda row: (row.closed_at is not None, row.number))


def open_rows(session: Session) -> list[InternalProjectRow]:
    return [row for row in list_rows(session) if row.closed_at is None]


def create(session: Session, data: dict[str, Any], actor_id: UUID | None, now: datetime) -> InternalProjectRow:
    row = InternalProjectRow(
        id=uuid4(), number=next_number(session), name=_name(data.get("name")),
        purpose=normalize_purpose(data.get("purpose"), allowed_purposes(session)), responsible_id=data.get("responsible_id"),
        note=_clean(data.get("note")), version=1, created_at=now, created_by=actor_id,
    )
    session.add(row)
    session.flush()
    return row


def update(
    session: Session, project_id: UUID, version: int, data: dict[str, Any], actor_id: UUID | None, now: datetime,
) -> InternalProjectRow:
    row = get(session, project_id)
    if row.version != version:
        raise InternalProjectError("Проект уже изменили — обновите и повторите", 409)
    if "name" in data:
        row.name = _name(data["name"])
    if "purpose" in data:
        row.purpose = normalize_purpose(data["purpose"], allowed_purposes(session))
    if "responsible_id" in data:
        row.responsible_id = data["responsible_id"]
    if "note" in data:
        row.note = _clean(data["note"])
    row.version += 1
    row.updated_at, row.updated_by = now, actor_id
    session.flush()
    return row


def set_closed(
    session: Session, project_id: UUID, closed: bool, actor_id: UUID | None, now: datetime,
) -> InternalProjectRow:
    row = get(session, project_id)
    if closed == (row.closed_at is not None):
        raise InternalProjectError("Проект уже закрыт" if closed else "Проект уже открыт", 409)
    row.closed_at = now if closed else None
    row.version += 1
    row.updated_at, row.updated_by = now, actor_id
    session.flush()
    return row
