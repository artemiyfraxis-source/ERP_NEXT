"""The item type dictionary of the stock (SK-14): the list the storekeeper saves, one row per type in dropdown
order. The table starts empty — until someone saves a list, the canonical one from the code is used, as
SUPPLYMAP does with an unset ScriptProperty. Lot types are not rewritten here: renaming a type is an explicit,
audited action."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0034_warehouse_item_types"
down_revision: str | None = "0033_warehouse_writeoff_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_item_types",
        sa.Column("position", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(80), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("warehouse_item_types")
