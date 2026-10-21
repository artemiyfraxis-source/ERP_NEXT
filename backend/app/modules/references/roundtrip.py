"""«Скачать для правки» / «Загрузить исправленную» (owner 14.10.2026: «во всех базах данных должна быть возможность
скачать таблицу и вставить исправленную»).

A tab of «База данных» goes out as an .xlsx (xlsx_file.py): «ID записи» first, every field of the tab under the title
the screen shows (the screen sends its titles), a hidden «Версия» — the row's fingerprint at the download — and a hidden
sheet «Поля» (title → field, the tab), so columns may be moved and the file still reads back.

The edited file comes back through `import_file`: a row with an ID is the record's edit (only the cells that differ),
a row without an ID is a new record, a record missing from the file is left alone — nothing is deleted through a file.
Every row goes through the very path of the edit form (`_edit_in` / `_create_in` / `save_gauge_in` / the map upsert)
in one transaction, each change audited like a manual edit (`via: file`); a row whose record changed since the
download (its «Версия» differs) is a conflict and skipped, a row the validators refuse is reported with the reason and
only that row is skipped (a savepoint each). A dry run runs the same path and rolls back, so its preview is what the
apply does."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from uuid import UUID

from app.shared.identity import Actor

from .application import (
    FieldEdit, SaveGaugeCommand, SaveHookupCommand, SetCoaxDiametersCommand, UnitOfWork, _audit, _clean_new_fields,
    _create_in, _edit_in, save_gauge_in, save_hookup_in, set_coax_diameters_in,
)
from .cable_structure import parse_hookup, shown
from .cables import DIAMETERS, CableKind, cable_mark, cable_rows, diameter_index
from .domain import (
    BASE_COLUMNS, CABLE_KIND_VALUES, COUNTRY_BY_MANUFACTURER, SUPPLIER_BY_LINK, ReferenceKind, ReferenceValidationError,
    StaleRecordError, TechOperationRecord, TechOperationTab, merge_country_pairs, normalized_cable_key,
    normalized_manufacturer_key, normalized_supplier_key,
)
from .equipment import EXTRA_KEYS, TARGETS_KEY
from .importers import _MapTarget, cable_list_columns
from .technology import OPERATION_TD_KEYS, schema_columns, schema_keys
from .xlsx_file import Cell, ReadSheet, Sheet, XlsxError, read_xlsx, write_xlsx

ID_TITLE = "ID записи"
VERSION_TITLE = "Версия"
FIELDS_SHEET = "Поля"
VIA = "file"
MAX_FILE_ROWS = 10_000
COAX_DIAMETERS = DIAMETERS  # the cable's Ø: not on the connector row («Коаксиальные разъёмы»), a column of the cable list
# Columns beyond the БД.ПРОВ cells, by list (owner 14.10): the coax cable's D1–D3 (СПР.КАБ, saved as the cable form saves
# them) and the structure of a hook-up wire that has no БД.ПРОВ cell (saved as the wire form saves it). Titled as
# «Кабели и провода» shows them (CableTables.tsx DIA_TITLES, CableStructure.tsx «Серия»); the structure's conductor
# material is no БД.ПРОВ «Материал жилы» cell, so its title says it is the series structure's.
HOOKUP_SERIES, HOOKUP_MATERIAL, HOOKUP_THICKNESS = "hookup_series", "hookup_material", "hookup_insulation_mm"
HOOKUP_TITLES = {HOOKUP_SERIES: "Серия", HOOKUP_MATERIAL: "Материал жилы (серия)",
                 HOOKUP_THICKNESS: "Толщина изоляции, мм"}
COAX_TITLES = {"D1": "D1 жила, мм", "D2": "D2 диэлектрик, мм", "D3": "D3 оплётка, мм"}

NOT_FOUND_REASON = "записи с этим ID нет — её удалили или файл скачан из другой вкладки"
CONFLICT_REASON = "запись изменили после скачивания файла — скачайте таблицу заново"
NO_VERSION_REASON = "нет «Версии» строки — скачайте таблицу заново"
DUPLICATE_ID_REASON = "ID повторяется в файле"


class RoundtripError(ValueError):
    """The whole file is refused (wrong tab, not our table); the message is for the user."""


# ── Tables ────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Item:
    id: str
    title: str  # what the preview calls the row
    version: str
    values: dict[str, str]


def fingerprint(*parts: object) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


class _Table(Protocol):
    label: str
    titles: Mapping[str, str]  # the table's own titles (gauges, maps); a БД tab takes the screen's

    def kind_ids(self, uow: UnitOfWork, kind: str) -> set[str] | None: ...
    def items(self, uow: UnitOfWork) -> list[Item]: ...
    def fields(self, items: Sequence[Item]) -> list[str]: ...
    def update(self, uow: UnitOfWork, item: Item, changes: dict[str, str], actor: Actor, now: datetime) -> None: ...
    def create(self, uow: UnitOfWork, values: dict[str, str], actor: Actor, now: datetime) -> str: ...
    def finish(self, uow: UnitOfWork, actor: Actor, now: datetime) -> None: ...


class _TechTable:
    """БД.ОБ / БД.ОП / БД.ТЕР / БД.КОАКС / БД.ПРОВ / БД.МАТ rows by record id.

    БД.ПРОВ of one cable list (owner 20.10.2026: «у каждого своя структура»): only the rows of that list
    (`TechOperationRecord.cable_kind`) and only that list's columns — `CABLE_COLUMNS[list]`, the owner's columns of the
    list, the cells its rows carry that are no other list's column, then the list's extras; a row created from the file
    goes to that list. Without a list — every row and column of БД.ПРОВ (a new row's list by the rule)."""

    titles: Mapping[str, str] = {}

    def __init__(self, tab: TechOperationTab, label: str, kinds: Mapping[str, str] | None = None) -> None:
        self.tab, self.label, self.kinds = tab, label, dict(kinds or {})
        self.extras: Mapping[str, str] = {}  # extra columns of the chosen list: field → title
        self.cable_kind = ""  # БД.ПРОВ: the cable list of the file ('' — all lists together)
        self.own_columns: tuple[str, ...] = ()  # БД.ПРОВ of a list: its columns, read in `items`
        self.foreign: frozenset[str] = frozenset()  # БД.ПРОВ of a list: the columns of the other lists only

    def for_kind(self, kind: str) -> "_TechTable":
        if self.tab == TechOperationTab.WIRE and kind:
            if kind not in CABLE_KIND_VALUES:
                raise RoundtripError(f"Списка «{kind}» в «Кабелях и проводах» нет — скачайте таблицу заново из "
                                     "коаксиальных, монтажных или многожильных")
            self.cable_kind = kind
            self.extras = COAX_TITLES if kind == "coax" else HOOKUP_TITLES if kind == "wire" else {}
            self.titles = self.extras
        return self

    def _extra_values(self, uow: UnitOfWork, records: Sequence[TechOperationRecord]) -> dict[str, dict[str, str]]:
        """The extra columns of every БД.ПРОВ row, read in the open `uow` (the cable catalogue's own join)."""
        rows = cable_rows(records, uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100_000))
        hookups = {wire.mark_key: wire for wire in uow.cable_structure.hookups()}
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            if row.record is None:
                continue
            if COAX_TITLES.keys() == self.extras.keys():
                fields = row.dia.fields if row.dia else {}
                result[str(row.record.id)] = {name: str(fields.get(name, "")).strip() for name in DIAMETERS}
            else:
                wire = hookups.get(row.key)
                result[str(row.record.id)] = {
                    HOOKUP_SERIES: wire.series if wire else "", HOOKUP_MATERIAL: wire.conductor_material if wire else "",
                    HOOKUP_THICKNESS: shown(wire.insulation_thickness_mm) if wire else "",
                }
        return result

    def _records(self, uow: UnitOfWork) -> list[TechOperationRecord]:
        records = list(uow.tech_operations.list(tab=self.tab, query="", limit=100_000))
        if self.cable_kind:  # БД.ПРОВ of one list: its rows only (an ID of another list's row is «not found»)
            records = [record for record in records if record.cable_kind == self.cable_kind]
        return records

    def kind_ids(self, uow: UnitOfWork, kind: str) -> set[str] | None:
        """The ids of one list of the tab («Оболочки» of БД.МАТ), read in `uow` (not open); a cable list of БД.ПРОВ is
        chosen by `_records` itself."""
        if not kind:
            return None
        if kind not in self.kinds:
            raise RoundtripError("Неизвестный список: " + kind)
        if self.tab == TechOperationTab.WIRE:
            return None
        from .coverings import build  # «Оболочки»: the БД.МАТ rows that cover a harness
        with uow:
            records = self._records(uow)
        return {str(row["id"]) for row in build([(str(item.id), item.fields) for item in records])["rows"]}

    def items(self, uow: UnitOfWork) -> list[Item]:
        records = self._records(uow)
        if self.cable_kind:
            columns = {kind: cable_list_columns(uow, kind) for kind in CABLE_KIND_VALUES}
            self.own_columns = columns[self.cable_kind]
            self.foreign = frozenset(name for kind, names in columns.items() if kind != self.cable_kind
                                     for name in names) - set(self.own_columns)
        extra = self._extra_values(uow, records) if self.extras else {}
        return [
            Item(str(record.id), record.display_text.strip(" /") or str(record.id),
                 fingerprint(record.display_text, record.fields, *([extra.get(str(record.id), {})] if self.extras else [])),
                 {**record.fields, **extra.get(str(record.id), {})})
            for record in records
        ]

    def fields(self, items: Sequence[Item]) -> list[str]:
        """Every field the tab's edit form edits: the sheet columns (the schema values the generator reads follow them
        on save), ПРОВ / МАТ — the base's columns first; a column only some rows have is still a column."""
        keys = schema_keys(self.tab.value) | set(self.extras) | self.foreign
        richest = sorted(items, key=lambda item: -sum(1 for name in item.values if name not in keys))
        ordered: dict[str, None] = dict.fromkeys(self.own_columns if self.cable_kind else BASE_COLUMNS.get(self.tab, ()))
        for item in richest:
            ordered.update(dict.fromkeys(name for name in item.values if name not in keys))
        if not ordered:  # a snapshot without the sheet's columns: the schema keys are the fields
            for item in richest:
                ordered.update(dict.fromkeys(name for name in item.values if name not in self.extras))
        if self.tab == TechOperationTab.EQUIPMENT:
            ordered.update(dict.fromkeys(EXTRA_KEYS))
            ordered.pop(TARGETS_KEY, None)  # «что обжимает» is a list kept by the tool's card, not a cell
        if self.tab == TechOperationTab.COAX:
            for name in COAX_DIAMETERS:
                ordered.pop(name, None)
        if self.tab == TechOperationTab.OPERATION:
            # «Профессия» / «Разряд» комплекта ТД: not sheet columns — their schema keys are the cells, unless a row
            # brought such a column (then the column is the cell and the key follows it).
            mapped = {key for item in items for key in schema_columns(self.tab.value, item.values)}
            ordered.update(dict.fromkeys(key for key in OPERATION_TD_KEYS if key not in mapped))
        ordered.update(dict.fromkeys(self.extras))
        return list(ordered)

    def update(self, uow, item, changes, actor, now):
        own = {name: value for name, value in changes.items() if name not in self.extras}
        extra = {name: value for name, value in changes.items() if name in self.extras}
        if extra and COAX_TITLES.keys() == self.extras.keys():
            self._set_diameters(uow, item, extra, actor, now)
        if own and self.tab == TechOperationTab.WIRE:
            # a монтажный wire's cell of the mark another colour's row of this file has already written (the mark's
            # cells follow to all its colours) is not this row's change
            current = self._record(uow, item).fields
            own = {name: value for name, value in own.items() if current.get(name, "") != value}
        if own:
            edits = tuple(FieldEdit(name, item.values.get(name, ""), value) for name, value in own.items())
            record, _updated = _edit_in(UUID(item.id), edits, now, uow)
            _audit("references.tech_record_edited", actor, now, {
                "record_id": str(record.id), "tab": record.tab.value, "fields": list(own), "via": VIA,
            }, uow)
        if extra and HOOKUP_TITLES.keys() == self.extras.keys():
            self._set_hookup(uow, item, extra, actor, now)

    def _record(self, uow: UnitOfWork, item: Item) -> TechOperationRecord:
        record = uow.tech_operations.get(UUID(item.id))
        if record is None:
            raise LookupError("Запись справочника не найдена")
        return record

    def _set_diameters(self, uow, item, extra, actor, now) -> None:
        """D1–D3 of the cable through the cable form's own save (a cable without a D row gets one)."""
        mark = cable_mark(self._record(uow, item))
        current = diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100_000)).get(
            normalized_cable_key(mark))
        old = {name: item.values.get(name, "") for name in DIAMETERS}
        set_coax_diameters_in(SetCoaxDiametersCommand(
            cable=mark, dia_id=current.id if current else None, old=old, value={**old, **extra}, actor=actor, now=now,
        ), uow, {"via": VIA})

    def _set_hookup(self, uow, item, extra, actor, now) -> None:
        """Series / conductor material / insulation thickness through the wire form's own save; a wire with no
        structure yet gets one from what its БД.ПРОВ row says (the size must be in the size table)."""
        record = self._record(uow, item)
        mark = cable_mark(record)
        current = uow.cable_structure.hookup(normalized_cable_key(mark))
        if current is not None:
            base = {"series": current.series, "gauge_id": current.gauge_id, "insulation": current.insulation,
                    "outer_d_mm": shown(current.outer_d_mm), "voltage_v": shown(current.voltage_v),
                    "temp_min_c": shown(current.temp_min_c), "temp_max_c": shown(current.temp_max_c)}
        else:
            parsed = parse_hookup(mark, record.fields, uow.cable_structure.gauges())
            if parsed.gauge is None:
                raise ReferenceValidationError("структуру этого провода задать нельзя: "
                                               + (parsed.problem or "размера нет в таблице сечений"))
            base = {"series": parsed.series, "gauge_id": parsed.gauge.id, "insulation": parsed.insulation,
                    "outer_d_mm": shown(parsed.outer_d_mm), "voltage_v": shown(parsed.voltage_v),
                    "temp_min_c": shown(parsed.temp_min_c), "temp_max_c": shown(parsed.temp_max_c)}
        save_hookup_in(SaveHookupCommand(
            record_id=record.id, series=extra.get(HOOKUP_SERIES, base["series"]), gauge_id=base["gauge_id"],
            insulation=base["insulation"], outer_d_mm=base["outer_d_mm"], voltage_v=base["voltage_v"],
            temp_min_c=base["temp_min_c"], temp_max_c=base["temp_max_c"], old=current, actor=actor, now=now,
            conductor_material=extra.get(HOOKUP_MATERIAL), insulation_thickness_mm=extra.get(HOOKUP_THICKNESS),
        ), uow, {"via": VIA})

    def create(self, uow, values, actor, now):
        """A row without an ID: the record (БД.ПРОВ of a list — in that list), then its extras through their own saves
        (D1–D3 of a coax cable, the structure of a монтажный wire), as `update` writes them."""
        own = {name: value for name, value in values.items() if name not in self.extras}
        extra = {name: value for name, value in values.items() if name in self.extras}
        kind = CableKind(self.cable_kind) if self.cable_kind else None
        record = _create_in(self.tab, _clean_new_fields(self.tab, own), uow, kind)
        _audit("references.tech_record_created", actor, now, {
            "record_id": str(record.id), "tab": record.tab.value, "via": VIA,
            **({"cable_kind": record.cable_kind} if record.cable_kind else {}),
        }, uow)
        current = self._extra_values(uow, [record]).get(str(record.id), {}) if extra else {}
        extra = {name: value for name, value in extra.items() if value != current.get(name, "")}
        if extra:  # a D row of that cable may already be there: the old values are what it holds
            self.update(uow, Item(str(record.id), record.display_text, "", {**record.fields, **current}), extra, actor, now)
        return record.display_text.strip(" /")

    def finish(self, uow, actor, now):
        pass


