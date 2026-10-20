"""Transactional replacement of reference maps and technology snapshots."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field as dc_field, replace
from datetime import datetime
from typing import Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .cable_structure import (
    MARK_FIELD, MIRRORED_FIELDS, CoaxCard, CoaxNames, HookupWire, WireGauge, alias_dia_rows, cards_from_seed,
    gauge_gost_awg, hookup_from_parse, names_from_articles, parse_hookup, refreshed_hookup, section_key,
    validate_aliases, validate_gauges, vocabulary_cells, vocabulary_changes, written_back_fields,
)
from .cables import (
    DIAMETERS, CableKind, CableRow, auto_kind, cable_mark, cable_rows, clean_diameter, connectors_by_cable, diameter_index,
)
from .domain import BASE_COLUMNS, CUSTOM_COLUMN_TABS, EDITABLE_TABS, MAX_COLUMN_NAME, cable_key, MAP_KINDS, MAX_ROW_EDITS, SHEET_TABLE_TABS, _clean_cell, normalized_cable_key, pair_is_blank, ReferenceEntry, ReferenceKind, ReferenceValidationError, StaleRecordError, TechOperationRecord, TechOperationTab, validate_entries
from .domain import StockArticle, StockLinkChange, StockLinkEntry, StockLinkEvent
from .equipment import (
    BASE_KEY, CODE_KEY, EXTRA_KEYS, INV_KEY, ROW_KEY, base_value as fields_base, field, next_instance_inventory,
    prepare_equipment,
)
from .technology import (
    COAX_CABLE_DIAMETERS_REASON, TECH_SNAPSHOT_SCHEMA_VERSION, is_coax_cable_column, schema_columns, schema_display_text, schema_keys, stored_schema_keys,
    sync_schema_fields, terminal_card_problems,
)


class ReferenceRepository(Protocol):
    def list(self) -> Sequence[ReferenceEntry]: ...
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None: ...


class TechOperationRepository(Protocol):
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> Sequence[TechOperationRecord]: ...
    def counts(self) -> dict[TechOperationTab, int]: ...
    def get(self, record_id: UUID) -> TechOperationRecord | None: ...
    def save(self, record: TechOperationRecord) -> None: ...
    def delete(self, record_id: UUID) -> None: ...
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime, keep_tabs: tuple[TechOperationTab, ...] = (), preserve_erp_edits: bool = False) -> dict[str, int]: ...
    def metadata(self) -> dict | None: ...


class AppendOnlyRepository(Protocol):
    def add(self, item) -> None: ...


class CableStructureRepository(Protocol):
    def gauges(self) -> list[WireGauge]: ...
    def save_gauge(self, gauge: WireGauge) -> None: ...
    def delete_gauge(self, gauge_id: UUID) -> None: ...
    def gauge_usage(self) -> dict[UUID, int]: ...
    def hookups(self) -> list[HookupWire]: ...
    def hookup(self, mark_key: str) -> HookupWire | None: ...
    def save_hookup(self, wire: HookupWire, now: datetime) -> None: ...
    def cards(self) -> list[CoaxCard]: ...
    def replace_cards(self, cards: Sequence[CoaxCard], now: datetime) -> dict[str, int]: ...
    def replace_names(self, names: dict[str, CoaxNames]) -> dict[str, int]: ...


class StockLinkRepository(Protocol):
    """«Склад → база данных» journal: append-only — rows are added and read, never changed."""
    def add(self, entry: StockLinkEntry) -> None: ...
    def list(self, *, record_id: UUID | None = None, lot_id: UUID | None = None, limit: int = 500) -> list[StockLinkEntry]: ...


class OperationTemplateCodes(Protocol):
    """Port (app/workflows/new_operation.py): the codes of the operation card templates (БД.ТК), read in the same
    transaction as the БД.ОП write. A unit of work without it (`operation_templates` absent) skips the template check."""
    def codes(self) -> set[str]: ...


class UnitOfWork(Protocol):
    references: ReferenceRepository
    tech_operations: TechOperationRepository
    cable_structure: CableStructureRepository
    audit: AppendOnlyRepository
    stock_links: StockLinkRepository
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


def _search_text(record: TechOperationRecord) -> str:
    return " ".join(f"{record.display_text} {' '.join(record.fields.values())}".casefold().split())


def _check_terminal_card(tab: TechOperationTab, fields: dict[str, str]) -> None:
    """БД.ТЕР: a card the database CHECKs would refuse (gender, insulation Ø, datasheet) is refused here in words."""
    if tab == TechOperationTab.TERMINAL and (problems := terminal_card_problems(fields)):
        raise ReferenceValidationError("; ".join(problems))


def _check_edit_count(edits: tuple[FieldEdit, ...]) -> None:
    if not edits or len(edits) > MAX_ROW_EDITS:
        raise ReferenceValidationError(f"Правок за раз: от 1 до {MAX_ROW_EDITS}")


def _editable(record_id: UUID, uow: UnitOfWork) -> TechOperationRecord:
    record = uow.tech_operations.get(record_id)
    if record is None:
        raise LookupError("Запись справочника не найдена")
    if record.tab not in EDITABLE_TABS:
        raise ReferenceValidationError("База только для просмотра")
    return record


def _without_empty_cable_columns(tab: TechOperationTab, fields: dict[str, str]) -> dict[str, str]:
    """БД.КОАКС (owner 16.10.2026): a connector row carries no D1–D3 — an emptied one leaves the row's fields."""
    if tab != TechOperationTab.COAX:
        return fields
    return {key: value for key, value in fields.items() if not (is_coax_cable_column(key) and not str(value or "").strip())}


