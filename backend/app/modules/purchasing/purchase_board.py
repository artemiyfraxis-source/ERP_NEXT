"""«Закупка» (owner 23.10, Витя's purchasing edit 13): the positions decided in requests for quotes, by supplier.

A card is one supplier's decided positions of one buyer — before the request is formed all of them together, after it
one card per our order (№ ЗАКАЗА), since the invoice and the payment go per order. The card shows the supplier, the
number of positions, the sum by the КП decisions (with VAT, the quote's currency) and the tag of where it is. No status
of its own: the tag is read off the procurement line chain —

* «Не заполнено» — «В работе»: the decision is in the line but the line misses something for the request;
* «Готов к запросу» → «Запросить счёт» (procurement's «Сформировать запрос», then the intent-to-buy letter);
* «Жду счёт» — «Жду счет»: the invoice is loaded, read and confirmed by the order's invoice flow;
* «Ожидает оплаты» — «В оплату»: «Внести ПП» (the ПП file is kept with the order) or «Оплачено» — the payment;
* «Оплачено» — «В пути», «Карантин», «Готово»: every position paid, the card goes to the archive; from then on the
  order lives in «Счета и поставки».

A card sits at its earliest position's stage (a «Не заполнено» position only when nothing else is there). Cancelled
and parked («Согласование») lines leave the board.

Purchasing reads its own tables (decisions, quotes, suppliers, requests) and sees procurement lines only through the
`ProcurementLines` port; every write goes through procurement's own commands (the screen calls them, versions in hand).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.shared.identity import Actor

from .infrastructure import DecisionRow, QuoteLineRow, QuoteRow, RfqLineRow, RfqRow, SupplierRow
from .rfq import LineView, ProcurementLines, UnitOfWork, _session, first_email, num

# Stage of a position, in the order a card walks them.
STAGES = (
    ("draft", "Не заполнено"),
    ("ready", "Готов к запросу"),
    ("awaiting", "Жду счёт"),
    ("to_pay", "Ожидает оплаты"),
    ("paid", "Оплачено"),
)
STAGE_LABEL = dict(STAGES)
_ORDER = {key: index for index, (key, _) in enumerate(STAGES)}
_BY_STATUS = {
    "В работе": "draft",
    "Готов к запросу": "ready",
    "Жду счет": "awaiting",
    "В оплату": "to_pay",
    "В пути": "paid",
    "Карантин": "paid",
    "Готово": "paid",
}
# The archive is history: the newest paid cards only.
ARCHIVE_LIMIT = 100


def stage_of(status: str) -> str | None:
    """The position's stage from its procurement status; None — the line is off the board."""
    return _BY_STATUS.get(status)


def card_stage(stages: Iterable[str]) -> str:
    """The earliest stage of the card's positions; «Не заполнено» only when nothing else is there."""
    found = set(stages)
    live = found - {"draft"}
    return min(live or found, key=_ORDER.__getitem__)


@dataclass
class _Card:
    supplier_id: UUID
    supplier: str
    email: str | None
    buyer_id: UUID | None
    order_number: str
    lines: list[dict] = field(default_factory=list)
    sums: dict[str, Decimal] = field(default_factory=dict)
    quotes: dict[tuple, dict] = field(default_factory=dict)
    invoices: list[str] = field(default_factory=list)
    decided_at: datetime | None = None


def _decided(session) -> list[tuple]:
    """Every applied decision of a live request line, with its quote line, quote and request number (one query)."""
    return list(session.execute(
        select(DecisionRow, RfqLineRow, QuoteLineRow, QuoteRow, RfqRow.number)
        .join(RfqLineRow, RfqLineRow.id == DecisionRow.rfq_line_id)
        .join(QuoteLineRow, QuoteLineRow.id == DecisionRow.quote_line_id)
        .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
        .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
        .where(DecisionRow.applied_at.is_not(None), RfqLineRow.removed_at.is_(None))
    ).all())


