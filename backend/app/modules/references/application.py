"""Transactional replacement of reference maps and technology snapshots."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .cables import DIAMETERS, CableKind, CableRow, auto_kind, cable_mark, cable_rows, clean_diameter, diameter_index
from .domain import EDITABLE_TABS, MAP_KINDS, MAX_ROW_EDITS, SHEET_TABLE_TABS, _clean_cell, normalized_cable_key, pair_is_blank, ReferenceEntry, ReferenceKind, ReferenceValidationError, TechOperationRecord, TechOperationTab, validate_entries
from .technology import TECH_SNAPSHOT_SCHEMA_VERSION, schema_display_text, schema_keys, sync_schema_fields


class ReferenceRepository(Protocol):
    def list(self) -> Sequence[ReferenceEntry]: ...
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None: ...


class TechOperationRepository(Protocol):
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> Sequence[TechOperationRecord]: ...
    def counts(self) -> dict[TechOperationTab, int]: ...
    def get(self, record_id: UUID) -> TechOperationRecord | None: ...
    def save(self, record: TechOperationRecord) -> None: ...
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime, keep_tabs: tuple[TechOperationTab, ...] = (), preserve_erp_edits: bool = False) -> dict[str, int]: ...
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
    # The maps this actor may change (None = all). The others keep what is stored: supplier and country maps are the
    # buyer's (portalCanWork_), ГОСТ→AWG belongs to «Справочник» (erpref).
    kinds: frozenset[ReferenceKind] | None = None


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
    keep_other_tabs: bool = False  # partial load (mirror ПРОВ/МАТ): every tab not in the payload stays as it is
    preserve_erp_edits: bool = False  # mirror reload: records edited/created in ERP are neither overwritten nor deleted


def mirror_keys(records) -> list[str]:
    """Identity of each mirror row: hash of (tab, display, sort_key) plus the occurrence number among equal ones,
    so twin rows get distinct stable keys. The key is stored on the record and does not change when it is edited."""
    seen: Counter = Counter()
    keys = []
    for item in records:
        base = f"{item.tab.value}\x1f{item.display_text.strip()}\x1f{str(item.sort_key or item.display_text).strip()}"
        keys.append(hashlib.sha1(f"{base}\x1f{seen[base]}".encode("utf-8")).hexdigest())
        seen[base] += 1
    return keys


def _audit(event_type: str, actor: Actor, now: datetime, details: dict, uow: UnitOfWork) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=UUID("00000000-0000-0000-0000-000000000010"),
        event_type=event_type, actor=actor, occurred_at=now, details=details,
    ))


def replace_maps(command: ReplaceMapsCommand, uow: UnitOfWork) -> tuple[ReferenceEntry, ...]:
    kinds = frozenset(MAP_KINDS) if command.kinds is None else command.kinds
    given = {
        ReferenceKind.SUPPLIER: command.suppliers,
        ReferenceKind.COUNTRY: command.countries,
        ReferenceKind.GOST_AWG: command.gost_awg,
    }
    fresh = tuple(
        ReferenceEntry.create(kind, pair.key, pair.value, position)
        for kind in (ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY, ReferenceKind.GOST_AWG) if kind in kinds
        for position, pair in enumerate(pair for pair in given[kind] if not pair_is_blank(pair.key, pair.value))
    )
    with uow:
        kept = tuple(entry for entry in uow.references.list() if entry.kind not in kinds)
        entries = kept + fresh
        validate_entries(entries)
        uow.references.replace(entries)
        _audit("references.maps_replaced", command.actor, command.now, {
            name: sum(1 for entry in entries if entry.kind is kind)
            for name, kind in (
                ("suppliers", ReferenceKind.SUPPLIER), ("countries", ReferenceKind.COUNTRY),
                ("gost_awg", ReferenceKind.GOST_AWG),
            )
        }, uow)
        uow.commit()
    return entries


def replace_snapshot(command: ReplaceSnapshotCommand, uow: UnitOfWork) -> int:
    return replace_snapshot_detailed(command, uow)[0]


def replace_snapshot_detailed(command: ReplaceSnapshotCommand, uow: UnitOfWork) -> tuple[int, dict[str, int]]:
    """(records written, {tab: mirror records skipped because they were edited in ERP})."""
    if command.schema_version != TECH_SNAPSHOT_SCHEMA_VERSION:
        raise ReferenceValidationError(
            f"Ожидается схема БД.Технология v{TECH_SNAPSHOT_SCHEMA_VERSION}"
        )
    records = tuple(TechOperationRecord.create(
        tab=item.tab, display_text=item.display_text, search_text=item.search_text,
        source_sheet=item.source_sheet, sort_key=item.sort_key, fields=item.fields, mirror_key=key,
    ) for item, key in zip(command.records, mirror_keys(command.records)))
    # БД.ПРОВ / БД.МАТ are replaced only when the snapshot carries them (rows or an explicit table).
    present = {item.tab for item in command.records} | set(command.declared_tabs)
    keep_tabs = tuple(tab for tab in (tuple(TechOperationTab) if command.keep_other_tabs else SHEET_TABLE_TABS) if tab not in present)
    with uow:
        skipped = uow.tech_operations.replace(
            records, keep_tabs=keep_tabs, schema_version=command.schema_version,
            source_updated_at=command.source_updated_at, imported_at=command.now,
            preserve_erp_edits=command.preserve_erp_edits,
        ) or {}
        details = {"schema_version": command.schema_version, "records": len(records)}
        if command.preserve_erp_edits:
            details["skipped_edited_in_erp"] = sum(skipped.values())
        _audit("references.tech_snapshot_replaced", command.actor, command.now, details, uow)
        uow.commit()
    return len(records), skipped


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
        if record.tab not in EDITABLE_TABS:
            raise ReferenceValidationError("База только для просмотра")
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
        # A sheet column edit also moves the schema value the generator reads (Название → opName).
        edited = {edit.field for edit in command.edits}
        synced = sync_schema_fields(record.tab.value, fields)
        keys = schema_keys(record.tab.value)
        fields = {key: (synced[key] if key in keys and key not in edited else value) for key, value in synced.items()}
        display = schema_display_text(record.tab.value, fields) or display
        updated = replace(record, fields=fields, display_text=display, edited_in_erp=True)
        updated = replace(updated, normalized_search=_search_text(updated))
        uow.tech_operations.save(updated)
        _audit("references.tech_record_edited", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
            "fields": [edit.field for edit in command.edits],
        }, uow)
        uow.commit()
    return updated


def create_record(command: CreateRecordCommand, uow: UnitOfWork) -> TechOperationRecord:
    if command.tab not in EDITABLE_TABS:
        raise ReferenceValidationError("База только для просмотра")
    fields = {str(key).strip(): _clean_cell(value) for key, value in command.fields.items() if str(key).strip()}
    filled = [value for value in fields.values() if value]
    if not filled:
        raise ReferenceValidationError("Заполни хотя бы одно поле")
    fields = sync_schema_fields(command.tab.value, fields)
    display = schema_display_text(command.tab.value, fields) or filled[0]
    record = TechOperationRecord.create(
        tab=command.tab, display_text=display, search_text="", source_sheet="",
        sort_key=display, fields=fields, edited_in_erp=True,
    )
    with uow:
        uow.tech_operations.save(record)
        _audit("references.tech_record_created", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
        }, uow)
        uow.commit()
    return record


# --- «Кабели и провода»: the three lists, moving a БД.ПРОВ row between them, D1·D2·D3 of a coax cable ---

def cable_overrides(entries: Sequence[ReferenceEntry]) -> dict[str, CableKind]:
    return {entry.normalized_key: CableKind(entry.value) for entry in entries if entry.kind == ReferenceKind.CABLE_KIND}


def list_cables(uow: UnitOfWork) -> list[CableRow]:
    """Every БД.ПРОВ row with its list, and the D-table cables without a БД.ПРОВ row, sorted by the cable key so a
    family («RG58», «RG-58 A/U», «RG-58 C/U») stands together."""
    with uow:
        wires = uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10_000)
        diameters = uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)
        overrides = cable_overrides(uow.references.list())
    return sorted(cable_rows(wires, diameters, overrides), key=lambda row: (row.key, row.cable))


@dataclass(frozen=True)
class SetCableKindCommand:
    record_id: UUID
    kind: CableKind
    actor: Actor
    now: datetime


def set_cable_kind(command: SetCableKindCommand, uow: UnitOfWork) -> CableRow:
    """Moves a БД.ПРОВ row to a list. The choice is stored by the cable's mark in the `cable_kind` map: it survives a
    mirror reload and leaves the row's cells to the mirror. Moving it back to its automatic list removes the entry."""
    with uow:
        record = uow.tech_operations.get(command.record_id)
        if record is None or record.tab != TechOperationTab.WIRE:
            raise LookupError("Кабель не найден в БД.ПРОВ")
        mark = cable_mark(record)
        key = normalized_cable_key(mark)
        diameters = uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)
        auto, _reason = auto_kind(record.fields, mark, frozenset(diameter_index(diameters)))
        entries = [entry for entry in uow.references.list()
                   if not (entry.kind == ReferenceKind.CABLE_KIND and entry.normalized_key == key)]
        if command.kind != auto:
            position = max((entry.position for entry in entries if entry.kind == ReferenceKind.CABLE_KIND), default=-1) + 1
            entries.append(ReferenceEntry.create(ReferenceKind.CABLE_KIND, mark, command.kind.value, position))
        uow.references.replace(tuple(entries))
        _audit("references.cable_kind_set", command.actor, command.now, {
            "record_id": str(record.id), "mark": mark, "kind": command.kind.value, "manual": command.kind != auto,
        }, uow)
        uow.commit()
    [row] = [item for item in cable_rows([record], diameters, cable_overrides(entries)) if item.record is not None]
    return row


