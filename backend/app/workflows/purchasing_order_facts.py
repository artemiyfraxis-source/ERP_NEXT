"""Port for «Сегодня» (CONTRACTS.md, orders slice): the facts of supplier orders that procurement does not keep.

`order_facts(session, order_numbers)` → `{order_number: {sent_to_pay_at, shipped_at, shipment_note, promised_date}}`
for every asked order that has lines (an order without facts gets None dates and an empty note). Dates are ISO days.
`promised_date` is derived on read (`purchasing_orders` module doc): the latest promise among the order's lines.
Read-only, two to three queries whatever the number of orders."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.purchasing.infrastructure import OrderFactRow

from .purchasing_orders import order_lines

_CHUNK = 500


def order_facts(session: Session, order_numbers: Iterable[str]) -> dict[str, dict]:
    wanted = sorted({str(number).strip() for number in order_numbers if str(number or "").strip()})
    if not wanted:
        return {}
    promised: dict[str, object] = {}
    for line in order_lines(session, order_numbers=wanted):
        if line.is_backorder or line.promised is None:
            continue
        known = promised.get(line.order_number)
        if known is None or line.promised > known:
            promised[line.order_number] = line.promised
    facts: dict[str, OrderFactRow] = {}
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start:start + _CHUNK]
        facts.update({row.order_number: row for row in session.scalars(
            select(OrderFactRow).where(OrderFactRow.order_number.in_(chunk))
        )})
    out: dict[str, dict] = {}
    for number in wanted:
        row = facts.get(number)
        if row is None and number not in promised:
            continue
        day = promised.get(number)
        out[number] = {
            "sent_to_pay_at": row.sent_to_pay_on.isoformat() if row and row.sent_to_pay_on else None,
            "shipped_at": row.shipped_on.isoformat() if row and row.shipped_on else None,
            "shipment_note": row.shipment_note if row else "",
            "promised_date": day.isoformat() if day else None,
        }
    return out
