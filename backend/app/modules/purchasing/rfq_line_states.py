"""Where procurement lines stand in the requests for quotes — read for «Заявки» закупщика (option procurement.requests_rb).

Purchasing never imports procurement: the caller hands procurement line ids and gets plain states back through
`app/workflows/procurement_requests_view.py`. Three indexed reads per 500 lines, whatever the number of requests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .infrastructure import DecisionRow, QuoteLineRow, RfqLineRow, RfqRow

_CHUNK = 500
# Why a line left a request (the rfq slice's «В новые» / «Перезапрос»): «new» means it is not a requote. Rows written
# before that column existed have none, and «Закрыть запрос» leaves a requote (CONTRACTS.md).
_NOT_REQUOTE = frozenset({"new"})


@dataclass(frozen=True)
class RfqLineState:
    rfq_id: UUID | None = None  # the live request holding the line
    rfq_number: str | None = None
    rfq_line_id: UUID | None = None
    quoted: bool = False  # a quote line with a price on the live request line
    decided: bool = False  # a supplier chosen for it
    requote: bool = False  # taken out of a request to be asked again, in none now
    requote_from: str | None = None  # the request it left last


def _moment(value) -> datetime:
    if value is None:
        return datetime.min
    return value.replace(tzinfo=None) if isinstance(value, datetime) else value


def states(session: Session, procurement_line_ids: Iterable[UUID]) -> dict[UUID, RfqLineState]:
    """Only lines that ever were in a request appear in the answer."""
    wanted = list(dict.fromkeys(procurement_line_ids))
    live: dict[UUID, tuple[RfqLineRow, str]] = {}
    removed: dict[UUID, tuple[RfqLineRow, str]] = {}
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start:start + _CHUNK]
        for line, number in session.execute(
            select(RfqLineRow, RfqRow.number).join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
            .where(RfqLineRow.procurement_line_id.in_(chunk))
        ):
            if line.removed_at is None:
                live[line.procurement_line_id] = (line, number)
            elif line.procurement_line_id not in removed or (
                _moment(removed[line.procurement_line_id][0].removed_at) <= _moment(line.removed_at)
            ):
                removed[line.procurement_line_id] = (line, number)
    live_ids = [line.id for line, _ in live.values()]
    quoted: set[UUID] = set()
    decided: set[UUID] = set()
    for start in range(0, len(live_ids), _CHUNK):
        chunk = live_ids[start:start + _CHUNK]
        quoted |= set(session.scalars(
            select(QuoteLineRow.rfq_line_id).where(
                QuoteLineRow.rfq_line_id.in_(chunk), QuoteLineRow.declined.is_(False),
                QuoteLineRow.unit_price.is_not(None),
            ).distinct()
        ))
        decided |= set(session.scalars(select(DecisionRow.rfq_line_id).where(DecisionRow.rfq_line_id.in_(chunk))))
    result: dict[UUID, RfqLineState] = {}
    for line_id, (line, number) in live.items():
        result[line_id] = RfqLineState(
            rfq_id=line.rfq_id, rfq_number=number, rfq_line_id=line.id, quoted=line.id in quoted,
            decided=line.id in decided,
        )
    for line_id, (line, number) in removed.items():
        if line_id in result:
            continue
        reason = getattr(line, "removed_reason", None)
        result[line_id] = RfqLineState(requote=reason not in _NOT_REQUOTE, requote_from=number)
    return result
