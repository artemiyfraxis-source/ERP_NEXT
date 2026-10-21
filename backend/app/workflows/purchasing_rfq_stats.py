"""Purchasing ↔ Procurement: the purchase statistics behind the request pool and the supplier suggestion — the
`PurchaseStats` port of app.modules.purchasing.rfq_pool.

One pass: every paid procurement line is read once (only the columns used) and attributed to a nomenclature item — its
link (purchasing_line_items), else the part key or the name the way «Собрать из закупок» links
(`purchasing_items_lines.match_item`). Prices are procurement's `unit_price_with_vat` (price_history), so «покупали»
here and «Покупали ранее» on the sheet agree."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.procurement.infrastructure import ProcurementLineRow as R
from app.modules.procurement.price_history import article_key, unit_price_with_vat
from app.modules.purchasing import items as items_repo
from app.modules.purchasing.infrastructure import ItemRow, LineItemRow
from app.modules.purchasing.rfq import LineView
from app.modules.purchasing.rfq_pool import Analysis, ItemMatch, LineInsight, PastPrice, SupplierStat
from app.modules.purchasing.suppliers import is_stock_source, supplier_key
from app.workflows.purchasing_items_lines import match_item

_PAID_COLUMNS = (
    R.id, R.design_article, R.calculated_name, R.invoice_item_name, R.item_type, R.supplier, R.paid_at, R.currency,
    R.actual_price, R.actual_vat_rate, R.actual_price_with_vat, R.source_price, R.source_price_with_vat,
    R.exchange_rate, R.actual_unit, R.calculated_unit,
)


@dataclass(slots=True)
class _Paid:
    supplier: str
    on: date
    price: Decimal | None
    unit: str  # normalized (шт/м/кг/…)
    item_id: UUID | None
    category: str


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


class _Nomenclature:
    """Active items, the alias index and the line links — read once per call."""

    def __init__(self, session: Session):
        self.items = {
            row.id: row for row in session.execute(
                select(ItemRow.id, ItemRow.name, ItemRow.category, ItemRow.part_key).where(ItemRow.archived_at.is_(None))
            )
        }
        self.index = items_repo.build_index(session, active_only=True)
        self.links = dict(session.execute(select(LineItemRow.procurement_line_id, LineItemRow.item_id)).all())
        self._guesses: dict[tuple[str, str | None], str] = {}
        self._matches: dict[tuple, tuple[UUID | None, str | None]] = {}

    def match(self, line_id: UUID, article: str | None, *names: str | None) -> tuple[UUID | None, str | None]:
        linked = self.links.get(line_id)
        if linked in self.items:
            return linked, "link"
        key = (article, *names)  # the same item is bought again and again under the same words
        if key not in self._matches:
            self._matches[key] = self._match(article, names)
        return self._matches[key]

    def _match(self, article: str | None, names: tuple[str | None, ...]) -> tuple[UUID | None, str | None]:
        part = article_key(article) or None
        for name in [name for name in names if name] or [None]:
            found, how = match_item(self.index, self.items, part, name)
            if found is not None:
                return found, how
        return None, None

    def category(self, item_id: UUID | None, name: str | None, item_type: str | None) -> str:
        if item_id is not None:
            return self.items[item_id].category
        key = (name or "", item_type)
        if key not in self._guesses:
            self._guesses[key] = items_repo.guess_category(name, item_type)
        return self._guesses[key]


class PurchaseStatsBridge:
    def __init__(self, procurement_uow_factory: Callable):
        self.procurement_uow_factory = procurement_uow_factory

    def _paid(self, nomenclature: _Nomenclature) -> list[_Paid]:
        with self.procurement_uow_factory() as uow:
            rows = uow.session.execute(select(*_PAID_COLUMNS).where(R.paid_at.is_not(None))).all()
        out: list[_Paid] = []
        for row in rows:
            item_id, _ = nomenclature.match(row.id, row.design_article, row.calculated_name, row.invoice_item_name)
            out.append(_Paid(
                supplier=" ".join((row.supplier or "").split()), on=_day(row.paid_at),
                price=unit_price_with_vat(
                    currency=row.currency, price=row.actual_price, vat_rate=row.actual_vat_rate,
                    price_with_vat=row.actual_price_with_vat, source_price=row.source_price,
                    source_price_with_vat=row.source_price_with_vat, exchange_rate=row.exchange_rate,
                ),
                unit=items_repo.normalize_unit(row.actual_unit or row.calculated_unit), item_id=item_id,
                category=nomenclature.category(item_id, row.calculated_name, row.item_type),
            ))
        return out

    def analyse(self, session: Session, views: Sequence[LineView], suppliers: bool = True) -> Analysis:
        nomenclature = _Nomenclature(session)
        paid = self._paid(nomenclature)
        priced: dict[UUID, list[_Paid]] = {}
        for purchase in paid:
            if purchase.item_id is not None and purchase.price is not None:
                priced.setdefault(purchase.item_id, []).append(purchase)
        insights: dict[UUID, LineInsight] = {}
        items: set[UUID] = set()
        categories: set[str] = set()
        for view in views:
            item_id, how = nomenclature.match(view.id, view.article, view.name)
            category = nomenclature.category(item_id, view.name, view.item_type)
            categories.add(category)
            if item_id is None or how is None:
                insights[view.id] = LineInsight(category=category)
                continue
            items.add(item_id)
            row = nomenclature.items[item_id]
            history = priced.get(item_id, [])
            unit = items_repo.normalize_unit(view.unit)
            same_unit = [p.price for p in history if p.unit == unit]
            insights[view.id] = LineInsight(
                category=category,
                item=ItemMatch(id=item_id, name=row.name, category=row.category, matched_by=how),
                purchases=len(history),
                last=_past(max(history, key=lambda p: p.on)) if history else None,
                cheapest=_past(min(history, key=lambda p: (p.price, -p.on.toordinal()))) if history else None,
                cheapest_same_unit=min(same_unit) if same_unit else None,
            )
        if not suppliers:
            return Analysis(lines=insights)
        return Analysis(
            lines=insights,
            by_items=_suppliers(p for p in paid if p.item_id is not None and p.item_id in items),
            by_categories=_suppliers(p for p in paid if p.category in categories),
        )


def _past(purchase: _Paid) -> PastPrice:
    assert purchase.price is not None
    return PastPrice(price=purchase.price, on=purchase.on, supplier=purchase.supplier or None, unit=purchase.unit)


def _suppliers(paid: Iterable[_Paid]) -> tuple[SupplierStat, ...]:
    """Paid lines per supplier (one supplier key, shown in its commonest spelling): count, last paid day, categories
    and the items (what the suggestion relates to each line)."""
    keys: dict[str, str] = {}
    groups: dict[str, tuple[Counter, list]] = {}
    for purchase in paid:
        if not purchase.supplier or is_stock_source(purchase.supplier):
            continue
        key = keys.get(purchase.supplier)
        if key is None:
            key = keys[purchase.supplier] = supplier_key(purchase.supplier)
        spellings, slot = groups.setdefault(key, (Counter(), [0, None, set(), set()]))
        spellings[purchase.supplier] += 1
        slot[0] += 1
        slot[1] = purchase.on if slot[1] is None or purchase.on > slot[1] else slot[1]
        slot[2].add(purchase.category)
        if purchase.item_id is not None:
            slot[3].add(purchase.item_id)
    return tuple(
        SupplierStat(text=spellings.most_common(1)[0][0], lines=lines, last=last, categories=tuple(sorted(found)),
                     items=frozenset(items))
        for spellings, (lines, last, found, items) in groups.values()
    )
