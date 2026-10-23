"""«Приход на склад» (owner 23.10.2026): a delivery header and the lots accepted from it.

- `warehouse_receipts` — one delivery: склад (CHECK ЦАР / ЖУК), поставщик, № и дата счёта, № и дата УПД, назначение
  по умолчанию, ссылка на скан УПД, version (≥ 1, optimistic lock of the header), who and when created / changed.
  Texts NOT NULL default ''. No date of acceptance: each lot's own arrival event is its moment.
- `warehouse_receipt_files` — the УПД scans («Отсканировать УПД»), bytes kept in the database as the procurement
  invoice files are (size > 0).
- `warehouse_lots.receipt_id` — nullable FK → warehouse_receipts.id ON DELETE RESTRICT, indexed. Every existing lot
  keeps NULL; nothing is backfilled.

Non-destructive: two new empty tables and one nullable column. DESTRUCTIVE = False. Downgrade drops the column (the
link of lots to their deliveries is lost, the lots stay) and both tables.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0143_warehouse_receipts"
down_revision: str | None = "0142_production_vswr_measurements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False
LOTS = "warehouse_lots"
FK = "fk_warehouse_lots_receipt_id"
INDEX = "ix_warehouse_lots_receipt_id"


def upgrade() -> None:
    op.create_table(
        "warehouse_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("warehouse", sa.String(8), nullable=False),
        sa.Column("supplier", sa.String(255), nullable=False, server_default=""),
        sa.Column("invoice_number", sa.String(64), nullable=False, server_default=""),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("upd_number", sa.String(64), nullable=False, server_default=""),
        sa.Column("upd_date", sa.Date(), nullable=True),
        sa.Column("purpose", sa.String(128), nullable=False, server_default=""),
        sa.Column("scan_file", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("warehouse IN ('ЦАР', 'ЖУК')", name="ck_warehouse_receipts_warehouse"),
        sa.CheckConstraint("version >= 1", name="ck_warehouse_receipts_version"),
    )
    op.create_index("ix_warehouse_receipts_created_at", "warehouse_receipts", ["created_at"])
    op.create_table(
        "warehouse_receipt_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size > 0", name="ck_warehouse_receipt_files_size"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.add_column(LOTS, sa.Column("receipt_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(FK, LOTS, "warehouse_receipts", ["receipt_id"], ["id"], ondelete="RESTRICT")
    else:
        # SQLite has no ALTER … ADD CONSTRAINT: the table is rebuilt with its rows.
        with op.batch_alter_table(LOTS, recreate="always") as batch:
            batch.add_column(sa.Column("receipt_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(FK, "warehouse_receipts", ["receipt_id"], ["id"], ondelete="RESTRICT")
    op.create_index(INDEX, LOTS, ["receipt_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name=LOTS)
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(FK, LOTS, type_="foreignkey")
        op.drop_column(LOTS, "receipt_id")
    else:
        with op.batch_alter_table(LOTS, recreate="always") as batch:
            batch.drop_constraint(FK, type_="foreignkey")
            batch.drop_column("receipt_id")
    op.drop_table("warehouse_receipt_files")
    op.drop_index("ix_warehouse_receipts_created_at", table_name="warehouse_receipts")
    op.drop_table("warehouse_receipts")
