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
    FieldEdit, SaveGaugeCommand, UnitOfWork, _audit, _clean_new_fields, _create_in, _edit_in, cable_catalog,
    save_gauge_in,
)
from .cable_structure import shown
from .cables import CableKind
from .domain import (
    BASE_COLUMNS, COUNTRY_BY_MANUFACTURER, SUPPLIER_BY_LINK, ReferenceKind, ReferenceValidationError, StaleRecordError,
    TechOperationRecord, TechOperationTab, merge_country_pairs, normalized_manufacturer_key, normalized_supplier_key,
)
from .equipment import EXTRA_KEYS, TARGETS_KEY
from .importers import _MapTarget
from .technology import schema_keys
from .xlsx_file import Cell, ReadSheet, Sheet, XlsxError, read_xlsx, write_xlsx

ID_TITLE = "ID записи"
VERSION_TITLE = "Версия"
FIELDS_SHEET = "Поля"
VIA = "file"
MAX_FILE_ROWS = 10_000
COAX_DIAMETERS = ("D1", "D2", "D3")  # the cable's Ø, edited in «Коаксиальные кабели», not on the connector row

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
    """БД.ОБ / БД.ОП / БД.ТЕР / БД.КОАКС / БД.ПРОВ / БД.МАТ rows by record id."""

    titles: Mapping[str, str] = {}

    def __init__(self, tab: TechOperationTab, label: str, kinds: Mapping[str, str] | None = None) -> None:
        self.tab, self.label, self.kinds = tab, label, dict(kinds or {})

    def _records(self, uow: UnitOfWork) -> list[TechOperationRecord]:
        return list(uow.tech_operations.list(tab=self.tab, query="", limit=100_000))

    def kind_ids(self, uow: UnitOfWork, kind: str) -> set[str] | None:
        """The ids of one list of the tab (a cable list of БД.ПРОВ, «Оболочки» of БД.МАТ), read in `uow` (not open)."""
        if not kind:
            return None
        if kind not in self.kinds:
            raise RoundtripError("Неизвестный список: " + kind)
        if self.tab == TechOperationTab.WIRE:
            wanted = CableKind(kind)
            return {str(row.record.id) for row in cable_catalog(uow).rows if row.kind == wanted and row.record}
        from .coverings import build  # «Оболочки»: the БД.МАТ rows that cover a harness
        with uow:
            records = self._records(uow)
        return {str(row["id"]) for row in build([(str(item.id), item.fields) for item in records])["rows"]}

    def items(self, uow: UnitOfWork) -> list[Item]:
        return [
            Item(str(record.id), record.display_text.strip(" /") or str(record.id),
                 fingerprint(record.display_text, record.fields), dict(record.fields))
            for record in self._records(uow)
        ]

    def fields(self, items: Sequence[Item]) -> list[str]:
        """Every field the tab's edit form edits: the sheet columns (the schema values the generator reads follow them
        on save), ПРОВ / МАТ — the base's columns first; a column only some rows have is still a column."""
        keys = schema_keys(self.tab.value)
        richest = sorted(items, key=lambda item: -sum(1 for name in item.values if name not in keys))
        ordered: dict[str, None] = dict.fromkeys(BASE_COLUMNS.get(self.tab, ()))
        for item in richest:
            ordered.update(dict.fromkeys(name for name in item.values if name not in keys))
        if not ordered:  # a snapshot without the sheet's columns: the schema keys are the fields
            for item in richest:
                ordered.update(dict.fromkeys(item.values))
        if self.tab == TechOperationTab.EQUIPMENT:
            ordered.update(dict.fromkeys(EXTRA_KEYS))
            ordered.pop(TARGETS_KEY, None)  # «что обжимает» is a list kept by the tool's card, not a cell
        if self.tab == TechOperationTab.COAX:
            for name in COAX_DIAMETERS:
                ordered.pop(name, None)
        return list(ordered)

    def update(self, uow, item, changes, actor, now):
        edits = tuple(FieldEdit(name, item.values.get(name, ""), value) for name, value in changes.items())
        record, _updated = _edit_in(UUID(item.id), edits, now, uow)
        _audit("references.tech_record_edited", actor, now, {
            "record_id": str(record.id), "tab": record.tab.value, "fields": list(changes), "via": VIA,
        }, uow)

    def create(self, uow, values, actor, now):
        record = _create_in(self.tab, _clean_new_fields(self.tab, values), uow)
        _audit("references.tech_record_created", actor, now, {
            "record_id": str(record.id), "tab": record.tab.value, "via": VIA,
        }, uow)
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


CABLE_KINDS = {"coax": "Коаксиальные кабели", "wire": "Монтажные провода", "multi": "Многожильные кабели",
               "other": "Прочие кабели"}


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


def _table(tab: str) -> _Table:
    factory = _tables().get(tab)
    if factory is None:
        raise LookupError(f"Нет таблицы «{tab}» для правки файлом; есть: " + ", ".join(ROUNDTRIP_TABLES))
    return factory()


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
    table = _table(tab)
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
    table = _table(tab)
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
    table = _table(tab)
    main, service = _sheets(content)
    service_titles, meta = _service_map(service)
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
