"""Procurement ↔ Purchasing ↔ Receiving: the facts «Показатели закупщика» cannot get from a procurement line.

`purchasing_kpi_facts(session, lines, start, end, buyer, today)` → `PurchasingFacts` (app.modules.procurement.kpi), read
only. `lines` are the procurement lines paid in the period (already filtered by buyer); the period and the buyer also
cut the request-side facts. Sources, Roller_Buyer `kpi-data.ts` on ERP tables:

* «Экономия к КП»  — `purchasing_decisions.baseline_unit_gross` (the cheapest КП of a key supplier at the moment of the
  decision, a snapshot) against the paid price; the decision of the line's request line, the same unit only.
* «К прошлой закупке» — the previous paid purchase of the same item (the part number, else the name — as «Покупали
  ранее»), same unit, paid before this line.
* «Доставка» — `purchasing_order_facts.delivery_cost` of the orders of the period's lines (what the buyer entered on the
  order; the delivery quoted in a КП is not what was billed).
* «Новых поставщиков» — suppliers whose first paid line (any buyer) falls in the period and who are in the period's lines.
* Брак / возвраты — `receiving_lines` of the line: nonconformity «Механическое повреждение / брак»; returned quantity from
  the audit event `receiving.returned_to_supplier`. The defect quantity is not stored, so «Брак, ₽» has no source.
* Решения / среднее КП / с неключевым / обход гейта — `purchasing_decisions` decided in the period (`decided_at`, Moscow day)
  of the buyer's requests: `priced_quotes` snapshot, non-key among the offers current on the decision day, override code.
* Спрошен / ответил / цена к лучшей — `purchasing_rfq_suppliers.sent_at` in the period and the quotes of that request;
  the offers received in the period against the cheapest current offer of the same request line (Roller `RatingOffer`).

Query count does not depend on the data: lists go to `IN` in chunks of 500."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.procurement.domain import ProcurementLine
from app.modules.procurement.infrastructure import ProcurementLineRow as R
from app.modules.procurement.kpi import (
    NO_BUYER, DecisionFacts, LineFacts, PurchasingFacts, SupplierFacts,
)
from app.modules.procurement.price_history import article_key, name_key, unit_price_with_vat
from app.modules.purchasing import offers as rules
from app.modules.purchasing.infrastructure import (
    DecisionRow, OrderFactRow, QuoteLineRow, QuoteRow, RfqLineRow, RfqRow, RfqSupplierRow, SupplierNameRow, SupplierRow,
)
from app.modules.purchasing.suppliers import is_stock_source, supplier_key
from app.modules.receiving.domain import MECHANICAL_DAMAGE, ReceivingStatus
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.shared.clock import MOSCOW, as_utc
from app.shared.persistence import AuditEventRow

_CHUNK = 500
_RETURN_EVENT = "receiving.returned_to_supplier"
ZERO = Decimal("0")
_NOT_INSPECTED = {ReceivingStatus.IN_TRANSIT.value}


def _chunks(values: Iterable) -> list[list]:
    items = sorted({value for value in values if value is not None}, key=str)
    return [items[i:i + _CHUNK] for i in range(0, len(items), _CHUNK)]


def _moscow_day(value: datetime) -> date:
    return as_utc(value).astimezone(MOSCOW).date()


def _space(text: str | None) -> str:
    return " ".join(str(text or "").split())


class _Buyers:
    """The buyer filter on a request: its `buyer_id` against the name chosen on the screen."""

    def __init__(self, session: Session, buyer: str | None):
        self.buyer = buyer
        self.ids: set[UUID] = set()
        if buyer and buyer != NO_BUYER:
            self.ids = set(session.scalars(select(IdentityUserRow.id).where(IdentityUserRow.name == buyer)))

    def allows(self, buyer_id: UUID | None) -> bool:
        if not self.buyer:
            return True
        if self.buyer == NO_BUYER:
            return buyer_id is None
        return buyer_id in self.ids


# ---- per line: decision baseline, receiving, previous purchase -----------------------------------------------------

def _decisions_of(session: Session, line_ids: list[UUID]) -> dict[UUID, tuple[Decimal | None, str]]:
    """procurement line → (the КП baseline unit price, the request line's unit) of its latest decision."""
    rfq_lines: dict[UUID, tuple[UUID, str]] = {}
    for chunk in _chunks(line_ids):
        for row in session.execute(
            select(RfqLineRow.id, RfqLineRow.procurement_line_id, RfqLineRow.unit)
            .where(RfqLineRow.procurement_line_id.in_(chunk))
        ):
            rfq_lines[row.id] = (row.procurement_line_id, row.unit)
    latest: dict[UUID, tuple[datetime, Decimal | None, str]] = {}
    for chunk in _chunks(rfq_lines):
        for row in session.execute(
            select(DecisionRow.rfq_line_id, DecisionRow.baseline_unit_gross, DecisionRow.decided_at)
            .where(DecisionRow.rfq_line_id.in_(chunk))
        ):
            line_id, unit = rfq_lines[row.rfq_line_id]
            if line_id not in latest or as_utc(row.decided_at) > as_utc(latest[line_id][0]):
                latest[line_id] = (row.decided_at, row.baseline_unit_gross, unit)
    return {line_id: (baseline, unit) for line_id, (_, baseline, unit) in latest.items()}


def _receiving_of(session: Session, line_ids: list[UUID]) -> dict[UUID, dict]:
    """procurement line → {received, defect, returned} from its receiving lines (revisions and batches included)."""
    facts: dict[UUID, dict] = defaultdict(lambda: {"received": False, "defect": False, "returned": ZERO})
    owner: dict[UUID, UUID] = {}
    for chunk in _chunks(line_ids):
        for row in session.execute(
            select(ReceivingLineRow.id, ReceivingLineRow.procurement_line_id, ReceivingLineRow.status,
                   ReceivingLineRow.nonconformities)
            .where(ReceivingLineRow.procurement_line_id.in_(chunk))
        ):
            owner[row.id] = row.procurement_line_id
            own = facts[row.procurement_line_id]
            own["received"] = own["received"] or row.status not in _NOT_INSPECTED
            own["defect"] = own["defect"] or MECHANICAL_DAMAGE in (row.nonconformities or [])
    for chunk in _chunks(owner):
        for row in session.execute(
            select(AuditEventRow.aggregate_id, AuditEventRow.details)
            .where(AuditEventRow.aggregate_id.in_(chunk), AuditEventRow.event_type == _RETURN_EVENT)
        ):
            try:
                quantity = Decimal(str((row.details or {}).get("quantity")))
            except Exception:  # noqa: BLE001 — a journal entry without a readable quantity is skipped, never fatal
                continue
            if quantity > 0:
                facts[owner[row.aggregate_id]]["returned"] += quantity
    return facts


class _PaidHistory:
    """Every paid line once (the columns used): the previous price of an item and the first order of a supplier."""

    def __init__(self, session: Session):
        columns = (
            R.id, R.design_article, R.calculated_name, R.invoice_item_name, R.supplier, R.paid_at, R.currency,
            R.actual_price, R.actual_vat_rate, R.actual_price_with_vat, R.source_price, R.source_price_with_vat,
            R.exchange_rate, R.actual_unit, R.calculated_unit,
        )
        self.by_article: dict[str, list[tuple]] = defaultdict(list)
        self.by_name: dict[str, list[tuple]] = defaultdict(list)
        self.first_paid: dict[str, date] = {}
        for row in session.execute(select(*columns).where(R.paid_at.is_not(None))):
            moment = as_utc(row.paid_at)
            price = unit_price_with_vat(
                currency=row.currency, price=row.actual_price, vat_rate=row.actual_vat_rate,
                price_with_vat=row.actual_price_with_vat, source_price=row.source_price,
                source_price_with_vat=row.source_price_with_vat, exchange_rate=row.exchange_rate,
            )
            entry = (row.id, moment, price, _space(row.actual_unit or row.calculated_unit).casefold())
            if price is not None:
                article = article_key(row.design_article)
                if article:
                    self.by_article[article].append(entry)
                for name in {name_key(row.calculated_name), name_key(row.invoice_item_name)} - {""}:
                    self.by_name[name].append(entry)
            supplier = supplier_key(row.supplier or "")
            if supplier and not is_stock_source(row.supplier):
                day = moment.astimezone(MOSCOW).date()
                if supplier not in self.first_paid or day < self.first_paid[supplier]:
                    self.first_paid[supplier] = day

    def previous_price(self, line: ProcurementLine) -> Decimal | None:
        """The latest earlier paid price of the same item in the line's unit (Roller `prevGrossUnit`)."""
        if line.paid_at is None or not line.actual_unit:
            return None
        article = article_key(line.design_article)
        if article:
            pool = self.by_article.get(article, [])
        else:
            pool = [entry for name in {name_key(line.calculated_name), name_key(line.invoice_item_name)} - {""}
                    for entry in self.by_name.get(name, [])]
        moment, unit = as_utc(line.paid_at), _space(line.actual_unit).casefold()
        earlier = [entry for entry in pool if entry[0] != line.id and entry[1] < moment and entry[3] == unit]
        return max(earlier, key=lambda entry: entry[1])[2] if earlier else None


def _line_facts(session: Session, lines: Sequence[ProcurementLine]) -> tuple[dict[UUID, LineFacts], _PaidHistory]:
    ids = [line.id for line in lines]
    decisions = _decisions_of(session, ids)
    receiving = _receiving_of(session, ids)
    history = _PaidHistory(session)
    out: dict[UUID, LineFacts] = {}
    for line in lines:
        baseline, unit = decisions.get(line.id, (None, ""))
        # A decision measures the price in the request line's unit; a line bought in another unit is not comparable.
        same_unit = bool(line.actual_unit) and line.actual_unit == unit
        got = receiving.get(line.id)
        out[line.id] = LineFacts(
            received=bool(got and got["received"]), defect=bool(got and got["defect"]),
            returned_quantity=got["returned"] if got else ZERO,
            kp_baseline_unit=baseline if same_unit else None,
            previous_unit=history.previous_price(line),
        )
    return out, history


def _delivery_spend(session: Session, lines: Sequence[ProcurementLine]) -> Decimal | None:
    numbers = {_space(line.order_number) for line in lines if _space(line.order_number)}
    costs = []
    for chunk in _chunks(numbers):
        costs += [cost for cost in session.scalars(
            select(OrderFactRow.delivery_cost).where(OrderFactRow.order_number.in_(chunk))
        ) if cost is not None]
    return sum(costs, ZERO) if costs else None


# ---- the requests side: decisions, asked / answered, price against the best ----------------------------------------

def _offer(row, today: date, need: Decimal | None = None) -> rules.Offer:
    """An offer of a quote line. With `need` it is priced the way the matrix prices it (MOQ, packs, breaks)."""
    price = row.unit_price
    gross = covered = None
    if price is not None and not row.declined:
        if need is not None:
            cost = rules.cover(
                need, price=price, vat_included=row.vat_included, vat_rate=row.vat_rate, moq=row.moq, pack=row.pack,
                tiers=rules.read_tiers(row.tiers),
            )
            gross, covered = (cost.unit_gross, cost.total_gross) if cost else (None, None)
        else:
            gross = rules.gross_unit(price, row.vat_included, row.vat_rate)
    return rules.Offer(
        quote_line_id=row.quote_line_id, supplier_id=row.supplier_id, is_key=row.supplier_is_key,
        received_on=row.received_on, gross_unit=gross, total=covered, declined=bool(row.declined),
        valid_until=row.valid_until, entered_at=row.created_at,
    )


def _quote_rows(session: Session, rfq_line_ids: Iterable[UUID]) -> dict[UUID, list]:
    rows: dict[UUID, list] = defaultdict(list)
    for chunk in _chunks(rfq_line_ids):
        for row in session.execute(
            select(
                QuoteLineRow.id.label("quote_line_id"), QuoteLineRow.rfq_line_id, QuoteLineRow.unit_price,
                QuoteLineRow.vat_included, QuoteLineRow.vat_rate, QuoteLineRow.moq, QuoteLineRow.pack,
                QuoteLineRow.tiers, QuoteLineRow.declined, QuoteRow.supplier_id, QuoteRow.supplier_is_key,
                QuoteRow.received_on, QuoteRow.valid_until, QuoteRow.created_at,
            ).join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id).where(QuoteLineRow.rfq_line_id.in_(chunk))
        ):
            rows[row.rfq_line_id].append(row)
    return rows


