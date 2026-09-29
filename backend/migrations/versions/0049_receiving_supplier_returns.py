"""Возврат поставщику (RC-6): ОПИСАНИЕ НЕСООТВ. и счётчик возвратов строки приёмки."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0049_receiving_supplier_returns"
down_revision: str | None = "0048_fulfillment_stage_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.add_column(
            sa.Column("nonconformity_description", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("supplier_returns", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_column("supplier_returns")
        batch.drop_column("nonconformity_description")
