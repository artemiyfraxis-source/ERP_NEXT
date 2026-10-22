"""«Покупали ранее»: past paid purchases of the same item.

The ERP has no item master, so an item is identified by text: the part number when the target line has
one (`code_key` of design_article), else the whole name compared exact after normalization.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .document_match import code_key
from .money import convert_to_rub, is_foreign, round_kopecks, vat_multiplier

LIMIT = 5
MIN_ARTICLE_LEN = 3


@dataclass(frozen=True)
class PriceItem:
    """A line seen as an item: part number plus every name it carries."""

    line_id: object
    article: str | None
    names: tuple[str, ...]


@dataclass(frozen=True)
class PriceCandidate:
    item: PriceItem
    paid_at: datetime | date
    supplier: str | None
    unit_price_with_vat: Decimal | None
    unit: str | None
    quantity: Decimal | None
    order_number: str | None


@dataclass(frozen=True)
class PastPurchase:
    line_id: object
    paid_at: datetime | date
    supplier: str | None
    price_with_vat: Decimal
    unit: str | None
    quantity: Decimal | None
    order_number: str | None


def name_key(value: str | None) -> str:
    """Whitespace collapsed, case-insensitive (the same as `designation_key`)."""
    return " ".join(str(value or "").split()).casefold()


def article_key(value: str | None) -> str:
    key = code_key(value)
    return key if len(key) >= MIN_ARTICLE_LEN else ""


def unit_price_with_vat(
    *,
    currency: str | None,
    price: Decimal | None,
    vat_rate: Decimal | None,
    price_with_vat: Decimal | None,
    source_price: Decimal | None = None,
    source_price_with_vat: Decimal | None = None,
    exchange_rate: Decimal | None = None,
) -> Decimal | None:
    """RUB gross price per unit. Stored fact fields are RUB once converted; a foreign line still carrying
    only the invoice-currency amounts is converted with its rate (`convert_to_rub`), else it has no price."""
    multiplier = vat_multiplier(vat_rate)
    if is_foreign(currency):
        gross = source_price_with_vat
        if gross is None and source_price is not None and multiplier:
            gross = source_price * multiplier
        return convert_to_rub(gross, exchange_rate)
    if price_with_vat is not None:
        return round_kopecks(price_with_vat)
    if price is not None and multiplier:
        return round_kopecks(price * multiplier)
    return None


def _same_item(target: PriceItem, other: PriceItem) -> bool:
    key = article_key(target.article)
    if key:
        return article_key(other.article) == key
    names = {name_key(n) for n in target.names if name_key(n)}
    return bool(names & {name_key(n) for n in other.names if name_key(n)})


def past_purchases(
    target: PriceItem, candidates: Iterable[PriceCandidate], limit: int = LIMIT
) -> list[PastPurchase]:
    """Paid, priced, same item, not the line itself; newest first."""
    found = [
        c
        for c in candidates
        if c.item.line_id != target.line_id
        and c.paid_at is not None
        and c.unit_price_with_vat is not None
        and _same_item(target, c.item)
    ]
    found.sort(key=lambda c: _as_datetime(c.paid_at), reverse=True)
    return [
        PastPurchase(c.item.line_id, c.paid_at, c.supplier, c.unit_price_with_vat, c.unit, c.quantity, c.order_number)
        for c in found[:limit]
    ]


def _as_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime(value.year, value.month, value.day)


def delta_pct(current: Decimal | None, unit: str | None, history: list[PastPurchase]) -> Decimal | None:
    """Current gross price vs the most recent past one, in percent; only when both exist and units are equal."""
    if not history or current is None:
        return None
    last = history[0]
    if not unit or not last.unit or unit.strip().casefold() != last.unit.strip().casefold():
        return None
    if not last.price_with_vat:
        return None
    return ((current - last.price_with_vat) / last.price_with_vat * 100).quantize(Decimal("0.1"))


def _chunk_filters(column, text: str | None):
    """Coarse SQL prefilter: the longest alphanumeric chunk, in a few letter cases (SQLite LIKE folds ASCII only)."""
    chunks = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", str(text or ""))
    if not chunks:
        return []
    chunk = max(chunks, key=len)
    variants = {chunk, chunk.lower(), chunk.upper(), chunk.capitalize()}
    return [column.contains(v, autoescape=True) for v in variants]


def load_candidates(session: Session, target: PriceItem) -> list[PriceCandidate]:
    """Paid lines of the target's coarse neighbourhood; the exact match is `past_purchases`."""
    from .infrastructure import ProcurementLineRow as R

    if article_key(target.article):
        coarse = _chunk_filters(R.design_article, target.article)
    else:
        coarse = []
        for name in target.names:
            coarse += _chunk_filters(R.calculated_name, name) + _chunk_filters(R.invoice_item_name, name)
    if not coarse:
        return []
    rows = session.scalars(
        select(R)
        .where(R.paid_at.is_not(None), R.id != target.line_id, or_(*coarse))
        .order_by(R.paid_at.desc())
        .limit(2000)
    ).all()
    return [_candidate(r) for r in rows]


