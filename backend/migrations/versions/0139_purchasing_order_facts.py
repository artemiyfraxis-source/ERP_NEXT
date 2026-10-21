"""«Счета и поставки» (стенд Вити, 16.10.2026): факты заказа поставщику, которых нет у строк закупки.

Branch vitya/hybrid only. One row per ERP supplier order (№ ЗАКАЗА of the procurement lines): the day the invoice went
to payment, the day the supplier shipped and the tracking note, the delivery billed on top of the lines, a note; who
changed it last and an optimistic version. Written only by `app/modules/purchasing/orders.py` (stage option
procurement.orders); nothing is copied from the procurement lines.

Downgrade drops the table with its rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0139_purchasing_order_facts"
down_revision: str | None = "0138_warehouse_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchasing_order_facts",
        sa.Column("order_number", sa.String(32), primary_key=True),
        sa.Column("sent_to_pay_on", sa.Date()),
        sa.Column("shipped_on", sa.Date()),
        sa.Column("shipment_note", sa.String(500), nullable=False, server_default=""),
        sa.Column("delivery_cost", sa.Numeric(18, 2)),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("updated_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("length(trim(order_number)) > 0", name="ck_purchasing_order_facts_number"),
        sa.CheckConstraint("delivery_cost IS NULL OR delivery_cost >= 0", name="ck_purchasing_order_facts_delivery"),
        sa.CheckConstraint("length(shipment_note) <= 500", name="ck_purchasing_order_facts_shipment_note"),
        sa.CheckConstraint("length(note) <= 2000", name="ck_purchasing_order_facts_note"),
        sa.CheckConstraint("version >= 1", name="ck_purchasing_order_facts_version"),
    )


def downgrade() -> None:
    op.drop_table("purchasing_order_facts")
