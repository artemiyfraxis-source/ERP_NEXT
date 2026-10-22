"""Prices from the UPD registry, Kaiten invoices and reference prices.

Ported from Roller_Buyer: item-card.ts, supplier-card.ts, purchase-history.ts, upd-docs.ts (catalog), item-import.ts
(planUpdItems / applyUpdItems), domain/upd.ts and shared units. What was really paid is read from UPD lines:
- a line belongs to the document's version in force (the buyer's manual lines replace the Google ones);
- a document is in force when it is not a twin copy, a duplicate, «не документ закупки», a correction, М-15 or still
  waiting in the queue; lines of services are never prices;
- a line is matched to an item by the part key (article_key = item.part_key), else by the name key (name_key =
  alias_key of the item name or of an alias; an alias of one supplier counts only for that supplier's documents);
- the price is gross (with VAT) per unit: gross / quantity, converted to the item's unit by `unit_factor`.
Money is integer kopecks. Lookups go through the article_key / name_key indexes in chunks; nothing queries per item."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from .infrastructure import (
    InvoiceRow,
    ItemAliasRow,
    ItemRow,
    PriceRefRow,
    SupplierRow,
    UpdDocumentRow,
    UpdLineRow,
)
from .items import add_alias, alias_key, create_item, guess_category, normalize_unit, part_key
from .upd_sellers import effective_header

CHUNK = 400
PAGE_SIZE = 100
RELIABLE = "reliable"  # parsed or verified: its numbers feed min / last prices, the catalog and the request pool

# ---- units ----------------------------------------------------------------------------------------------------------

# A UPD's unit (by name or ОКЕИ code) as one of our units times a factor: «км» is metres × 1000.
_UNIT_TABLE: tuple[tuple[re.Pattern, tuple[str, ...], str, float], ...] = tuple(
    (re.compile(pattern), codes, unit, factor) for pattern, codes, unit, factor in (
        (r"^(м|метр|метров|мтр|пог\.?\s*м|п\.?\s*м)$", ("006",), "м", 1.0),
        (r"^(км|километр)$", ("008",), "м", 1000.0),
        (r"^(см|сантиметр)$", ("004",), "м", 0.01),
        (r"^(мм|миллиметр)$", ("003",), "м", 0.001),
        (r"^(шт|штук|штука|pcs|ед|единица)$", ("796",), "шт", 1.0),
        (r"^(тыс\.?\s*шт|1000\s*шт)$", ("798",), "шт", 1000.0),
        (r"^(упак|уп|упаковка|пач|пачка)$", ("778",), "уп", 1.0),
        (r"^(кг|килограмм)$", ("166",), "кг", 1.0),
        (r"^(г|гр|грамм)$", ("163",), "кг", 0.001),
        (r"^(т|тонна)$", ("168",), "кг", 1000.0),
        (r"^(л|литр)$", ("112",), "л", 1.0),
        (r"^(мл|миллилитр)$", ("111",), "л", 0.001),
        (r"^(компл|комплект|к-т|набор)$", ("839", "704"), "компл", 1.0),
    )
)


def read_upd_unit(name: str, code: str = "") -> tuple[str, float] | None:
    code = (code or "").strip()
    if code:
        padded = code.zfill(3)
        for _, codes, unit, factor in _UNIT_TABLE:
            if padded in codes:
                return unit, factor
    text = (name or "").strip().lower().replace("ё", "е").rstrip(".")
    if text:
        for pattern, _, unit, factor in _UNIT_TABLE:
            if pattern.match(text):
                return unit, factor
    return None


def unit_factor(upd_name: str, upd_code: str, item_unit: str) -> float | None:
    """How many item units one UPD unit is. 1 — same units (or the UPD names none: assumed the same); 1000 — «км»
    into metres; None — cannot tell («бухта» into metres)."""
    if not (upd_name or "").strip() and not (upd_code or "").strip():
        return 1.0
    read = read_upd_unit(upd_name, upd_code)
    if read is None:
        return None
    return read[1] if read[0] == normalize_unit(item_unit) else None


def unit_from_upd(unit: str, code: str = "") -> str | None:
    """Our unit for a UPD line that becomes a new item; None — a service («усл»)."""
    text = re.sub(r"[.\s]", "", (unit or "").lower())
    if text.startswith("усл"):
        return None
    for pattern, unit_name in (
        (r"^(м|m|метр|погм|пм|км)$", "м"), (r"^(шт|штук|штука|pcs|pc)$", "шт"), (r"^(уп|упак|упаковка|пач|пачка)$", "уп"),
        (r"^(кг|kg)$", "кг"), (r"^(л|l|литр)$", "л"), (r"^(компл|комплект|набор|к-т|кт)$", "компл"),
    ):
        if re.match(pattern, text):
            return unit_name
    by_code = {"006": "м", "008": "м", "018": "м", "796": "шт", "778": "уп", "166": "кг", "112": "л", "839": "компл"}
    return by_code.get(re.sub(r"^0+(?=\d{3}$)", "", (code or "").strip()), "шт")


def is_advance_line(name: str) -> bool:
    """Advance-payment lines sit in documents as goods but are money, not a position."""
    return bool(re.search(r"предварительн\S*\s+оплат|(?<![а-яё])аванс", name or "", re.IGNORECASE))


def vat_percent(vat_rate: str) -> float | None:
    text = (vat_rate or "").strip()
    if re.search(r"без\s*ндс", text, re.IGNORECASE):
        return 0.0
    if not text:
        return None
    try:
        number = float(text.replace("%", "").replace(",", ".").strip())
    except ValueError:
        return None
    return float(round(number * 100)) if 0 < number < 1 else number


def upd_unit_gross(quantity, gross, price, vat_rate: str) -> int | None:
    """Unit price with VAT in kopecks: gross / quantity, or price + VAT when there are no totals."""
    if gross is not None and quantity is not None and Decimal(quantity) > 0:
        return int((Decimal(gross) / Decimal(quantity)).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
    rate = vat_percent(vat_rate)
    if price is not None and rate is not None:
        return int((Decimal(price) * (100 + Decimal(str(rate)))).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
    return None


_HAS_LETTER = re.compile(r"[a-zа-яё]", re.IGNORECASE)


def article_counts(article: str | None) -> bool:
    """A purely numeric «article» is a customs code or the seller's internal number: it never means a part number."""
    return bool(article) and bool(_HAS_LETTER.search(article))


