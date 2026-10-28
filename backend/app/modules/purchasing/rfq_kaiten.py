"""Kaiten invoices in a request for quotes (port of Roller_Buyer kaiten-rfq.ts + domain/kaiten-offer.ts).

Which invoices of «Счета Kaiten» answer the request — dated on or after it (a purchase cannot answer a request made
later) — with their positions matched to the request lines: by the prices of the supplier's quote (the quote was typed
from this very invoice), then by the supplier's remembered wording, the part number, the name. «Оформить» turns one
into the offer, the choice and the order: lines the supplier has no price for get a quote made from the invoice
(source «invoice», numbered and dated as the invoice, its PDF kept), each line is chosen by the same rules as by hand
(missing reasons are reported, not skipped), and the procurement lines go through procurement's own commands
(KaitenOrderBridge): «Сформировать запрос» → the invoice for the whole order → «В оплату». The dry run says all of
that and writes nothing. Money of an invoice is kopecks (purchasing_invoices), quotes keep rubles."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from app.shared.identity import Actor

from . import items as items_repo
from . import quote_import
from . import settings as purchasing_settings
from .doc_refs import norm_doc_number
from .infrastructure import (
    DecisionRow,
    DocumentFileRow,
    InvoiceFileRow,
    InvoiceRow,
    ItemAliasRow,
    QuoteLineRow,
    QuoteRow,
    RfqSupplierRow,
    SupplierRow,
)
from .offers import current_offers, gross_unit
from .rfq import (
    CANCELLED,
    Conflict,
    DecisionInput,
    LineView,
    NotFound,
    ProcurementLines,
    Refused,
    RfqError,
    UnitOfWork,
    _entries,
    _live_lines,
    _open,
    _own,
    _rfq,
    _session,
    _today,
    check_pick,
    decide,
    num,
)

KOP = Decimal(100)
RATES = (Decimal(10), Decimal(20), Decimal(22))


# ── pure rules (domain/kaiten-offer.ts) ─────────────────────────────────────────────────────────────────────────


def line_amount(line: dict) -> int | None:
    """What a position costs as printed (kopecks): its sum, else price × quantity."""
    if line.get("sum") is not None:
        return int(line["sum"])
    if line.get("price") is not None and line.get("qty") is not None:
        return int((Decimal(str(line["price"])) * Decimal(str(line["qty"]))).quantize(Decimal(1), ROUND_HALF_UP))
    return None


def invoice_vat(total: int | None, vat: int | None, vat_free: bool, lines: Sequence[dict], default_rate: Decimal) -> tuple[bool, Decimal]:
    """(prices with VAT, the rate): the rate from the VAT amount snapped to 10 / 20 / 22 % when that close; «with VAT»
    when the positions add up to the total, «without» when they add up to it less the VAT. Unknown — with VAT."""
    if vat_free or vat == 0:
        return True, Decimal(0)
    rate = default_rate
    if total and vat and total > vat:
        found = Decimal(vat) / Decimal(total - vat) * 100
        near = min(RATES, key=lambda value: abs(value - found))
        if abs(near - found) <= Decimal("1.5"):
            rate = near
    amounts = sum(line_amount(line) or 0 for line in lines)
    if not total or not amounts:
        return True, rate
    as_gross = abs(amounts - total)
    as_net = abs(Decimal(amounts) * (1 + rate / 100) - total)
    return as_gross <= as_net, rate


_DELIVERY = re.compile(r"доставк|транспортн|перевозк|экспедир|экспедиц", re.I)


def is_delivery_line(name: str) -> bool:
    """Delivery billed as a position: it goes to the order's delivery, not to a line."""
    return bool(_DELIVERY.search(name or ""))


_PACK_WORD = re.compile(r"^(уп|упак|упаковка|pack|reel|катушка|бобина|рулон)\.?$", re.I)
_PACK_IN_NAME = re.compile(r"(?:упаковк[аиу]?|уп\.)\s*(?:по\s*)?(\d[\d\s]*\d|\d)\s*(?:шт|pcs)", re.I)


