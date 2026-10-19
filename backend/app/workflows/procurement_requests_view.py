"""«Заявки» закупщика (option procurement.requests_rb) ↔ the other modules: the one place the view meets them.

Procurement builds the view (`procurement.requests_view`) and never imports purchasing or the warehouse; this workflow
hands it, for a list of lines and on the caller's session (read-only, a few indexed reads per call):
- the lines' places in the requests for quotes (purchasing `rfq_line_states`) as `RfqState`;
- the free stock of each line's item: warehouse lots with something available (balance − reserve), the same part
  number (else the same name) and unit — RB «На складе: свободно N»;
- the last UPD purchase of the item (purchasing `line_purchases`, with option references.price_book on).
And the request-for-quotes half of «Разделить» (`PurchasingRfqSplitter`), inside the procurement command's transaction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.procurement.domain import same_unit
from app.modules.procurement.price_history import article_key, name_key
from app.modules.procurement.requests_view import LastPurchase, RfqState, ViewLine
from app.modules.purchasing import line_purchases, rfq_line_states, rfq_split
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared import stage_options
from app.shared.events import AuditEvent
from app.shared.identity import Actor

PRICE_BOOK_OPTION = "references.price_book"


def rfq_states(session: Session, line_ids: Iterable[UUID]) -> dict[UUID, RfqState]:
    return {
        line_id: RfqState(
            live=state.rfq_id is not None, rfq_id=state.rfq_id, rfq_number=state.rfq_number, quoted=state.quoted,
            decided=state.decided, requote=state.requote, requote_from=state.requote_from,
        )
        for line_id, state in rfq_line_states.states(session, line_ids).items()
    }


def free_stock(session: Session, lines: Sequence[ViewLine]) -> dict[UUID, Decimal]:
    """Available quantity on the lots of each line's item; lines with nothing are left out."""
    if not lines:
        return {}
    L = StockLotRow
    lots = session.execute(
        select(L.article, L.name, L.unit, L.inbound, L.written_off, L.reserved).where(
            L.inbound - L.written_off - L.reserved > 0
        )
    ).all()
    by_article: dict[str, list[tuple[str, Decimal]]] = {}
    by_name: dict[str, list[tuple[str, Decimal]]] = {}
    for lot in lots:
        available = lot.inbound - lot.written_off - lot.reserved
        key = article_key(lot.article)
        if key:
            by_article.setdefault(key, []).append((lot.unit, available))
        name = name_key(lot.name)
        if name:
            by_name.setdefault(name, []).append((lot.unit, available))
    result: dict[UUID, Decimal] = {}
    for line in lines:
        key = article_key(line.article)
        pool = by_article.get(key, []) if key else []
        if not pool:
            pool = by_name.get(name_key(line.name), [])
        total = sum((available for unit, available in pool if same_unit(line.unit, unit)), Decimal("0"))
        if total > 0:
            result[line.id] = total
    return result


def _day(value: str) -> date:
    return date.fromisoformat(value[:10])


def last_purchases(
    session: Session, lines: Sequence[ViewLine], paid: Mapping[UUID, LastPurchase],
) -> dict[UUID, LastPurchase]:
    """The newest of the procurement's own paid lines (`paid`) and, with option references.price_book, the UPDs."""
    result = dict(paid)
    if not stage_options.enabled(PRICE_BOOK_OPTION) or not lines:
        return result
    wanted = [line_purchases.Wanted(line.id, line.article, line.name, line.unit) for line in lines]
    for line_id, upd in line_purchases.last_purchases(session, wanted).items():
        on = _day(upd.date)
        known = result.get(line_id)
        if known is None or known.on < on:
            unit = next((line.unit for line in lines if line.id == line_id), None)
            result[line_id] = LastPurchase(price=upd.price, on=on, supplier=upd.supplier or None, unit=unit, source="upd")
    return result


class PurchasingRfqSplitter:
    """procurement's `RfqLineSplitter` port over purchasing's `rfq_split`, on the procurement unit of work's session."""

    def live_request(self, uow, line_id: UUID) -> str | None:
        return rfq_split.live_request(uow.session, line_id)

    def split(
        self, uow, *, line_id: UUID, new_line_id: UUID, kept: Decimal, split: Decimal, actor: Actor, now: datetime,
    ) -> dict | None:
        copied = rfq_split.split_rfq_line(
            uow.session, procurement_line_id=line_id, new_procurement_line_id=new_line_id, kept_quantity=kept,
            split_quantity=split, actor_id=actor.id, now=now,
        )
        if copied is None:
            return None
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=copied.rfq_id, event_type="purchasing.rfq_line_split", actor=actor, occurred_at=now,
            details={"number": copied.rfq_number, "quotes": str(copied.quotes), "procurement_line_id": str(line_id),
                     "new_procurement_line_id": str(new_line_id)},
        ))
        return {"rfq_id": str(copied.rfq_id), "rfq_number": copied.rfq_number, "quotes": copied.quotes}