def catalog_key(name_key: str, article: str | None, article_key: str | None) -> str:
    if article_counts(article) and article_key:
        return f"a:{article_key}"
    return f"n:{name_key}" if name_key else ""


# ---- documents and lines in force ---------------------------------------------------------------------------------

def _document_in_force():
    return and_(
        UpdDocumentRow.twin_of.is_(None),
        UpdDocumentRow.status != "Дубликат",
        UpdDocumentRow.status != "Не документ закупки",
        UpdDocumentRow.kind != "М-15",
        not_(or_(UpdDocumentRow.kind.like("%коррект%"), UpdDocumentRow.kind.like("%Коррект%"))),
        not_(and_(UpdDocumentRow.status == "В очереди", UpdDocumentRow.verified_at.is_(None))),
    )


def _line_in_force():
    manual = aliased(UpdLineRow)
    other_manual = exists().where(manual.document_id == UpdLineRow.document_id, manual.source == "manual")
    return and_(or_(UpdLineRow.source == "manual", not_(other_manual)), UpdLineRow.is_service.is_(False))


def _is_reliable(document: UpdDocumentRow) -> bool:
    return document.verified_at is not None or document.status == "Разобран"


def _chunks(values: list, size: int = CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _fetch_lines(session: Session, article_keys: set[str], name_keys: set[str]) -> list[tuple[UpdLineRow, UpdDocumentRow]]:
    found: dict[UUID, tuple[UpdLineRow, UpdDocumentRow]] = {}
    for column, keys in ((UpdLineRow.article_key, sorted(article_keys)), (UpdLineRow.name_key, sorted(name_keys))):
        for chunk in _chunks(keys):
            rows = session.execute(
                select(UpdLineRow, UpdDocumentRow)
                .join(UpdDocumentRow, UpdDocumentRow.id == UpdLineRow.document_id)
                .where(column.in_(chunk), _document_in_force(), _line_in_force())
            ).all()
            for line, document in rows:
                found[line.id] = (line, document)
    return list(found.values())


def _supplier_names(session: Session) -> dict[UUID, str]:
    return {row_id: name for row_id, name in session.execute(select(SupplierRow.id, SupplierRow.name))}


@dataclass(frozen=True)
class Purchase:
    """One UPD line bought, as an item sees it."""

    document_id: str
    number: str
    date: str | None
    supplier_id: UUID | None
    supplier_name: str
    line_name: str
    article: str
    quantity: Decimal | None
    upd_unit: str
    unit_code: str
    gross_unit: int | None  # kopecks per UPD unit
    reliable: bool

    def price_in(self, unit: str) -> int | None:
        """Kopecks per `unit` of the item, None when the UPD unit cannot be told in it."""
        factor = unit_factor(self.upd_unit, self.unit_code, unit)
        if self.gross_unit is None or not factor:
            return None
        return round(self.gross_unit / factor)


def _purchase(line: UpdLineRow, document: UpdDocumentRow, names: dict[UUID, str]) -> Purchase:
    header = effective_header(document)
    supplier = names.get(document.supplier_id) if document.supplier_id else None
    return Purchase(
        document_id=document.id, number=header["number"] or "", date=header["date"], supplier_id=document.supplier_id,
        supplier_name=supplier or header["seller_name"] or "", line_name=line.name, article=line.article,
        quantity=line.quantity, upd_unit=line.unit, unit_code=line.unit_code,
        gross_unit=upd_unit_gross(line.quantity, line.gross, line.price, line.vat_rate), reliable=_is_reliable(document),
    )


def match_items(session: Session, items: list[ItemRow]) -> dict[UUID, list[Purchase]]:
    """UPD lines of all given items in a few indexed queries. A line goes to the item with the same part number; failing
    that, to the item whose name or alias (of this supplier first, then generic) equals the line's name."""
    result: dict[UUID, list[Purchase]] = {item.id: [] for item in items}
    if not items:
        return result
    by_article: dict[str, set[UUID]] = defaultdict(set)
    generic: dict[str, set[UUID]] = defaultdict(set)
    specific: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
    ids = [item.id for item in items]
    for item in items:
        if item.part_key:
            by_article[item.part_key].add(item.id)
        generic[alias_key(item.name)].add(item.id)
    for chunk in _chunks(ids):
        for item_id, supplier_id, key in session.execute(
            select(ItemAliasRow.item_id, ItemAliasRow.supplier_id, ItemAliasRow.alias_key).where(ItemAliasRow.item_id.in_(chunk))
        ):
            if supplier_id is None:
                generic[key].add(item_id)
            else:
                specific[(supplier_id, key)].add(item_id)
    name_keys = set(generic) | {key for _, key in specific}
    names = _supplier_names(session)
    for line, document in _fetch_lines(session, set(by_article), name_keys):
        targets = by_article.get(line.article_key or "") if article_counts(line.article) else None
        if not targets and document.supplier_id is not None:
            targets = specific.get((document.supplier_id, line.name_key))
        if not targets:
            targets = generic.get(line.name_key)
        if not targets:
            continue
        purchase = _purchase(line, document, names)
        for item_id in targets:
            result[item_id].append(purchase)
    for rows in result.values():
        rows.sort(key=lambda p: p.date or "", reverse=True)
    return result


# ---- the item card ------------------------------------------------------------------------------------------------

def _refs_json(session: Session, item_ids: list[UUID]) -> list[PriceRefRow]:
    rows: list[PriceRefRow] = []
    for chunk in _chunks(item_ids):
        rows.extend(session.scalars(select(PriceRefRow).where(PriceRefRow.item_id.in_(chunk))))
    return rows


_CURRENCY_ORDER = {"RUB": 0, "CNY": 1, "USD": 2}


def refs_of(session: Session, item_id: UUID) -> list[dict]:
    rows = sorted(_refs_json(session, [item_id]), key=lambda r: (_CURRENCY_ORDER.get(r.currency, 9), r.unit_price4))
    return [{
        "id": str(r.id), "supplier_id": str(r.supplier_id) if r.supplier_id else None, "supplier_name": r.supplier_name,
        "offered_part": r.offered_part, "offered_manufacturer": r.offered_manufacturer, "currency": r.currency,
        "unit_price4": r.unit_price4, "vat_included": r.vat_included,
        "quantity": format(r.quantity.normalize(), "f") if r.quantity is not None else None,
        "lead_time": r.lead_time, "source": r.source,
    } for r in rows]


def _number(value) -> str | None:
    return format(Decimal(value).normalize(), "f") if value is not None else None


def invoice_positions(session: Session, item: ItemRow, alias_keys: set[str], limit: int = 30) -> list[dict]:
    """Positions of Kaiten invoices that name the item by part number or by one of its names."""
    wanted_names = {alias_key(item.name), *alias_keys}
    names = _supplier_names(session)
    found = []
    for invoice in session.scalars(select(InvoiceRow)):
        for position in invoice.lines or []:
            article = str(position.get("article") or "")
            same_article = bool(item.part_key) and article_counts(article) and part_key(article) == item.part_key
            if not same_article and alias_key(position.get("name")) not in wanted_names:
                continue
            found.append({
                "invoice_id": str(invoice.id), "number": invoice.number, "date": invoice.doc_date.isoformat() if invoice.doc_date else None,
                "status": invoice.status, "supplier_id": str(invoice.supplier_id) if invoice.supplier_id else None,
                "supplier_name": names.get(invoice.supplier_id) or invoice.seller_name or "",
                "name": position.get("name") or "", "article": article, "quantity": position.get("qty"),
                "unit": position.get("unit") or "", "price": position.get("price"), "sum": position.get("sum"),
            })
    found.sort(key=lambda p: p["date"] or "", reverse=True)
    return found[:limit]


def item_prices(session: Session, item: ItemRow) -> dict:
    """The «Цены» part of an item card: reference prices, UPD purchases, Kaiten invoice positions, min / last."""
    purchases = match_items(session, [item])[item.id]
    alias_keys = {key for (key,) in session.execute(select(ItemAliasRow.alias_key).where(ItemAliasRow.item_id == item.id))}
    upd = []
    for p in purchases:
        price = p.price_in(item.unit)
        upd.append({
            "upd_id": p.document_id, "number": p.number, "date": p.date, "supplier_id": str(p.supplier_id) if p.supplier_id else None,
            "supplier_name": p.supplier_name, "line_name": p.line_name,
            "quantity": _number(p.quantity), "unit": p.upd_unit, "unit_price_with_vat": p.gross_unit,
            "item_unit_price_with_vat": price, "needs_review": not p.reliable,
        })
    comparable = [(p, p.price_in(item.unit)) for p in purchases if p.reliable and p.price_in(item.unit) is not None]
    summary = {"count": len(comparable), "min": None, "last": None, "last_date": None, "last_supplier": None}
    if comparable:
        summary["min"] = min(price for _, price in comparable)
        newest, price = max(comparable, key=lambda pair: pair[0].date or "")
        summary.update(last=price, last_date=newest.date, last_supplier=newest.supplier_name)
    return {
        "unit": item.unit, "refs": refs_of(session, item.id), "upd": upd, "summary": summary,
        "invoices": invoice_positions(session, item, alias_keys),
    }


# ---- the supplier card --------------------------------------------------------------------------------------------

TOP_POSITIONS = 10
RECENT = 10


def supplier_prices(session: Session, supplier: SupplierRow) -> dict:
    """The «Документы и цены» part of a supplier card: its UPDs, Kaiten invoices, positions bought most, offers."""
    docs = [d for d in session.scalars(select(UpdDocumentRow).where(UpdDocumentRow.supplier_id == supplier.id, _document_in_force()))]
    headers = [(d, effective_header(d)) for d in docs]
    gross = sum(int(h["total_gross"] or 0) for _, h in headers)
    dates = [h["date"] for _, h in headers if h["date"]]
    recent = sorted(headers, key=lambda pair: pair[1]["date"] or "", reverse=True)[:RECENT]
    total_all = 0
    for any_doc in session.scalars(select(UpdDocumentRow).where(_document_in_force())):
        total_all += int(effective_header(any_doc)["total_gross"] or 0)

    invoices = list(session.scalars(select(InvoiceRow).where(InvoiceRow.supplier_id == supplier.id)))
    unpaid = [i for i in invoices if i.status == "to_pay"]

    positions: dict[str, dict] = {}
    doc_ids = [d.id for d in docs]
    doc_by_id = {d.id: d for d in docs}
    lines = []
    for chunk in _chunks(doc_ids):
        lines.extend(session.scalars(select(UpdLineRow).where(UpdLineRow.document_id.in_(chunk), _line_in_force())))
    for line in lines:
        key = catalog_key(line.name_key, line.article, line.article_key)
        if not key:
            continue
        date = effective_header(doc_by_id[line.document_id])["date"] or ""
        slot = positions.setdefault(key, {"key": key, "name": line.name, "article": line.article, "unit": line.unit,
                                          "purchases": 0, "sum": 0, "last_date": "", "last_price": None})
        slot["purchases"] += 1
        slot["sum"] += int(line.gross or 0)
        if date >= slot["last_date"]:
            slot.update(last_date=date, name=line.name, article=line.article, unit=line.unit,
                        last_price=upd_unit_gross(line.quantity, line.gross, line.price, line.vat_rate))
    top = sorted(positions.values(), key=lambda p: (-p["sum"], p["name"]))[:TOP_POSITIONS]

    refs = list(session.execute(
        select(PriceRefRow, ItemRow.name).join(ItemRow, ItemRow.id == PriceRefRow.item_id).where(PriceRefRow.supplier_id == supplier.id)
    ))
    best: dict[UUID, tuple[PriceRefRow, str]] = {}
    for ref, name in refs:
        cur = best.get(ref.item_id)
        rank = (_CURRENCY_ORDER.get(ref.currency, 9), ref.unit_price4)
        if cur is None or rank < (_CURRENCY_ORDER.get(cur[0].currency, 9), cur[0].unit_price4):
            best[ref.item_id] = (ref, name)
    offers = [{
        "item_id": str(ref.item_id), "item_name": name, "offered_part": ref.offered_part, "currency": ref.currency,
        "unit_price4": ref.unit_price4, "vat_included": ref.vat_included, "lead_time": ref.lead_time,
    } for ref, name in sorted(best.values(), key=lambda pair: pair[1])[:50]]

    return {
        "upd": {
            "count": len(docs), "total_gross": gross, "last_date": max(dates) if dates else None,
            # Share of this supplier in everything bought by UPD (kopecks of all documents in force).
            "share_pct": round(gross * 100 / total_all, 1) if total_all else None,
            "recent": [{
                "id": d.id, "number": h["number"] or "", "date": h["date"], "kind": d.kind, "total_gross": h["total_gross"],
            } for d, h in recent],
        },
        "invoices": {
            "count": len(invoices), "total": sum(int(i.total or 0) for i in invoices),
            "unpaid_count": len(unpaid), "unpaid_total": sum(int(i.total or 0) for i in unpaid),
            "last_date": max((i.doc_date.isoformat() for i in invoices if i.doc_date), default=None),
            "recent": [{
                "id": str(i.id), "number": i.number, "date": i.doc_date.isoformat() if i.doc_date else None,
                "status": i.status, "total": i.total,
            } for i in sorted(invoices, key=lambda i: i.doc_date.isoformat() if i.doc_date else "", reverse=True)[:RECENT]],
        },
        "top_positions": top,
        "offers": offers, "offers_total": len(best),
    }


# ---- the catalog of purchases -------------------------------------------------------------------------------------

def _reliable_lines(session: Session) -> list[tuple[UpdLineRow, UpdDocumentRow]]:
    return list(session.execute(
        select(UpdLineRow, UpdDocumentRow).join(UpdDocumentRow, UpdDocumentRow.id == UpdLineRow.document_id)
        .where(_document_in_force(), _line_in_force(),
               or_(UpdDocumentRow.verified_at.is_not(None), UpdDocumentRow.status == "Разобран"))
    ).all())


def catalog(session: Session, q: str = "", page: int = 1) -> dict:
    """Positions bought by UPD, grouped by part number (else by name): price points and suppliers, newest first."""
    needle = [alias_key(word) for word in q.split() if word]
    names = _supplier_names(session)
    groups: dict[str, list[tuple[UpdLineRow, dict, str]]] = defaultdict(list)
    for line, document in _reliable_lines(session):
        key = catalog_key(line.name_key, line.article, line.article_key)
        if not key:
            continue
        if needle:
            hay = alias_key(f"{line.name} {line.article}")
            if not all(word in hay for word in needle):
                continue
        header = effective_header(document)
        seller = names.get(document.supplier_id) if document.supplier_id else None
        groups[key].append((line, header, seller or header["seller_name"] or ""))
    entries = []
    for key, facts in groups.items():
        facts.sort(key=lambda f: f[1]["date"] or "", reverse=True)
        line, header, seller = facts[0]
        prices = [p for p in (upd_unit_gross(f[0].quantity, f[0].gross, f[0].price, f[0].vat_rate) for f in facts) if p is not None]
        entries.append({
            "key": key, "name": line.name, "article": line.article, "unit": line.unit, "purchases": len(facts),
            "suppliers": len({f[2] for f in facts}),
            "last_price": upd_unit_gross(line.quantity, line.gross, line.price, line.vat_rate),
            "last_date": header["date"], "last_supplier": seller,
            "min_price": min(prices) if prices else None, "max_price": max(prices) if prices else None,
        })
    # Lines with no unit price (insurance, cargo tracking — services without a quantity) go last.
    entries.sort(key=lambda e: (e["min_price"] is None, -e["purchases"], e["name"].casefold()))
    return {
        "total": len(entries), "page": page, "page_size": PAGE_SIZE,
        "entries": entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE],
    }


