"""НЕДОБОР written when a fulfillment request is created (issueCreateTasks_ shortfall + shortNote)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0061_fulfillment_shortfall_note"
down_revision: str | None = "0060_drop_calculation_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.add_column(sa.Column("shortfall_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fulfillment_requests") as batch:
        batch.drop_column("shortfall_note")
