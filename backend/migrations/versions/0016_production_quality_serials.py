"""Add serial numbers, QC inspections, and measurement instruments."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016_production_quality_serials"
down_revision: str | None = "0015_production_defects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("production_boxes", sa.Column("rejected", sa.Numeric(18, 6), server_default="0", nullable=False))
    op.create_table(
        "production_instruments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("inventory_number", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("instrument_type", sa.String(255), nullable=False),
        sa.Column("verification_number", sa.String(255), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inventory_number"),
    )
    op.create_index("ix_production_instruments_verification_due", "production_instruments", ["verification_due"])
    op.create_index("ix_production_instruments_status", "production_instruments", ["status"])
    op.create_table(
        "production_serials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.String(128), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_box_id", sa.Uuid(), nullable=True),
        sa.Column("finished_goods_grn", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["current_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial_number"),
    )
    op.create_index("ux_production_serial_task_position", "production_serials", ["task_id", "position"], unique=True)
    op.create_index("ix_production_serials_task_id", "production_serials", ["task_id"])
    op.create_index("ix_production_serials_status", "production_serials", ["status"])
    op.create_index("ix_production_serials_current_box_id", "production_serials", ["current_box_id"])
    op.create_index("ix_production_serials_finished_goods_grn", "production_serials", ["finished_goods_grn"])
    op.create_table(
        "production_qc_inspections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("box_id", sa.Uuid(), nullable=False),
        sa.Column("inspector_id", sa.Uuid(), nullable=False),
        sa.Column("inspected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("defect_number", sa.String(20), nullable=True),
        sa.Column("defect_character", sa.String(255), nullable=False),
        sa.Column("defect_description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["inspector_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("box_id"),
        sa.UniqueConstraint("defect_number"),
    )
    op.create_index("ix_production_qc_inspections_inspector_id", "production_qc_inspections", ["inspector_id"])
    op.create_table(
        "production_qc_instruments",
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["production_qc_inspections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["production_instruments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("inspection_id", "instrument_id"),
    )


def downgrade() -> None:
    op.drop_table("production_qc_instruments")
    op.drop_index("ix_production_qc_inspections_inspector_id", table_name="production_qc_inspections")
    op.drop_table("production_qc_inspections")
    op.drop_index("ix_production_serials_finished_goods_grn", table_name="production_serials")
    op.drop_index("ix_production_serials_current_box_id", table_name="production_serials")
    op.drop_index("ix_production_serials_status", table_name="production_serials")
    op.drop_index("ix_production_serials_task_id", table_name="production_serials")
    op.drop_index("ux_production_serial_task_position", table_name="production_serials")
    op.drop_table("production_serials")
    op.drop_index("ix_production_instruments_status", table_name="production_instruments")
    op.drop_index("ix_production_instruments_verification_due", table_name="production_instruments")
    op.drop_table("production_instruments")
    op.drop_column("production_boxes", "rejected")

