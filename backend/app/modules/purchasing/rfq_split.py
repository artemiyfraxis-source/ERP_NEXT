"""«Разделить» a procurement line by quantity (option procurement.requests_rb): the request-for-quotes side.

Roller_Buyer `splitRequestLine` with `inRfq`: the new part joins the original's request and gets a copy of every quote
line, so each part is decided on its own; decisions are not copied (the original keeps its own). Called inside the
procurement command's transaction through `app/workflows/procurement_requests_view.py`, on the same session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure import QuoteLineRow, RfqLineRow, RfqRow, RfqSupplierLineRow

# Every column of a quote line except its identity and the request line it answers.
_QUOTE_LINE_COPIED = tuple(
    column.key for column in QuoteLineRow.__table__.columns if column.key not in {"id", "rfq_line_id"}
)


@dataclass(frozen=True)
class SplitCopy:
    rfq_id: UUID
    rfq_number: str
    rfq_line_id: UUID  # the new part's line in the request
    quotes: int  # quote lines copied


def live_request(session: Session, procurement_line_id: UUID) -> str | None:
    """The number of the request for quotes the line is in now, None — in none."""
    return session.scalar(
        select(RfqRow.number).join(RfqLineRow, RfqLineRow.rfq_id == RfqRow.id).where(
            RfqLineRow.procurement_line_id == procurement_line_id, RfqLineRow.removed_at.is_(None),
        )
    )


def split_rfq_line(
    session: Session,
    *,
    procurement_line_id: UUID,
    new_procurement_line_id: UUID,
    kept_quantity: Decimal,
    split_quantity: Decimal,
    actor_id: UUID | None,
    now: datetime,
) -> SplitCopy | None:
    """The live request line of the original asks `kept_quantity` now; a copy asks `split_quantity` for the new line,
    of the same suppliers (their line subsets) and with every quote line copied. None — the line is in no request."""
    live = session.scalar(
        select(RfqLineRow).where(
            RfqLineRow.procurement_line_id == procurement_line_id, RfqLineRow.removed_at.is_(None),
        )
    )
    if live is None:
        return None
    rfq = session.get(RfqRow, live.rfq_id)
    copy = RfqLineRow(
        id=uuid4(), rfq_id=live.rfq_id, procurement_line_id=new_procurement_line_id, item_id=live.item_id,
        name=live.name, article=live.article, quantity=split_quantity, unit=live.unit, need_by=live.need_by,
        budget_unit_gross=live.budget_unit_gross, created_at=now,
    )
    live.quantity = kept_quantity
    session.add(copy)
    session.flush()
    for (rfq_supplier_id,) in session.execute(
        select(RfqSupplierLineRow.rfq_supplier_id).where(RfqSupplierLineRow.rfq_line_id == live.id)
    ):
        session.add(RfqSupplierLineRow(rfq_supplier_id=rfq_supplier_id, rfq_line_id=copy.id))
    quotes = list(session.scalars(select(QuoteLineRow).where(QuoteLineRow.rfq_line_id == live.id)))
    for quote in quotes:
        session.add(QuoteLineRow(
            id=uuid4(), rfq_line_id=copy.id, **{key: getattr(quote, key) for key in _QUOTE_LINE_COPIED},
        ))
    rfq.updated_at, rfq.updated_by = now, actor_id
    rfq.version += 1
    session.flush()
    return SplitCopy(rfq_id=rfq.id, rfq_number=rfq.number, rfq_line_id=copy.id, quotes=len(quotes))
