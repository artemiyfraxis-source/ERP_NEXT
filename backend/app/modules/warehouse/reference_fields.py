"""Reference fields of the stock in bulk (SK-14): the item type dictionary, renaming a type in every row, and the
export / import of a table the storekeeper edits in Excel (SUPPLYMAP Stock05.gs:1085-1383, Portal.gs:947-996).

Only REFERENCE fields go this way. Quantities are never taken from a table on purpose: stock moves by
write-off, return and receipt, each with its own trace; a table that went through Excel must not silently
rewrite БАЛАНС. A row is found by склад + ГРН + пакет, like the manual row edit."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import UnitOfWork, edited_unit_mass
from .domain import LOT_ROW_FIELDS, WAREHOUSES, StockLot, WarehouseValidationError, lot_row_changes
from .item_types import ITEM_TYPES_CANON, canonical_item_type, merge_item_types, normalize_item_types


ITEM_TYPES_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:warehouse:item-types")
BULK_EDIT_LIMIT = 5000  # STOCK05_BULK_LIMIT_
# STOCK05_EDIT_TEXT_ (Stock05.gs:982-989): the fields a bulk edit may set, in the order of the journal.
BULK_EDIT_FIELDS = (
    "article", "series", "name", "item_type", "unit", "purpose", "storage_location", "unit_mass_g",
    "expiry_date", "manufacturer", "description", "storage_conditions",
)
# Column widths of warehouse_lots; storage_location, mass and date are checked by their own rules.
_TEXT_LIMITS = {
    "article": 1024, "series": 255, "name": 1024, "item_type": 255, "unit": 32, "purpose": 128,
    "manufacturer": 255, "description": 4096, "storage_conditions": 1024,
}
_SHEET_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


# ── Item type dictionary ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemTypeDictionary:
    types: list[str]  # the dictionary itself: saved, or the canonical list
    counts: dict[str, int]  # stock rows per type as written in the rows (stock05TypeCounts_)
    choices: list[str]  # the dropdown: the dictionary + the canonical types of the stock as a tail (stock05Refs_)


def _dictionary_types(uow: UnitOfWork) -> list[str]:
    return uow.item_types.saved() or list(ITEM_TYPES_CANON)


def item_types(uow: UnitOfWork) -> list[str]:
    """The dictionary alone (no stock tail): what a УПД model may name as a line's type."""
    with uow:
        return _dictionary_types(uow)


def item_type_dictionary(uow: UnitOfWork) -> ItemTypeDictionary:
    with uow:
        types = _dictionary_types(uow)
        counts: dict[str, int] = {}
        for lot in uow.warehouse.every():
            written = lot.item_type.strip()
            if written:
                counts[written] = counts.get(written, 0) + 1
        tail = sorted({canonical_item_type(written) for written in counts})
        return ItemTypeDictionary(types=types, counts=counts, choices=merge_item_types(types, tail))


def save_item_types(values: list[str], actor: Actor, now: datetime, uow: UnitOfWork) -> list[str]:
    """stock05TypesSave_ + its journal line (Portal.gs:956-968)."""
    types = normalize_item_types(values)
    if not types:
        raise WarehouseValidationError("Список типов пуст — нужен хотя бы один.")
    with uow:
        uow.item_types.replace(types)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=ITEM_TYPES_AGGREGATE, event_type="warehouse.item_types_saved",
            actor=actor, occurred_at=now,
            details={"types": json.dumps(types, ensure_ascii=False), "count": str(len(types))},
        ))
        uow.commit()
    return types


# ── Bulk edit: the import of a table and the rename of a type ───────────────────────────────────────


@dataclass(frozen=True)
class ReferenceEdit:
    """One row of an imported table. `fields` holds the columns the table has (a missing column is not
    touched), as text; the warehouse may be empty — then the first lot with this ГРН + пакет is taken."""

    grn: str
    package: str = ""
    warehouse: str = ""
    fields: dict[str, str] | None = None


@dataclass(frozen=True)
class BulkChange:
    warehouse: str
    grn: str
    package: str
    name: str  # article, or the name when there is no article — what the preview shows
    column: str
    was: str
    now: str


