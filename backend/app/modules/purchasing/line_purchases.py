"""«Покупали» of «Заявки» закупщика from the UPD registry (options procurement.requests_rb + references.price_book).

What was really bought, as the item card reads it (`upd_prices`): UPD lines of documents in force, reliable ones only,
gross per unit. A procurement line has no item link of its own here, so it is matched like a UPD line is matched to an
item: by the part key of its article (when the article has a letter), else by the name key. A few indexed reads for the
whole list."""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .items import alias_key, part_key
from .upd_prices import Purchase, _fetch_lines, _purchase, _supplier_names, article_counts


@dataclass(frozen=True)
class Wanted:
    key: Hashable
    article: str | None
    name: str
    unit: str


@dataclass(frozen=True)
class UpdPurchase:
    price: Decimal  # roubles per the wanted unit, with VAT
    date: str  # ISO date of the UPD
    supplier: str
    number: str


def last_purchases(session: Session, wanted: Sequence[Wanted]) -> dict[Hashable, UpdPurchase]:
    if not wanted:
        return {}
    articles = {part_key(item.article) for item in wanted if article_counts(item.article)} - {None}
    names = {alias_key(item.name) for item in wanted if alias_key(item.name)}
    by_article: dict[str, list[Purchase]] = {}
    by_name: dict[str, list[Purchase]] = {}
    supplier_names = _supplier_names(session)
    for line, document in _fetch_lines(session, set(articles), names):
        purchase = _purchase(line, document, supplier_names)
        if not purchase.reliable or not purchase.date:
            continue
        if line.article_key and article_counts(line.article):
            by_article.setdefault(line.article_key, []).append(purchase)
        if line.name_key:
            by_name.setdefault(line.name_key, []).append(purchase)
    result: dict[Hashable, UpdPurchase] = {}
    for item in wanted:
        key = part_key(item.article) if article_counts(item.article) else None
        pool = by_article.get(key, []) if key else by_name.get(alias_key(item.name), [])
        best: UpdPurchase | None = None
        for purchase in pool:
            kopecks = purchase.price_in(item.unit)
            if kopecks is None:
                continue
            if best is None or purchase.date > best.date:
                best = UpdPurchase(
                    price=Decimal(kopecks) / 100, date=purchase.date, supplier=purchase.supplier_name,
                    number=purchase.number,
                )
        if best is not None:
            result[item.key] = best
    return result