GAUGE_TITLES = {"awg": "AWG", "section_mm2": "Сечение, мм²", "conductor_d_mm": "Ø жилы, мм"}


class _GaugeTable:
    """«Сечения AWG ↔ мм²» by size row id; saved as the size form saves (`save_gauge_in`)."""

    label = "Сечения AWG ↔ мм²"
    titles = GAUGE_TITLES

    def kind_ids(self, uow, kind):
        return _no_kinds(kind)

    def items(self, uow):
        result = []
        for gauge in uow.cable_structure.gauges():
            values = {"awg": gauge.awg or "", "section_mm2": shown(gauge.section_mm2),
                      "conductor_d_mm": shown(gauge.conductor_d_mm)}
            result.append(Item(str(gauge.id), gauge.label, fingerprint(values), values))
        return result

    def fields(self, items):
        return list(GAUGE_TITLES)

    def _save(self, uow, gauge_id, values, actor, now):
        save_gauge_in(SaveGaugeCommand(
            gauge_id=gauge_id, awg=values.get("awg", ""), section_mm2=values.get("section_mm2", ""),
            conductor_d_mm=values.get("conductor_d_mm", ""), actor=actor, now=now,
        ), uow, {"via": VIA})

    def update(self, uow, item, changes, actor, now):
        self._save(uow, UUID(item.id), {**item.values, **changes}, actor, now)

    def create(self, uow, values, actor, now):
        self._save(uow, None, values, actor, now)
        return f"{values['awg']} AWG" if values.get("awg") else f"{values.get('section_mm2', '')} мм²"

    def finish(self, uow, actor, now):
        pass


