"""The СПИСАНИЕ journal (SK-13): one append-only row per movement of one lot — kitting issue, manual write-off,
return of a write-off, and the SUPPLYMAP rows brought in by the stock import.

Movements made before this table existed are carried over from what the database already holds: the audit
events of manual write-offs, returns and imported journal rows, and the issued positions of handed-out kitting
requests. ОСТАТОК ПОСЛЕ of those is not known (NULL), except for imported rows, which carry the sheet value."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision: str = "0033_warehouse_writeoff_journal"
down_revision: str | None = "0032_receiving_vk_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISSUE_AUTHOR = "выдача по заявке"  # Issue.gs:500
_MOSCOW = ZoneInfo("Europe/Moscow")
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_DATE_PATTERNS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()),
    sa.column("aggregate_id", sa.Uuid()),
    sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()),
    sa.column("actor_name", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)),
    sa.column("details", sa.JSON()),
)
_lots = sa.table(
    "warehouse_lots",
    sa.column("id", sa.Uuid()),
    sa.column("warehouse", sa.String()),
    sa.column("grn", sa.String()),
    sa.column("package", sa.String()),
    sa.column("name", sa.String()),
    sa.column("unit", sa.String()),
)
_requests = sa.table("fulfillment_requests", sa.column("id", sa.Uuid()), sa.column("number", sa.String()))
_lines = sa.table(
    "fulfillment_lines",
    sa.column("id", sa.Uuid()),
    sa.column("request_id", sa.Uuid()),
    sa.column("position", sa.Integer()),
    sa.column("name", sa.Text()),
    sa.column("unit", sa.String()),
    sa.column("issued", sa.Numeric(18, 6)),
    sa.column("lot_id", sa.Uuid()),
    sa.column("lot_warehouse", sa.String()),
    sa.column("lot_grn", sa.String()),
    sa.column("lot_package", sa.String()),
)


def _columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("warehouse", sa.String(8), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("package", sa.String(32), nullable=False),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("purpose", sa.String(512), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("posted", sa.Boolean(), nullable=False),
        sa.Column("remaining_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("source_key", sa.String(255), nullable=True),
    ]


def _decimal(text) -> Decimal | None:
    cleaned = re.sub(r"[\s  ]", "", str(text if text is not None else "")).replace(",", ".")
    if not _NUMBER.fullmatch(cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _sheet_date(text, fallback: datetime) -> datetime:
    value = str(text or "").strip()
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=_MOSCOW)  # SUPPLYMAP writes Moscow time
        except ValueError:
            continue
    return fallback


def _text(value, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _backfill(connection, journal: sa.Table) -> None:
    lots = {row.id: row for row in connection.execute(sa.select(_lots))}
    rows: list[dict] = []
    kinds = {
        "warehouse.written_off": "writeoff",
        "warehouse.writeoff_returned": "return",
        "warehouse.legacy_writeoff_history": "legacy",
    }
    events = connection.execute(
        sa.select(_audit).where(_audit.c.event_type.in_(list(kinds))).order_by(_audit.c.occurred_at, _audit.c.id)
    )
    for event in events:
        lot = lots.get(event.aggregate_id)
        details = event.details or {}
        quantity = _decimal(details.get("quantity"))
        if lot is None or quantity is None or quantity <= 0:
            continue
        kind = kinds[event.event_type]
        legacy = kind == "legacy"
        rows.append({
            "id": uuid4(),
            "occurred_at": _sheet_date(details.get("date"), event.occurred_at) if legacy else event.occurred_at,
            "kind": kind,
            "lot_id": lot.id,
            "warehouse": lot.warehouse,
            "grn": lot.grn,
            "package": lot.package,
            "name": lot.name,
            "quantity": quantity,
            "unit": _text(details.get("unit") or lot.unit, 32) if legacy else lot.unit,
            "reason": _text(details.get("reason"), 512),
            "purpose": _text(details.get("purpose"), 512),
            "author": _text(details.get("author"), 255) if legacy else _text(event.actor_name, 255),
            "posted": str(details.get("posted") or "").strip().casefold() == "да" if legacy else True,
            "remaining_after": _decimal(details.get("remaining_after")) if legacy else None,
            "actor_id": event.actor_id,
            "actor_name": _text(event.actor_name, 255),
            "source_key": (
                f"supplymap-writeoff:{details.get('source', '')}:{details.get('row', '')}"
                if legacy and details.get("row") else f"audit:{event.id}"
            )[:255],
        })

    issued = connection.execute(
        sa.select(_audit, _requests.c.number)
        .join(_requests, _requests.c.id == _audit.c.aggregate_id)
        .where(_audit.c.event_type == "fulfillment.issued")
        .order_by(_audit.c.occurred_at, _audit.c.id)
    )
    seen: set = set()
    for event in issued:
        if event.aggregate_id in seen:
            continue
        seen.add(event.aggregate_id)
        lines = connection.execute(
            sa.select(_lines).where(_lines.c.request_id == event.aggregate_id).order_by(_lines.c.position)
        )
        for line in lines:
            lot = lots.get(line.lot_id)
            if line.issued is None or Decimal(line.issued) <= 0 or (lot is None and not line.lot_grn):
                continue
            rows.append({
                "id": uuid4(),
                "occurred_at": event.occurred_at,
                "kind": "issue",
                "lot_id": line.lot_id,
                "warehouse": line.lot_warehouse or (lot.warehouse if lot else ""),
                "grn": line.lot_grn or lot.grn,
                "package": line.lot_package if line.lot_package is not None else (lot.package if lot else ""),
                "name": _text(line.name, 1024),
                "quantity": Decimal(line.issued),
                "unit": line.unit,
                "reason": f"Выдача {event.number}",
                "purpose": "",
                "author": _ISSUE_AUTHOR,
                "posted": True,
                "remaining_after": None,
                "actor_id": event.actor_id,
                "actor_name": _text(event.actor_name, 255),
                "source_key": f"fulfillment-issue:{line.id}",
            })
    unique = list({row["source_key"]: row for row in reversed(rows)}.values())[::-1]  # first one wins
    if unique:
        op.bulk_insert(journal, unique)


def upgrade() -> None:
    journal = op.create_table(
        "warehouse_writeoffs",
        *_columns(),
        sa.UniqueConstraint("source_key", name="uq_warehouse_writeoffs_source_key"),
    )
    op.create_index("ix_warehouse_writeoffs_occurred_at", "warehouse_writeoffs", ["occurred_at"])
    op.create_index("ix_warehouse_writeoffs_lot_id", "warehouse_writeoffs", ["lot_id"])
    op.create_index("ix_warehouse_writeoffs_grn", "warehouse_writeoffs", ["grn"])
    _backfill(op.get_bind(), journal)


def downgrade() -> None:
    op.drop_index("ix_warehouse_writeoffs_grn", table_name="warehouse_writeoffs")
    op.drop_index("ix_warehouse_writeoffs_lot_id", table_name="warehouse_writeoffs")
    op.drop_index("ix_warehouse_writeoffs_occurred_at", table_name="warehouse_writeoffs")
    op.drop_table("warehouse_writeoffs")
