"""«Сегодня» (option procurement.today) ↔ the requests for quotes of purchasing: what the buyer's requests still need.

Procurement must not import purchasing, so the page gets plain facts from here (read-only, one session, a fixed number of
queries whatever the number of requests):
- `silent_suppliers` — RB `awaitingQuotes` per (request, supplier): the supplier was marked sent, did not decline, has no
  quote in that request, and a line asked of him is still undecided. The wait (`quote_wait_days`) is applied by
  `procurement.today.followups`, like RB does (`days >= quoteWaitDays`);
- `first_sent` — the day each request was first sent («Выбрать поставщика» waits since then);
- `decisions` — the quote chosen for a procurement line in its live request (supplier, lead time, day)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.procurement.today import Decision, SilentSupplier
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierLineRow,
    RfqSupplierRow,
    SupplierRow,
)
from app.shared.clock import MOSCOW, as_utc

_CHUNK = 500


def _day(value) -> date:
    return as_utc(value).astimezone(MOSCOW).date() if isinstance(value, datetime) else value


def _chunks(values: list) -> Iterable[list]:
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def silent_suppliers(session: Session, buyer_id: UUID) -> list[SilentSupplier]:
    """Five queries: the buyer's open requests with their live lines, the decided ones, who quoted, who was sent, which
    lines each supplier was asked for (none stored — every line)."""
    live = session.execute(
        select(RfqLineRow.id, RfqLineRow.rfq_id, RfqRow.number)
        .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
        .where(RfqRow.buyer_id == buyer_id, RfqRow.closed_at.is_(None), RfqLineRow.removed_at.is_(None))
    ).all()
    if not live:
        return []
    numbers = {rfq_id: number for _, rfq_id, number in live}
    lines_of: dict[UUID, set[UUID]] = {}
    for line_id, rfq_id, _ in live:
        lines_of.setdefault(rfq_id, set()).add(line_id)
    decided: set[UUID] = set()
    for chunk in _chunks([line_id for line_id, _, _ in live]):
        decided |= set(session.scalars(select(DecisionRow.rfq_line_id).where(DecisionRow.rfq_line_id.in_(chunk))))
    open_of = {rfq_id: ids - decided for rfq_id, ids in lines_of.items()}
    rfq_ids = [rfq_id for rfq_id, ids in open_of.items() if ids]
    if not rfq_ids:
        return []
    answered: set[tuple[UUID, UUID]] = set()
    sent = []
    for chunk in _chunks(rfq_ids):
        answered |= {(rfq_id, supplier_id) for rfq_id, supplier_id in session.execute(
            select(QuoteRow.rfq_id, QuoteRow.supplier_id).where(QuoteRow.rfq_id.in_(chunk)).distinct()
        )}
        sent.extend(session.execute(
            select(RfqSupplierRow.id, RfqSupplierRow.rfq_id, RfqSupplierRow.supplier_id, RfqSupplierRow.sent_at,
                   SupplierRow.name)
            .join(SupplierRow, SupplierRow.id == RfqSupplierRow.supplier_id)
            .where(RfqSupplierRow.rfq_id.in_(chunk), RfqSupplierRow.sent_at.is_not(None),
                   RfqSupplierRow.declined_at.is_(None))
        ).all())
    waiting = [row for row in sent if (row.rfq_id, row.supplier_id) not in answered]
    asked: dict[UUID, set[UUID]] = {}
    for chunk in _chunks([row.id for row in waiting]):
        for rfq_supplier_id, rfq_line_id in session.execute(
            select(RfqSupplierLineRow.rfq_supplier_id, RfqSupplierLineRow.rfq_line_id)
            .where(RfqSupplierLineRow.rfq_supplier_id.in_(chunk))
        ):
            asked.setdefault(rfq_supplier_id, set()).add(rfq_line_id)
    out = []
    for row in waiting:
        still_open = open_of[row.rfq_id] & asked[row.id] if row.id in asked else open_of[row.rfq_id]
        if still_open:
            out.append(SilentSupplier(
                rfq_id=row.rfq_id, rfq_number=numbers[row.rfq_id], supplier=row.name, sent_on=_day(row.sent_at),
                lines=len(still_open),
            ))
    return out


def first_sent(session: Session, rfq_ids: Iterable[UUID]) -> dict[UUID, date]:
    wanted = sorted(set(rfq_ids), key=str)
    out: dict[UUID, date] = {}
    for chunk in _chunks(wanted):
        for rfq_id, sent_at in session.execute(
            select(RfqSupplierRow.rfq_id, func.min(RfqSupplierRow.sent_at))
            .where(RfqSupplierRow.rfq_id.in_(chunk), RfqSupplierRow.sent_at.is_not(None))
            .group_by(RfqSupplierRow.rfq_id)
        ):
            if sent_at is not None:
                out[rfq_id] = _day(sent_at)
    return out


def decisions(session: Session, procurement_line_ids: Iterable[UUID]) -> dict[UUID, Decision]:
    """Procurement line id → the quote chosen for it in its live request (one query per 500 lines)."""
    wanted = list(dict.fromkeys(procurement_line_ids))
    out: dict[UUID, Decision] = {}
    for chunk in _chunks(wanted):
        rows = session.execute(
            select(RfqLineRow.procurement_line_id, RfqLineRow.rfq_id, RfqRow.number, DecisionRow.decided_at,
                   QuoteLineRow.lead_days, SupplierRow.name)
            .join(DecisionRow, DecisionRow.rfq_line_id == RfqLineRow.id)
            .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
            .join(QuoteLineRow, QuoteLineRow.id == DecisionRow.quote_line_id)
            .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
            .join(SupplierRow, SupplierRow.id == QuoteRow.supplier_id)
            .where(RfqLineRow.procurement_line_id.in_(chunk), RfqLineRow.removed_at.is_(None))
        )
        for line_id, rfq_id, number, decided_at, lead_days, supplier in rows:
            out[line_id] = Decision(rfq_id=rfq_id, rfq_number=number, decided_on=_day(decided_at),
                                    lead_days=lead_days, supplier=supplier)
    return out