def _line(view: LineView, decision: DecisionRow, rfq_line: RfqLineRow, quote_line: QuoteLineRow, quote: QuoteRow,
          rfq_number: str, stage: str) -> dict:
    return {
        "line_id": str(view.id), "legacy_id": view.legacy_id, "version": view.version, "status": view.status,
        "stage": stage, "name": view.name or rfq_line.name, "article": view.article or rfq_line.article,
        "project": view.project, "quantity": num(view.actual_quantity if view.actual_quantity is not None else decision.cover_quantity),
        "unit": view.actual_unit or rfq_line.unit, "unit_gross": num(decision.unit_gross),
        "total_gross": num(decision.total_gross), "currency": quote.currency, "vat_included": quote_line.vat_included,
        "order_number": view.order_number, "invoice_number": view.invoice_number,
        "rfq_id": str(rfq_line.rfq_id), "rfq_number": rfq_number,
        "quote_number": quote.number or "", "quote_date": quote.received_on.isoformat(),
        "delivery_point": view.delivery_point or "",
    }


def board(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, *, all_buyers: bool = False) -> dict:
    """The cards: active ones (anything not yet paid) and the newest `ARCHIVE_LIMIT` paid ones. Two queries for the
    purchasing side, the procurement lines in one batch through the port."""
    with uow:
        session = _session(uow)
        rows = _decided(session)
        views = lines.get_many([rfq_line.procurement_line_id for _, rfq_line, _, _, _ in rows])
        supplier_ids = {quote.supplier_id for _, _, _, quote, _ in rows}
        suppliers = {row.id: row for row in session.scalars(select(SupplierRow).where(SupplierRow.id.in_(supplier_ids)))} if supplier_ids else {}
        cards: dict[tuple, _Card] = {}
        for decision, rfq_line, quote_line, quote, rfq_number in rows:
            view = views.get(rfq_line.procurement_line_id)
            stage = stage_of(view.status) if view is not None else None
            if view is None or stage is None:
                continue
            if not all_buyers and view.buyer_id != actor.id:
                continue
            order_number = (view.order_number or "").strip()
            key = (view.buyer_id, quote.supplier_id, order_number)
            supplier = suppliers.get(quote.supplier_id)
            card = cards.setdefault(key, _Card(
                supplier_id=quote.supplier_id, supplier=supplier.name if supplier else (view.supplier or ""),
                email=first_email(supplier.email) if supplier else None, buyer_id=view.buyer_id,
                order_number=order_number,
            ))
            card.lines.append(_line(view, decision, rfq_line, quote_line, quote, rfq_number, stage))
            card.sums[quote.currency] = card.sums.get(quote.currency, Decimal(0)) + Decimal(decision.total_gross)
            card.quotes.setdefault((quote.number or "", quote.received_on), {
                "number": quote.number or "", "date": quote.received_on.isoformat(), "rfq_id": str(rfq_line.rfq_id),
                "rfq_number": rfq_number,
            })
            if view.invoice_number and view.invoice_number not in card.invoices:
                card.invoices.append(view.invoice_number)
            if card.decided_at is None or decision.decided_at > card.decided_at:
                card.decided_at = decision.decided_at
    out = [_card_json(card, actor) for card in cards.values()]
    active = sorted((card for card in out if not card["archived"]),
                    key=lambda card: (_ORDER[card["stage"]], card["supplier"].casefold(), card["order_number"]))
    archived = sorted((card for card in out if card["archived"]), key=lambda card: card["decided_at"] or "", reverse=True)
    return {
        "stages": [{"key": key, "title": title} for key, title in STAGES],
        "cards": active + archived[:ARCHIVE_LIMIT],
        "archived_total": len(archived),
    }


def _card_json(card: _Card, actor: Actor) -> dict:
    card.lines.sort(key=lambda line: line["legacy_id"])
    stage = card_stage(line["stage"] for line in card.lines)
    return {
        "key": f"{card.buyer_id or ''}:{card.supplier_id}:{card.order_number}", "supplier_id": str(card.supplier_id), "supplier": card.supplier, "email": card.email,
        "mine": card.buyer_id == actor.id, "order_number": card.order_number or None, "stage": stage,
        "tag": STAGE_LABEL[stage], "archived": stage == "paid", "count": len(card.lines),
        "sums": [{"currency": currency, "sum": num(total)} for currency, total in sorted(card.sums.items())],
        "quotes": sorted(card.quotes.values(), key=lambda quote: (quote["date"], quote["number"])),
        "invoices": card.invoices, "decided_at": card.decided_at.isoformat() if card.decided_at else None,
        "stage_counts": {name: sum(1 for line in card.lines if line["stage"] == name) for name, _ in STAGES
                         if any(line["stage"] == name for line in card.lines)},
        "lines": card.lines,
    }