def catalog_points(session: Session, key: str) -> list[dict]:
    names = _supplier_names(session)
    points = []
    for line, document in _reliable_lines(session):
        if catalog_key(line.name_key, line.article, line.article_key) != key:
            continue
        header = effective_header(document)
        seller = names.get(document.supplier_id) if document.supplier_id else None
        points.append({
            "upd_id": document.id, "number": header["number"] or "", "date": header["date"],
            "supplier_name": seller or header["seller_name"] or "", "quantity": _number(line.quantity), "unit": line.unit,
            "unit_price_with_vat": upd_unit_gross(line.quantity, line.gross, line.price, line.vat_rate),
        })
    points.sort(key=lambda p: p["date"] or "", reverse=True)
    return points


# ---- items from UPD lines -----------------------------------------------------------------------------------------

@dataclass
class _Fact:
    supplier_id: UUID | None
    supplier_name: str
    date: str | None
    name: str
    article: str
    article_key: str | None
    name_key: str
    unit: str


def _mode(facts: list[_Fact], pick) -> str | None:
    counts: dict[str, list] = {}
    for fact in facts:
        value = pick(fact)
        if not value:
            continue
        slot = counts.setdefault(value, [0, ""])
        slot[0] += 1
        slot[1] = max(slot[1], fact.date or "")
    best, top = None, (0, "")
    for value, (count, date) in counts.items():
        if (count, date) > top:
            best, top = value, (count, date)
    return best


