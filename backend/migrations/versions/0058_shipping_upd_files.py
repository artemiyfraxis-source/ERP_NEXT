"""Storage for shipment УПД files (SUPPLYMAP shipUpdAttach_ kept them in a Drive folder)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0058_shipping_upd_files"
down_revision: str | None = "0057_shipping_optional_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipping_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shipping_files_shipment_id", "shipping_files", ["shipment_id"])


def downgrade() -> None:
    op.drop_index("ix_shipping_files_shipment_id", table_name="shipping_files")
    op.drop_table("shipping_files")