def _edit_in(record_id: UUID, edits: tuple[FieldEdit, ...], now: datetime, uow: UnitOfWork) -> tuple[TechOperationRecord, TechOperationRecord]:
    """The row edit inside an open unit of work (nothing committed): returns the record before and after."""
    record = _editable(record_id, uow)
    fields = dict(record.fields)
    display = record.display_text
    refused: list[str] = []
    stored = stored_schema_keys(record.tab.value)
    own_columns: list[str] | None = None  # the owner's columns: a row that came from a mirror reload may lack one
    for edit in edits:
        if edit.field not in record.fields and own_columns is None:
            own_columns = custom_columns(record.tab, uow)
        known = (edit.field in record.fields or edit.field in stored or edit.field in (own_columns or ())
                 or (record.tab == TechOperationTab.EQUIPMENT and edit.field in EXTRA_KEYS)
                 or (record.tab == TechOperationTab.COAX and edit.field in _coax_card_columns()))
        if not known:
            refused.append(f"{edit.field}: поля нет в записи")
            continue
        try:
            value = _clean_cell(edit.value)
        except ReferenceValidationError as error:
            refused.append(f"{edit.field}: {error}")
            continue
        if fields.get(edit.field, "") != str(edit.old or "").strip():
            refused.append(f"{edit.field}: запись уже поменяли — откройте её заново")
            continue
        if record.tab == TechOperationTab.COAX and is_coax_cable_column(edit.field) and value:
            refused.append(f"{edit.field}: {COAX_CABLE_DIAMETERS_REASON}")
            continue
        if display == fields.get(edit.field) and value:
            display = value
        fields[edit.field] = value
    if refused:
        raise StaleRecordError("Не записано ничего — отклонено полей: " + str(len(refused)) + "; " + "; ".join(refused))
    if record.tab == TechOperationTab.EQUIPMENT:
        others = [item for item in _equipment_rows(uow)]
        before_base = fields_base(record.fields)
        fields = prepare_equipment(fields, others, self_id=record.id, creating=False, previous=record.fields)
        if before_base == record.fields.get(BASE_KEY, "") and fields_base(fields) != fields.get(BASE_KEY):
            fields[BASE_KEY] = fields_base(fields)
    # A sheet column edit also moves the schema value the generator reads (Название → opName); a schema value edited
    # (the generator's «Записать в БД»: terLMinus) goes to its sheet column too, else the sync takes it back.
    edited = {edit.field for edit in edits}
    for key, column in schema_columns(record.tab.value, record.fields).items():
        if key in edited and column not in edited:
            fields[column] = fields[key]
    synced = sync_schema_fields(record.tab.value, fields)
    keys = schema_keys(record.tab.value)
    fields = {key: (fields[key] if key in keys and key in edited else value) for key, value in synced.items()}
    fields = _without_empty_cable_columns(record.tab, fields)
    _check_terminal_card(record.tab, fields)
    if record.tab == TechOperationTab.OPERATION:
        _check_operation_code(record, fields, uow)
    display = schema_display_text(record.tab.value, fields) or display
    updated = replace(record, fields=fields, display_text=display, edited_in_erp=True)
    updated = replace(updated, normalized_search=_search_text(updated))
    uow.tech_operations.save(updated)
    if record.tab == TechOperationTab.WIRE and edited & {MARK_FIELD, *MIRRORED_FIELDS}:
        _follow_wire_edit(uow, record, updated, edited, now)
    return record, updated


# БД.КОАКС (владелец 19.10.2026: «у одного артикула не могут быть разные значения»): строка — разъём × кабель, свойства
# разъёма у всех строк артикула одни. Своё у строки — только кабель и предложение продавца (ссылка, цена, дата цены),
# наименование, примечание и источник.
COAX_ROW_OWN = frozenset({"Артикул", "coaxArticle", "Провод", "coaxWire", "Наименование", "Ссылка", "Цена, ₽", "Дата цены",
                          "Примечание", "Источник данных"})


def _coax_article(fields: Mapping[str, str]) -> str:
    return " ".join(str(fields.get("coaxArticle") or fields.get("Артикул") or "").casefold().split())


def _same_article(updated: TechOperationRecord, edits: tuple[FieldEdit, ...], now: datetime, uow: UnitOfWork) -> int:
    """Пишет правленые свойства разъёма в остальные строки того же артикула (тем же путём, что ячейку); возвращает,
    сколько строк поправлено. Не БД.КОАКС или правка только своих полей строки — ничего."""
    if updated.tab != TechOperationTab.COAX:
        return 0
    names = [edit.field for edit in edits if edit.field not in COAX_ROW_OWN]
    article = _coax_article(updated.fields)
    if not names or not article:
        return 0
    known = set(_coax_card_columns()) | set(custom_columns(updated.tab, uow))
    written = 0
    for sibling in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100_000):
        if sibling.id == updated.id or _coax_article(sibling.fields) != article:
            continue
        follow = tuple(FieldEdit(name, sibling.fields.get(name, ""), updated.fields.get(name, "")) for name in names
                       if (name in sibling.fields or name in known) and sibling.fields.get(name, "") != updated.fields.get(name, ""))
        if follow:
            _edit_in(sibling.id, follow, now, uow)
            written += 1
    return written


def edit_record(command: EditRecordCommand, uow: UnitOfWork) -> TechOperationRecord:
    _check_edit_count(command.edits)
    with uow:
        record, updated = _edit_in(command.record_id, command.edits, command.now, uow)
        _same_article(updated, command.edits, command.now, uow)
        _audit("references.tech_record_edited", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
            "fields": [edit.field for edit in command.edits],
        }, uow)
        uow.commit()
    return updated


# --- «+ Столбец»: the owner's own column of a table (owner 14.10.2026: «добавить свои столбцы») ---

