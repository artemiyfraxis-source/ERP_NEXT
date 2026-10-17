"""Add manual kit stage and techcard review event fields."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0023_production_plan_kit_marks"
down_revision: str | None = "0022_warehouse_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "production_plan_events",
        sa.Column("item_index", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "production_plan_events",
        sa.Column("value", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("production_plan_events", "value")
    op.drop_column("production_plan_events", "item_index")
