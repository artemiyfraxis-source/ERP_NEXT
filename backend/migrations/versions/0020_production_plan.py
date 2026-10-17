"""Add the native production-plan project projection and append-only journal."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020_production_plan"
down_revision: str | None = "0019_operation_card_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_plan_projects",
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("customer", sa.String(512), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project"),
    )
    op.create_table(
        "production_plan_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("who", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("blocker", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_production_plan_events_project",
        "production_plan_events",
        ["project"],
    )
    op.create_index(
        "ux_production_plan_events_project_sequence",
        "production_plan_events",
        ["project", "sequence"],
        unique=True,
    )
    op.create_index(
        "ix_production_plan_events_project_time",
        "production_plan_events",
        ["project", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ux_production_plan_events_project_sequence", table_name="production_plan_events")
    op.drop_index("ix_production_plan_events_project_time", table_name="production_plan_events")
    op.drop_index("ix_production_plan_events_project", table_name="production_plan_events")
    op.drop_table("production_plan_events")
    op.drop_table("production_plan_projects")