@dataclass(frozen=True)
class SetCoaxDiametersCommand:
    cable: str
    dia_id: UUID | None  # the D-table row the screen showed (None — the cable had none)
    old: dict[str, str]
    value: dict[str, str]
    actor: Actor
    now: datetime


def set_coax_diameters(command: SetCoaxDiametersCommand, uow: UnitOfWork) -> TechOperationRecord:
    """D1·D2·D3 of a coax cable from «Коаксиальные кабели» (owner 04.10.2026; the SUPPLYMAP portal keeps СПР.КАБ
    view-only, ERP edits it here). The row edited is the one the generator uses for the name (the last of its key); a
    cable without one gets a new D-table row named as the cable, so the generator finds it by that name. Values are
    numbers in mm or empty; the old values must match (stale otherwise). The row is marked edited in ERP, so a mirror
    reload keeps it."""
    key = normalized_cable_key(command.cable)
    values = {name: clean_diameter(name, command.value.get(name, "")) for name in DIAMETERS}
    with uow:
        current = diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)).get(key)
        if (current.id if current else None) != command.dia_id:
            raise StaleRecordError("Диаметры этого кабеля уже поменяли — откройте его заново")
        if current is None:
            if not any(values.values()):
                raise ReferenceValidationError("Заполните хотя бы один диаметр")
            cable = str(command.cable).strip()
            # After every sheet row («00001»…): the list order is the generator's, a new name must not reorder twins.
            record = TechOperationRecord.create(
                tab=TechOperationTab.KAB_COAX, display_text=cable, search_text="", source_sheet="",
                sort_key=f"99999 {cable}", fields={"Кабель": cable, **values}, edited_in_erp=True,
            )
            event = "references.tech_record_created"
        else:
            stale = [name for name in DIAMETERS
                     if str(current.fields.get(name, "")).strip() != str(command.old.get(name, "")).strip()]
            if stale:
                raise StaleRecordError(f"{', '.join(stale)}: уже поменяли — откройте кабель заново")
            record = replace(current, fields={**current.fields, **values}, edited_in_erp=True)
            record = replace(record, normalized_search=_search_text(record))
            event = "references.tech_record_edited"
        uow.tech_operations.save(record)
        _audit(event, command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value, "fields": list(DIAMETERS),
        }, uow)
        uow.commit()
    return record
