"""Входные коробки работы — в порядке ввода: годные + брак берутся из них по порядку (SUPPLYMAP prodPick_).

Revision ID: 0064_production_work_input_order
Revises: 0063_techcard_template_sheets
"""

import sqlalchemy as sa
from alembic import op


revision = "0064_production_work_input_order"
down_revision = "0063_techcard_template_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("production_work_inputs") as batch:
        batch.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("production_work_inputs") as batch:
        batch.drop_column("position")
