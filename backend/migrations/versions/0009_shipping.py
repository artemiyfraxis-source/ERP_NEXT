"""Create finished-goods and shipment workflow."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_shipping"
down_revision: str | None = "0008_production_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for role, mode in (("admin", "edit"), ("pm", "edit"), ("receiver", "edit"), ("master", "view"), ("prodhead", "view"), ("management", "view")):
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, 'shipping', :mode)
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, mode=mode))
    op.create_table(
        "finished_goods",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False), sa.Column("item_index", sa.String(255), nullable=False),
        sa.Column("customer", sa.String(512), nullable=False), sa.Column("article", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False), sa.Column("ordered", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False), sa.Column("accepted", sa.Numeric(18, 6), nullable=False),
        sa.Column("shipped", sa.Numeric(18, 6), nullable=False), sa.Column("received_at", sa.Date(), nullable=True),
        sa.Column("grn", sa.String(32), nullable=True), sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("grn"),
    )
    op.create_index("ix_finished_goods_project", "finished_goods", ["project"])
    op.create_index("ux_finished_goods_project_item", "finished_goods", ["project", "item_index"], unique=True)
    op.create_table(
        "shipments",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("number", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("customer", sa.String(512), nullable=False),
        sa.Column("receiver", sa.String(512), nullable=False), sa.Column("shipping_point", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False), sa.Column("phone", sa.String(128), nullable=False),
        sa.Column("terms", sa.String(32), nullable=False), sa.Column("carrier", sa.String(255), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True), sa.Column("upd_type", sa.String(32), nullable=False),
        sa.Column("upd_number", sa.String(255), nullable=False), sa.Column("upd_date", sa.Date(), nullable=True),
        sa.Column("upd_url", sa.Text(), nullable=False), sa.Column("pl_number", sa.String(255), nullable=False),
        sa.Column("dn_number", sa.String(255), nullable=False), sa.Column("track_number", sa.String(255), nullable=False),
        sa.Column("packed_at", sa.Date(), nullable=True), sa.Column("shipped_at", sa.Date(), nullable=True),
        sa.Column("manager", sa.String(255), nullable=False), sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("number"),
    )
    op.create_index("ix_shipments_status", "shipments", ["status"])
    op.create_index("ix_shipments_created_id", "shipments", ["created_at", "id"])
    op.create_table(
        "shipment_lines",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("shipment_id", sa.Uuid(), nullable=False),
        sa.Column("finished_good_id", sa.Uuid(), nullable=False), sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False), sa.Column("item_index", sa.String(255), nullable=False),
        sa.Column("article", sa.String(255), nullable=False), sa.Column("name", sa.String(512), nullable=False),
        sa.Column("requested", sa.Numeric(18, 6), nullable=False), sa.Column("picked", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False), sa.Column("grn", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finished_good_id"], ["finished_goods.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipment_lines_shipment_id", "shipment_lines", ["shipment_id"])
    op.create_table("shipping_sequences", sa.Column("name", sa.String(64), nullable=False), sa.Column("value", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("name"))


def downgrade() -> None:
    op.drop_table("shipping_sequences")
    op.drop_index("ix_shipment_lines_shipment_id", table_name="shipment_lines")
    op.drop_table("shipment_lines")
    op.drop_index("ix_shipments_created_id", table_name="shipments")
    op.drop_index("ix_shipments_status", table_name="shipments")
    op.drop_table("shipments")
    op.drop_index("ux_finished_goods_project_item", table_name="finished_goods")
    op.drop_index("ix_finished_goods_project", table_name="finished_goods")
    op.drop_table("finished_goods")
    op.execute(sa.text("DELETE FROM identity_role_access WHERE section = 'shipping'"))
