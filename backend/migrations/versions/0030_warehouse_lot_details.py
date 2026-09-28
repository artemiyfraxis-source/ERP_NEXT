"""Lot passport (SK-6): МАССА 1 ШТ, Г, СРОК ГОДНОСТИ, ПРОИЗВОДИТЕЛЬ, ОПИСАНИЕ, УСЛОВИЯ ХРАНЕНИЯ, DATASHEET,
СЕРТИФИКАТ on warehouse lots, and the mass weighed at incoming control on receiving lines."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0030_warehouse_lot_details"
down_revision: str | None = "0029_procurement_supplier_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT_COLUMNS = (
    ("manufacturer", sa.String(255)),
    ("description", sa.Text()),
    ("storage_conditions", sa.String(1024)),
    ("datasheet", sa.Text()),
    ("certificate", sa.Text()),
)


def upgrade() -> None:
    op.add_column("warehouse_lots", sa.Column("unit_mass_g", sa.Numeric(18, 5), nullable=True))
    op.add_column("warehouse_lots", sa.Column("expiry_date", sa.Date(), nullable=True))
    for name, column_type in _TEXT_COLUMNS:
        op.add_column("warehouse_lots", sa.Column(name, column_type, nullable=False, server_default=""))
    op.add_column("receiving_lines", sa.Column("unit_mass_g", sa.Numeric(18, 5), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_column("unit_mass_g")
    with op.batch_alter_table("warehouse_lots") as batch:
        for name, _ in reversed(_TEXT_COLUMNS):
            batch.drop_column(name)
        batch.drop_column("expiry_date")
        batch.drop_column("unit_mass_g")
