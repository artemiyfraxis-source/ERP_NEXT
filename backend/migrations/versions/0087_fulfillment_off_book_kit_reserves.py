"""ГРН на все КБ проекта, «есть, но не заведено», резервы техкарт (владелец, 05.10.2026).

Schema only, every change defaulted or nullable (the production database is live):
- `fulfillment_lines.off_book` — «есть, но не заведено»: the position passes without a lot, without a reserve and
  without a СПИСАНИЕ row at issue. Existing lines — false.
- `fulfillment_kit_reserves` — techcard reserves: one КБ of a project holds part of a warehouse lot before its kit
  request exists (the lot's РЕЗЕРВ carries it); the kit request of the same project + КБ takes it over after the
  project's «Со склада» covers. Empty at creation.
- `procurement_stock_covers.lot_id` → nullable: «есть на складе без ГРН» in procurement (the rule comes later).

Downgrade drops the table and the column and makes `lot_id` NOT NULL again — it fails while a cover without a lot
exists; such covers have to be removed first.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0087_fulfillment_off_book_kit_reserves"
down_revision: str | None = "0086_identity_access_levels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.add_column(sa.Column("off_book", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "fulfillment_kit_reserves",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(128), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False, server_default=""),
        sa.Column("article", sa.String(255), nullable=False, server_default=""),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse", sa.String(8), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("package", sa.String(32), nullable=False, server_default=""),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("consumed", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("consumed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_fulfillment_kit_reserves_lot_quantity", "fulfillment_kit_reserves", ["lot_id", "quantity"])
    op.create_index(
        "ix_fulfillment_kit_reserves_project_item", "fulfillment_kit_reserves", ["project", "item_index"],
    )
    with op.batch_alter_table("procurement_stock_covers") as batch:
        batch.alter_column("lot_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("procurement_stock_covers") as batch:
        batch.alter_column("lot_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_fulfillment_kit_reserves_project_item", table_name="fulfillment_kit_reserves")
    op.drop_index("ix_fulfillment_kit_reserves_lot_quantity", table_name="fulfillment_kit_reserves")
    op.drop_table("fulfillment_kit_reserves")
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.drop_column("off_book")