def custom_columns(tab: TechOperationTab, uow: UnitOfWork) -> list[str]:
    """The columns the owner added to the tab, in the order added."""
    references = getattr(uow, "references", None)  # the workflows' units of work carry no maps: no own columns there
    if tab not in CUSTOM_COLUMN_TABS or references is None:
        return []
    prefix = f"{tab.value}:"
    own = [entry for entry in references.list() if entry.kind == ReferenceKind.CUSTOM_COLUMN and entry.key.startswith(prefix)]
    return [entry.value for entry in sorted(own, key=lambda entry: entry.position)]


def _coax_card_columns() -> tuple[str, ...]:
    """БД.КОАКС card columns (a load may bring them before a row has them): a cell of any row takes them."""
    from .importers import COAX_CARD_COLUMNS
    return COAX_CARD_COLUMNS


def _check_column_tab(tab: TechOperationTab) -> None:
    if tab not in CUSTOM_COLUMN_TABS:
        raise ReferenceValidationError("В этой таблице свои столбцы не добавляются")


def _folded(name: str) -> str:
    return " ".join(str(name).casefold().split())


def _taken_names(tab: TechOperationTab, records: Sequence[TechOperationRecord], uow: UnitOfWork) -> set[str]:
    """Every name the tab already uses as a column or a schema value (folded), the owner's own columns included."""
    names = set(BASE_COLUMNS.get(tab, ())) | set(schema_keys(tab.value)) | set(stored_schema_keys(tab.value))
    names |= {name for record in records for name in record.fields} | set(custom_columns(tab, uow))
    if tab == TechOperationTab.COAX:
        from .importers import TECH_TABLES
        names |= set(TECH_TABLES["coax"].card_columns)
    return {_folded(name) for name in names}


@dataclass(frozen=True)
class ColumnCommand:
    tab: TechOperationTab
    name: str
    actor: Actor
    now: datetime


def add_column(command: ColumnCommand, uow: UnitOfWork) -> int:
    """Adds an empty column to every record of the tab in one transaction; the records are marked edited in ERP, so a
    mirror reload keeps the column. Returns the number of records written."""
    _check_column_tab(command.tab)
    name = " ".join(str(command.name or "").split())
    if not name:
        raise ReferenceValidationError("Назовите столбец")
    if len(name) > MAX_COLUMN_NAME:
        raise ReferenceValidationError(f"Название столбца — не длиннее {MAX_COLUMN_NAME} знаков")
    _clean_cell(name)  # a formula is not a name
    with uow:
        records = uow.tech_operations.list(tab=command.tab, query="", limit=100_000)
        if _folded(name) in _taken_names(command.tab, records, uow):
            raise ReferenceValidationError(f"Столбец «{name}» уже есть в таблице")
        if not records:
            raise ReferenceValidationError("В таблице нет записей — столбцу негде жить")
        for record in records:
            uow.tech_operations.save(replace(record, fields={**record.fields, name: ""}, edited_in_erp=True))
        entries = list(uow.references.list())
        position = max((entry.position for entry in entries if entry.kind == ReferenceKind.CUSTOM_COLUMN), default=-1) + 1
        uow.references.replace((*entries, ReferenceEntry.create(
            ReferenceKind.CUSTOM_COLUMN, f"{command.tab.value}:{name}", name, position)))
        _audit("references.column_added", command.actor, command.now, {
            "tab": command.tab.value, "column": name, "records": len(records)}, uow)
        uow.commit()
    return len(records)


def remove_column(command: ColumnCommand, uow: UnitOfWork) -> int:
    """Removes the owner's own column, only when every value in it is empty. Returns the number of records written."""
    _check_column_tab(command.tab)
    with uow:
        name = next((item for item in custom_columns(command.tab, uow) if _folded(item) == _folded(command.name)), None)
        if name is None:
            raise ReferenceValidationError("Удалить можно только свой столбец")
        records = uow.tech_operations.list(tab=command.tab, query="", limit=100_000)
        filled = sum(1 for record in records if str(record.fields.get(name, "")).strip())
        if filled:
            raise ReferenceValidationError(f"В столбце «{name}» есть данные ({filled}) — очистите их, потом удаляйте")
        written = 0
        for record in records:
            if name in record.fields:
                uow.tech_operations.save(replace(record, fields={k: v for k, v in record.fields.items() if k != name}, edited_in_erp=True))
                written += 1
        key = f"{command.tab.value}:{name}"
        uow.references.replace(tuple(entry for entry in uow.references.list()
                                     if not (entry.kind == ReferenceKind.CUSTOM_COLUMN and entry.key == key)))
        _audit("references.column_removed", command.actor, command.now, {
            "tab": command.tab.value, "column": name, "records": written}, uow)
        uow.commit()
    return written


@dataclass(frozen=True)
class SetCoveringGroupCommand:
    record_id: UUID
    group: str  # «ТУТ» / «ТТК» / «Оплётки» / «Гофра» / «Прочее», or "" — back to the automatic one
    actor: Actor
    now: datetime


def set_covering_group(command: SetCoveringGroupCommand, uow: UnitOfWork) -> TechOperationRecord:
    """«Оболочки»: the manual group of a БД.МАТ row — the cell «Раздел оболочек», created on the first move."""
    from .coverings import OVERRIDE_CHOICES, OVERRIDE_FIELD
    group = command.group.strip()
    if group and group not in OVERRIDE_CHOICES:
        raise ReferenceValidationError("Раздел оболочек: " + ", ".join(OVERRIDE_CHOICES))
    with uow:
        record = uow.tech_operations.get(command.record_id)
        if record is None:
            raise LookupError("Запись справочника не найдена")
        if record.tab != TechOperationTab.MATERIAL:
            raise ReferenceValidationError("Раздел оболочек есть только у материалов")
        fields = {**record.fields, OVERRIDE_FIELD: group}
        updated = replace(record, fields=fields, edited_in_erp=True)
        updated = replace(updated, normalized_search=_search_text(updated))
        uow.tech_operations.save(updated)
        _audit("references.covering_group_set", command.actor, command.now,
               {"record_id": str(record.id), "group": group}, uow)
        uow.commit()
    return updated


