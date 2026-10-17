"""A lot keeps an old storage address as is: the place column fits free text, not only a rack code."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0027_warehouse_location_as_is"
down_revision: str | None = "0026_production_output_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.alter_column(
            "storage_location", existing_type=sa.String(16), type_=sa.String(255), existing_nullable=False,
        )
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.alter_column(
            "lot_storage_location", existing_type=sa.String(16), type_=sa.String(255), existing_nullable=True,
        )


def downgrade() -> None:
    # Fails on PostgreSQL while an old address longer than 16 characters is stored.
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.alter_column(
            "lot_storage_location", existing_type=sa.String(255), type_=sa.String(16), existing_nullable=True,
        )
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.alter_column(
            "storage_location", existing_type=sa.String(255), type_=sa.String(16), existing_nullable=False,
        )