class _MapTable:
    """«Домен → поставщик» / «Изготовитель → страна»: the key is the row's identity (the ID column carries it); a new
    key is a new row, the key of a row is not renamed through a file. Rows as the screen shows them (the seeds
    included); written through the natural-key import's map target, audited as the maps save is."""

    def __init__(self, kind: ReferenceKind, label: str, key_title: str, value_title: str,
                 normalize: Callable[[str], str]) -> None:
        self.kind, self.label, self.normalize = kind, label, normalize
        self.titles = {"key": key_title, "value": value_title}
        self.target: _MapTarget | None = None
        self.written = 0

    def kind_ids(self, uow, kind):
        return _no_kinds(kind)

    def items(self, uow):
        entries = list(uow.references.list())
        saved = [(entry.key, entry.value) for entry in entries if entry.kind == self.kind]
        if self.kind == ReferenceKind.SUPPLIER:
            nothing_saved = not any(entry.kind in (ReferenceKind.SUPPLIER, ReferenceKind.COUNTRY) for entry in entries)
            pairs = list(SUPPLIER_BY_LINK) if nothing_saved else saved
        else:
            pairs = list(merge_country_pairs(saved))
        return [Item(key, key, fingerprint(value), {"key": key, "value": value}) for key, value in pairs]

    def fields(self, items):
        return ["key", "value"]

    def _map(self, uow) -> _MapTarget:
        if self.target is None:
            self.target = _MapTarget(uow, VIA, self.kind, self.normalize)
        return self.target

    def update(self, uow, item, changes, actor, now):
        if "key" in changes and _folded(changes["key"]) != _folded(item.values["key"]):
            raise ReferenceValidationError(
                f"«{self.titles['key']}» строки не меняется — новое значение добавьте строкой без ID")
        self._put(uow, item.values["key"], {**item.values, **changes}.get("value", ""))

    def create(self, uow, values, actor, now):
        key = values.get("key", "")
        if not key:
            raise ReferenceValidationError(f"не заполнено «{self.titles['key']}»")
        target = self._map(uow)
        try:
            normalized = self.normalize(key)
        except ReferenceValidationError:
            normalized = ""
        if normalized and (normalized in target.saved or normalized in target.seed):
            raise ReferenceValidationError(f"«{key}» уже есть в таблице — правьте ту строку")
        self._put(uow, key, values.get("value", ""))
        return key

    def _put(self, uow, key: str, value: str) -> None:
        from app.shared.bulk_import import RowRefused
        try:
            self._map(uow).create(key, {"value": value})
        except RowRefused as error:
            raise ReferenceValidationError(str(error)) from error
        self.written += 1

    def finish(self, uow, actor, now):
        if self.target is None or not self.written:
            return
        self.target.flush()
        _audit("references.maps_replaced", actor, now, {"map": self.kind.value, "rows": self.written, "via": VIA}, uow)