def coverings_catalog(uow: UnitOfWork) -> dict:
    from .coverings import build
    with uow:
        records = uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=10_000)
    return build([(str(item.id), item.fields) for item in records])


def _equipment_rows(uow: UnitOfWork) -> list[tuple[object, dict[str, str]]]:
    return [(item.id, item.fields) for item in uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=10_000)]


# --- БД.ОП: «Номер» is the operation code, the key that joins the row to its card template (БД.ТК) ---

OPERATION_CODE_KEY = "opNumber"
OPERATION_NAME_KEY = "opName"


def operation_code(fields: Mapping[str, str]) -> str:
    return str(fields.get(OPERATION_CODE_KEY) or "").strip()


def _operation_rows(uow: UnitOfWork) -> list[TechOperationRecord]:
    return list(uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000))


def _check_operation_code(before: TechOperationRecord | None, fields: Mapping[str, str], uow: UnitOfWork) -> None:
    """«Номер» of a БД.ОП row is unique (letter case aside: «cut» next to «CUT» is a typo, not a second operation), and
    a row is not renamed away from a card template only it carries. Read under the transaction of the write."""
    new = operation_code(fields)
    old = operation_code(before.fields) if before is not None else ""
    if new == old:
        return
    others = [item for item in _operation_rows(uow) if before is None or item.id != before.id]
    if new and any(operation_code(item.fields).casefold() == new.casefold() for item in others):
        raise ReferenceValidationError(f"Операция с номером «{new}» уже есть — номер операции должен быть уникальным")
    templates: OperationTemplateCodes | None = getattr(uow, "operation_templates", None)
    if (old and templates is not None and old in templates.codes()
            and not any(operation_code(item.fields) == old for item in others)):
        raise ReferenceValidationError(
            f"Номер «{old}» не меняется: по нему к строке привязана карточка операции, она осталась бы без строки. "
            "Нужна другая операция — добавьте новую"
        )


def create_operation_row_in(code: str, name: str, copy_of: str, uow: UnitOfWork) -> TechOperationRecord:
    """A new БД.ОП row inside an open unit of work (nothing committed): «Номер» = `code`, «Название» = `name`. A copy
    takes every other cell of the first row of `copy_of` (the time norms among them); otherwise the row gets the
    columns of the existing rows, empty. The code's uniqueness is checked by `_create_in` under the same transaction."""
    rows = _operation_rows(uow)
    source = next((item for item in rows if copy_of and operation_code(item.fields) == copy_of), None)
    if source is not None:
        fields = dict(source.fields)
    else:
        fields = {key: "" for key in (rows[0].fields if rows else {})}
    fields.update({OPERATION_CODE_KEY: code, OPERATION_NAME_KEY: name})
    columns = schema_columns(TechOperationTab.OPERATION.value, fields)
    for key, value in ((OPERATION_CODE_KEY, code), (OPERATION_NAME_KEY, name)):
        if key in columns:
            fields[columns[key]] = value
    return _create_in(TechOperationTab.OPERATION, _clean_new_fields(TechOperationTab.OPERATION, fields), uow)


def _clean_new_fields(tab: TechOperationTab, values: dict[str, str]) -> dict[str, str]:
    if tab not in EDITABLE_TABS:
        raise ReferenceValidationError("База только для просмотра")
    return {str(key).strip(): _clean_cell(value) for key, value in values.items() if str(key).strip()}


def _create_in(tab: TechOperationTab, fields: dict[str, str], uow: UnitOfWork) -> TechOperationRecord:
    """A new record inside an open unit of work (nothing committed); `fields` cleaned by `_clean_new_fields`."""
    if tab == TechOperationTab.EQUIPMENT:
        # Number, row and code are checked and given under this same transaction as the save.
        fields = prepare_equipment(fields, _equipment_rows(uow), creating=True)
    if tab == TechOperationTab.COAX and any(is_coax_cable_column(key) and value for key, value in fields.items()):
        raise ReferenceValidationError(COAX_CABLE_DIAMETERS_REASON)
    filled = [value for value in fields.values() if value]
    if not filled:
        raise ReferenceValidationError("Заполни хотя бы одно поле")
    fields = _without_empty_cable_columns(
        tab, {**{name: "" for name in custom_columns(tab, uow)}, **sync_schema_fields(tab.value, fields)})
    _check_terminal_card(tab, fields)
    if tab == TechOperationTab.OPERATION:
        _check_operation_code(None, fields, uow)
    display = schema_display_text(tab.value, fields) or filled[0]
    record = TechOperationRecord.create(
        tab=tab, display_text=display, search_text="", source_sheet="",
        sort_key=display, fields=fields, edited_in_erp=True,
    )
    uow.tech_operations.save(record)
    return record


def create_record(command: CreateRecordCommand, uow: UnitOfWork) -> TechOperationRecord:
    fields = _clean_new_fields(command.tab, command.fields)
    with uow:
        record = _create_in(command.tab, fields, uow)
        _audit("references.tech_record_created", command.actor, command.now, {
            "record_id": str(record.id), "tab": record.tab.value,
        }, uow)
        uow.commit()
    return record


# --- «Склад → база данных»: a stock article tied to, or made into, a record; each event is journaled (T14) ---

