"""Shipment delivery terms and УПД type may stay empty, as in SUPPLYMAP shipRequestCreate_."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0051_shipping_optional_terms"
down_revision: str | None = "0050_shipping_boxes_requisites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shipments") as batch:
        batch.alter_column("terms", existing_type=sa.String(32), nullable=True)
        batch.alter_column("upd_type", existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE shipments SET terms = 'САМОВЫВОЗ' WHERE terms IS NULL")
    op.execute("UPDATE shipments SET upd_type = 'БУМАГА' WHERE upd_type IS NULL")
    with op.batch_alter_table("shipments") as batch:
        batch.alter_column("upd_type", existing_type=sa.String(32), nullable=False)
        batch.alter_column("terms", existing_type=sa.String(32), nullable=False)