def _folded(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


CABLE_KINDS = {"coax": "Коаксиальные кабели", "wire": "Монтажные провода", "multi": "Многожильные кабели"}


def _tables() -> dict[str, Callable[[], _Table]]:
    return {
        "ob": lambda: _TechTable(TechOperationTab.EQUIPMENT, "Оборудование"),
        "op": lambda: _TechTable(TechOperationTab.OPERATION, "Операции (БД.ОП)"),
        "ter": lambda: _TechTable(TechOperationTab.TERMINAL, "Терминалы"),
        "coax": lambda: _TechTable(TechOperationTab.COAX, "Коаксиальные разъёмы"),
        "prov": lambda: _TechTable(TechOperationTab.WIRE, "Кабели и провода", CABLE_KINDS),
        "mat": lambda: _TechTable(TechOperationTab.MATERIAL, "Материалы", {"cov": "Оболочки"}),
        "wire-gauges": _GaugeTable,
        "suppliers": lambda: _MapTable(ReferenceKind.SUPPLIER, "Домен → поставщик", "Домен или ссылка", "Поставщик",
                                       normalized_supplier_key),
        "countries": lambda: _MapTable(ReferenceKind.COUNTRY, "Изготовитель → страна", "Изготовитель", "Страна",
                                       normalized_manufacturer_key),
    }


ROUNDTRIP_TABLES = tuple(_tables())
MAP_ROUNDTRIP_TABLES = ("suppliers", "countries")


def _table(tab: str, kind: str = "") -> _Table:
    factory = _tables().get(tab)
    if factory is None:
        raise LookupError(f"Нет таблицы «{tab}» для правки файлом; есть: " + ", ".join(ROUNDTRIP_TABLES))
    table = factory()
    return table.for_kind(kind) if isinstance(table, _TechTable) else table


def _label(table: _Table, kind: str) -> str:
    kinds = getattr(table, "kinds", {})
    return kinds.get(kind) or table.label


def _no_kinds(kind: str) -> None:
    if kind:
        raise RoundtripError("Неизвестный список: " + kind)
    return None


# ── Export ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _listed(table: _Table, uow_factory: Callable[[], UnitOfWork], kind: str) -> list[Item]:
    wanted = table.kind_ids(uow_factory(), kind)
    with uow_factory() as uow:
        items = table.items(uow)
    return [item for item in items if wanted is None or item.id in wanted]


def table_fields(tab: str, uow_factory: Callable[[], UnitOfWork], kind: str = "") -> dict:
    """What «Скачать для правки» writes: the fields in order and the table's own titles (the screen adds its own)."""
    table = _table(tab, kind)
    items = _listed(table, uow_factory, kind)
    return {"tab": tab, "kind": kind, "label": _label(table, kind), "fields": table.fields(items),
            "titles": dict(table.titles), "rows": len(items)}


def _unique_titles(fields: Sequence[str], titles: Mapping[str, str]) -> list[str]:
    result, seen = [], {ID_TITLE.casefold(), VERSION_TITLE.casefold()}
    for name in fields:
        title = " ".join(str(titles.get(name) or name).split()) or name
        if title.casefold() in seen:
            title = f"{title} ({name})"
        seen.add(title.casefold())
        result.append(title)
    return result


@dataclass(frozen=True)
class ExportedFile:
    content: bytes
    label: str
    rows: int


def export_file(tab: str, uow_factory: Callable[[], UnitOfWork], titles: Mapping[str, str], now: datetime,
                kind: str = "") -> ExportedFile:
    table = _table(tab, kind)
    items = _listed(table, uow_factory, kind)
    fields = table.fields(items)
    headers = _unique_titles(fields, {**titles, **table.titles})
    rows = [[ID_TITLE, VERSION_TITLE, *headers]]
    rows += [[item.id, item.version, *(item.values.get(name, "") for name in fields)] for item in items]
    label = _label(table, kind)
    service = [["Заголовок", "Поле"], *([title, name] for title, name in zip(headers, fields)),
               ["", ""], ["Таблица", tab], ["Список", kind], ["Название", label],
               ["Скачано", now.strftime("%d.%m.%Y %H:%M")]]
    content = write_xlsx([
        Sheet(label, rows, hidden_columns={1}, grey_columns={0}),
        Sheet(FIELDS_SHEET, service, hidden=True),
    ])
    return ExportedFile(content, label, len(items))


# ── Import ────────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Report:
    tab: str
    label: str
    dry_run: bool
    columns: list[dict[str, str]] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    not_found: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0
    applied: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tab": self.tab, "label": self.label, "dry_run": self.dry_run, "applied": self.applied,
            "columns": self.columns, "ignored_columns": self.ignored_columns, "updated": self.updated,
            "created": self.created, "conflicts": self.conflicts, "not_found": self.not_found, "errors": self.errors,
            "unchanged": self.unchanged,
        }


