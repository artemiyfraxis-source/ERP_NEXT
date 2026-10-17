"""Add append-only decisions for QC rejections."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_production_qc_decisions"
down_revision: str | None = "0016_production_quality_serials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_qc_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("act_reference", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["production_qc_inspections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inspection_id"),
    )


def downgrade() -> None:
    op.drop_table("production_qc_decisions")