@dataclass(frozen=True)
class LinkStockCommand:
    """`record_id` — an existing record (with `edits` — its fields change from the article; without — only tied);
    no `record_id` — a new record in `tab` from `fields`."""
    article: StockArticle
    actor: Actor
    now: datetime
    record_id: UUID | None = None
    tab: TechOperationTab | None = None
    fields: dict[str, str] | None = None
    edits: tuple[FieldEdit, ...] = ()


@dataclass(frozen=True)
class StockLinkResult:
    record: TechOperationRecord
    entry: StockLinkEntry | None  # None: the article was already tied to this record and nothing changed — no new row


def _journal(event: StockLinkEvent, command: LinkStockCommand, record: TechOperationRecord,
             changes: tuple[StockLinkChange, ...], uow: UnitOfWork) -> StockLinkEntry:
    entry = StockLinkEntry(
        id=uuid4(), occurred_at=command.now, event=event, article=command.article, record_id=record.id,
        record_tab=record.tab, record_text=record.display_text, changes=changes,
        actor_id=command.actor.id, actor_name=command.actor.name,
    )
    uow.stock_links.add(entry)
    return entry


def _changes(before: dict[str, str], after: dict[str, str], named: set[str]) -> tuple[StockLinkChange, ...]:
    """What changed, as the person named it: the fields they gave; the schema values derived from them (БД.ТЕР
    «Product Name» → terComponent) only when none of theirs changed — the row reads once, not twice."""
    changed = [key for key, value in after.items() if before.get(key, "") != value]
    own = [key for key in changed if key in named] or changed
    return tuple(StockLinkChange(key, before.get(key, ""), after[key]) for key in own)


def link_stock_article(command: LinkStockCommand, uow: UnitOfWork) -> StockLinkResult:
    """One event of «склад → база данных», written with the record change in one transaction: the record and its
    journal row are saved together or not at all. Tying the same article to the same record again writes nothing."""
    article = {"lot_id": str(command.article.lot_id), "grn": command.article.grn, "source": "warehouse"}
    if command.record_id is None:
        if command.tab is None:
            raise ReferenceValidationError("Укажите базу новой записи")
        fields = _clean_new_fields(command.tab, command.fields or {})
        with uow:
            record = _create_in(command.tab, fields, uow)
            changes = _changes({}, record.fields, set(fields))
            entry = _journal(StockLinkEvent.CREATED, command, record, changes, uow)
            _audit("references.tech_record_created", command.actor, command.now,
                   {"record_id": str(record.id), "tab": record.tab.value, **article}, uow)
            uow.commit()
        return StockLinkResult(record, entry)
    if command.edits:
        _check_edit_count(command.edits)
    with uow:
        record = _editable(command.record_id, uow)
        if command.tab is not None and record.tab != command.tab:
            raise ReferenceValidationError("Запись из другой базы")
        changes: tuple[StockLinkChange, ...] = ()
        if command.edits:
            before, record = _edit_in(command.record_id, command.edits, command.now, uow)
            _same_article(record, command.edits, command.now, uow)
            changes = _changes(before.fields, record.fields, {edit.field for edit in command.edits})
        if changes:
            entry = _journal(StockLinkEvent.UPDATED, command, record, changes, uow)
            _audit("references.tech_record_edited", command.actor, command.now, {
                "record_id": str(record.id), "tab": record.tab.value, "fields": [change.field for change in changes],
                **article}, uow)
        elif uow.stock_links.list(record_id=record.id, lot_id=command.article.lot_id, limit=1):
            return StockLinkResult(record, None)
        else:
            entry = _journal(StockLinkEvent.LINKED, command, record, (), uow)
        uow.commit()
    return StockLinkResult(record, entry)


def stock_links(uow: UnitOfWork, *, record_id: UUID | None = None, limit: int = 500) -> list[StockLinkEntry]:
    """The journal, newest first: of one record, or all of it."""
    with uow:
        return uow.stock_links.list(record_id=record_id, limit=limit)


def duplicate_equipment(record_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork) -> TechOperationRecord:
    """«Ещё такой же»: the unit's fields copied, the next instance number («ОБ.070.8» → «ОБ.070.9»), a new row number.
    The source and every other record keep their numbers; the tool code stays with the original."""
    with uow:
        source = uow.tech_operations.get(record_id)
        if source is None:
            raise LookupError("Запись справочника не найдена")
        if source.tab != TechOperationTab.EQUIPMENT:
            raise ReferenceValidationError("Копировать можно только оборудование")
        rows = _equipment_rows(uow)
        fields = {key: value for key, value in source.fields.items() if key not in (CODE_KEY, ROW_KEY)}
        fields[INV_KEY] = next_instance_inventory(field(source.fields, INV_KEY), [item[1] for item in rows])
        fields = prepare_equipment(fields, rows, creating=True)
        fields = sync_schema_fields(source.tab.value, fields)
        display = schema_display_text(source.tab.value, fields) or source.display_text
        record = TechOperationRecord.create(
            tab=source.tab, display_text=display, search_text="", source_sheet="",
            sort_key=display, fields=fields, edited_in_erp=True,
        )
        uow.tech_operations.save(record)
        _audit("references.tech_record_created", actor, now, {
            "record_id": str(record.id), "tab": record.tab.value, "copy_of": str(source.id),
        }, uow)
        uow.commit()
    return record


# --- «Кабели и провода»: the three lists, moving a БД.ПРОВ row between them, D1·D2·D3 of a coax cable ---

def cable_overrides(entries: Sequence[ReferenceEntry]) -> dict[str, CableKind]:
    return {entry.normalized_key: CableKind(entry.value) for entry in entries if entry.kind == ReferenceKind.CABLE_KIND}


def list_cables(uow: UnitOfWork) -> list[CableRow]:
    """Every БД.ПРОВ row with its list, the D-table cables without a БД.ПРОВ row and the coax cards without either,
    sorted by the cable key so a family («RG58», «RG-58 A/U», «RG-58 C/U») stands together."""
    return cable_catalog(uow).rows


