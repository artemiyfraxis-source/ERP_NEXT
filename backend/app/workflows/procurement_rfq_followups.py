"""Port of the purchasing hybrid to «Сегодня» of procurement: what the buyer's open requests for quotes still need.

Procurement must not import purchasing, so the board gets two plain dicts from here (read-only, one session):
- «Нет ответа на запрос КП»: a supplier was marked sent more than `quote_wait_days` ago, has no quote and did not decline;
- «Выбрать поставщика»: a request line has priced quotes (the gate passes, or at least one) and no decision yet.
Both are keyed by the procurement line id, so the board shows them as project cards like any other field."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.purchasing import settings as purchasing_settings
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierRow,
    SupplierRow,
)
from app.modules.purchasing.offers import current_offers, gate_status
from app.modules.purchasing.rfq import line_offers

NO_ANSWER = "rfq_no_answer"
TO_DECIDE = "rfq_to_decide"


def _day(value) -> date:
    return value.date() if hasattr(value, "date") else value


def rfq_followups(session: Session, buyer_id: UUID, today: date) -> dict[str, dict[UUID, dict]]:
    """{field key: {procurement line id: {rfq_id, rfq_number, date, days, hint}}} for the buyer's open requests."""
    result: dict[str, dict[UUID, dict]] = {NO_ANSWER: {}, TO_DECIDE: {}}
    rfqs = list(session.scalars(select(RfqRow).where(RfqRow.buyer_id == buyer_id, RfqRow.closed_at.is_(None))))
    if not rfqs:
        return result
    values = purchasing_settings.load(session)
    wait_days = int(values["quote_wait_days"])
    min_quotes, require_non_key = int(values["gate_min_quotes"]), bool(values["gate_require_non_key"])
    valid_days = int(values["quote_valid_days"])
    for rfq in rfqs:
        rfq_lines = list(session.scalars(
            select(RfqLineRow).where(RfqLineRow.rfq_id == rfq.id, RfqLineRow.removed_at.is_(None))
        ))
        if not rfq_lines:
            continue
        by_id = {line.id: line for line in rfq_lines}
        decided = set(session.scalars(select(DecisionRow.rfq_line_id).where(DecisionRow.rfq_line_id.in_(list(by_id)))))
        open_lines = [line for line in rfq_lines if line.id not in decided]
        if not open_lines:
            continue
        answered = set(session.scalars(select(QuoteRow.supplier_id).where(QuoteRow.rfq_id == rfq.id)))
        waiting: list[tuple[str, date]] = []
        for rfq_supplier, supplier in session.execute(
            select(RfqSupplierRow, SupplierRow).join(SupplierRow, SupplierRow.id == RfqSupplierRow.supplier_id)
            .where(RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.sent_at.is_not(None), RfqSupplierRow.declined_at.is_(None))
        ):
            sent = _day(rfq_supplier.sent_at)
            if rfq_supplier.supplier_id not in answered and (today - sent).days > wait_days:
                waiting.append((supplier.name, sent))
        if waiting:
            oldest = min(sent for _, sent in waiting)
            names = ", ".join(sorted(name for name, _ in waiting))
            for line in open_lines:
                result[NO_ANSWER][line.procurement_line_id] = {
                    "rfq_id": str(rfq.id), "rfq_number": rfq.number, "date": oldest, "days": (today - oldest).days,
                    "hint": f"{rfq.number}: нет ответа на запрос — {names}. Напомните или отметьте отказ",
                }
        offers = line_offers(session, rfq.id, by_id, valid_days)
        for line in open_lines:
            current = current_offers(offers.get(line.id, []), today)
            gate = gate_status(current, min_quotes, require_non_key)
            if not (gate.passes or gate.priced >= 1):
                continue
            first = min(offer.received_on for offer in current if offer.priced)
            note = "КП достаточно" if gate.passes else f"КП с ценой {gate.priced} из {gate.required}"
            result[TO_DECIDE][line.procurement_line_id] = {
                "rfq_id": str(rfq.id), "rfq_number": rfq.number, "date": first, "days": (today - first).days,
                "hint": f"{rfq.number}: {note} — выберите поставщика в «Запросах КП»",
            }
    return result