def _cell_text(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return format(value, ".10g")
    return str(value).strip()


_DOT_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_COMMA_DECIMAL = re.compile(r"^-?\d+,\d+$")


def _value(cell: Cell, old: str, comma_column: bool) -> str:
    """A number Excel stored as a number comes back in the base's writing: «1,5», not «1.5», where the cell or its
    column writes a comma."""
    text = _cell_text(cell)
    if isinstance(cell, float) and _DOT_DECIMAL.match(text) and (_COMMA_DECIMAL.match(old) or (not old and comma_column)):
        return text.replace(".", ",")
    return text


def _sheets(content: bytes) -> tuple[ReadSheet, ReadSheet | None]:
    try:
        sheets = read_xlsx(content)
    except XlsxError as error:
        raise RoundtripError(str(error)) from error
    main = next((sheet for sheet in sheets if not sheet.hidden and sheet.name != FIELDS_SHEET), None)
    service = next((sheet for sheet in sheets if sheet.name == FIELDS_SHEET), None)
    if main is None:
        raise RoundtripError("В файле нет листа с таблицей")
    return main, service


def _service_map(service: ReadSheet | None) -> tuple[dict[str, str], dict[str, str]]:
    """(title → field, meta: Таблица / Список)."""
    titles: dict[str, str] = {}
    meta: dict[str, str] = {}
    if service is None:
        return titles, meta
    in_meta = False
    for row in service.rows[1:]:
        first, second = (_cell_text(row[0]) if row else ""), (_cell_text(row[1]) if len(row) > 1 else "")
        if not first and not second:
            in_meta = True
            continue
        if in_meta:
            meta[first] = second
        elif first and second:
            titles[_folded(first)] = second
    return titles, meta


def _columns(header: list[Cell], fields: Sequence[str], service_titles: Mapping[str, str],
             own_titles: Mapping[str, str]) -> tuple[int, int, dict[int, str], list[str], list[dict[str, str]]]:
    """(ID column, version column, column → field, ignored headers, [{field, title}])."""
    known = set(fields)
    by_title = {_folded(title): name for title, name in service_titles.items() if name in known}
    by_title.update({_folded(title): name for title, name in ((own_titles.get(name, ""), name) for name in fields)
                     if title and _folded(title) not in by_title})
    by_key = {_folded(name): name for name in fields}
    id_column = version_column = -1
    mapping: dict[int, str] = {}
    ignored: list[str] = []
    for index, cell in enumerate(header):
        title = _cell_text(cell)
        folded = _folded(title)
        if not folded:
            continue
        if folded == _folded(ID_TITLE) and id_column < 0:
            id_column = index
            continue
        if folded == _folded(VERSION_TITLE) and version_column < 0:
            version_column = index
            continue
        name = by_title.get(folded) or by_key.get(folded)
        if name is None or name in mapping.values():
            ignored.append(title)
            continue
        mapping[index] = name
    columns = [{"field": name, "title": _cell_text(header[index])} for index, name in mapping.items()]
    return id_column, version_column, mapping, ignored, columns


@contextlib.contextmanager
def _savepoint(uow: UnitOfWork) -> Iterator[None]:
    """One row: what it wrote is undone when it fails, the rows before it stay."""
    session = getattr(uow, "session", None)
    if session is None:
        yield
        return
    session.flush()
    connection = session.connection()
    raw = getattr(connection.connection, "dbapi_connection", None)
    if connection.dialect.name == "sqlite" and not getattr(raw, "in_transaction", True):
        # pysqlite opens its transaction only at the first write: a SAVEPOINT before it would be the transaction
        # itself, and its RELEASE a commit — a dry run would write. Open it first (preview.db and the tests are SQLite).
        connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        yield


_ROW_ERRORS = (ReferenceValidationError, StaleRecordError, LookupError, ValueError)


def import_file(tab: str, uow_factory: Callable[[], UnitOfWork], content: bytes, actor: Actor, now: datetime, *,
                dry_run: bool, kind: str = "") -> Report:
    _table(tab)  # an unknown table is refused before the file is read
    main, service = _sheets(content)
    service_titles, meta = _service_map(service)
    table = _table(tab, kind or meta.get("Список", ""))
    if meta.get("Таблица") and meta["Таблица"] != tab:
        raise RoundtripError(f"Файл скачан из другой таблицы («{meta.get('Название') or meta['Таблица']}») — "
                             f"загрузите его в ней")
    if not main.rows:
        raise RoundtripError("Лист таблицы пуст")
    if len(main.rows) - 1 > MAX_FILE_ROWS:
        raise RoundtripError(f"Строк в файле больше {MAX_FILE_ROWS}")
    report = Report(tab=tab, label=_label(table, kind or meta.get("Список", "")), dry_run=dry_run)
    uow = uow_factory()
    with uow:
        items = table.items(uow)
        fields = table.fields(items)
        id_column, version_column, mapping, ignored, columns = _columns(main.rows[0], fields, service_titles, table.titles)
        if id_column < 0:
            raise RoundtripError(f"В файле нет колонки «{ID_TITLE}» — скачайте таблицу кнопкой «Скачать для правки» "
                                 "и правьте её")
        if not mapping:
            raise RoundtripError("Ни одна колонка файла не совпала с полями таблицы")
        report.columns, report.ignored_columns = columns, ignored
        titles = {item["field"]: item["title"] for item in columns}
        by_id = {item.id.casefold(): item for item in items}
        comma = {name: any(_COMMA_DECIMAL.match(item.values.get(name, "")) for item in items[:2000])
                 for name in mapping.values()}
        ids = [(_cell_text(row[id_column]) if id_column < len(row) else "").casefold() for row in main.rows[1:]]
        repeated = {value for value, count in _counts(ids).items() if value and count > 1}
        for number, row in enumerate(main.rows[1:], start=2):
            cells = {name: row[index] if index < len(row) else None for index, name in mapping.items()}
            record_id = _cell_text(row[id_column]) if id_column < len(row) else ""
            if not record_id and not any(_cell_text(value) for value in cells.values()):
                continue  # an empty line
            if not record_id:
                values = {name: _value(value, "", comma[name]) for name, value in cells.items() if _cell_text(value)}
                try:
                    with _savepoint(uow):
                        title = table.create(uow, values, actor, now)
                except _ROW_ERRORS as error:
                    report.errors.append({"row": number, "id": "", "title": _row_title(values, columns),
                                          "reason": str(error)})
                    continue
                report.created.append({"row": number, "title": title or _row_title(values, columns),
                                       "fields": [{"field": name, "title": titles[name], "value": value}
                                                  for name, value in values.items()]})
                continue
            item = by_id.get(record_id.casefold())
            if record_id.casefold() in repeated:
                report.errors.append({"row": number, "id": record_id, "title": item.title if item else "",
                                      "reason": DUPLICATE_ID_REASON})
                continue
            if item is None:
                report.not_found.append({"row": number, "id": record_id, "reason": NOT_FOUND_REASON})
                continue
            version = _cell_text(row[version_column]) if 0 <= version_column < len(row) else ""
            if not version:
                report.conflicts.append({"row": number, "id": item.id, "title": item.title, "reason": NO_VERSION_REASON})
                continue
            if version != item.version:
                report.conflicts.append({"row": number, "id": item.id, "title": item.title, "reason": CONFLICT_REASON})
                continue
            changes = {}
            for name, cell in cells.items():
                old = item.values.get(name, "")
                new = _value(cell, old, comma[name])
                if new != old.strip():
                    changes[name] = new
            if not changes:
                report.unchanged += 1
                continue
            try:
                with _savepoint(uow):
                    table.update(uow, item, changes, actor, now)
            except _ROW_ERRORS as error:
                report.errors.append({"row": number, "id": item.id, "title": item.title, "reason": str(error)})
                continue
            report.updated.append({"row": number, "id": item.id, "title": item.title, "changes": [
                {"field": name, "title": titles[name], "old": item.values.get(name, ""), "new": value}
                for name, value in changes.items()]})
        if report.updated or report.created:
            table.finish(uow, actor, now)
            if not dry_run:
                uow.commit()
                report.applied = True
    return report


def _counts(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _row_title(values: Mapping[str, str], columns: Sequence[Mapping[str, str]]) -> str:
    for column in columns:
        if values.get(column["field"]):
            return values[column["field"]]
    return ""