def pack_in_name(name: str, unit: str) -> Decimal | None:
    """Pieces in a pack the position is sold by: «SHF-001T##5000@JST упаковка 5000 шт.» with no unit or «уп» is 5000;
    a position in «шт» is counted in pieces already."""
    text = (unit or "").strip()
    if text and not _PACK_WORD.match(text):
        return None
    found = _PACK_IN_NAME.search(name or "")
    if not found:
        return None
    size = Decimal(re.sub(r"\s", "", found.group(1)))
    return size if size > 1 else None


def same_price(a: Decimal, b: Decimal) -> bool:
    """Two prices the same to a kopeck or half a percent: the quote typed from the invoice and the invoice."""
    return abs(a - b) <= max(Decimal("0.01"), Decimal("0.005") * max(a, b))


_FACTORS = {("км", "м"): Decimal(1000), ("м", "км"): Decimal("0.001"), ("кг", "г"): Decimal(1000), ("г", "кг"): Decimal("0.001"),
            ("м", "мм"): Decimal(1000), ("мм", "м"): Decimal("0.001")}


def quantity_in(qty: Any, unit: str, line_unit: str, pack: Decimal | None) -> Decimal | None:
    """The position's quantity in the request line's unit: «км» into metres, a pack into pieces. None — the units do not
    convert («бухта» with no length): the buyer says."""
    if qty is None:
        return None
    value = Decimal(str(qty))
    if value <= 0:
        return None
    if pack:
        return (value * pack).quantize(Decimal("0.001"))
    source = items_repo.normalize_unit(unit) if (unit or "").strip() else items_repo.normalize_unit(line_unit)
    target = items_repo.normalize_unit(line_unit)
    if source == target:
        return value
    factor = _FACTORS.get((source, target))
    return (value * factor).quantize(Decimal("0.001")) if factor else None


# ── the request's world ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    rfq_line_id: UUID
    procurement_line_id: UUID
    item_id: UUID | None
    name: str
    article: str | None
    quantity: Decimal
    unit: str
    open: bool  # not cancelled and still before «Жду счет»: may be ordered from an invoice


@dataclass
class World:
    rfq: Any
    targets: list[Target]
    quotes: list[QuoteRow]
    asked: set[UUID]
    offers: dict[tuple[UUID, UUID], Any]  # (supplier, rfq line) → the current Offer (priced or declined)
    unit_price: dict[tuple[UUID, UUID], QuoteLineRow]
    views: dict[UUID, LineView]

    @property
    def open(self) -> list[Target]:
        return [target for target in self.targets if target.open]


def _world(session: Any, lines: ProcurementLines, rfq: Any, today: date) -> World:
    values = purchasing_settings.load(session)
    rows = _live_lines(session, rfq.id)
    views = lines.get_many([row.procurement_line_id for row in rows])
    targets = []
    for row in rows:
        view = views.get(row.procurement_line_id)
        if view is None or view.status == CANCELLED:
            continue
        targets.append(Target(row.id, row.procurement_line_id, row.item_id, row.name, row.article, Decimal(row.quantity),
                              row.unit, bool(view.editable)))
    entries = _entries(session, rfq.id, {row.id: row for row in rows}, int(values["quote_valid_days"]))
    offers: dict[tuple[UUID, UUID], Any] = {}
    quote_lines: dict[tuple[UUID, UUID], QuoteLineRow] = {}
    for line_id, found in entries.items():
        by_id = {entry.offer.quote_line_id: entry for entry in found}
        for offer in current_offers([entry.offer for entry in found], today):
            offers[(offer.supplier_id, line_id)] = offer
            quote_lines[(offer.supplier_id, line_id)] = by_id[offer.quote_line_id].line
    quotes = list(session.scalars(select(QuoteRow).where(QuoteRow.rfq_id == rfq.id).order_by(QuoteRow.created_at.desc())))
    asked = set(session.scalars(select(RfqSupplierRow.supplier_id).where(RfqSupplierRow.rfq_id == rfq.id)))
    return World(rfq, targets, quotes, asked, offers, quote_lines, views)


def _rub(kopecks: Any) -> Decimal | None:
    return None if kopecks is None else (Decimal(str(kopecks)) / KOP)


