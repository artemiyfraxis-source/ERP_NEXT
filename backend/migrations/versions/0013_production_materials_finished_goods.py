"""Connect first production operation to issued lots and last box to finished goods."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_production_materials_finished_goods"
down_revision: str | None = "0012_production_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_work_material_inputs",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("fulfillment_line_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_line_id"], ["fulfillment_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["production_work_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "fulfillment_line_id"),
    )
    op.create_table(
        "production_material_consumption",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fulfillment_line_id", sa.Uuid(), nullable=False),
        sa.Column("target_box_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_id", sa.Uuid(), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("package", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["fulfillment_line_id"], ["fulfillment_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["output_id"], ["production_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_material_consumption_fulfillment_line_id", "production_material_consumption", ["fulfillment_line_id"])
    op.create_index("ix_production_material_consumption_target_box_id", "production_material_consumption", ["target_box_id"])
    op.create_index("ix_production_material_consumption_output_id", "production_material_consumption", ["output_id"])
    op.create_index("ix_production_material_consumption_grn", "production_material_consumption", ["grn"])
    op.create_table(
        "production_finished_goods_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_box_id", sa.Uuid(), nullable=False),
        sa.Column("finished_good_id", sa.Uuid(), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["finished_good_id"], ["finished_goods.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_box_id"),
    )
    op.create_index("ix_production_finished_goods_receipts_finished_good_id", "production_finished_goods_receipts", ["finished_good_id"])
    op.create_index("ix_production_finished_goods_receipts_grn", "production_finished_goods_receipts", ["grn"])


def downgrade() -> None:
    op.drop_index("ix_production_finished_goods_receipts_grn", table_name="production_finished_goods_receipts")
    op.drop_index("ix_production_finished_goods_receipts_finished_good_id", table_name="production_finished_goods_receipts")
    op.drop_table("production_finished_goods_receipts")
    op.drop_index("ix_production_material_consumption_grn", table_name="production_material_consumption")
    op.drop_index("ix_production_material_consumption_output_id", table_name="production_material_consumption")
    op.drop_index("ix_production_material_consumption_target_box_id", table_name="production_material_consumption")
    op.drop_index("ix_production_material_consumption_fulfillment_line_id", table_name="production_material_consumption")
    op.drop_table("production_material_consumption")
    op.drop_table("production_work_material_inputs")

