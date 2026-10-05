"""Номенклатура ↔ procurement lines: the one place where the item master meets procurement's tables.

Reads procurement lines (facts for the card, the list and «Собрать из закупок») and writes the purchasing side
(items, aliases, line links). Price logic is procurement's `unit_price_with_vat` (price_history), article key is
`article_key`/`code_key`."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.procurement.domain import ProcurementStatus
from app.modules.procurement.infrastructure import ProcurementLineRow as R
from app.modules.procurement.price_history import article_key, name_key, unit_price_with_vat
from app.modules.purchasing import items as items_repo
from app.modules.purchasing.infrastructure import ItemRow, LineItemRow, SupplierNameRow
from app.modules.purchasing.suppliers import supplier_key
from app.shared.identity import Actor

CLOSED_STATUSES = (ProcurementStatus.DONE.value, ProcurementStatus.CANCELLED.value)
_CHUNK = 500


@dataclass(frozen=True)
class LineFacts:
    id: UUID
    status: str
    article: str | None
    name: str
    invoice_name: str | None
    unit: str
    quantity: Decimal
    supplier: str | None
    manufacturer: str | None
    item_type: str | None
    paid_at: datetime | None
    price_with_vat: Decimal | None
    order_number: str | None


def _facts(row: R) -> LineFacts:
    return LineFacts(
        id=row.id, status=row.status, article=row.design_article, name=row.calculated_name,
        invoice_name=row.invoice_item_name, unit=row.actual_unit or row.calculated_unit,
        quantity=row.actual_quantity if row.actual_quantity is not None else row.calculated_quantity,
        supplier=row.supplier, manufacturer=row.manufacturer, item_type=row.item_type, paid_at=row.paid_at,
        price_with_vat=unit_price_with_vat(
            currency=row.currency, price=row.actual_price, vat_rate=row.actual_vat_rate,
            price_with_vat=row.actual_price_with_vat, source_price=row.source_price,
            source_price_with_vat=row.source_price_with_vat, exchange_rate=row.exchange_rate,
        ),
        order_number=row.order_number,
    )


def load_lines(procurement_uow_factory: Callable, ids: Iterable[UUID] | None = None) -> list[LineFacts]:
    """Facts of the given procurement lines (unknown ids are skipped); None = every line."""
    uow = procurement_uow_factory()
    with uow:
        if ids is None:
            return [_facts(row) for row in uow.session.scalars(select(R))]
        wanted = list(dict.fromkeys(ids))
        out: list[LineFacts] = []
        for start in range(0, len(wanted), _CHUNK):
            out += [_facts(row) for row in uow.session.scalars(select(R).where(R.id.in_(wanted[start:start + _CHUNK])))]
        return out


def line_exists(procurement_uow_factory: Callable, line_id: UUID) -> bool:
    uow = procurement_uow_factory()
    with uow:
        return uow.session.scalar(select(R.id).where(R.id == line_id)) is not None


# ---- card and list statistics ------------------------------------------------------------------------------------

def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _qty(value: Decimal) -> str:
    return format(value.normalize(), "f")


def item_stats(lines: Iterable[LineFacts]) -> dict:
    """Demand (open lines by unit) and the price history of the paid ones (newest first) with min / average / last."""
    lines = list(lines)
    demand: dict[str, list] = {}
    for line in lines:
        if line.status in CLOSED_STATUSES:
            continue
        slot = demand.setdefault(line.unit, [Decimal(0), 0])
        slot[0] += line.quantity
        slot[1] += 1
    paid = sorted(
        (line for line in lines if line.paid_at is not None and line.price_with_vat is not None),
        key=lambda line: line.paid_at.replace(tzinfo=None), reverse=True,
    )
    prices = [line.price_with_vat for line in paid]
    return {
        "linked_lines": len(lines),
        "demand": [{"unit": unit, "quantity": _qty(total), "lines": count} for unit, (total, count) in sorted(demand.items())],
        "history": [
            {
                "line_id": str(line.id), "date": line.paid_at.date().isoformat(), "supplier": line.supplier,
                "unit_price_with_vat": _money(line.price_with_vat), "quantity": _qty(line.quantity), "unit": line.unit,
                "order_number": line.order_number,
            }
            for line in paid
        ],
        "price": {
            "min": _money(min(prices)) if prices else None,
            "avg": _money(sum(prices) / len(prices)) if prices else None,
            "last": _money(prices[0]) if prices else None,
        },
    }


def stats_by_item(session, procurement_uow_factory: Callable, item_ids: Iterable[UUID]) -> dict[UUID, dict]:
    """item_stats for each item id (items without lines get the empty stats)."""
    ids = list(item_ids)
    links: dict[UUID, list[UUID]] = {item_id: [] for item_id in ids}
    for start in range(0, len(ids), _CHUNK):
        for line_id, item_id in session.execute(
            select(LineItemRow.procurement_line_id, LineItemRow.item_id).where(LineItemRow.item_id.in_(ids[start:start + _CHUNK]))
        ):
            links[item_id].append(line_id)
    facts = {line.id: line for line in load_lines(procurement_uow_factory, [i for lst in links.values() for i in lst])}
    return {item_id: item_stats(facts[i] for i in line_ids if i in facts) for item_id, line_ids in links.items()}


# ---- «Собрать из закупок» -------------------------------------------------------------------------------------------

def _majority(values: Iterable[str | None]) -> str | None:
    """The most frequent non-empty value (by name_key), shown in its commonest spelling; ties go to the shortest."""
    spellings = Counter(" ".join(v.split()) for v in values if v and v.strip())
    if not spellings:
        return None
    by_key: Counter = Counter()
    for text, count in spellings.items():
        by_key[name_key(text)] += count
    best = min(by_key, key=lambda key: (-by_key[key], len(key), key))
    return max((t for t in spellings if name_key(t) == best), key=lambda t: (spellings[t], -len(t)))


@dataclass
class Group:
    kind: str  # "article" | "name"
    key: str
    lines: list[LineFacts]


def group_lines(lines: Iterable[LineFacts]) -> list[Group]:
    """One group per part key (design_article); lines without a usable article go by the alias_key of the name.
    Article groups come first, so a name group that repeats an article group's name links to it."""
    by_article: dict[str, Group] = {}
    by_name: dict[str, Group] = {}
    for line in lines:
        article = article_key(line.article)
        if article:
            by_article.setdefault(article, Group("article", article, [])).lines.append(line)
            continue
        key = items_repo.alias_key(line.name or line.invoice_name)
        if key:
            by_name.setdefault(key, Group("name", key, [])).lines.append(line)
    return sorted(by_article.values(), key=lambda g: g.key) + sorted(by_name.values(), key=lambda g: g.key)


