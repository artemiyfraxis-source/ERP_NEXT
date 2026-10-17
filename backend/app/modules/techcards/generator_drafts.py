"""Черновик генератора ТК на сервере (владелец 11.10.2026): the unfinished generator form survives another computer or
browser, not only a tab switch.

The browser keeps its own copy (localStorage) and sends the form here a couple of seconds after the person stops
typing. A draft belongs to one person and one project position (КБ): only that person reads, saves or deletes it. Every
save names the version it was made over (`base_version`); a save over a version someone else already replaced is
refused (409) and the newer draft stays — the browser decides what to keep. The first save of a position (or a save
after the draft was deleted elsewhere) creates it at version 1. No audit event per save: a draft is a scratch pad, not a
record of work."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.shared.clock import MOSCOW

from .generator_drafts_infrastructure import MAX_PAYLOAD_CHARS, GeneratorDraftRow

# What the API takes: the form as the database stores it (JSON text with non-ASCII escaped), well under the column's
# own limit — a real generator form is a few kilobytes.
MAX_PAYLOAD_TEXT = 512 * 1024
assert MAX_PAYLOAD_TEXT <= MAX_PAYLOAD_CHARS


class GeneratorDraftTooLarge(ValueError):
    """The form is larger than MAX_PAYLOAD_TEXT (413); the message is for the person."""


class GeneratorDraftConflict(Exception):
    """The draft was saved elsewhere after `base_version` (409); `current` is the draft that stays."""

    def __init__(self, current: GeneratorDraft):
        super().__init__(conflict_message(current))
        self.current = current


@dataclass(frozen=True)
class GeneratorDraft:
    project: str
    item_index: str
    payload: dict[str, Any]
    build_fingerprint: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SaveGeneratorDraftCommand:
    user_id: UUID
    project: str
    item_index: str
    payload: dict[str, Any]
    build_fingerprint: str
    base_version: int
    now: datetime


def moscow_time(moment: datetime) -> datetime:
    # SQLite gives the stored moment back without its zone: it was written in Moscow time (moscow_now).
    return moment.replace(tzinfo=MOSCOW) if moment.tzinfo is None else moment.astimezone(MOSCOW)


def conflict_message(current: GeneratorDraft) -> str:
    return f"Черновик изменён в другом месте в {moscow_time(current.updated_at):%H:%M}"


def _domain(row: GeneratorDraftRow) -> GeneratorDraft:
    return GeneratorDraft(
        project=row.project, item_index=row.item_index, payload=deepcopy(row.payload),
        build_fingerprint=row.build_fingerprint, version=row.version,
        created_at=moscow_time(row.created_at), updated_at=moscow_time(row.updated_at),
    )


def _position(project: str, item_index: str) -> tuple[str, str]:
    return project.strip(), item_index.strip()


def _find(session, user_id: UUID, project: str, item_index: str) -> GeneratorDraftRow | None:
    return session.scalars(select(GeneratorDraftRow).where(
        GeneratorDraftRow.user_id == user_id,
        GeneratorDraftRow.project == project,
        GeneratorDraftRow.item_index == item_index,
    )).one_or_none()


def get_generator_draft(
    user_id: UUID, project: str, item_index: str, uow_factory: Callable[[], Any],
) -> GeneratorDraft | None:
    project, item_index = _position(project, item_index)
    with uow_factory() as uow:
        row = _find(uow.session, user_id, project, item_index)
        return None if row is None else _domain(row)


def _check_size(payload: dict[str, Any]) -> None:
    size = len(json.dumps(payload))
    if size > MAX_PAYLOAD_TEXT:
        raise GeneratorDraftTooLarge(
            f"Черновик слишком большой: {size // 1024} КБ при пределе {MAX_PAYLOAD_TEXT // 1024} КБ. "
            "Он сохранён только на этом компьютере."
        )


def _save(uow, command: SaveGeneratorDraftCommand, project: str, item_index: str) -> GeneratorDraft | None:
    """The saved draft, or None when a concurrent save over the same version won the race."""
    session = uow.session
    row = _find(session, command.user_id, project, item_index)
    if row is None:
        row = GeneratorDraftRow(
            id=uuid4(), user_id=command.user_id, project=project, item_index=item_index,
            payload=deepcopy(command.payload), build_fingerprint=command.build_fingerprint, version=1,
            created_at=command.now, updated_at=command.now,
        )
        session.add(row)
        uow.commit()  # a concurrent first save of the position: IntegrityError (UNIQUE), the caller re-reads
        return _domain(row)
    if row.version != command.base_version:
        raise GeneratorDraftConflict(_domain(row))
    # The version in the WHERE: a concurrent save over the same base updates nothing here.
    changed = session.execute(
        update(GeneratorDraftRow)
        .where(GeneratorDraftRow.id == row.id, GeneratorDraftRow.version == command.base_version)
        .values(
            payload=deepcopy(command.payload), build_fingerprint=command.build_fingerprint,
            version=command.base_version + 1, updated_at=command.now,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        return None
    uow.commit()
    return GeneratorDraft(
        project=project, item_index=item_index, payload=deepcopy(command.payload),
        build_fingerprint=command.build_fingerprint, version=command.base_version + 1,
        created_at=moscow_time(row.created_at), updated_at=moscow_time(command.now),
    )


def save_generator_draft(command: SaveGeneratorDraftCommand, uow_factory: Callable[[], Any]) -> GeneratorDraft:
    _check_size(command.payload)
    project, item_index = _position(command.project, command.item_index)
    try:
        with uow_factory() as uow:
            saved = _save(uow, command, project, item_index)
    except IntegrityError:
        current = get_generator_draft(command.user_id, project, item_index, uow_factory)
        if current is None:
            raise  # not the race (a foreign key, a check): a real error
        raise GeneratorDraftConflict(current) from None
    if saved is not None:
        return saved
    current = get_generator_draft(command.user_id, project, item_index, uow_factory)
    if current is None:  # deleted between the read and the update: the next save creates it again
        current = GeneratorDraft(
            project=project, item_index=item_index, payload={}, build_fingerprint="", version=0,
            created_at=command.now, updated_at=command.now,
        )
    raise GeneratorDraftConflict(current)


def delete_generator_draft(user_id: UUID, project: str, item_index: str, uow_factory: Callable[[], Any]) -> bool:
    project, item_index = _position(project, item_index)
    with uow_factory() as uow:
        removed = uow.session.execute(delete(GeneratorDraftRow).where(
            GeneratorDraftRow.user_id == user_id,
            GeneratorDraftRow.project == project,
            GeneratorDraftRow.item_index == item_index,
        )).rowcount
        uow.commit()
        return removed > 0
