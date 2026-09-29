"""Раскладка стеллажей склада, сохраняемая с экрана (SUPPLYMAP portalStock05PlacesSave)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0052_warehouse_rack_layout"
down_revision: str | None = "0051_receiving_acts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_rack_layout",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("racks", sa.Integer(), nullable=False),
        sa.Column("shelves", sa.Integer(), nullable=False),
        sa.Column("per_rack", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("warehouse_rack_layout")
