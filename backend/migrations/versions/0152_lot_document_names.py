"""Документы поставки у партии склада (owner 26.10.2026: «склад увеличиваем: там должно быть, но скрыто —
наименование по счёту, наименование по УПД…»; ПРД — «К платёжно-расчётному документу № 916 от 19.10.2026»).

- `warehouse_lots.upd_name` — the item row as the УПД prints it; the lot's own `name` is ours (the reference DB's, a
  known lot's or the model's short one). '' — unknown.
- `warehouse_lots.invoice_name` — the row as the paid invoice names it; filled later by the purchasing chain, '' now.
- `warehouse_receipts.prd_number` / `prd_date` — the payment document(s) of the УПД (not the invoice): the numbers
  joined by «, », the first one's date. '' / NULL — none.

Backfill: a lot accepted with a delivery whose УПД was scanned (`warehouse_receipts.scan_file` set) was accepted with
the full printed line as its name until now — that name is copied into `upd_name` (only where it is still ''). No lot
is renamed, no row is added or removed. Non-destructive: DESTRUCTIVE = False. Downgrade drops the four columns.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0152_lot_document_names"
down_revision: str | None = "0151_upd_llm_recognition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False


def upgrade() -> None:
    with op.batch_alter_table("warehouse_receipts") as batch:
        batch.add_column(sa.Column("prd_number", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("prd_date", sa.Date(), nullable=True))
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.add_column(sa.Column("upd_name", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("invoice_name", sa.Text(), nullable=False, server_default=""))
    op.execute(
        "UPDATE warehouse_lots SET upd_name = name"
        " WHERE upd_name = '' AND name <> '' AND receipt_id IN"
        " (SELECT id FROM warehouse_receipts WHERE scan_file <> '')"
    )


def downgrade() -> None:
    with op.batch_alter_table("warehouse_lots") as batch:
        batch.drop_column("invoice_name")
        batch.drop_column("upd_name")
    with op.batch_alter_table("warehouse_receipts") as batch:
        batch.drop_column("prd_date")
        batch.drop_column("prd_number")
