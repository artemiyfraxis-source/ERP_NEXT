"""«Со склада» в листе закупки (владелец, 04.10: «можем закрыть со склада — выбираем партию, она резервируется»).

Строка закупки держит резерв на партиях склада вместо покупки: `procurement_stock_covers` — кто, сколько, на какой
партии (РЕЗЕРВ самой партии растёт на то же число), `procurement_lines.stock_covered` — сколько из КОЛ-ВО (Расчет)
закрыто со склада. Существующие строки — ноль."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0079_procurement_stock_covers"
down_revision: str | None = "0078_wipe_projects_once"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.add_column(sa.Column("stock_covered", sa.Numeric(18, 6), nullable=False, server_default="0"))
    op.create_table(
        "procurement_stock_covers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "line_id", sa.Uuid(), sa.ForeignKey("procurement_lines.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("legacy_id", sa.String(32), nullable=False),
        sa.Column("project_reference", sa.String(128), nullable=True),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse", sa.String(8), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("package", sa.String(32), nullable=False),
        sa.Column("storage_location", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("trimmed", sa.Numeric(18, 6), nullable=False),
        sa.Column("consumed", sa.Numeric(18, 6), nullable=False),
        sa.Column("consumed_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_procurement_stock_covers_line_id", "procurement_stock_covers", ["line_id"])
    op.create_index("ix_procurement_stock_covers_project_reference", "procurement_stock_covers", ["project_reference"])
    op.create_index("ix_procurement_stock_covers_lot_quantity", "procurement_stock_covers", ["lot_id", "quantity"])


def downgrade() -> None:
    op.drop_index("ix_procurement_stock_covers_lot_quantity", table_name="procurement_stock_covers")
    op.drop_index("ix_procurement_stock_covers_project_reference", table_name="procurement_stock_covers")
    op.drop_index("ix_procurement_stock_covers_line_id", table_name="procurement_stock_covers")
    op.drop_table("procurement_stock_covers")
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_column("stock_covered")