def _candidate(r) -> PriceCandidate:
    return PriceCandidate(
        item=PriceItem(r.id, r.design_article, (r.calculated_name, r.invoice_item_name or "")),
        paid_at=r.paid_at,
        supplier=r.supplier,
        unit_price_with_vat=unit_price_with_vat(
            currency=r.currency,
            price=r.actual_price,
            vat_rate=r.actual_vat_rate,
            price_with_vat=r.actual_price_with_vat,
            source_price=r.source_price,
            source_price_with_vat=r.source_price_with_vat,
            exchange_rate=r.exchange_rate,
        ),
        unit=r.actual_unit,
        quantity=r.actual_quantity,
        order_number=r.order_number,
    )


def history_for_row(session: Session, row) -> dict:
    """One line's hint: past purchases and the delta of its current price against the latest one."""
    target = PriceItem(row.id, row.design_article, (row.calculated_name, row.invoice_item_name or ""))
    return _payload(row, past_purchases(target, load_candidates(session, target)))


def history_for_rows(session: Session, rows) -> dict[str, dict]:
    """The batch for a whole list: every paid line is read once and indexed by article and name key, instead of one
    prefiltered query per row (200 rows × up to 2000 candidates each on a real sheet)."""
    from .infrastructure import ProcurementLineRow as R

    rows = list(rows)
    if not rows:
        return {}
    by_article: dict[str, list[PriceCandidate]] = {}
    by_name: dict[str, list[PriceCandidate]] = {}
    for r in session.scalars(select(R).where(R.paid_at.is_not(None))):
        candidate = _candidate(r)
        key = article_key(candidate.item.article)
        if key:
            by_article.setdefault(key, []).append(candidate)
        for name in {name_key(n) for n in candidate.item.names if name_key(n)}:
            by_name.setdefault(name, []).append(candidate)
    out = {}
    for row in rows:
        target = PriceItem(row.id, row.design_article, (row.calculated_name, row.invoice_item_name or ""))
        key = article_key(target.article)
        if key:
            pool = by_article.get(key, [])
        else:
            seen: dict = {}
            for name in {name_key(n) for n in target.names if name_key(n)}:
                for candidate in by_name.get(name, []):
                    seen[candidate.item.line_id] = candidate
            pool = list(seen.values())
        out[str(row.id)] = _payload(row, past_purchases(target, pool))
    return out


def _payload(row, history: list[PastPurchase]) -> dict:
    current = unit_price_with_vat(
        currency=row.currency,
        price=row.actual_price,
        vat_rate=row.actual_vat_rate,
        price_with_vat=row.actual_price_with_vat,
        source_price=row.source_price,
        source_price_with_vat=row.source_price_with_vat,
        exchange_rate=row.exchange_rate,
    )
    if current is None:
        current = row.calculated_price_with_vat
    unit = row.actual_unit or row.calculated_unit
    delta = delta_pct(current, unit, history)
    return {
        "purchases": [
            {
                "line_id": str(p.line_id),
                "date": p.paid_at.date().isoformat() if isinstance(p.paid_at, datetime) else p.paid_at.isoformat(),
                "supplier": p.supplier,
                "price_with_vat": format(p.price_with_vat, "f"),
                "unit": p.unit,
                "quantity": format(p.quantity.normalize(), "f") if p.quantity is not None else None,
                "order_number": p.order_number,
            }
            for p in history
        ],
        "delta_pct": format(delta, "f") if delta is not None else None,
    }
