"""Add the lossless SUPPLYMAP operation-card template library."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0019_operation_card_library"
down_revision: str | None = "0018_project_techcards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "techcard_operation_templates",
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("code", sa.String(255), nullable=False),
        sa.Column("side", sa.String(1), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("category", sa.String(255), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("raw_rows", sa.JSON(), nullable=False),
        sa.Column("source_commit", sa.String(40), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_techcard_operation_templates_code_side",
        "techcard_operation_templates",
        ["code", "side"],
    )
    op.create_table(
        "techcard_operation_template_snapshots",
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("source_commit", sa.String(40), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("techcard_operation_template_snapshots")
    op.drop_index(
        "ix_techcard_operation_templates_code_side",
        table_name="techcard_operation_templates",
    )
    op.drop_table("techcard_operation_templates")