@dataclass(frozen=True)
class BulkRowError:
    warehouse: str
    grn: str
    package: str
    error: str


@dataclass(frozen=True)
class BulkEditResult:
    applied: bool
    found: int  # table rows found on the stock
    rows: int  # stock rows that change
    changes: tuple[BulkChange, ...]
    missing: tuple[str, ...]  # «склад ГРН / пакет» of table rows not found on the stock
    errors: tuple[BulkRowError, ...]  # rows skipped because a value breaks the lot (ERP only)


def _expiry(text: str) -> date | None:
    """СРОК ГОДНОСТИ from a table: empty clears it; ДД.ММ.ГГГГ (the export and Russian Excel) or ГГГГ-ММ-ДД."""
    if not text:
        return None
    match = _SHEET_DATE.fullmatch(text)
    try:
        if match:
            return date(int(match[3]), int(match[2]), int(match[1]))
        return date.fromisoformat(text)
    except ValueError as error:
        raise WarehouseValidationError(f"Срок годности — дата ДД.ММ.ГГГГ, а не «{text}»") from error


def row_edit_values(fields: dict[str, str]) -> dict[str, object]:
    """stock05RowDiff_ on the way in: a field that is not in the table is not touched, the type goes to its
    canonical form, an empty name is NOT written (wiping the name by import is almost surely a shifted
    column, not an intent); any other empty text clears the field. → changes for `StockLot.edit_row`."""
    values: dict[str, object] = {}
    for field_name in BULK_EDIT_FIELDS:
        if field_name not in fields:
            continue
        text = str(fields[field_name] if fields[field_name] is not None else "").strip()
        if field_name == "item_type":
            text = canonical_item_type(text)
        if field_name == "name" and not text:
            continue
        limit = _TEXT_LIMITS.get(field_name)
        if limit is not None and len(text) > limit:
            raise WarehouseValidationError(f"{LOT_ROW_FIELDS[field_name]}: длиннее {limit} символов")
        if field_name == "unit_mass_g":
            values[field_name] = edited_unit_mass(text)
        elif field_name == "expiry_date":
            values[field_name] = _expiry(text)
        else:
            values[field_name] = text
    return values


def _label(warehouse: str, grn: str, package: str) -> str:
    return (f"{warehouse} " if warehouse else "") + grn + (f" / {package}" if package else "")


def _bulk_edit(
    edits: list[tuple[StockLot | None, ReferenceEdit]],
    apply: bool,
    actor: Actor,
    now: datetime,
    reason: str,
    uow: UnitOfWork,
) -> BulkEditResult:
    """stock05BulkEditLocked_: the diff of every row, and with `apply` the write — one version bump and one
    `warehouse.row_edited` event per changed row, «КОЛОНКА: «было» → «стало»» as in the row edit journal.
    Runs inside the caller's `with uow`; commits only with `apply`."""
    original: dict[UUID, StockLot] = {}
    current: dict[UUID, StockLot] = {}
    changes: list[BulkChange] = []
    missing: list[str] = []
    errors: list[BulkRowError] = []
    touched: dict[UUID, list[tuple[str, str, str, str]]] = {}
    for lot, edit in edits:
        grn, package = edit.grn.strip(), edit.package.strip()
        if lot is None:
            missing.append(_label(edit.warehouse.strip(), grn, package))
            continue
        original.setdefault(lot.id, lot)
        before = current.get(lot.id, lot)
        try:
            after = before.edit_row(row_edit_values(edit.fields or {}))
        except WarehouseValidationError as error:
            errors.append(BulkRowError(lot.warehouse, lot.grn, lot.package, str(error)))
            continue
        diff = lot_row_changes(before, after)
        if not diff:
            continue
        current[lot.id] = after
        touched.setdefault(lot.id, []).extend(diff)
        changes.extend(
            BulkChange(lot.warehouse, lot.grn, lot.package, before.article or before.name, column, was, now_value)
            for _, column, was, now_value in diff
        )
    if apply and touched:
        for lot_id, diff in touched.items():
            # One version step per stock row, however many table rows touched it.
            updated = replace(current[lot_id], version=original[lot_id].version + 1)
            details = {
                "grn": updated.grn,
                "package": updated.package,
                "warehouse": updated.warehouse,
                "reason": reason,
                "changes": "; ".join(f"{column}: «{was or '—'}» → «{value or '—'}»" for _, column, was, value in diff),
            }
            for field_name, _, was, value in diff:
                details.setdefault(f"{field_name}_from", was)
                details[f"{field_name}_to"] = value
            uow.warehouse.add(updated)
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=lot_id, event_type="warehouse.row_edited",
                actor=actor, occurred_at=now, details=details,
            ))
        uow.commit()
    return BulkEditResult(
        applied=apply, found=sum(lot is not None for lot, _ in edits), rows=len(touched), changes=tuple(changes), missing=tuple(missing), errors=tuple(errors),
    )


