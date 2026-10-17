"""Procurement ЗАМЕНА / РЕШ. ЗАМЕНЫ: an open substitute blocks the supplier request."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0035_procurement_replacement"
down_revision: str | None = "0034_warehouse_item_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("procurement_lines", sa.Column("replacement", sa.Boolean(), nullable=True))
    op.add_column("procurement_lines", sa.Column("replacement_decision", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("procurement_lines", "replacement_decision")
    op.drop_column("procurement_lines", "replacement")