def _decision_facts(session: Session, start: date, end: date, buyers: _Buyers) -> DecisionFacts:
    low = datetime.combine(start - timedelta(days=1), time.min)
    high = datetime.combine(end + timedelta(days=2), time.min)
    decisions = []
    for row in session.execute(
        select(DecisionRow.rfq_line_id, DecisionRow.priced_quotes, DecisionRow.gate_override_code,
               DecisionRow.decided_at, RfqRow.buyer_id)
        .join(RfqLineRow, RfqLineRow.id == DecisionRow.rfq_line_id).join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
        .where(DecisionRow.decided_at >= low, DecisionRow.decided_at < high)
    ):
        day = _moscow_day(row.decided_at)
        if start <= day <= end and buyers.allows(row.buyer_id):
            decisions.append((row, day))
    quotes = _quote_rows(session, {row.rfq_line_id for row, _ in decisions})
    with_non_key = 0
    for row, day in decisions:
        # The gate saw the offers received by the decision day and still valid on it.
        seen = [_offer(quote, day) for quote in quotes.get(row.rfq_line_id, []) if quote.received_on <= day]
        current = rules.current_offers(seen, day)
        with_non_key += rules.gate_status(current, 1, False).has_non_key
    return DecisionFacts(
        decided=len(decisions), priced_total=sum(row.priced_quotes for row, _ in decisions), with_non_key=with_non_key,
        overridden=sum(1 for row, _ in decisions if row.gate_override_code is not None),
    )


