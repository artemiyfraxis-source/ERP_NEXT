"""«Покупали» of «Заявки» закупщика from the UPD registry (options procurement.requests_rb + references.price_book).

What was really bought, as the item card reads it (`upd_prices.match_items`): UPD lines of documents in force matched to
the nomenclature item by part number or any of its names, reliable documents only, gross per unit in the wanted unit.
The caller attributes each procurement line to an item first (the request pool's nomenclature match)."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure import ItemRow
from .upd_prices import _chunks, match_items


@dataclass(frozen=True)
class Wanted:
    key: Hashable
    item_id: UUID
    unit: str


@dataclass(frozen=True)
class UpdPurchase:
    price: Decimal  # roubles per the wanted unit, with VAT
    date: str  # ISO date of the UPD
    supplier: str
    number: str


def last_purchases(session: Session, wanted: Sequence[Wanted]) -> dict[Hashable, UpdPurchase]:
    """The newest reliable UPD purchase of each wanted item that can be told in the wanted unit."""
    ids = list({item.item_id for item in wanted})
    if not ids:
        return {}
    items: list[ItemRow] = []
    for chunk in _chunks(ids):
        items.extend(session.scalars(select(ItemRow).where(ItemRow.id.in_(chunk))))
    purchases = match_items(session, items)
    result: dict[Hashable, UpdPurchase] = {}
    for item in wanted:
        for purchase in purchases.get(item.item_id, []):  # newest first
            kopecks = purchase.price_in(item.unit) if purchase.reliable and purchase.date else None
            if kopecks is not None:
                result[item.key] = UpdPurchase(
                    price=Decimal(kopecks) / 100, date=purchase.date, supplier=purchase.supplier_name,
                    number=purchase.number,
                )
                break
    return result
