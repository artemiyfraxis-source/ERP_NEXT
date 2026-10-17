"""Supplier requests: № ЗАКАЗА / ДАТА ЗАКАЗА on procurement lines and the issued orders with their letter."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0029_procurement_supplier_orders"
down_revision: str | None = "0028_operation_card_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("procurement_lines", sa.Column("order_number", sa.String(32), nullable=True))
    op.add_column("procurement_lines", sa.Column("order_date", sa.Date(), nullable=True))
    op.create_index("ix_procurement_lines_order_number", "procurement_lines", ["order_number"])
    op.create_table(
        "procurement_supplier_orders",
        sa.Column("order_number", sa.String(32), primary_key=True),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("supplier", sa.String(512), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("delivery_point", sa.String(512), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_name", sa.String(255), nullable=False),
        sa.Column("buyer_department", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("letter", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_procurement_supplier_orders_buyer_id", "procurement_supplier_orders", ["buyer_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_procurement_supplier_orders_buyer_id", table_name="procurement_supplier_orders")
    op.drop_table("procurement_supplier_orders")
    op.drop_index("ix_procurement_lines_order_number", table_name="procurement_lines")
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_column("order_date")
        batch.drop_column("order_number")