def _qty_for(line: dict, target: Target, pack: Decimal | None = None) -> Decimal | None:
    return quantity_in(line.get("qty"), line.get("unit") or "", target.unit, pack or pack_in_name(line.get("name") or "", line.get("unit") or ""))


def _share(line: dict, candidates: list[Target]) -> list[dict]:
    """One candidate takes the position; several (the same goods asked twice, for two projects) share it by their
    needs, the rest to the first — when the quantities agree."""
    if not candidates:
        return []
    if len(candidates) == 1:
        return [{"rfq_line_id": candidates[0].rfq_line_id, "quantity": _qty_for(line, candidates[0])}]
    if any(item.unit != candidates[0].unit for item in candidates):
        return []
    total = _qty_for(line, candidates[0])
    if total is None:
        return []
    need = sum((item.quantity for item in candidates), Decimal(0))
    same_item = all(item.item_id == candidates[0].item_id for item in candidates)
    if abs(need - total) > Decimal("0.000001") and not (same_item and total > need):
        exact = [item for item in candidates if abs(item.quantity - total) < Decimal("0.000001")]
        return [{"rfq_line_id": exact[0].rfq_line_id, "quantity": total}] if len(exact) == 1 else []
    return [{"rfq_line_id": item.rfq_line_id, "quantity": item.quantity + (total - need if index == 0 else 0)}
            for index, item in enumerate(candidates)]


def _by_price(goods: list[dict], vat: tuple[bool, Decimal], supplier_id: UUID, world: World) -> dict[int, list[dict]]:
    """Positions priced as this supplier's quote lines — the quote was typed from this very invoice."""
    out: dict[int, list[dict]] = {}
    taken: set[UUID] = set()
    for line in goods:
        price = _rub(line.get("price"))
        if price is None:
            continue
        raw = gross_unit(price, vat[0], vat[1])
        candidates = []
        for target in world.open:
            offer = world.offers.get((supplier_id, target.rfq_line_id))
            if target.rfq_line_id in taken or offer is None or offer.declined or offer.gross_unit is None:
                continue
            quantity = _qty_for(line, target)
            per_unit = raw * Decimal(str(line["qty"])) / quantity if quantity and line.get("qty") else None
            if same_price(offer.gross_unit, raw) or (per_unit is not None and same_price(offer.gross_unit, per_unit)):
                candidates.append(target)
        allocations = _share(line, candidates)
        if allocations:
            out[int(line["seq"])] = allocations
            taken.update(item["rfq_line_id"] for item in allocations)
    return out


def _resolve_supplier(session: Any, inv: InvoiceRow, goods: list[dict], vat: tuple[bool, Decimal], world: World) -> dict:
    """Who the invoice's order goes to: the supplier of this request's quote with the invoice's number; the invoice's
    own supplier when he has a quote here; else the one whose quote carries the invoice's prices (the seller is in the
    directory twice); else the invoice's own supplier."""
    key = norm_doc_number(inv.number or "") if inv.number else ""
    same_number = next((quote for quote in world.quotes if key and norm_doc_number(quote.number or "") == key), None)
    quoted = lambda supplier_id: next((quote for quote in world.quotes if quote.supplier_id == supplier_id), None)  # noqa: E731
    pick = same_number.supplier_id if same_number else (inv.supplier_id if inv.supplier_id and quoted(inv.supplier_id) else None)
    if pick is None:
        priced = len([line for line in goods if line.get("price") is not None])
        best: tuple[int, UUID] | None = None
        for quote in world.quotes:
            if quote.supplier_id == inv.supplier_id or (best and best[1] == quote.supplier_id):
                continue
            count = len(_by_price(goods, vat, quote.supplier_id, world))
            if count >= 2 and count * 2 >= priced and (best is None or count > best[0]):
                best = (count, quote.supplier_id)
        pick = best[1] if best else inv.supplier_id
    supplier = session.get(SupplierRow, pick) if pick else None
    quote = same_number or (quoted(pick) if pick else None)
    return {"supplier_id": pick, "supplier_name": supplier.name if supplier else None,
            "quote_number": quote.number if quote else None, "same_document": same_number is not None}


