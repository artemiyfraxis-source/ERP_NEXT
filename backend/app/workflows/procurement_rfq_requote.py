"""Port of the purchasing hybrid to «Сегодня» / «Заявки»: which procurement lines are «Перезапрос» (CONTRACTS.md).

A line is «перезапрос» when it left a request for quotes without a decision — taken out with «Перезапрос», or closed
with the request (removed_reason NULL) — and is in no live request now. Taken out «В новые» it is not. Read-only, plain
dicts keyed by the procurement line id, so procurement never imports purchasing."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.purchasing.infrastructure import RfqLineRow, RfqRow

_CHUNK = 500


def requote_lines(session: Session, line_ids: Iterable[UUID]) -> dict[UUID, dict]:
    """{procurement line id: {at, rfq_id, rfq_number, note}} — the latest way out of a request, when it says «перезапрос»."""
    wanted = list(dict.fromkeys(line_ids))
    latest: dict[UUID, tuple[RfqLineRow, RfqRow]] = {}
    live: set[UUID] = set()
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start:start + _CHUNK]
        for row, rfq in session.execute(
            select(RfqLineRow, RfqRow).join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
            .where(RfqLineRow.procurement_line_id.in_(chunk))
        ):
            if row.removed_at is None:
                live.add(row.procurement_line_id)
                continue
            seen = latest.get(row.procurement_line_id)
            if seen is None or row.removed_at > seen[0].removed_at:
                latest[row.procurement_line_id] = (row, rfq)
    return {
        line_id: {"at": row.removed_at, "rfq_id": str(rfq.id), "rfq_number": rfq.number, "note": row.removed_note}
        for line_id, (row, rfq) in latest.items()
        if line_id not in live and row.removed_reason != "new"
    }
