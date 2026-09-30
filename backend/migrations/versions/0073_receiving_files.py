"""Storage for receiving documents — УПД/М-15 and quality documents (SUPPLYMAP kept them on Drive)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0073_receiving_files"
down_revision: str | None = "0072_shipment_inherited_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "receiving_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "receiving_line_id",
            sa.Uuid(),
            sa.ForeignKey("receiving_lines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_receiving_files_receiving_line_id", "receiving_files", ["receiving_line_id"])


def downgrade() -> None:
    op.drop_index("ix_receiving_files_receiving_line_id", table_name="receiving_files")
    op.drop_table("receiving_files")
