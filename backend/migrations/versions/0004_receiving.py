"""Create Receiving-owned intake and incoming-control storage."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_receiving"
down_revision: str | None = "0003_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for role, section in (
        ("admin", "receiving"),
        ("admin", "warehouse"),
        ("receiver", "receiving"),
        ("receiver", "warehouse"),
    ):
        op.execute(
            sa.text(
                """
                INSERT INTO identity_role_access (role, section, mode)
                VALUES (:role, :section, 'edit')
                ON CONFLICT (role, section) DO NOTHING
                """
            ).bindparams(role=role, section=section)
        )
    op.add_column(
        "procurement_lines",
        sa.Column("quality_document", sa.String(length=8), nullable=False, server_default="Нет"),
    )
    op.add_column(
        "procurement_lines", sa.Column("supplier_request_reference", sa.Text(), nullable=True)
    )
    op.add_column("procurement_lines", sa.Column("expected_arrival", sa.Date(), nullable=True))
    op.add_column("procurement_lines", sa.Column("invoice_url", sa.Text(), nullable=True))
    op.add_column(
        "procurement_lines", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "receiving_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("procurement_line_id", sa.Uuid(), nullable=False),
        sa.Column("legacy_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warehouse", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=1024), nullable=False),
        sa.Column("item_type", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("goal", sa.String(length=128), nullable=False),
        sa.Column("source_link", sa.Text(), nullable=False),
        sa.Column("supplier", sa.String(length=512), nullable=False),
        sa.Column("ordered_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quality_document_required", sa.Boolean(), nullable=False),
        sa.Column("document_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("fact_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("shipping_document_url", sa.Text(), nullable=True),
        sa.Column("quality_document_url", sa.Text(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("disposition", sa.String(length=255), nullable=True),
        sa.Column("verdict", sa.String(length=64), nullable=True),
        sa.Column("nonconformities", sa.JSON(), nullable=False),
        sa.Column("accepted_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("backorder_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("grn", sa.String(length=32), nullable=True),
        sa.Column("package", sa.String(length=32), nullable=False),
        sa.Column("storage_location", sa.String(length=16), nullable=True),
        sa.Column("inspector_id", sa.Uuid(), nullable=True),
        sa.Column("inspector_name", sa.String(length=255), nullable=True),
        sa.Column("inspector_department", sa.String(length=255), nullable=True),
        sa.Column("warehouse_event_emitted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grn", "package", name="uq_receiving_grn_package"),
        sa.UniqueConstraint("procurement_line_id"),
        sa.UniqueConstraint("source_event_id"),
        sa.UniqueConstraint("source_idempotency_key"),
    )
    op.create_index("ix_receiving_lines_legacy_id", "receiving_lines", ["legacy_id"])
    op.create_index("ix_receiving_lines_status", "receiving_lines", ["status"])
    op.create_index("ix_receiving_lines_inspector_id", "receiving_lines", ["inspector_id"])
    op.create_index(
        "ix_receiving_created_id", "receiving_lines", ["created_at", "id"]
    )
    op.create_index(
        "ix_receiving_status_created_id", "receiving_lines", ["status", "created_at", "id"]
    )
    op.create_index(
        "ix_receiving_warehouse_status_created_id",
        "receiving_lines",
        ["warehouse", "status", "created_at", "id"],
    )
    op.create_table(
        "warehouse_lots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("warehouse", sa.String(length=8), nullable=False),
        sa.Column("grn", sa.String(length=32), nullable=False),
        sa.Column("package", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=1024), nullable=False),
        sa.Column("item_type", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("storage_location", sa.String(length=16), nullable=False),
        sa.Column("inbound", sa.Numeric(18, 6), nullable=False),
        sa.Column("written_off", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 6), nullable=False),
        sa.Column("source_receiving_ids", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("warehouse", "grn", "package", name="uq_warehouse_lot_key"),
    )
    op.create_index("ix_warehouse_lots_warehouse", "warehouse_lots", ["warehouse"])
    op.create_index("ix_warehouse_lots_grn", "warehouse_lots", ["grn"])
    op.create_index(
        "ix_warehouse_location", "warehouse_lots", ["warehouse", "storage_location"]
    )


def downgrade() -> None:
    op.drop_index("ix_warehouse_location", table_name="warehouse_lots")
    op.drop_index("ix_warehouse_lots_grn", table_name="warehouse_lots")
    op.drop_index("ix_warehouse_lots_warehouse", table_name="warehouse_lots")
    op.drop_table("warehouse_lots")
    op.drop_index("ix_receiving_warehouse_status_created_id", table_name="receiving_lines")
    op.drop_index("ix_receiving_status_created_id", table_name="receiving_lines")
    op.drop_index("ix_receiving_created_id", table_name="receiving_lines")
    op.drop_index("ix_receiving_lines_inspector_id", table_name="receiving_lines")
    op.drop_index("ix_receiving_lines_status", table_name="receiving_lines")
    op.drop_index("ix_receiving_lines_legacy_id", table_name="receiving_lines")
    op.drop_table("receiving_lines")
    op.drop_column("procurement_lines", "paid_at")
    op.drop_column("procurement_lines", "invoice_url")
    op.drop_column("procurement_lines", "expected_arrival")
    op.drop_column("procurement_lines", "supplier_request_reference")
    op.drop_column("procurement_lines", "quality_document")
    op.execute(
        sa.text(
            """
            DELETE FROM identity_role_access
            WHERE (role = 'admin' OR role = 'receiver')
              AND (section = 'receiving' OR section = 'warehouse')
            """
        )
    )
