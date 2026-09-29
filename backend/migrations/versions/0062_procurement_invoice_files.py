"""Хранилище файлов счетов закупки (SUPPLYMAP uploadInvoice клал их в папку Drive «Счета»)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0062_procurement_invoice_files"
down_revision: str | None = "0061_procurement_traceability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_procurement_files_line_id", "procurement_files", ["line_id"])


def downgrade() -> None:
    op.drop_index("ix_procurement_files_line_id", table_name="procurement_files")
    op.drop_table("procurement_files")
