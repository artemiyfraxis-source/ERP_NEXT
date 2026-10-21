"""Печать этикеток ГРН: настройки принтера этикеток (стенд Вити, опция warehouse.labels, 19.10.2026).

Branch vitya/hybrid only. One row per installation (id = 1, like `warehouse_rack_layout`): the Windows print queue's
name, the label size and resolution, the code type, the gap from the printer's calibration, head heat, speed and the
print offset. The bounds are those of Roller_Buyer's `printSettingsSchema`, held by CHECK constraints so a bad setting
cannot be written. No row = the defaults proven on the TSC MB341 (density 12, speed 2, gap 2 mm).

Downgrade drops the table with its row.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0138_warehouse_labels"
down_revision: str | None = "0137_purchasing_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_label_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("printer_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("code_type", sa.String(8), nullable=False),
        sa.Column("width_mm", sa.Numeric(5, 2), nullable=False),
        sa.Column("height_mm", sa.Numeric(5, 2), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False),
        sa.Column("gap_mm", sa.Numeric(4, 2), nullable=False),
        sa.Column("density", sa.Integer(), nullable=False),
        sa.Column("speed", sa.Integer(), nullable=False),
        sa.Column("offset_x_mm", sa.Numeric(4, 2), nullable=False),
        sa.Column("offset_y_mm", sa.Numeric(4, 2), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default=""),
        sa.CheckConstraint("id = 1", name="ck_warehouse_label_settings_single_row"),
        sa.CheckConstraint("code_type IN ('qr', 'code128', 'both')", name="ck_warehouse_label_settings_code_type"),
        sa.CheckConstraint("width_mm >= 45 AND width_mm <= 110", name="ck_warehouse_label_settings_width"),
        sa.CheckConstraint("height_mm >= 20 AND height_mm <= 80", name="ck_warehouse_label_settings_height"),
        sa.CheckConstraint("dpi IN (203, 300)", name="ck_warehouse_label_settings_dpi"),
        sa.CheckConstraint("gap_mm >= 0 AND gap_mm <= 20", name="ck_warehouse_label_settings_gap"),
        sa.CheckConstraint("density >= 0 AND density <= 15", name="ck_warehouse_label_settings_density"),
        sa.CheckConstraint("speed >= 1 AND speed <= 12", name="ck_warehouse_label_settings_speed"),
        sa.CheckConstraint("offset_x_mm >= -5 AND offset_x_mm <= 5", name="ck_warehouse_label_settings_offset_x"),
        sa.CheckConstraint("offset_y_mm >= -5 AND offset_y_mm <= 5", name="ck_warehouse_label_settings_offset_y"),
    )


def downgrade() -> None:
    op.drop_table("warehouse_label_settings")
