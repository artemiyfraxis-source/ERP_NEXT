"""Transactional replacement of reference maps and technology snapshots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .domain import MAX_ROW_EDITS, SHEET_TABLE_TABS, _clean_cell, ReferenceEntry, ReferenceKind, ReferenceValidationError, TechOperationRecord, TechOperationTab, validate_entries
from .technology import TECH_SNAPSHOT_SCHEMA_VERSION


class ReferenceRepository(Protocol):
    def list(self) -> Sequence[ReferenceEntry]: ...
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None: ...


class TechOperationRepository(Protocol):
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> Sequence[TechOperationRecord]: ...
    def get(self, record_id: UUID) -> TechOperationRecord | None: ...
    def save(self, record: TechOperationRecord) -> None: ...
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
    gost_awg: tuple[ReferencePair, ...]
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
    declared_tabs: tuple[TechOperationTab, ...] = ()


def _audit(event_type: str, actor: Actor, now: datetime, details: dict, uow: UnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=UUID("00000000-0000-0000-0000-000000000010"),
        event_type=event_type, actor=actor, occurred_at=now, details=details,
    ))


def replace_maps(command: ReplaceMapsCommand, uow: UnitOfWork) -> tuple[ReferenceEntry, ...]:
    entries = tuple(
        ReferenceEntry.create(kind, pair.key, pair.value, position)
        for kind, pairs in (
            (ReferenceKind.SUPPLIER, command.suppliers),
            (ReferenceKind.COUNTRY, command.countries),
            (ReferenceKind.GOST_AWG, command.gost_awg),
        )
        for position, pair in enumerate(pairs)
    )
    validate_entries(entries)
    with uow:
        uow.references.replace(entries)
        _audit("references.maps_replaced", command.actor, command.now, {
            "suppliers": len(command.suppliers), "countries": len(command.countries),
            "gost_awg": len(command.gost_awg),
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
    # БД.ПРОВ / БД.МАТ are replaced only when the snapshot carries them (rows or an explicit table).
    present = {item.tab for item in command.records} | set(command.declared_tabs)
    keep_tabs = tuple(tab for tab in SHEET_TABLE_TABS if tab not in present)
    with uow:
        uow.tech_operations.replace(
            records, keep_tabs=keep_tabs, schema_version=command.schema_version,
            source_updated_at=command.source_updated_at, imported_at=command.now,
        )
        _audit("references.tech_snapshot_replaced", command.actor, command.now, {
            "schema_version": command.schema_version, "records": len(records),
        }, uow)
        uow.commit()
    return len(records)


@dataclass(frozen=True)
class FieldEdit:
    field: str
    old: str
    value: str


@dataclass(frozen=True)
class EditRecordCommand:
    record_id: UUID
    edits: tuple[FieldEdit, ...]
    actor: Actor
    now: datetime


@dataclass(frozen=True)
class CreateRecordCommand:
    tab: TechOperationTab
    fields: dict[str, str]
    actor: Actor
    now: datetime


class StaleRecordError(Exception):
    pass


def _search_text(record: TechOperationRecord) -> str:
    return " ".join(f"{record.display_text} {' '.join(record.fields.values())}".casefold().split())


def edit_record(command: EditRecordCommand, uow: UnitOfWork) -> TechOperationRecord:
    if not command.edits or len(command.edits) > MAX_ROW_EDITS:
        raise ReferenceValidationError(f"Правок за раз: от 1 до {MAX_ROW_EDITS}")
    with uow:
        record = uow.tech_operations.get(command.record_id)
        if record is None:
            raise LookupError("Запись справочника не найдена")
        fields = dict(record.fields)
        display = record.display_text
        refused: list[str] = []
        for edit in command.edits:
            if edit.field not in record.fields:
                refused.append(f"{edit.field}: поля нет в записи")
                continue
            try:
                value = _clean_cell(edit.value)
            except ReferenceValidationError as error:
                refused.append(f"{edit.field}: {error}")
                continue
            if fields[edit.field] != str(edit.old or "").strip():
                refused.append(f"{edit.field}: запись уже поменяли — откройте её заново")
                continue
            if display == fields[edit.field] and value:
                display = value
            fields[edit.field] = value
        if refused:
            raise StaleRecordError("Не записано ничего — отклонено полей: " + str(len(refused)) + "; " + "; ".join(refused))
        updated = replace(record, fields=fields, display_text=display)
        updated = replace(updated, normalized_search=_search_text(updated))
        uow.tech_operations.save(updated)
        _audit("references.tech_record_edited", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
            "fields": [edit.field for edit in command.edits],
        }, uow)
        uow.commit()
    return updated


def create_record(command: CreateRecordCommand, uow: UnitOfWork) -> TechOperationRecord:
    fields = {str(key).strip(): _clean_cell(value) for key, value in command.fields.items() if str(key).strip()}
    filled = [value for value in fields.values() if value]
    if not filled:
        raise ReferenceValidationError("Заполни хотя бы одно поле")
    record = TechOperationRecord.create(
        tab=command.tab, display_text=filled[0], search_text="", source_sheet="",
        sort_key=filled[0], fields=fields,
    )
    with uow:
        uow.tech_operations.save(record)
        _audit("references.tech_record_created", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
        }, uow)
        uow.commit()
    return record