def _aliases(session: Any, supplier_ids: list[UUID], targets: list[Target]) -> dict[str, UUID]:
    item_ids = [target.item_id for target in targets if target.item_id]
    if not item_ids or not supplier_ids:
        return {}
    rows = session.execute(select(ItemAliasRow.alias_key, ItemAliasRow.item_id).where(
        ItemAliasRow.item_id.in_(item_ids), or_(*[ItemAliasRow.supplier_id == supplier_id for supplier_id in supplier_ids])))
    return {key: item_id for key, item_id in rows}


def _read_lines(inv: InvoiceRow, vat: tuple[bool, Decimal], prices: dict[int, list[dict]], world: World,
                aliases: dict[str, UUID]) -> list[dict]:
    """The invoice's positions with suggested request lines: by the supplier's quote prices, then by wording."""
    taken = {item["rfq_line_id"] for allocations in prices.values() for item in allocations}
    rest = [target for target in world.open if target.rfq_line_id not in taken]
    goods = [line for line in inv.lines or [] if not is_delivery_line(line.get("name") or "") and int(line["seq"]) not in prices]
    imported = [quote_import.ImportedLine(
        source_name=line.get("name") or "", source_article=line.get("article") or "",
        qty=Decimal(str(line["qty"])) if line.get("qty") is not None else None, unit=line.get("unit") or "",
        price=_rub(line.get("price")),
    ) for line in goods]
    match_targets = [quote_import.Target(target.rfq_line_id, target.name, target.article, target.quantity,
                                         items_repo.normalize_unit(target.unit), target.item_id) for target in rest]
    matched = quote_import.apply_packs(quote_import.match_lines(imported, match_targets, aliases), match_targets)
    by_words = {int(line["seq"]): found for line, found in zip(goods, matched)}
    out = []
    for line in inv.lines or []:
        seq = int(line["seq"])
        base = {"seq": seq, "name": line.get("name") or "", "article": line.get("article") or "", "qty": num(Decimal(str(line["qty"]))) if line.get("qty") is not None else None,
                "unit": line.get("unit") or "", "price": num(_rub(line.get("price"))), "sum": num(_rub(line.get("sum")))}
        if seq in prices:
            out.append({**base, "delivery": False, "matched_by": "price",
                        "targets": [{"rfq_line_id": str(item["rfq_line_id"]), "quantity": num(item["quantity"])} for item in prices[seq]]})
            continue
        found = by_words.get(seq)
        if found is None:
            out.append({**base, "delivery": True, "matched_by": None, "targets": []})
            continue
        target = next((item for item in rest if item.rfq_line_id == found.rfq_line_id), None)
        quantity = _qty_for(line, target, found.pack) if target else None
        out.append({**base, "delivery": False, "matched_by": found.matched_by if target else None,
                    "targets": [{"rfq_line_id": str(target.rfq_line_id), "quantity": num(quantity)}] if target else []})
    return out


def _invoice_day(inv: InvoiceRow) -> date:
    return inv.doc_date or inv.card_created.date()


def _invoice_file(session: Any, inv: InvoiceRow) -> tuple[InvoiceFileRow | None, DocumentFileRow | None]:
    files = list(session.scalars(select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == inv.id, InvoiceFileRow.kind == "invoice")
                                 .order_by(InvoiceFileRow.seq)))
    row = next((item for item in files if item.file_id is not None and item.name.lower().endswith(".pdf")), None)
    return row, (session.get(DocumentFileRow, row.file_id) if row else None)


