"""Persist production-kit requests and human lot bindings."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_fulfillment"
down_revision: str | None = "0005_warehouse_manual_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for role, mode in (
        ("admin", "edit"),
        ("receiver", "edit"),
        ("engineer", "edit"),
        ("prodhead", "view"),
        ("master", "view"),
    ):
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, 'fulfillment', :mode)
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, mode=mode))
    op.create_table(
        "fulfillment_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("source_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requester", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=512), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("handout_location", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("source_idempotency_key"),
    )
    op.create_index("ix_fulfillment_requests_status", "fulfillment_requests", ["status"])
    op.create_index("ix_fulfillment_created_id", "fulfillment_requests", ["created_at", "id"])
    op.create_index("ix_fulfillment_status_created_id", "fulfillment_requests", ["status", "created_at", "id"])
    op.create_table(
        "fulfillment_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("article", sa.String(length=255), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("requirement", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 6), nullable=False),
        sa.Column("issued", sa.Numeric(18, 6), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("lot_warehouse", sa.String(length=8), nullable=True),
        sa.Column("lot_grn", sa.String(length=32), nullable=True),
        sa.Column("lot_package", sa.String(length=32), nullable=True),
        sa.Column("lot_storage_location", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["fulfillment_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fulfillment_lines_request_id", "fulfillment_lines", ["request_id"])
    op.create_index("ix_fulfillment_lines_requirement_id", "fulfillment_lines", ["requirement_id"])
    op.create_index("ix_fulfillment_lines_lot_id", "fulfillment_lines", ["lot_id"])


def downgrade() -> None:
    op.drop_index("ix_fulfillment_lines_lot_id", table_name="fulfillment_lines")
    op.drop_index("ix_fulfillment_lines_requirement_id", table_name="fulfillment_lines")
    op.drop_index("ix_fulfillment_lines_request_id", table_name="fulfillment_lines")
    op.drop_table("fulfillment_lines")
    op.drop_index("ix_fulfillment_status_created_id", table_name="fulfillment_requests")
    op.drop_index("ix_fulfillment_created_id", table_name="fulfillment_requests")
    op.drop_index("ix_fulfillment_requests_status", table_name="fulfillment_requests")
    op.drop_table("fulfillment_requests")
    op.execute(sa.text("""
        DELETE FROM identity_role_access
        WHERE section = 'fulfillment'
          AND role IN ('admin', 'receiver', 'engineer', 'prodhead', 'master')
    """))
