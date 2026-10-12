"""Номенклатура (stage option references.items): the item master of the purchasing hybrid, as Roller_Buyer's /items.

Keys: `part_key` (normalized part number, reuses procurement's `code_key`) identifies an item; `alias_key` folds a name
(case, spaces, Latin/Cyrillic look-alikes) so the same words typed in either alphabet meet. Writes take the unit of
work (audit events are part of the transaction); nothing here reads procurement tables — see
app/workflows/purchasing_items_lines.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.workflows.article_key import code_key
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .infrastructure import (
    ITEM_CATEGORIES,
    ItemAliasRow,
    ItemRow,
    LineItemRow,
    LotItemRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    SupplierRow,
)

PAGE_SIZE = 100


class ItemNotFound(LookupError):
    pass


class ItemConflict(ValueError):
    pass


class ItemInvalid(ValueError):
    pass


# ---- keys ------------------------------------------------------------------------------------------------------

_LOOK_ALIKES = str.maketrans("авекмнорстхё", "abekmhopctxe")


def part_key(value: str | None) -> str | None:
    """Normalized part number, or None for an empty one (many items may have no part number)."""
    return code_key(value) or None


def alias_key(text: str | None) -> str:
    """casefold + collapsed spaces + Latin/Cyrillic look-alikes folded to Latin (А/A, В/B, Е/E, К/K, М/M, Н/H, О/O,
    Р/P, С/C, Т/T, Х/X, ё/е)."""
    return " ".join(str(text or "").split()).casefold().translate(_LOOK_ALIKES)


_CATEGORY_WORDS = (
    ("cable", ("кабел", "провод", "шнур", "cable", "wire")),
    ("connector", ("разъем", "разъём", "клемм", "терминал", "вилка", "розетка", "коннектор", "connector")),
    ("ekb", ("микросхем", "резистор", "конденсатор", "диод", "транзистор")),
)


def guess_category(name: str | None, item_type: str | None = None) -> str:
    """cable / connector / ekb by words of the name (the item type is the fallback), else other."""
    # The word that comes first wins: «Разъём DB-9 вилка на кабель» is a connector, «Кабель с разъёмом» a cable.
    for text in (name, item_type):
        low = str(text or "").casefold()
        hits = [(low.find(word), category) for category, words in _CATEGORY_WORDS for word in words if word in low]
        if hits:
            return min(hits)[1]
    return "other"


_UNITS = {
    "шт": ("шт", "шт.", "штука", "штук", "штуки", "pcs", "pc", "ед"),
    "м": ("м", "м.", "метр", "метра", "метров", "мет", "m"),
    "кг": ("кг", "кг.", "килограмм", "kg"),
    "л": ("л", "л.", "литр", "литров", "l"),
    "уп": ("уп", "уп.", "упак", "упак.", "упаковка", "упаковки"),
    "компл": ("компл", "компл.", "комплект", "комплекта", "к-т", "набор", "set"),
}
_UNIT_BY_SPELLING = {spelling: unit for unit, spellings in _UNITS.items() for spelling in spellings}


def normalize_unit(value: str | None) -> str:
    """шт/м/кг/л/уп/компл; an unknown unit is kept as typed, an empty one is «шт»."""
    text = " ".join(str(value or "").split())
    if not text:
        return "шт"
    return _UNIT_BY_SPELLING.get(text.casefold(), text)


# ---- helpers ---------------------------------------------------------------------------------------------------

def _clean(value: str | None) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def _audit(uow, item_id: UUID, event: str, actor: Actor, now: datetime, **details) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=item_id, event_type=f"purchasing.item_{event}", actor=actor, occurred_at=now,
        details={key: str(value) for key, value in details.items()},
    ))


def get_item(session: Session, item_id: UUID) -> ItemRow:
    row = session.get(ItemRow, item_id)
    if row is None:
        raise ItemNotFound("Позиция не найдена")
    return row


def _article_conflict(session: Session, key: str | None, own_id: UUID | None) -> None:
    if not key:
        return
    other = session.scalar(select(ItemRow).where(ItemRow.part_key == key))
    if other is not None and other.id != own_id:
        raise ItemConflict(f"Такой артикул уже есть: {other.name}")


def _category(value: str | None, name: str) -> str:
    if value is None:
        return guess_category(name)
    if value not in ITEM_CATEGORIES:
        raise ItemInvalid("Категория: кабель, разъёмы, ЭКБ или прочее")
    return value


# ---- writes ----------------------------------------------------------------------------------------------------

def create_item(
    uow, *, name: str, actor: Actor, now: datetime, part_number: str | None = None, manufacturer: str | None = None,
    category: str | None = None, item_type: str | None = None, unit: str | None = None, notes: str | None = None,
    source: str = "manual",
) -> ItemRow:
    session = uow.session
    name = _clean(name) or ""
    if not name:
        raise ItemInvalid("Наименование обязательно")
    part_number = _clean(part_number)
    key = part_key(part_number)
    _article_conflict(session, key, None)
    row = ItemRow(
        id=uuid4(), name=name, part_number=part_number, part_key=key, manufacturer=_clean(manufacturer),
        category=_category(category, name), item_type=_clean(item_type), unit=normalize_unit(unit),
        notes=_clean(notes), version=1, created_at=now, created_by=actor.id,
    )
    session.add(row)
    session.flush()
    _audit(uow, row.id, "created", actor, now, name=name, part_number=part_number or "", source=source)
    return row


_EDITABLE = ("name", "part_number", "manufacturer", "category", "item_type", "unit", "notes")


def update_item(uow, item_id: UUID, version: int, changes: dict, actor: Actor, now: datetime) -> ItemRow:
    session = uow.session
    row = get_item(session, item_id)
    if row.version != version:
        raise ItemConflict("Позицию уже изменили — обновите карточку")
    before = {field: getattr(row, field) for field in _EDITABLE}
    if "part_number" in changes:  # checked before any assignment: a query would autoflush the half-edited row
        _article_conflict(session, part_key(_clean(changes["part_number"])), row.id)
    for field in _EDITABLE:
        if field not in changes:
            continue
        value = changes[field]
        if field == "category":
            value = _category(value, row.name)
        elif field == "unit":
            value = normalize_unit(value)
        else:
            value = _clean(value)
        if field == "name" and not value:
            raise ItemInvalid("Наименование обязательно")
        setattr(row, field, value)
    if "part_number" in changes:
        row.part_key = part_key(row.part_number)
    diff = {field: getattr(row, field) or "" for field in _EDITABLE if getattr(row, field) != before[field]}
    if diff:
        row.version += 1
        row.updated_at, row.updated_by = now, actor.id
        session.flush()
        _audit(uow, row.id, "updated", actor, now, **diff)
    return row


def archive_item(uow, item_id: UUID, actor: Actor, now: datetime) -> ItemRow:
    row = get_item(uow.session, item_id)
    if row.archived_at is None:
        row.archived_at, row.updated_at, row.updated_by = now, now, actor.id
        row.version += 1
        uow.session.flush()
        _audit(uow, row.id, "archived", actor, now, name=row.name)
    return row


def add_alias(
    uow, item_id: UUID, text: str, supplier_id: UUID | None, actor: Actor, now: datetime,
) -> tuple[ItemAliasRow, bool]:
    """Another name of the item, generic or as one supplier writes it; the same text twice is a no-op.
    Returns the row and whether it was created."""
    session = uow.session
    item = get_item(session, item_id)
    text = _clean(text)
    key = alias_key(text)
    if not key:
        raise ItemInvalid("Название обязательно")
    if supplier_id is not None and session.get(SupplierRow, supplier_id) is None:
        raise ItemNotFound("Поставщик не найден")
    found = session.scalar(select(ItemAliasRow).where(
        ItemAliasRow.alias_key == key,
        ItemAliasRow.supplier_id.is_(None) if supplier_id is None else ItemAliasRow.supplier_id == supplier_id,
    ))
    if found is not None:
        if found.item_id != item.id:
            owner = session.get(ItemRow, found.item_id)
            raise ItemConflict(f"Такое название уже у позиции: {owner.name if owner else '—'}")
        return found, False
    row = ItemAliasRow(
        id=uuid4(), item_id=item.id, supplier_id=supplier_id, alias_key=key, alias_text=text or "",
        created_at=now, created_by=actor.id,
    )
    session.add(row)
    session.flush()
    _audit(uow, item.id, "alias_added", actor, now, text=text or "", supplier_id=supplier_id or "")
    return row, True


def delete_alias(uow, item_id: UUID, alias_id: UUID, actor: Actor, now: datetime) -> None:
    row = uow.session.get(ItemAliasRow, alias_id)
    if row is None or row.item_id != item_id:
        raise ItemNotFound("Название не найдено")
    text = row.alias_text
    uow.session.delete(row)
    uow.session.flush()
    _audit(uow, item_id, "alias_removed", actor, now, text=text)


def merge_items(uow, keep_id: UUID, drop_id: UUID, actor: Actor, now: datetime) -> ItemRow:
    """Moves aliases, line links and RFQ lines of `drop` to `keep`, keeps drop's name (and part number) as aliases,
    then archives drop."""
    session = uow.session
    if keep_id == drop_id:
        raise ItemInvalid("Нельзя объединить позицию с самой собой")
    keep, drop = get_item(session, keep_id), get_item(session, drop_id)
    if drop.archived_at is not None:
        raise ItemInvalid("Объединяемая позиция уже в архиве")
    keep_aliases = {
        (alias.supplier_id, alias.alias_key)
        for alias in session.scalars(select(ItemAliasRow).where(ItemAliasRow.item_id == keep.id))
    }
    moved = 0
    for alias in session.scalars(select(ItemAliasRow).where(ItemAliasRow.item_id == drop.id)).all():
        if (alias.supplier_id, alias.alias_key) in keep_aliases:
            session.delete(alias)
        else:
            alias.item_id = keep.id
            keep_aliases.add((alias.supplier_id, alias.alias_key))
            moved += 1
    session.flush()
    extra = [drop.name]
    if drop.part_key and keep.part_key != drop.part_key:
        if keep.part_key is None:
            key, number = drop.part_key, drop.part_number
            drop.part_key = None
            session.flush()
            keep.part_key, keep.part_number = key, number
        else:
            extra.append(drop.part_number or "")
    for text in extra:
        key = alias_key(text)
        if key and key != alias_key(keep.name) and (None, key) not in keep_aliases:
            session.add(ItemAliasRow(
                id=uuid4(), item_id=keep.id, supplier_id=None, alias_key=key, alias_text=_clean(text) or "",
                created_at=now, created_by=actor.id,
            ))
            keep_aliases.add((None, key))
    links = session.execute(update(LineItemRow).where(LineItemRow.item_id == drop.id).values(item_id=keep.id)).rowcount
    session.execute(update(RfqLineRow).where(RfqLineRow.item_id == drop.id).values(item_id=keep.id))
    session.execute(update(LotItemRow).where(LotItemRow.item_id == drop.id).values(item_id=keep.id))
    for row in (keep, drop):
        row.version += 1
        row.updated_at, row.updated_by = now, actor.id
    drop.archived_at = now
    session.flush()
    _audit(uow, keep.id, "merged", actor, now, dropped_id=drop.id, dropped_name=drop.name, aliases=moved, lines=links)
    _audit(uow, drop.id, "archived", actor, now, name=drop.name, merged_into=keep.id)
    return keep


def link_line(uow, procurement_line_id: UUID, item_id: UUID, actor: Actor, now: datetime) -> LineItemRow:
    """The caller has checked that the procurement line exists (workflows layer)."""
    session = uow.session
    item = get_item(session, item_id)
    if item.archived_at is not None:
        raise ItemInvalid("Позиция в архиве")
    row = session.get(LineItemRow, procurement_line_id)
    if row is None:
        row = LineItemRow(procurement_line_id=procurement_line_id, item_id=item.id, linked_at=now, linked_by=actor.id)
        session.add(row)
    elif row.item_id == item.id:
        return row
    else:
        row.item_id, row.linked_at, row.linked_by = item.id, now, actor.id
    session.flush()
    _audit(uow, item.id, "line_linked", actor, now, procurement_line_id=procurement_line_id)
    return row


def link_lot(uow, lot_id: UUID, item_id: UUID, source: str, actor: Actor, now: datetime) -> LotItemRow:
    """Explicit lot → item link; the caller has checked that the lot exists (workflows layer). `manual` overrides
    whatever is there; `match` never replaces a manual choice."""
    session = uow.session
    item = get_item(session, item_id)
    if item.archived_at is not None:
        raise ItemInvalid("Позиция в архиве")
    row = session.get(LotItemRow, lot_id)
    if row is None:
        row = LotItemRow(lot_id=lot_id, item_id=item.id, source=source, linked_at=now, linked_by=actor.id)
        session.add(row)
    elif row.item_id == item.id and (row.source == source or source == "match"):
        return row
    elif row.source == "manual" and source == "match":
        return row
    else:
        row.item_id, row.source, row.linked_at, row.linked_by = item.id, source, now, actor.id
    session.flush()
    _audit(uow, item.id, "lot_linked", actor, now, lot_id=lot_id, source=source)
    return row


def unlink_lot(uow, lot_id: UUID, actor: Actor, now: datetime) -> None:
    row = uow.session.get(LotItemRow, lot_id)
    if row is None:
        raise ItemNotFound("Явной связи партии с позицией нет")
    item_id = row.item_id
    uow.session.delete(row)
    uow.session.flush()
    _audit(uow, item_id, "lot_unlinked", actor, now, lot_id=lot_id)


# ---- reads -----------------------------------------------------------------------------------------------------

def item_json(row: ItemRow) -> dict:
    return {
        "id": str(row.id), "name": row.name, "part_number": row.part_number, "manufacturer": row.manufacturer,
        "category": row.category, "item_type": row.item_type, "unit": row.unit, "notes": row.notes,
        "archived": row.archived_at is not None, "version": row.version,
    }


def search(
    session: Session, q: str | None = None, category: str | None = None, include_archived: bool = False,
    only_ids: set[UUID] | None = None,
) -> tuple[list[ItemRow], dict[str, int]]:
    """Words in any order over name, part number, manufacturer, type and aliases (look-alike letters fold).
    Returns the rows (name order) and the per-category counts BEFORE the category filter."""
    rows = list(session.scalars(select(ItemRow).order_by(ItemRow.name)))
    if not include_archived:
        rows = [row for row in rows if row.archived_at is None]
    if only_ids is not None:
        rows = [row for row in rows if row.id in only_ids]
    words = [word for word in str(q or "").split() if word]
    if words:
        texts: dict[UUID, list[str]] = {}
        for alias in session.scalars(select(ItemAliasRow)):
            texts.setdefault(alias.item_id, []).append(alias.alias_text)
        wanted = [(alias_key(word), code_key(word)) for word in words]
        found = []
        for row in rows:
            parts = [row.name, row.part_number or "", row.manufacturer or "", row.item_type or "", *texts.get(row.id, [])]
            hay = alias_key(" ".join(parts))
            key = row.part_key or ""
            if all(a in hay or (len(c) >= 3 and c in key) for a, c in wanted):
                found.append(row)
        rows = found
    counts = {name: 0 for name in ITEM_CATEGORIES}
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1
    if category:
        rows = [row for row in rows if row.category == category]
    return rows, counts


def aliases_of(session: Session, item_id: UUID) -> list[dict]:
    names = {row.id: row.name for row in session.scalars(select(SupplierRow))}
    rows = session.scalars(
        select(ItemAliasRow).where(ItemAliasRow.item_id == item_id).order_by(ItemAliasRow.alias_text)
    ).all()
    return [
        {
            "id": str(row.id), "text": row.alias_text, "supplier_id": str(row.supplier_id) if row.supplier_id else None,
            "supplier_name": names.get(row.supplier_id) if row.supplier_id else None,
        }
        for row in rows
    ]


def linked_line_ids(session: Session, item_id: UUID) -> list[UUID]:
    return list(session.scalars(select(LineItemRow.procurement_line_id).where(LineItemRow.item_id == item_id)))


def quotes_of(session: Session, item_id: UUID, limit: int = 20) -> list[dict]:
    """Quote lines that answered an RFQ line carrying this item, newest first; price with VAT (grossUnit)."""
    rows = session.execute(
        select(QuoteLineRow, QuoteRow, SupplierRow)
        .join(RfqLineRow, RfqLineRow.id == QuoteLineRow.rfq_line_id)
        .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
        .join(SupplierRow, SupplierRow.id == QuoteRow.supplier_id)
        .where(RfqLineRow.item_id == item_id)
        .order_by(QuoteRow.received_on.desc(), QuoteRow.created_at.desc())
        .limit(limit)
    ).all()
    out = []
    for line, quote, supplier in rows:
        gross = None
        if line.unit_price is not None:
            gross = line.unit_price if line.vat_included else line.unit_price * (1 + line.vat_rate / 100)
        out.append({
            "quote_id": str(quote.id), "date": quote.received_on.isoformat(), "supplier": supplier.name,
            "unit_price_with_vat": format(gross.quantize(Decimal("0.01")), "f") if gross is not None else None,
            "moq": format(line.moq.normalize(), "f") if line.moq is not None else None,
            "declined": line.declined, "analog": line.analog,
        })
    return out


@dataclass(frozen=True)
class AliasIndex:
    """Where a name or a part key already lives: for import collisions."""

    by_part: dict[str, UUID]
    by_name: dict[str, UUID]


def build_index(session: Session) -> AliasIndex:
    by_part: dict[str, UUID] = {}
    by_name: dict[str, UUID] = {}
    for row in session.scalars(select(ItemRow)):
        if row.part_key:
            by_part[row.part_key] = row.id
        by_name.setdefault(alias_key(row.name), row.id)
    for alias in session.scalars(select(ItemAliasRow)):
        by_name.setdefault(alias.alias_key, alias.item_id)
    return AliasIndex(by_part, by_name)