def _plan(session: Session) -> dict:
    names = _supplier_names(session)
    waiting = 0
    skipped_services = 0
    skipped_advances = 0
    usable: dict[str, UpdDocumentRow] = {}
    for document in session.scalars(select(UpdDocumentRow).where(
        UpdDocumentRow.twin_of.is_(None), UpdDocumentRow.status != "Дубликат", UpdDocumentRow.status != "Не документ закупки",
    )):
        if document.status == "В очереди" and document.verified_at is None:
            waiting += 1
            continue
        if "коррект" in (document.kind or "").lower():
            continue
        usable[document.id] = document
    groups: dict[str, list[_Fact]] = defaultdict(list)
    for line in session.scalars(select(UpdLineRow).where(_line_in_force())):
        document = usable.get(line.document_id)
        if document is None:
            continue
        unit = unit_from_upd(line.unit, line.unit_code)
        if unit is None:
            skipped_services += 1
            continue
        if is_advance_line(line.name):
            skipped_advances += 1
            continue
        key = catalog_key(line.name_key, line.article, line.article_key)
        if not key:
            continue
        header = effective_header(document)
        groups[key].append(_Fact(
            document.supplier_id, names.get(document.supplier_id) or header["seller_name"] or "", header["date"],
            " ".join(line.name.split()), line.article.strip(), line.article_key, line.name_key, unit,
        ))
    # What the directory already knows.
    part_keys: set[str] = set()
    name_keys: set[str] = set()
    for key, name in session.execute(select(ItemRow.part_key, ItemRow.name)):
        if key:
            part_keys.add(key)
        name_keys.add(alias_key(name))
    aliases = {(supplier_id, key) for supplier_id, key in session.execute(select(ItemAliasRow.supplier_id, ItemAliasRow.alias_key))}
    alias_names = {key for supplier_id, key in aliases if supplier_id is None}
    candidates = []
    known = 0
    for key, facts in groups.items():
        if any(
            (f.article_key in part_keys and article_counts(f.article)) or f.name_key in name_keys or f.name_key in alias_names
            or (f.supplier_id is not None and (f.supplier_id, f.name_key) in aliases)
            for f in facts
        ):
            known += 1
            continue
        name = _mode(facts, lambda f: f.name) or ""
        candidates.append({
            "key": key, "name": name, "article": _mode(facts, lambda f: f.article) or "",
            "unit": _mode(facts, lambda f: f.unit) or "шт", "category": guess_category(name), "lines": len(facts),
            "suppliers": sorted({f.supplier_name for f in facts if f.supplier_name}),
            "last_date": max((f.date for f in facts if f.date), default=None), "_facts": facts,
        })
    candidates.sort(key=lambda c: (c["category"], c["name"].casefold()))
    return {
        "candidates": candidates, "known": known, "skipped_services": skipped_services,
        "skipped_advances": skipped_advances, "waiting_documents": waiting,
    }