def kaiten_invoices(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, rfq_id: UUID, today: date) -> dict:
    with uow:
        session = _session(uow)
        rfq = _rfq(session, rfq_id)
        world = _world(session, lines, rfq, today)
        default_vat = Decimal(purchasing_settings.load(session)["default_vat_rate"])
        rfq_day = rfq.created_at.date()
        ordered = {}
        for view in world.views.values():
            if view.invoice_number:
                ordered.setdefault(norm_doc_number(view.invoice_number), []).append(view.legacy_id)
        # Kaiten cards only: an invoice uploaded in procurement already stands on its order's lines.
        rows = list(session.scalars(select(InvoiceRow).where(InvoiceRow.status != "return", InvoiceRow.source == "kaiten")))
        out, unread = [], 0
        for inv in rows:
            if _invoice_day(inv) < rfq_day:
                continue
            if not inv.lines:
                unread += 1
            vat = invoice_vat(inv.total, inv.vat, inv.vat_free, inv.lines or [], default_vat)
            goods = [line for line in inv.lines or [] if not is_delivery_line(line.get("name") or "")]
            resolved = _resolve_supplier(session, inv, goods, vat, world)
            prices = _by_price(goods, vat, resolved["supplier_id"], world) if resolved["supplier_id"] else {}
            alias_suppliers = [item for item in dict.fromkeys([inv.supplier_id, resolved["supplier_id"]]) if item]
            read = _read_lines(inv, vat, prices, world, _aliases(session, alias_suppliers, world.open))
            matched = len([line for line in read if line["targets"]])
            ours = ordered.get(norm_doc_number(inv.number or ""), []) if inv.number else []
            sure = any(line["matched_by"] in ("price", "alias", "article") for line in read)
            asked = resolved["supplier_id"] is not None and resolved["supplier_id"] in world.asked
            if not ours and not resolved["same_document"] and not asked and not sure:
                continue
            delivery = sum((gross_unit(_rub(line_amount(line) or 0), vat[0], vat[1])
                            for line in inv.lines or [] if is_delivery_line(line.get("name") or "")), Decimal(0))
            file_row, _ = _invoice_file(session, inv)
            out.append({
                "invoice_id": str(inv.id), "title": inv.title, "number": inv.number,
                "date": inv.doc_date.isoformat() if inv.doc_date else None, "status": inv.status,
                "seller_name": inv.seller_name or "", "seller_inn": inv.seller_inn or "",
                "supplier_id": str(resolved["supplier_id"]) if resolved["supplier_id"] else None,
                "supplier_name": resolved["supplier_name"], "quote_number": resolved["quote_number"],
                "same_document": resolved["same_document"], "total": num(_rub(inv.total)), "vat_included": vat[0],
                "vat_rate": num(vat[1]), "delivery_gross": num(delivery.quantize(Decimal("0.01"))), "lines": read,
                "matched": matched, "ordered_lines": ours, "has_file": file_row is not None,
                "lead_days": None,
            })
        out.sort(key=lambda item: (-item["matched"], item["date"] or "", item["invoice_id"]))
        synced = max((inv.synced_at for inv in rows), default=None)
        return {"rfq_date": rfq_day.isoformat(), "synced_at": synced.isoformat() if synced else None, "unread": unread,
                "invoices": out}


def invoice_file(uow: UnitOfWork, rfq_id: UUID, invoice_id: UUID) -> tuple[bytes, str]:
    with uow:
        session = _session(uow)
        _rfq(session, rfq_id)
        inv = session.get(InvoiceRow, invoice_id)
        if inv is None:
            raise NotFound("Счёт Kaiten не найден")
        row, stored = _invoice_file(session, inv)
        if row is None or stored is None:
            raise NotFound("Файл счёта не скачан из Kaiten")
        return bytes(stored.content), row.name


# ── «Оформить» ──────────────────────────────────────────────────────────────────────────────────────────────────


class ProcurementOrders(Protocol):
    def blockers(self, line_ids, actor: Actor) -> dict[UUID, str]: ...
    def form_orders(self, line_ids, actor: Actor, now: datetime): ...
    def attach_invoice(self, order_number: str, line_ids, *, file_name: str, content: bytes, invoice_number: str,
                       invoice_date: date, expected_arrival: date, actor: Actor, now: datetime) -> str: ...


@dataclass(frozen=True)
class KaitenPick:
    seq: int
    rfq_line_id: UUID
    quantity: Decimal


class KaitenPartial(Conflict):
    """«Оформить» stopped at a step: what was done stays (each step is a procurement or purchasing command)."""

    def __init__(self, done: Sequence[str], failed: str, message: str):
        text = f"Сделано: {'; '.join(done) if done else 'ничего'}. Остановлено на шаге «{failed}»: {message}"
        super().__init__(text)
        self.payload = {"code": "kaiten_partial", "message": text, "done": list(done), "failed": failed, "reason": message}


REASON_NEEDS = {"gate_override_reason": "gate", "not_cheapest_reason": "not_cheapest", "analog_approved_by": "analog"}


