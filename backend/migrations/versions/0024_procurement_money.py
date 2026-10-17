"""Procurement fact money: prices, VAT, sum, currency, CBR rate and invoice identity."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0024_procurement_money"
down_revision: str | None = "0023_production_plan_kit_marks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("currency", sa.String(8)),
    ("actual_price", sa.Numeric(18, 6)),
    ("actual_vat_rate", sa.Numeric(5, 2)),
    ("actual_price_with_vat", sa.Numeric(18, 6)),
    ("actual_sum", sa.Numeric(20, 2)),
    ("money_driver", sa.String(8)),
    ("source_currency", sa.String(8)),
    ("source_price", sa.Numeric(18, 6)),
    ("source_price_with_vat", sa.Numeric(18, 6)),
    ("source_sum", sa.Numeric(20, 6)),
    ("exchange_rate", sa.Numeric(24, 10)),
    ("exchange_rate_date", sa.Date()),
    ("invoice_item_name", sa.String(1024)),
    ("invoice_number", sa.String(128)),
    ("invoice_date", sa.Date()),
)


def upgrade() -> None:
    for name, column_type in _COLUMNS:
        op.add_column("procurement_lines", sa.Column(name, column_type, nullable=True))
    op.add_column(
        "procurement_lines",
        sa.Column("exchange_rate_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("procurement_lines", "exchange_rate_manual")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("procurement_lines", name)