def import_reference_fields(
    edits: list[ReferenceEdit], apply: bool, actor: Actor, now: datetime, uow: UnitOfWork,
) -> BulkEditResult:
    """stock05BulkEdit_ for «Загрузить правки склада»: apply=False is the preview — the same diff, nothing
    written. The preview and the write are two calls: the write diffs against the stock as it is then."""
    if len(edits) > BULK_EDIT_LIMIT:
        raise WarehouseValidationError(f"За раз не больше {BULK_EDIT_LIMIT} строк.")
    with uow:
        by_key = {lot.key: lot for lot in uow.warehouse.every()}
        matched: list[tuple[StockLot | None, ReferenceEdit]] = []
        for edit in edits:
            wanted = edit.warehouse.strip()
            # A known warehouse narrows the search; an unknown or empty one searches ЦАР, then ЖУК.
            order = (wanted,) if wanted in WAREHOUSES else WAREHOUSES
            key = (edit.grn.strip(), edit.package.strip())
            lot = next((by_key[(name, *key)] for name in order if (name, *key) in by_key), None)
            matched.append((lot, edit))
        return _bulk_edit(matched, apply, actor, now, "импорт таблицы", uow)


def rename_item_type(source: str, target: str, actor: Actor, now: datetime, uow: UnitOfWork) -> BulkEditResult:
    """stock05TypeRename_: every stock row (both warehouses, zero balance too) whose ТИП is exactly `source`
    gets `target` — through the bulk edit, so the target goes to its canonical form and every row is journaled."""
    old, new = str(source or "").strip(), str(target or "").strip()
    if not old or not new:
        raise WarehouseValidationError("Нужны оба названия: что меняем и на что.")
    with uow:
        edits = [
            (lot, ReferenceEdit(grn=lot.grn, package=lot.package, warehouse=lot.warehouse, fields={"item_type": new}))
            for lot in uow.warehouse.every()
            if lot.item_type.strip() == old
        ]
        if not edits:
            return BulkEditResult(applied=True, found=0, rows=0, changes=(), missing=(), errors=())
        return _bulk_edit(edits, True, actor, now, f"переименование типа «{old}» → «{new}»", uow)


def export_rows(uow: UnitOfWork) -> list[dict[str, str]]:
    """stock05Export_: every stock row, zero balance too, as text for the table — the key, the balance (for
    checking only, never read back) and the reference fields. The mass has a decimal comma and the date is
    ДД.ММ.ГГГГ, so Russian Excel reads them as a number and a date, not «1.5» as the 1st of May."""
    with uow:
        rows = []
        for lot in uow.warehouse.every():
            row = {"warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package,
                   "balance": format(lot.balance.normalize(), "f")}
            for field_name in BULK_EDIT_FIELDS:
                value = getattr(lot, field_name)
                if field_name == "unit_mass_g":
                    row[field_name] = "" if value is None else format(value.normalize(), "f").replace(".", ",")
                elif field_name == "expiry_date":
                    row[field_name] = value.strftime("%d.%m.%Y") if value else ""
                else:
                    row[field_name] = value
            rows.append(row)
        return rows