def _ask_counts(session: Session, start: date, end: date, buyers: _Buyers) -> dict[UUID, list[int]]:
    """supplier → [asked, answered] over the requests sent in the period."""
    low = datetime.combine(start - timedelta(days=1), time.min)
    high = datetime.combine(end + timedelta(days=2), time.min)
    asked: list[tuple[UUID, UUID]] = []
    for row in session.execute(
        select(RfqSupplierRow.rfq_id, RfqSupplierRow.supplier_id, RfqSupplierRow.sent_at, RfqRow.buyer_id)
        .join(RfqRow, RfqRow.id == RfqSupplierRow.rfq_id)
        .where(RfqSupplierRow.sent_at >= low, RfqSupplierRow.sent_at < high)
    ):
        if start <= _moscow_day(row.sent_at) <= end and buyers.allows(row.buyer_id):
            asked.append((row.rfq_id, row.supplier_id))
    answered: set[tuple[UUID, UUID]] = set()
    for chunk in _chunks({rfq_id for rfq_id, _ in asked}):
        answered.update((row.rfq_id, row.supplier_id) for row in session.execute(
            select(QuoteRow.rfq_id, QuoteRow.supplier_id).where(QuoteRow.rfq_id.in_(chunk))
        ))
    counts: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
    for key in asked:
        counts[key[1]][0] += 1
        counts[key[1]][1] += key in answered
    return counts