def plan_upd_items(session: Session) -> dict:
    plan = _plan(session)
    return {**plan, "candidates": [{k: v for k, v in c.items() if k != "_facts"} for c in plan["candidates"]]}


def apply_upd_items(uow, keys: list[str], actor, now: datetime) -> dict:
    """Creates items for the chosen candidates; every seller's wording of the goods that differs from the item name is
    remembered as that seller's alias (an existing alias is never overwritten)."""
    session = uow.session
    wanted = set(keys)
    created = 0
    aliases = 0
    for candidate in _plan(session)["candidates"]:
        if candidate["key"] not in wanted:
            continue
        facts: list[_Fact] = candidate["_facts"]
        article = candidate["article"] if article_counts(candidate["article"]) else None
        try:
            with session.begin_nested():
                item = create_item(
                    uow, name=candidate["name"][:300], actor=actor, now=now, part_number=article, category=candidate["category"],
                    unit=candidate["unit"], source="upd",
                )
        except Exception:  # noqa: BLE001 - the part number was taken meanwhile: the candidate is known now, skip it
            continue
        created += 1
        seen = {(None, alias_key(item.name))}
        for fact in facts:
            if fact.supplier_id is None or (fact.supplier_id, fact.name_key) in seen or fact.name_key == alias_key(item.name):
                continue
            seen.add((fact.supplier_id, fact.name_key))
            try:
                with session.begin_nested():
                    _, added = add_alias(uow, item.id, fact.name, fact.supplier_id, actor, now)
                aliases += int(added)
            except Exception:  # noqa: BLE001 - the wording belongs to another item: leave it
                continue
    return {"created": created, "aliases": aliases}


