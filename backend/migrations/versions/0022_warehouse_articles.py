"""Separate warehouse article, description, and manufacturer series."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0022_warehouse_articles"
down_revision: str | None = "0021_production_scheduling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receiving_lines",
        sa.Column("article", sa.String(1024), nullable=False, server_default=""),
    )
    op.add_column(
        "receiving_lines",
        sa.Column("series", sa.String(255), nullable=False, server_default=""),
    )
    op.execute("UPDATE receiving_lines SET article = name WHERE article = ''")
    op.add_column(
        "warehouse_lots",
        sa.Column("article", sa.String(1024), nullable=False, server_default=""),
    )
    op.add_column(
        "warehouse_lots",
        sa.Column("series", sa.String(255), nullable=False, server_default=""),
    )
    op.execute("UPDATE warehouse_lots SET article = name, name = '' WHERE article = ''")
    op.create_index("ix_warehouse_lots_article", "warehouse_lots", ["article"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_lots_article", table_name="warehouse_lots")
    op.execute("UPDATE warehouse_lots SET name = article WHERE article <> ''")
    op.drop_column("warehouse_lots", "series")
    op.drop_column("warehouse_lots", "article")
    op.drop_column("receiving_lines", "series")
    op.drop_column("receiving_lines", "article")
