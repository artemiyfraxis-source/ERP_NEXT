"""Add shop shift and operator competency matrix for automatic scheduling."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021_production_scheduling"
down_revision: str | None = "0020_production_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_shift",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("weekdays", sa.String(32), nullable=False),
        sa.Column("start_minutes", sa.Integer(), nullable=False),
        sa.Column("finish_minutes", sa.Integer(), nullable=False),
        sa.Column("breaks_json", sa.Text(), nullable=False),
        sa.Column("effective_seconds", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "production_operator_skills",
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operation_code", sa.String(255), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("coefficient", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operator_id", "operation_code"),
    )
    op.create_index(
        "ix_production_operator_skills_code_allowed",
        "production_operator_skills",
        ["operation_code", "allowed"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_production_operator_skills_code_allowed",
        table_name="production_operator_skills",
    )
    op.drop_table("production_operator_skills")
    op.drop_table("production_shift")
