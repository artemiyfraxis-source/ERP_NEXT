"""Add transactional yearly numbering for manually counted stock."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_warehouse_manual_sequence"
down_revision: str | None = "0004_receiving"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_sequences",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("warehouse_sequences")
