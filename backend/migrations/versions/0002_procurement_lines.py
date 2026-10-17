"""Create Procurement-owned line storage."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_procurement"
down_revision: str | None = "0001_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "procurement_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_request_id", sa.Uuid(), nullable=False),
        sa.Column("legacy_id", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("initiator", sa.String(length=255), nullable=False),
        sa.Column("department", sa.String(length=255), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("designation", sa.String(length=128), nullable=False),
        sa.Column("project_reference", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.String(length=64), nullable=False),
        sa.Column("delivery_point", sa.String(length=512), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("item_type", sa.String(length=255), nullable=False),
        sa.Column("design_article", sa.String(length=512), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("calculated_name", sa.String(length=1024), nullable=False),
        sa.Column("calculated_quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("calculated_unit", sa.String(length=32), nullable=False),
        sa.Column("calculated_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column(
            "calculated_price_with_vat",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
        ),
        sa.Column("calculated_sum", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("buyer_name", sa.String(length=255), nullable=True),
        sa.Column("buyer_department", sa.String(length=255), nullable=True),
        sa.Column("buyer_id", sa.Uuid(), nullable=True),
        sa.Column("supplier", sa.String(length=512), nullable=True),
        sa.Column("site_name", sa.String(length=512), nullable=True),
        sa.Column("actual_quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("actual_unit", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_event_id"),
        sa.UniqueConstraint("source_idempotency_key"),
    )
    op.create_index(
        "ix_procurement_lines_designation", "procurement_lines", ["designation"]
    )
    op.create_index("ix_procurement_lines_buyer_id", "procurement_lines", ["buyer_id"])
    op.create_index(
        "ix_procurement_lines_legacy_id", "procurement_lines", ["legacy_id"]
    )
    op.create_index(
        "ix_procurement_lines_project_reference",
        "procurement_lines",
        ["project_reference"],
    )
    op.create_index(
        "ix_procurement_lines_source_request_id",
        "procurement_lines",
        ["source_request_id"],
    )
    op.create_index("ix_procurement_lines_status", "procurement_lines", ["status"])
    op.create_index(
        "ix_procurement_status_created_id",
        "procurement_lines",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_procurement_buyer_status_created_id",
        "procurement_lines",
        ["buyer_id", "status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_procurement_buyer_status_created_id", table_name="procurement_lines"
    )
    op.drop_index("ix_procurement_status_created_id", table_name="procurement_lines")
    op.drop_index("ix_procurement_lines_status", table_name="procurement_lines")
    op.drop_index(
        "ix_procurement_lines_source_request_id", table_name="procurement_lines"
    )
    op.drop_index(
        "ix_procurement_lines_project_reference", table_name="procurement_lines"
    )
    op.drop_index("ix_procurement_lines_legacy_id", table_name="procurement_lines")
    op.drop_index("ix_procurement_lines_designation", table_name="procurement_lines")
    op.drop_index("ix_procurement_lines_buyer_id", table_name="procurement_lines")
    op.drop_table("procurement_lines")