@dataclass(frozen=True)
class CableCatalog:
    rows: list[CableRow]
    gauges: list[WireGauge]
    hookups: dict[str, HookupWire]  # by mark key
    usage: dict[UUID, int]  # gauge id → hook-up wires on it
    columns: list[str] = dc_field(default_factory=list)  # the owner's own БД.ПРОВ columns, after the base ones
    connectors: dict[str, list[TechOperationRecord]] = dc_field(default_factory=dict)  # cable key → БД.КОАКС rows


def cable_catalog(uow: UnitOfWork) -> CableCatalog:
    with uow:
        wires = uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10_000)
        diameters = uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=10_000)
        overrides = cable_overrides(uow.references.list())
        cards = {card.name_key: card for card in uow.cable_structure.cards()}
        gauges = uow.cable_structure.gauges()
        hookups = {wire.mark_key: wire for wire in uow.cable_structure.hookups()}
        usage = uow.cable_structure.gauge_usage()
        own_columns = custom_columns(TechOperationTab.WIRE, uow)
        connectors = connectors_by_cable(uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=10_000))
    rows = sorted(cable_rows(wires, diameters, overrides, cards), key=lambda row: (row.key, row.cable))
    return CableCatalog(rows, gauges, hookups, usage, own_columns, connectors)


# --- «Сечения AWG ↔ мм²» ---

@dataclass(frozen=True)
class SaveGaugeCommand:
    gauge_id: UUID | None  # None — a new size row
    awg: str
    section_mm2: str
    conductor_d_mm: str
    actor: Actor
    now: datetime


def _sync_gost_awg(uow: UnitOfWork, before: WireGauge | None, after: WireGauge | None) -> None:
    """Keeps the workshop ГОСТ section → AWG map (`gost_awg`, read by `gost_awg_map` and the ETK build) in step with
    the size table: the entry of the old section goes if it was this row's, the new section gets this row's AWG."""
    entries = list(uow.references.list())
    drop: set[str] = set()
    if before is not None and before.awg_number is not None:
        key = section_key(before.section_mm2)
        drop |= {key} if any(entry.kind == ReferenceKind.GOST_AWG and entry.normalized_key == key and entry.value == str(before.awg_number) for entry in entries) else set()
    fresh = gauge_gost_awg([after]) if after is not None else {}
    drop |= set(fresh)
    kept = [entry for entry in entries if not (entry.kind == ReferenceKind.GOST_AWG and entry.normalized_key in drop)]
    if fresh:
        position = max((entry.position for entry in kept if entry.kind == ReferenceKind.GOST_AWG), default=-1) + 1
        kept += [ReferenceEntry.create(ReferenceKind.GOST_AWG, key.replace(".", ","), str(awg), position)
                 for key, awg in fresh.items()]
    if [(entry.kind, entry.normalized_key, entry.value) for entry in kept] != [(entry.kind, entry.normalized_key, entry.value) for entry in entries]:
        validate_entries(tuple(kept))
        uow.references.replace(tuple(kept))


def save_gauge_in(command: SaveGaugeCommand, uow: UnitOfWork, details: Mapping[str, str] | None = None) -> WireGauge:
    """The size row save inside an open unit of work (nothing committed), audited; `details` go into the audit."""
    gauges = uow.cable_structure.gauges()
    before = next((gauge for gauge in gauges if gauge.id == command.gauge_id), None)
    if command.gauge_id is not None and before is None:
        raise LookupError("Сечение не найдено — откройте таблицу заново")
    gauge = WireGauge.create(
        id=command.gauge_id, awg=command.awg, section_mm2=command.section_mm2,
        conductor_d_mm=command.conductor_d_mm,
        position=before.position if before else max((item.position for item in gauges), default=-1) + 1,
    )
    validate_gauges([item for item in gauges if item.id != gauge.id] + [gauge])
    uow.cable_structure.save_gauge(gauge)
    _sync_gost_awg(uow, before, gauge)
    _audit("references.wire_gauge_saved", command.actor, command.now, {
        "gauge_id": str(gauge.id), "awg": gauge.awg, "section_mm2": section_key(gauge.section_mm2),
        "created": before is None, **(details or {}),
    }, uow)
    return gauge


def save_gauge(command: SaveGaugeCommand, uow: UnitOfWork) -> WireGauge:
    with uow:
        gauge = save_gauge_in(command, uow)
        uow.commit()
    return gauge


def delete_record(record_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork, reason: str = "") -> TechOperationRecord:
    """Удаляет строку таблицы «Базы данных» (владелец 20.10.2026: слитые дубли БД.КОАКС не должны висеть на экране).
    Всё в одной транзакции: строка уходит, а в журнал пишется `references.record_deleted` с её полной старой записью —
    по нему строку можно восстановить. Ссылки по имени (партии, комплекты, техкарты) здесь не проверяются — это дело
    вызывающего: он решает, можно ли удалять."""
    with uow:
        record = _editable(record_id, uow)
        uow.tech_operations.delete(record_id)
        _audit("references.record_deleted", actor, now, {
            "record_id": str(record.id), "tab": record.tab.value, "display_text": record.display_text,
            "sort_key": record.sort_key, "source_sheet": record.source_sheet, "mirror_key": record.mirror_key,
            "edited_in_erp": record.edited_in_erp, "fields": dict(record.fields), "reason": reason,
        }, uow)
        uow.commit()
    return record


