"""Supplier corrections and explicit receipt confirmations; no historical learning."""

from alembic import op
import sqlalchemy as sa

revision = "0174_supplier_item_rules"
down_revision = "0173_shipment_line_lots"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    with op.batch_alter_table("warehouse_receipts") as batch:
        batch.add_column(sa.Column("supplier_inn", sa.String(12), nullable=False, server_default=""))
    op.create_table(
        "warehouse_supplier_item_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("supplier_key", sa.String(260), nullable=False),
        sa.Column("supplier_code_key", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(2000), nullable=False),
        sa.Column("name_hash", sa.CHAR(64), nullable=False),
        sa.Column("article", sa.String(255), nullable=False),
        sa.Column("manufacturer", sa.String(255), nullable=False),
        sa.Column("stock_unit", sa.String(16), nullable=False),
        sa.Column("pack_size", sa.Numeric(18, 6), nullable=True),
        sa.Column("quantity_basis", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("supplier_key", "supplier_code_key", "name_hash", name="uq_supplier_item_rule_key"),
        sa.CheckConstraint("pack_size IS NULL OR pack_size > 0", name="ck_supplier_item_rule_pack"),
        sa.CheckConstraint("supplier_code_key <> '' OR name_key <> ''", name="ck_supplier_item_rule_item"),
        sa.CheckConstraint("supplier_key <> ''", name="ck_supplier_item_rule_supplier"),
        sa.CheckConstraint("quantity_basis IN ('base', 'packs')", name="ck_supplier_item_rule_basis"),
        sa.CheckConstraint("quantity_basis <> 'packs' OR pack_size IS NOT NULL", name="ck_supplier_item_rule_packs"),
        sa.CheckConstraint("version >= 1", name="ck_supplier_item_rule_version"),
    )
    op.create_table(
        "warehouse_receipt_line_confirmations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("acceptance_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), sa.ForeignKey("warehouse_receipt_files.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("warehouse_receipts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lot_id", sa.Uuid(), sa.ForeignKey("warehouse_lots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("warehouse_supplier_item_rules.id", ondelete="RESTRICT")),
        sa.Column("source_line_key", sa.String(128), nullable=False),
        sa.Column("rule_version", sa.Integer()),
        sa.Column("supplier_key", sa.String(260), nullable=False),
        sa.Column("original", sa.JSON(), nullable=False),
        sa.Column("accepted", sa.JSON(), nullable=False),
        sa.Column("learned", sa.Boolean(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("acceptance_id", name="uq_receipt_confirmation_acceptance"),
        sa.UniqueConstraint("lot_id", name="uq_receipt_confirmation_lot"),
    )
    op.create_index("ix_receipt_confirmation_source", "warehouse_receipt_line_confirmations", ["file_id", "source_line_key"])


def downgrade():
    op.drop_table("warehouse_receipt_line_confirmations")
    op.drop_table("warehouse_supplier_item_rules")
    with op.batch_alter_table("warehouse_receipts") as batch:
        batch.drop_column("supplier_inn")