# ---- the request pool ---------------------------------------------------------------------------------------------

SOURCE_LABELS = {"upd": "по УПД", "ref": "прайс"}


def cheapest_known(
    session: Session, wanted: list[tuple[UUID, str]], vat_rate: Decimal,
) -> dict[tuple[UUID, str], dict]:
    """The lowest rouble price with VAT per (item id, unit) among reliable UPD lines and RUB reference prices, in the
    given unit only. Reference prices without VAT are raised by `vat_rate` percent. -> {price (Decimal rubles), source,
    date, supplier}."""
    if not wanted:
        return {}
    item_ids = list({item_id for item_id, _ in wanted})
    items: list[ItemRow] = []
    for chunk in _chunks(item_ids):
        items.extend(session.scalars(select(ItemRow).where(ItemRow.id.in_(chunk))))
    purchases = match_items(session, items)
    refs: dict[UUID, list[PriceRefRow]] = defaultdict(list)
    for ref in _refs_json(session, item_ids):
        if ref.currency == "RUB":
            refs[ref.item_id].append(ref)
    out: dict[tuple[UUID, str], dict] = {}
    for item_id, unit in set(wanted):
        best: dict | None = None
        for p in purchases.get(item_id, []):
            price = p.price_in(unit) if p.reliable else None
            if price is not None and (best is None or price < best["kop"]):
                best = {"kop": price, "source": "upd", "date": p.date, "supplier": p.supplier_name}
        item_unit = next((i.unit for i in items if i.id == item_id), None)
        if normalize_unit(unit) == normalize_unit(item_unit):  # a reference price is per the item's own unit
            for ref in refs.get(item_id, []):
                kop = Decimal(ref.unit_price4) / 100
                if not ref.vat_included:
                    kop = kop * (100 + vat_rate) / 100
                kop = int(kop.quantize(Decimal(1)))
                if best is None or kop < best["kop"]:
                    best = {"kop": kop, "source": "ref", "date": None, "supplier": ref.supplier_name or None}
        if best is not None:
            out[(item_id, unit)] = {
                "price": Decimal(best["kop"]) / 100, "source": best["source"], "date": best["date"], "supplier": best["supplier"],
            }
    return out