def delete_gauge(gauge_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork) -> None:
    """A size row no hook-up wire uses (the FK refuses the rest, this says it in words first)."""
    with uow:
        gauge = next((item for item in uow.cable_structure.gauges() if item.id == gauge_id), None)
        if gauge is None:
            raise LookupError("Сечение не найдено — откройте таблицу заново")
        used = uow.cable_structure.gauge_usage().get(gauge_id, 0)
        if used:
            raise ReferenceValidationError(f"На этом сечении {used} монтажных проводов — сначала смените им сечение")
        uow.cable_structure.delete_gauge(gauge_id)
        _sync_gost_awg(uow, gauge, None)
        _audit("references.wire_gauge_deleted", actor, now, {"gauge_id": str(gauge_id), "awg": gauge.awg}, uow)
        uow.commit()


# --- «Монтажные провода» ---

@dataclass(frozen=True)
class SaveHookupCommand:
    record_id: UUID  # the БД.ПРОВ row
    series: str
    gauge_id: UUID
    insulation: str
    outer_d_mm: str
    voltage_v: str
    temp_min_c: str
    temp_max_c: str
    old: HookupWire | None  # what the screen showed (None — not structured yet); stale otherwise
    actor: Actor
    now: datetime
    conductor_material: str | None = None  # None — keep what is stored (the screen does not edit it yet)
    insulation_thickness_mm: str | None = None


def _same_hookup(left: HookupWire | None, right: HookupWire | None) -> bool:
    fields = ("series", "gauge_id", "insulation", "outer_d_mm", "voltage_v", "temp_min_c", "temp_max_c")
    if left is None or right is None:
        return left is None and right is None
    return all(getattr(left, name) == getattr(right, name) for name in fields)


def save_hookup_in(command: SaveHookupCommand, uow: UnitOfWork,
                   details: Mapping[str, str] | None = None) -> tuple[HookupWire, TechOperationRecord]:
    """The structure of a hook-up wire: series, its conductor (a size row), insulation, outer Ø, voltage and
    temperature. The БД.ПРОВ row is written in step (only the cells whose meaning changed), so the warehouse, the
    harness catalogue and the ETK generator read the same values. Inside an open unit of work, nothing committed;
    `details` go into the audit."""
    record = uow.tech_operations.get(command.record_id)
    if record is None or record.tab != TechOperationTab.WIRE:
        raise LookupError("Провод не найден в БД.ПРОВ")
    mark = cable_mark(record)
    current = uow.cable_structure.hookup(normalized_cable_key(mark))
    if not _same_hookup(current, command.old):
        raise StaleRecordError("Провод уже поменяли — откройте его заново")
    gauges = uow.cable_structure.gauges()
    gauge = next((item for item in gauges if item.id == command.gauge_id), None)
    if gauge is None:
        raise ReferenceValidationError("Выберите сечение из таблицы сечений")
    wire = HookupWire.create(
        id=current.id if current else None, mark=mark, series=command.series, gauge_id=gauge.id,
        insulation=command.insulation, outer_d_mm=command.outer_d_mm, voltage_v=command.voltage_v,
        temp_min_c=command.temp_min_c, temp_max_c=command.temp_max_c,
        conductor_material=(current.conductor_material if current else "") if command.conductor_material is None
        else command.conductor_material,
        insulation_thickness_mm=(current.insulation_thickness_mm if current else None)
        if command.insulation_thickness_mm is None else command.insulation_thickness_mm,
    )
    uow.cable_structure.save_hookup(wire, command.now)
    changes = written_back_fields(record.fields, wire, gauge, gauges)
    if changes:
        fields = {**record.fields, **changes}
        record = replace(record, fields=fields, edited_in_erp=True)
        record = replace(record, normalized_search=_search_text(record))
        uow.tech_operations.save(record)
    _audit("references.hookup_wire_saved", command.actor, command.now, {
        "record_id": str(record.id), "mark": mark, "gauge_id": str(gauge.id), "created": current is None,
        "fields_written": sorted(changes), **(details or {}),
    }, uow)
    return wire, record


def save_hookup(command: SaveHookupCommand, uow: UnitOfWork) -> tuple[HookupWire, TechOperationRecord]:
    with uow:
        result = save_hookup_in(command, uow)
        uow.commit()
    return result


def _follow_wire_edit(uow: UnitOfWork, before: TechOperationRecord, after: TechOperationRecord, edited: set[str],
                      now: datetime) -> None:
    """A БД.ПРОВ edit of a structured wire's mirrored cells (or its mark) moves the structure with it."""
    wire = uow.cable_structure.hookup(normalized_cable_key(cable_mark(before))) if cable_mark(before) else None
    if wire is None:
        return
    mark = cable_mark(after)
    if not normalized_cable_key(mark):
        raise ReferenceValidationError("Марка провода обязательна")
    uow.cable_structure.save_hookup(refreshed_hookup(wire, mark, after.fields, edited, uow.cable_structure.gauges()), now)


def structure_hookups(actor: Actor, now: datetime, uow_factory, *, dry_run: bool = False) -> dict[str, object]:
    """Idempotent: every монтажный БД.ПРОВ wire without a structure gets one when its size is in the table (the same
    rule the 0089 migration applied); a structured wire is never touched. Returns the counts and the marks left for
    the owner with the reason."""
    catalog = cable_catalog(uow_factory())
    created: list[HookupWire] = []
    left: list[str] = []
    for row in catalog.rows:
        if row.kind != CableKind.WIRE or row.record is None or row.key in catalog.hookups:
            continue
        parsed = parse_hookup(row.cable, row.record.fields, catalog.gauges)
        wire = hookup_from_parse(row.cable, parsed)
        if wire is None or wire.mark_key in {item.mark_key for item in created}:
            left.append(f"{row.cable}: {parsed.problem or 'повтор марки'}")
            continue
        created.append(wire)
    uow = uow_factory()
    with uow:
        for wire in created:
            uow.cable_structure.save_hookup(wire, now)
        _audit("references.hookup_wires_structured", actor, now, {"created": len(created), "left": len(left), "dry_run": dry_run}, uow)
        if not dry_run:
            uow.commit()
    return {"created": len(created), "structured_before": len(catalog.hookups), "left": left}