def _checked(session: Any, lines: ProcurementLines, actor: Actor, rfq: Any, invoice_id: UUID, picks: Sequence[KaitenPick],
             today: date) -> dict:
    """Every check before anything is written; the invoice, its supplier, the picks and their unit prices."""
    inv = session.get(InvoiceRow, invoice_id)
    if inv is None:
        raise NotFound("Счёт Kaiten не найден")
    if inv.status == "return":
        raise Refused("Это карточка возврата, а не закупки")
    if not inv.number or not inv.doc_date:
        raise Refused("Номер или дата счёта не прочитаны из PDF — внесите счёт в листе закупки вручную")
    if inv.doc_date < rfq.created_at.date():
        raise Refused(f"Счёт от {inv.doc_date:%d.%m.%Y} раньше запроса КП от {rfq.created_at:%d.%m.%Y} — "
                      "он не может быть ответом на этот запрос")
    world = _world(session, lines, rfq, today)
    default_vat = Decimal(purchasing_settings.load(session)["default_vat_rate"])
    vat = invoice_vat(inv.total, inv.vat, inv.vat_free, inv.lines or [], default_vat)
    goods = [line for line in inv.lines or [] if not is_delivery_line(line.get("name") or "")]
    resolved = _resolve_supplier(session, inv, goods, vat, world)
    if resolved["supplier_id"] is None:
        inn = f" (ИНН {inv.seller_inn})" if inv.seller_inn else ""
        raise Refused(f"Продавца {inv.seller_name or ''}{inn} нет в справочнике — добавьте поставщика и обновите счета из Kaiten")
    if not picks:
        raise Refused("Отметьте позиции счёта для строк запроса")
    targets = {target.rfq_line_id: target for target in world.targets}
    by_seq = {int(line["seq"]): line for line in inv.lines or []}
    seen: set[UUID] = set()
    for pick in picks:
        if pick.seq not in by_seq:
            raise Refused("Такой позиции в счёте нет")
        target = targets.get(pick.rfq_line_id)
        if target is None:
            raise Refused("Строка не из этого запроса КП или отменена")
        if not target.open:
            view = world.views.get(target.procurement_line_id)
            raise Refused(f"«{target.name}» уже «{view.status if view else ''}» — по счёту второй раз не оформляется")
        if pick.rfq_line_id in seen:
            raise Refused("Строка запроса указана дважды")
        if pick.quantity <= 0:
            raise Refused(f"«{target.name}»: количество больше нуля")
        seen.add(pick.rfq_line_id)
    per_seq: dict[int, Decimal] = {}
    for pick in picks:
        per_seq[pick.seq] = per_seq.get(pick.seq, Decimal(0)) + pick.quantity
    priced = []
    for pick in picks:
        amount = line_amount(by_seq[pick.seq])
        unit_price = (Decimal(amount) / KOP / per_seq[pick.seq]).quantize(Decimal("0.0001"), ROUND_HALF_UP) if amount else Decimal(0)
        if unit_price <= 0:
            raise Refused(f"У позиции «{by_seq[pick.seq].get('name')}» не прочитана цена — внесите счёт вручную")
        priced.append((pick, unit_price, targets[pick.rfq_line_id]))
    return {"inv": inv, "world": world, "vat": vat, "supplier_id": resolved["supplier_id"], "priced": priced, "by_seq": by_seq}