def _price_vs_best(session: Session, start: date, end: date, today: date, buyers: _Buyers) -> dict[UUID, list[Decimal]]:
    """supplier → % above the cheapest current offer, one entry per offer received in the period."""
    rfq_lines = {
        row.id: row for row in session.execute(
            select(RfqLineRow.id, RfqLineRow.quantity, RfqRow.buyer_id)
            .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
            .join(QuoteLineRow, QuoteLineRow.rfq_line_id == RfqLineRow.id)
            .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
            .where(QuoteRow.received_on >= start, QuoteRow.received_on <= end).distinct()
        ) if buyers.allows(row.buyer_id)
    }
    quotes = _quote_rows(session, rfq_lines)
    gaps: dict[UUID, list[Decimal]] = defaultdict(list)
    for rfq_line_id, rows in quotes.items():
        current = rules.current_offers([_offer(row, today, rfq_lines[rfq_line_id].quantity) for row in rows], today)
        best = rules.cheapest(current)
        if best is None:
            continue
        for offer in current:
            if offer.priced and start <= offer.received_on <= end:
                gaps[offer.supplier_id].append((offer.gross_unit / best.gross_unit - 1) * 100)
    return gaps


def _supplier_cards(
    session: Session, texts: Iterable[str], asks: dict[UUID, list[int]], gaps: dict[UUID, list[Decimal]],
) -> tuple[dict[str, SupplierFacts], tuple[SupplierFacts, ...]]:
    """The supplier card of each line's supplier text (by the name keys), and the cards left without a line."""
    cards = {row.id: row for row in session.execute(select(SupplierRow.id, SupplierRow.name, SupplierRow.name_key, SupplierRow.is_key))}
    by_key = {row.name_key: row.id for row in cards.values()}
    by_key.update({row.name_key: row.supplier_id for row in session.execute(select(SupplierNameRow))})

    def facts_of(supplier_id: UUID) -> SupplierFacts:
        card = cards[supplier_id]
        asked, answered = asks.get(supplier_id, (0, 0))
        spread = gaps.get(supplier_id)
        return SupplierFacts(
            name=card.name, is_key=bool(card.is_key), asked=asked, answered=answered,
            price_vs_best_pct=sum(spread, ZERO) / len(spread) if spread else None,
        )

    matched: dict[str, SupplierFacts] = {}
    used: set[UUID] = set()
    for text in texts:
        supplier_id = by_key.get(supplier_key(text))
        if supplier_id in cards:
            matched[text.casefold()] = facts_of(supplier_id)
            used.add(supplier_id)
    unplaced = tuple(
        facts_of(supplier_id) for supplier_id in sorted(set(asks) | set(gaps), key=str)
        if supplier_id in cards and supplier_id not in used
    )
    return matched, unplaced


# ---- entry ---------------------------------------------------------------------------------------------------------

def purchasing_kpi_facts(
    session: Session, lines: Sequence[ProcurementLine], start: date, end: date, buyer: str | None, today: date,
) -> PurchasingFacts:
    per_line, history = _line_facts(session, lines)
    buyers = _Buyers(session, buyer)
    asks = _ask_counts(session, start, end, buyers)
    gaps = _price_vs_best(session, start, end, today, buyers)
    texts = {(line.supplier or "").strip() for line in lines if (line.supplier or "").strip()}
    matched, unplaced = _supplier_cards(session, texts, asks, gaps)
    new = {supplier_key(text) for text in texts if not is_stock_source(text)}
    new = {key for key in new if key and start <= history.first_paid.get(key, date.min) <= end}
    return PurchasingFacts(
        lines=per_line, decisions=_decision_facts(session, start, end, buyers), suppliers=matched,
        unplaced=unplaced, delivery_spend=_delivery_spend(session, lines), new_suppliers=len(new),
    )