# --- «Коаксиальные кабели»: the seed file ---

def import_coax_cards(document, actor: Actor, now: datetime, uow: UnitOfWork, *, dry_run: bool = False) -> dict[str, int]:
    """Idempotent load of the coax card file: cards matched by name key are rewritten from it, new ones added, ones
    the file no longer names removed. Run again with a corrected file — no migration needed."""
    cards = cards_from_seed(document)
    with uow:
        result = uow.cable_structure.replace_cards(cards, now)
        _audit("references.coax_cards_imported", actor, now, {**result, "cards": len(cards), "dry_run": dry_run}, uow)
        if not dry_run:
            uow.commit()
    return {**result, "cards": len(cards)}


def import_coax_names(document, actor: Actor, now: datetime, uow: UnitOfWork, *, dry_run: bool = False) -> dict[str, object]:
    """Idempotent load of the supplier file (`coax-articles.json`) after the cards: each card gets its full name and
    aliases (`cable_structure.names_from_articles`); a card the file does not name has none. Nothing is renamed: the
    card keeps its mark, the D-table its «Кабель». Returns the counts and what was left out, with the reason."""
    with uow:
        cards = uow.cable_structure.cards()
        plan = names_from_articles(document, cards)
        validate_aliases(plan.names, cards)
        result = uow.cable_structure.replace_names(plan.names)
        aliases = sum(len(item.aliases) for item in plan.names.values())
        _audit("references.coax_names_imported", actor, now, {
            **result, "aliases": aliases, "refused": len(plan.refused), "dry_run": dry_run}, uow)
        if not dry_run:
            uow.commit()
    return {**result, "cables": len(document.get("cables") or []), "aliases": aliases, "family_only": plan.family_only,
            "variants": plan.variants, "no_card": list(plan.no_card), "refused": list(plan.refused)}


def coax_dia_rows(uow: UnitOfWork, limit: int = 100_000) -> list[dict[str, str]]:
    """The D1·D2·D3 table as the ETK generator and the harness catalogue read it, over an open unit of work: the
    СПР.КАБ · коаксиал rows in their order, then one row per other name of a cable (`alias_dia_rows`) — so a BOM that
    names the cable by its full name, an article or another spelling of the mark gets the same D1–D3. Rows are only
    appended, for names without a row of their own: every name found before finds the same row."""
    records = uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=limit)
    index = {key: record.fields for key, record in diameter_index(records).items()}
    return [dict(record.fields) for record in records] + alias_dia_rows(index, uow.cable_structure.cards(), DIAMETERS)


def apply_coax_vocabulary(document, actor: Actor, now: datetime, uow: UnitOfWork, *, dry_run: bool = False) -> dict[str, object]:
    """One-off (`load_cables --coax-vocabulary`): the БД.ПРОВ coax cells «Тип провода», «Изоляция», «Экран» get the
    terms of the coax vocabulary (`coax-vocabulary.json`, map entries of «БД.ПРОВ»). Only a cell that still holds the
    old text is rewritten; the row is marked edited in ERP, so a mirror reload keeps it. A second run changes nothing."""
    by_mark: dict[str, list] = {}
    for cell in vocabulary_cells(document):
        by_mark.setdefault(cell.mark_key, []).append(cell)
    rewritten: list[str] = []
    problems: list[str] = []
    found: set[str] = set()
    with uow:
        for record in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=10_000):
            key = cable_key(cable_mark(record))
            if key not in by_mark:
                continue
            found.add(key)
            changes, row_problems = vocabulary_changes(record.fields, by_mark[key])
            problems += row_problems
            if not changes:
                continue
            updated = replace(record, fields={**record.fields, **changes}, edited_in_erp=True)
            uow.tech_operations.save(replace(updated, normalized_search=_search_text(updated)))
            rewritten += [f"{cable_mark(record)}, {name}: «{record.fields.get(name, '')}» → «{value}»"
                          for name, value in changes.items()]
        missing = sorted(group[0].mark for key, group in by_mark.items() if key not in found)
        _audit("references.coax_vocabulary_applied", actor, now, {
            "cells": len(rewritten), "problems": len(problems), "missing": len(missing), "dry_run": dry_run}, uow)
        if not dry_run:
            uow.commit()
    return {"cells": sum(len(group) for group in by_mark.values()), "rewritten": rewritten, "problems": problems,
            "missing": missing}


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


def set_coax_diameters_in(command: SetCoaxDiametersCommand, uow: UnitOfWork,
                          details: Mapping[str, str] | None = None) -> TechOperationRecord:
    """D1·D2·D3 of a coax cable from «Коаксиальные кабели» (owner 04.10.2026; the SUPPLYMAP portal keeps СПР.КАБ
    view-only, ERP edits it here). The row edited is the one the generator uses for the name (the last of its key); a
    cable without one gets a new D-table row named as the cable, so the generator finds it by that name. Values are
    numbers in mm or empty; the old values must match (stale otherwise). The row is marked edited in ERP, so a mirror
    reload keeps it. Inside an open unit of work, nothing committed; `details` go into the audit."""
    key = normalized_cable_key(command.cable)
    values = {name: clean_diameter(name, command.value.get(name, "")) for name in DIAMETERS}
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
        "record_id": str(record.id), "tab": record.tab.value, "fields": list(DIAMETERS), **(details or {}),
    }, uow)
    return record


def set_coax_diameters(command: SetCoaxDiametersCommand, uow: UnitOfWork) -> TechOperationRecord:
    with uow:
        record = set_coax_diameters_in(command, uow)
        uow.commit()
    return record
