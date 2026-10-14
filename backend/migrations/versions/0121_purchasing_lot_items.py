"""Номенклатура ↔ склад (стенд Вити, 06.10.2026): явная привязка партии к позиции номенклатуры.

Branch vitya/hybrid only. A lot that came through ERP receiving finds its item through the chain lot →
receiving line → procurement line → purchasing_line_items, computed on read. This table holds only the lots without
that chain (imported stock) or an explicit choice, which wins over the chain. No cross-module FK to warehouse_lots.

Downgrade drops the table with its rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0121_purchasing_lot_items"
down_revision: str | None = "0120_purchasing_hybrid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchasing_lot_items",
        sa.Column("lot_id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("purchasing_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("source IN ('match', 'manual')", name="ck_purchasing_lot_items_source"),
    )
    op.create_index("ix_purchasing_lot_items_item", "purchasing_lot_items", ["item_id"])


def downgrade() -> None:
    op.drop_table("purchasing_lot_items")