def kaiten_order(
    make_uow: Callable[[], UnitOfWork], lines: ProcurementLines, orders: ProcurementOrders, actor: Actor, now: datetime,
    rfq_id: UUID, invoice_id: UUID, picks: Sequence[KaitenPick], *, dry_run: bool, expected_arrival: date | None,
    reasons: DecisionInput,
) -> dict:
    """The dry run: what would be done and what is missing, nothing written. Otherwise the steps one by one; a step
    that fails stops the rest and the answer says exactly what was done (KaitenPartial)."""
    today = _today(now)
    with make_uow() as uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        found = _checked(session, lines, actor, rfq, invoice_id, picks, today)
        inv, world, vat, supplier_id = found["inv"], found["world"], found["vat"], found["supplier_id"]
        supplier = session.get(SupplierRow, supplier_id)
        # Procurement is read before anything is added here: its unit of work must not see (or, on a shared
        # connection, roll back) the quote this session is about to stage.
        blockers = orders.blockers([target.procurement_line_id for _, _, target in found["priced"]], actor)
        decided = {row.rfq_line_id: row for row in session.scalars(
            select(DecisionRow).where(DecisionRow.rfq_line_id.in_([pick.rfq_line_id for pick in picks])))}
        # The quote made from the invoice for the lines its supplier has no price for here: in this session only.
        fresh = [(pick, price, target) for pick, price, target in found["priced"]
                 if world.offers.get((supplier_id, pick.rfq_line_id)) is None
                 or world.offers[(supplier_id, pick.rfq_line_id)].declined]
        quote_id = uuid4() if fresh else None
        fresh_lines: dict[UUID, UUID] = {}
        if fresh:
            session.add(QuoteRow(
                id=quote_id, rfq_id=rfq.id, supplier_id=supplier_id, supplier_is_key=supplier.is_key,
                received_on=inv.doc_date, currency="RUB", source="invoice", invoice_id=inv.id, number=inv.number[:64],
                note=f"Из счёта Kaiten: {inv.title}"[:2000], created_at=now, created_by=actor.id,
            ))
            session.flush()
            for pick, price, target in fresh:
                fresh_lines[pick.rfq_line_id] = uuid4()
                session.add(QuoteLineRow(id=fresh_lines[pick.rfq_line_id], quote_id=quote_id, rfq_line_id=pick.rfq_line_id,
                                         unit_price=price, vat_included=vat[0], vat_rate=vat[1], tiers=[]))
            session.flush()
        values = purchasing_settings.load(session)
        needs = {"gate": 0, "not_cheapest": 0, "analog": 0}
        problems: dict[str, int] = {}
        result_lines = []
        to_decide: list[tuple[KaitenPick, UUID]] = []
        for pick, price, target in found["priced"]:
            own = world.offers.get((supplier_id, pick.rfq_line_id))
            quote_line_id = fresh_lines.get(pick.rfq_line_id) or own.quote_line_id
            before = decided.get(pick.rfq_line_id)
            kind = "kept" if before and before.quote_line_id == quote_line_id else "changed" if before else "new"
            result_lines.append({
                "rfq_line_id": str(pick.rfq_line_id), "name": target.name, "seq": pick.seq, "quantity": num(pick.quantity),
                "unit_price": num(price), "offer": "invoice" if pick.rfq_line_id in fresh_lines else "quote", "decision": kind,
                "quote_gross": num(own.gross_unit) if own and not own.declined else None,
                "invoice_gross": num(gross_unit(price, vat[0], vat[1])),
            })
            if kind == "changed":
                problems[f"«{target.name}»: уже выбран другой поставщик — отмените выбор в матрице"] = 1
                continue
            if kind == "kept":
                continue
            data = replace(reasons, rfq_line_id=pick.rfq_line_id, quote_line_id=quote_line_id, expected_version=None)
            try:
                # Chosen no later than the invoice was issued: the offers as they stood on its day.
                check_pick(session, lines, actor, rfq, data if not dry_run else replace(
                    data, gate_override_code=None, gate_override_reason=None, not_cheapest_code=None,
                    not_cheapest_reason=None, analog_approved_by=None), values, inv.doc_date, world.views)
            except RfqError as error:
                fields = [violation.field for violation in error.violations]
                if dry_run and fields and all(field in REASON_NEEDS for field in fields):
                    for field in fields:
                        needs[REASON_NEEDS[field]] += 1
                else:
                    problems[str(error)] = problems.get(str(error), 0) + 1
                continue
            to_decide.append((pick, quote_line_id))
        for _, _, target in found["priced"]:
            if target.procurement_line_id in blockers:
                problems[f"«{target.name}»: {blockers[target.procurement_line_id]}"] = 1
        file_row, stored = _invoice_file(session, inv)
        leads = [line.lead_days for (supplier, _), line in world.unit_price.items() if supplier == supplier_id and line.lead_days is not None]
        suggestion = (inv.doc_date + timedelta(days=max(leads))) if leads else None
        report = {
            "dry_run": dry_run, "lines": result_lines, "needs": needs,
            "problems": [f"{text} — строк: {count}" if count > 1 else text for text, count in problems.items()],
            "expected_suggestion": suggestion.isoformat() if suggestion else None, "has_file": stored is not None,
            "invoice": {"number": inv.number, "date": inv.doc_date.isoformat(), "supplier": supplier.name},
        }
        if dry_run:
            return report  # the session is rolled back: nothing was written
        if report["problems"]:
            raise Refused("; ".join(report["problems"]))
        if stored is None:
            raise Refused("Файл счёта не скачан из Kaiten — внесите счёт в листе закупки вручную")
        if expected_arrival is None:
            raise Refused("Укажите ожидаемую дату поставки")
        if expected_arrival < inv.doc_date:
            raise Refused(f"Ожидаемая дата не может быть раньше даты счёта ({inv.doc_date:%d.%m.%Y})")
        if fresh:
            row = session.scalar(select(RfqSupplierRow).where(RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
            answered_at = datetime.combine(inv.doc_date, time(12), now.tzinfo)
            if row is None:
                session.add(RfqSupplierRow(id=uuid4(), rfq_id=rfq.id, supplier_id=supplier_id, reason="manual",
                                           sent_at=answered_at, created_at=now))
            elif row.sent_at is None:
                row.sent_at = answered_at
            if stored is not None:
                quote = session.get(QuoteRow, quote_id)
                quote.file_id, quote.file_name = stored.id, (file_row.name if file_row else stored.file_name)[:255]
        uow.commit()
        content = bytes(stored.content)
        file_name = file_row.name if file_row else stored.file_name

    done: list[str] = ["КП из счёта"] if fresh else []
    step = "выбор поставщика"
    try:
        for pick, quote_line_id in to_decide:
            data = replace(reasons, rfq_line_id=pick.rfq_line_id, quote_line_id=quote_line_id, expected_version=None)
            decide(make_uow(), lines, actor, now, rfq_id, data, today=inv.doc_date)
        if to_decide:
            done.append(f"выбор поставщика по {len(to_decide)} строк")
        step = "запрос поставщику"
        line_ids = [target.procurement_line_id for _, _, target in found["priced"]]
        views = lines.get_many(line_ids)
        ready = [line_id for line_id in line_ids if views.get(line_id) and views[line_id].status != "Жду счет"]
        formed = orders.form_orders(ready, actor, now) if ready else None
        if formed and formed.skipped:
            names = "; ".join(f"{views[line_id].legacy_id if line_id in views else line_id}: {why}" for line_id, why in formed.skipped)
            raise RfqError(names)
        order_lines: dict[str, list[UUID]] = {}
        for order in (formed.orders if formed else ()):
            order_lines[order.order_number] = list(order.line_ids)
        for line_id in line_ids:
            view = views.get(line_id)
            if view and view.status == "Жду счет" and view.order_number:
                order_lines.setdefault(view.order_number, []).append(line_id)
        if formed and formed.orders:
            done.append(f"запрос поставщику {', '.join(order.order_number for order in formed.orders)}")
        step = "счёт"
        for number, ids in order_lines.items():
            orders.attach_invoice(number, ids, file_name=file_name, content=content, invoice_number=inv.number,
                                  invoice_date=inv.doc_date, expected_arrival=expected_arrival, actor=actor, now=now)
            done.append(f"счёт № {inv.number} в заказе {number} — «В оплату»")
    except RfqError as error:
        raise KaitenPartial(done, step, str(error)) from error
    except Exception as error:  # a failure midway is reported with what was done, never swallowed
        raise KaitenPartial(done, step, "сбой записи — обновите и проверьте строки в листе закупки") from error
    if fresh:
        quote_import.remember_aliases(make_uow, actor, now, rfq_id, supplier_id, [
            (pick.rfq_line_id, f"{found['by_seq'][pick.seq].get('article') or ''} {found['by_seq'][pick.seq].get('name') or ''}".strip())
            for pick, _, _ in found["priced"]
            if sum(1 for other in picks if other.seq == pick.seq) == 1])
    return {**report, "dry_run": False, "done": done, "orders": list(order_lines)}
