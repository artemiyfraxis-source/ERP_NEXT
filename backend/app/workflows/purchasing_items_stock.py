"""Номенклатура ↔ склад и приёмка: where the item master meets warehouse lots and receiving lines.

Chain: lot.source_receiving_ids → receiving_lines.procurement_line_id → purchasing_line_items.item_id. An explicit
purchasing_lot_items row (manual choice or an earlier match) wins over the chain. Lots with neither are matched by
part key of the article, then by the name key (aliases of any supplier, the item's own name). Reads of warehouse and
receiving tables happen here, never inside purchasing/."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.procurement.price_history import article_key
from app.modules.purchasing import items as items_repo
from app.modules.purchasing.infrastructure import ItemAliasRow, ItemRow, LineItemRow, LotItemRow
from app.modules.receiving.infrastructure import ReceivingLineRow as RC
from app.modules.warehouse.infrastructure import StockLotRow as LOT
from app.shared.identity import Actor
from app.workflows.purchasing_items_lines import _majority, _qty

_CHUNK = 500


@dataclass(frozen=True)
class LotFacts:
    id: UUID
    warehouse: str
    grn: str
    package: str
    article: str
    name: str
    item_type: str
    unit: str
    storage_location: str
    manufacturer: str
    balance: Decimal
    reserved: Decimal
    receiving_ids: tuple[str, ...]

    @property
    def available(self) -> Decimal:
        return self.balance - self.reserved


def _lot(row: LOT) -> LotFacts:
    return LotFacts(
        id=row.id, warehouse=row.warehouse, grn=row.grn, package=row.package, article=row.article, name=row.name,
        item_type=row.item_type, unit=row.unit, storage_location=row.storage_location, manufacturer=row.manufacturer or "",
        balance=row.inbound - row.written_off, reserved=row.reserved,
        receiving_ids=tuple(str(i) for i in (row.source_receiving_ids or [])),
    )


def _chunks(values: list, size: int = _CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def load_lots(warehouse_factory: Callable | None, ids: Iterable[UUID] | None = None) -> list[LotFacts]:
    """Lots by id (unknown skipped); None = every lot."""
    if warehouse_factory is None:
        return []
    uow = warehouse_factory()
    with uow:
        if ids is None:
            return [_lot(row) for row in uow.session.scalars(select(LOT))]
        out: list[LotFacts] = []
        for part in _chunks(list(dict.fromkeys(ids))):
            out += [_lot(row) for row in uow.session.scalars(select(LOT).where(LOT.id.in_(part)))]
        return out


def lot_exists(warehouse_factory: Callable | None, lot_id: UUID) -> bool:
    if warehouse_factory is None:
        return False
    uow = warehouse_factory()
    with uow:
        return uow.session.scalar(select(LOT.id).where(LOT.id == lot_id)) is not None


def _receiving_procurement(receiving_factory: Callable | None, ids: Iterable[UUID]) -> dict[UUID, UUID]:
    """receiving line id → procurement line id."""
    if receiving_factory is None:
        return {}
    uow = receiving_factory()
    with uow:
        out: dict[UUID, UUID] = {}
        for part in _chunks(list(dict.fromkeys(ids))):
            for rid, pid in uow.session.execute(select(RC.id, RC.procurement_line_id).where(RC.id.in_(part))):
                out[rid] = pid
        return out


def _uuid(text: str) -> UUID | None:
    try:
        return UUID(str(text))
    except ValueError:
        return None


def resolve_lots(session, receiving_factory: Callable | None, lots: list[LotFacts]) -> dict[UUID, tuple[UUID, str]]:
    """lot id → (item id, via 'manual' | 'match' | 'chain'); lots without an item are absent."""
    explicit = {
        lot_id: (item_id, source)
        for part in _chunks([lot.id for lot in lots])
        for lot_id, item_id, source in session.execute(
            select(LotItemRow.lot_id, LotItemRow.item_id, LotItemRow.source).where(LotItemRow.lot_id.in_(part))
        )
    }
    chain_ids = {
        rid for lot in lots if lot.id not in explicit for text in lot.receiving_ids if (rid := _uuid(text)) is not None
    }
    procurement = _receiving_procurement(receiving_factory, chain_ids)
    item_of_line = {
        line_id: item_id
        for part in _chunks(list(set(procurement.values())))
        for line_id, item_id in session.execute(
            select(LineItemRow.procurement_line_id, LineItemRow.item_id).where(LineItemRow.procurement_line_id.in_(part))
        )
    }
    out: dict[UUID, tuple[UUID, str]] = {}
    for lot in lots:
        if lot.id in explicit:
            out[lot.id] = explicit[lot.id]
            continue
        for text in lot.receiving_ids:
            rid = _uuid(text)
            item_id = item_of_line.get(procurement.get(rid)) if rid is not None else None
            if item_id is not None:
                out[lot.id] = (item_id, "chain")
                break
    return out


# ---- batch reads for the warehouse and receiving screens -----------------------------------------------------------

def _brief(item: ItemRow, via: str) -> dict:
    return {"item_id": str(item.id), "name": item.name, "category": item.category, "via": via}


def _items(session, ids: Iterable[UUID]) -> dict[UUID, ItemRow]:
    wanted = list(set(ids))
    return {row.id: row for part in _chunks(wanted) for row in session.scalars(select(ItemRow).where(ItemRow.id.in_(part)))}


def items_for_lots(session, warehouse_factory, receiving_factory, ids: list[UUID]) -> dict[str, dict]:
    lots = load_lots(warehouse_factory, ids)
    resolved = resolve_lots(session, receiving_factory, lots)
    items = _items(session, (item_id for item_id, _ in resolved.values()))
    return {str(lot_id): _brief(items[item_id], via) for lot_id, (item_id, via) in resolved.items() if item_id in items}


def items_for_receiving(session, receiving_factory, ids: list[UUID]) -> dict[str, dict]:
    procurement = _receiving_procurement(receiving_factory, ids)
    item_of_line = {
        line_id: item_id
        for part in _chunks(list(set(procurement.values())))
        for line_id, item_id in session.execute(
            select(LineItemRow.procurement_line_id, LineItemRow.item_id).where(LineItemRow.procurement_line_id.in_(part))
        )
    }
    items = _items(session, item_of_line.values())
    return {
        str(rid): _brief(items[item_of_line[pid]], "chain")
        for rid, pid in procurement.items() if item_of_line.get(pid) in items
    }


# ---- card and list ------------------------------------------------------------------------------------------------

def _totals(rows: list[dict]) -> list[dict]:
    by_unit: dict[str, list[Decimal]] = {}
    for row in rows:
        slot = by_unit.setdefault(row["unit"], [Decimal(0), Decimal(0), Decimal(0)])
        slot[0] += Decimal(row["balance"])
        slot[1] += Decimal(row["reserved"])
        slot[2] += Decimal(row["available"])
    return [
        {"unit": unit, "balance": _qty(b), "reserved": _qty(r), "available": _qty(a)}
        for unit, (b, r, a) in sorted(by_unit.items())
    ]


def lots_by_item(session, warehouse_factory, receiving_factory, item_ids: set[UUID] | None = None) -> dict[UUID, list[tuple[LotFacts, str]]]:
    """item id → [(lot, via)] over every lot, resolved in one pass (no per-item queries)."""
    lots = {lot.id: lot for lot in load_lots(warehouse_factory)}
    out: dict[UUID, list[tuple[LotFacts, str]]] = {}
    for lot_id, (item_id, via) in resolve_lots(session, receiving_factory, list(lots.values())).items():
        if item_ids is None or item_id in item_ids:
            out.setdefault(item_id, []).append((lots[lot_id], via))
    return out


def stock_of_item(session, warehouse_factory, receiving_factory, item_id: UUID) -> dict:
    linked = lots_by_item(session, warehouse_factory, receiving_factory, {item_id}).get(item_id, [])
    rows = [
        {
            "lot_id": str(lot.id), "warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package, "name": lot.name,
            "storage_location": lot.storage_location, "unit": lot.unit, "balance": _qty(lot.balance),
            "reserved": _qty(lot.reserved), "available": _qty(lot.available), "via": via,
        }
        for lot, via in sorted(linked, key=lambda pair: (pair[0].warehouse, pair[0].grn, pair[0].package))
    ]
    return {"lots": rows, "totals": _totals(rows)}


def available_by_item(session, warehouse_factory, receiving_factory, item_ids: list[UUID]) -> dict[UUID, dict]:
    """item id → {available, unit, lots}: available is the sum over linked lots when they share one unit, else None."""
    found = lots_by_item(session, warehouse_factory, receiving_factory, set(item_ids))
    out: dict[UUID, dict] = {}
    for item_id in item_ids:
        pairs = found.get(item_id, [])
        units = {lot.unit for lot, _ in pairs}
        total = sum((lot.available for lot, _ in pairs), Decimal(0))
        out[item_id] = {
            "available": _qty(total) if pairs and len(units) == 1 else None,
            "available_unit": next(iter(units)) if len(units) == 1 else None,
            "stock_lots": len(pairs),
        }
    return out


def receipts_of_item(session, receiving_factory: Callable | None, item_id: UUID) -> list[dict]:
    """Receiving lines of the procurement lines linked to the item, newest first."""
    line_ids = items_repo.linked_line_ids(session, item_id)
    if receiving_factory is None or not line_ids:
        return []
    uow = receiving_factory()
    with uow:
        rows = [row for part in _chunks(line_ids) for row in uow.session.scalars(select(RC).where(RC.procurement_line_id.in_(part)))]
        out = []
        for row in rows:
            when = row.document_date or row.inspected_on or row.created_at.date()
            out.append({
                "receiving_id": str(row.id), "date": when.isoformat(), "document_number": row.document_number or None,
                "supplier": row.supplier, "unit": row.unit, "ordered": _qty(row.ordered_quantity),
                "fact": _qty(row.fact_quantity) if row.fact_quantity is not None else None, "status": row.status,
                "verdict": row.verdict, "grn": row.grn, "package": row.package,
            })
    return sorted(out, key=lambda r: (r["date"], r["receiving_id"]), reverse=True)


# ---- matching and «Собрать со склада» --------------------------------------------------------------------------------

@dataclass
class MatchIndex:
    by_part: dict[str, UUID]
    by_name: dict[str, set[UUID]]
    items: dict[UUID, ItemRow]

    def add_item(self, item: ItemRow) -> None:
        self.items[item.id] = item
        if item.part_key:
            self.by_part[item.part_key] = item.id
        self.by_name.setdefault(items_repo.alias_key(item.name), set()).add(item.id)


def build_match_index(session) -> MatchIndex:
    index = MatchIndex({}, {}, {})
    for row in session.scalars(select(ItemRow).where(ItemRow.archived_at.is_(None))):
        index.add_item(row)
    for alias in session.scalars(select(ItemAliasRow)):
        if alias.item_id in index.items:
            index.by_name.setdefault(alias.alias_key, set()).add(alias.item_id)
    return index


def _real_article(lot: LotFacts) -> str:
    """Warehouse fills an empty article with the name: that is not a part number."""
    if not lot.article.strip() or items_repo.alias_key(lot.article) == items_repo.alias_key(lot.name):
        return ""
    return lot.article


def match_lot(lot: LotFacts, index: MatchIndex) -> tuple[str, list[UUID]]:
    """('part_key'|'name', [item id]) — one id is a match, several are ambiguous, none is ('none', [])."""
    key = items_repo.part_key(lot.article)
    if key and key in index.by_part:
        return "part_key", [index.by_part[key]]
    found = index.by_name.get(items_repo.alias_key(lot.name), set())
    if found:
        return "name", sorted(found, key=str)
    return "none", []


@dataclass
class _Group:
    kind: str
    key: str
    lots: list[LotFacts]


def _group(lots: Iterable[LotFacts]) -> list[_Group]:
    by_article: dict[str, _Group] = {}
    by_name: dict[str, _Group] = {}
    for lot in lots:
        article = article_key(_real_article(lot))
        if article:
            by_article.setdefault(article, _Group("article", article, [])).lots.append(lot)
            continue
        key = items_repo.alias_key(lot.name)
        if key:
            by_name.setdefault(key, _Group("name", key, [])).lots.append(lot)
    return sorted(by_article.values(), key=lambda g: g.key) + sorted(by_name.values(), key=lambda g: g.key)


def _lot_json(lot: LotFacts) -> dict:
    return {"lot_id": str(lot.id), "warehouse": lot.warehouse, "grn": lot.grn, "package": lot.package,
            "name": lot.name, "article": lot.article}


def run_import(uow, warehouse_factory, receiving_factory, actor: Actor, now: datetime) -> dict:
    """Links lots without an item (neither explicit nor through the chain) and proposes items for the rest. The
    caller commits (apply) or rolls back (dry run); both run this same code. Warehouse is read before any write."""
    session = uow.session
    lots = load_lots(warehouse_factory)
    resolved = resolve_lots(session, receiving_factory, lots)
    pending = sorted((lot for lot in lots if lot.id not in resolved), key=lambda l: (l.warehouse, l.grn, l.package))
    index = build_match_index(session)

    linked: list[dict] = []
    ambiguous: list[dict] = []
    unmatched: list[LotFacts] = []
    for lot in pending:
        reason, ids = match_lot(lot, index)
        if len(ids) == 1:
            items_repo.link_lot(uow, lot.id, ids[0], "match", actor, now)
            linked.append({**_lot_json(lot), "item_id": str(ids[0]), "item_name": index.items[ids[0]].name, "reason": reason})
        elif ids:
            ambiguous.append({**_lot_json(lot), "candidates": [{"item_id": str(i), "name": index.items[i].name} for i in ids]})
        else:
            unmatched.append(lot)

    created: list[dict] = []
    for group in _group(unmatched):
        # An earlier group may have created the item this group's name belongs to.
        hit = None
        if group.kind == "name":
            names = index.by_name.get(group.key, set())
            if len(names) == 1:
                hit = next(iter(names))
        name = _majority(l.name for l in group.lots) or ""
        part_number = _majority(_real_article(l) for l in group.lots) if group.kind == "article" else None
        unit = items_repo.normalize_unit(_majority(items_repo.normalize_unit(l.unit) for l in group.lots))
        if hit is None:
            item_type = _majority(l.item_type for l in group.lots)
            item = items_repo.create_item(
                uow, name=name, actor=actor, now=now, part_number=part_number,
                manufacturer=_majority(l.manufacturer for l in group.lots), category=items_repo.guess_category(name, item_type),
                item_type=item_type, unit=unit, source="stock_import",
            )
            index.add_item(item)
            own = items_repo.alias_key(item.name)
            for text in dict.fromkeys(" ".join(l.name.split()) for l in group.lots):
                key = items_repo.alias_key(text)
                if key and key != own and index.by_name.get(key, {item.id}) <= {item.id}:
                    items_repo.add_alias(uow, item.id, text, None, actor, now)
                    index.by_name.setdefault(key, set()).add(item.id)
            created.append({
                "kind": group.kind, "key": group.key, "item_id": str(item.id), "name": item.name,
                "part_number": item.part_number, "unit": item.unit, "category": item.category,
                "lots": [_lot_json(l) for l in group.lots],
            })
            hit = item.id
        else:
            linked.extend({**_lot_json(l), "item_id": str(hit), "item_name": index.items[hit].name, "reason": "name"} for l in group.lots)
        for lot in group.lots:
            items_repo.link_lot(uow, lot.id, hit, "match", actor, now)
    return {"linked": linked, "create": created, "ambiguous": ambiguous, "lots": len(pending)}
