"""Transactional replacement of reference maps and technology snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import ReferenceEntry, ReferenceKind, ReferenceValidationError, TechOperationRecord, TechOperationTab, validate_entries
from .technology import TECH_SNAPSHOT_SCHEMA_VERSION


class ReferenceRepository(Protocol):
    def list(self) -> Sequence[ReferenceEntry]: ...
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None: ...


class TechOperationRepository(Protocol):
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> Sequence[TechOperationRecord]: ...
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime) -> None: ...
    def metadata(self) -> dict | None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class UnitOfWork(Protocol):
    references: ReferenceRepository
    tech_operations: TechOperationRepository
    audit: AppendOnlyRepository
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def commit(self) -> None: ...


@dataclass(frozen=True)
class ReferencePair:
    key: str
    value: str


@dataclass(frozen=True)
class ReplaceMapsCommand:
    suppliers: tuple[ReferencePair, ...]
    countries: tuple[ReferencePair, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class TechRecordInput:
    tab: TechOperationTab
    display_text: str
    search_text: str
    source_sheet: str
    sort_key: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ReplaceSnapshotCommand:
    schema_version: int
    source_updated_at: datetime | None
    records: tuple[TechRecordInput, ...]
    actor: Actor
    now: datetime


def _audit(event_type: str, actor: Actor, now: datetime, details: dict, uow: UnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=UUID("00000000-0000-0000-0000-000000000010"),
        event_type=event_type, actor=actor, occurred_at=now, details=details,
    ))


def replace_maps(command: ReplaceMapsCommand, uow: UnitOfWork) -> tuple[ReferenceEntry, ...]:
    entries = tuple(
        ReferenceEntry.create(kind, pair.key, pair.value, position)
        for kind, pairs in ((ReferenceKind.SUPPLIER, command.suppliers), (ReferenceKind.COUNTRY, command.countries))
        for position, pair in enumerate(pairs)
    )
    validate_entries(entries)
    with uow:
        uow.references.replace(entries)
        _audit("references.maps_replaced", command.actor, command.now, {
            "suppliers": len(command.suppliers), "countries": len(command.countries),
        }, uow)
        uow.commit()
    return entries


def replace_snapshot(command: ReplaceSnapshotCommand, uow: UnitOfWork) -> int:
    if command.schema_version != TECH_SNAPSHOT_SCHEMA_VERSION:
        raise ReferenceValidationError(
            f"Ожидается схема БД.Технология v{TECH_SNAPSHOT_SCHEMA_VERSION}"
        )
    records = tuple(TechOperationRecord.create(
        tab=item.tab, display_text=item.display_text, search_text=item.search_text,
        source_sheet=item.source_sheet, sort_key=item.sort_key, fields=item.fields,
    ) for item in command.records)
    with uow:
        uow.tech_operations.replace(
            records, schema_version=command.schema_version,
            source_updated_at=command.source_updated_at, imported_at=command.now,
        )
        _audit("references.tech_snapshot_replaced", command.actor, command.now, {
            "schema_version": command.schema_version, "records": len(records),
        }, uow)
        uow.commit()
    return len(records)