def run_import(uow, procurement_uow_factory: Callable, actor: Actor, now: datetime) -> dict:
    """Creates / links items for every unlinked procurement line. The caller commits (apply) or leaves the unit of
    work uncommitted (dry run): both paths run the same code, so the preview is exactly what apply does."""
    session = uow.session
    linked = set(session.scalars(select(LineItemRow.procurement_line_id)))
    lines = [line for line in load_lines(procurement_uow_factory) if line.id not in linked]
    index = items_repo.build_index(session)
    items = {row.id: row for row in session.scalars(select(ItemRow))}
    supplier_ids = {key: supplier_id for key, supplier_id in session.execute(select(SupplierNameRow.name_key, SupplierNameRow.supplier_id))}
    report = []
    created = linked_groups = aliases_added = lines_linked = 0

    for group in group_lines(lines):
        name = _majority(line.name for line in group.lines) or _majority(line.invoice_name for line in group.lines) or ""
        part_number = _majority(line.article for line in group.lines) if group.kind == "article" else None
        unit = items_repo.normalize_unit(_majority(items_repo.normalize_unit(line.unit) for line in group.lines))
        manufacturer = _majority(line.manufacturer for line in group.lines)
        item_type = _majority(line.item_type for line in group.lines)
        category = items_repo.guess_category(name, item_type)

        target_id, reason = None, None
        if group.kind == "article" and group.key in index.by_part:
            target_id, reason = index.by_part[group.key], "part_key"
        else:
            found = index.by_name.get(items_repo.alias_key(name))
            # A name never links across different part numbers: an article group only meets items without one.
            if found is not None and (group.kind == "name" or not items[found].part_key):
                target_id, reason = found, "alias"
        entry = {
            "kind": group.kind, "key": group.key, "name": name, "part_number": part_number, "unit": unit,
            "category": category, "manufacturer": manufacturer, "lines": len(group.lines),
        }
        if target_id is None:
            item = items_repo.create_item(
                uow, name=name, actor=actor, now=now, part_number=part_number, manufacturer=manufacturer,
                category=category, item_type=item_type, unit=unit, source="procurement_import",
            )
            items[item.id] = item
            if item.part_key:
                index.by_part[item.part_key] = item.id
            index.by_name.setdefault(items_repo.alias_key(item.name), item.id)
            target_id = item.id
            created += 1
            entry.update(action="create", reason="new")
        else:
            linked_groups += 1
            entry.update(action="link", reason=reason, existing_name=items[target_id].name, archived=items[target_id].archived_at is not None)
        target = items[target_id]
        entry["item_id"] = str(target_id)

        own = items_repo.alias_key(target.name)
        for line in group.lines:
            for text, supplier_text in ((line.name, None), (line.invoice_name, line.supplier)):
                key = items_repo.alias_key(text)
                if not key or key == own:
                    continue
                supplier_id = supplier_ids.get(supplier_key(supplier_text)) if supplier_text else None
                if supplier_id is None and index.by_name.get(key, target_id) != target_id:
                    continue  # a generic name that already belongs to another item is not stolen
                try:
                    _, added = items_repo.add_alias(uow, target_id, text, supplier_id, actor, now)
                except items_repo.ItemConflict:
                    continue
                aliases_added += added
                index.by_name.setdefault(key, target_id)
            if target.archived_at is None:
                items_repo.link_line(uow, line.id, target_id, actor, now)
                lines_linked += 1
        report.append(entry)
    return {
        "groups": report, "created": created, "linked_groups": linked_groups, "aliases": aliases_added,
        "lines": lines_linked,
    }
