"""Приёмка: реквизиты УПД, примечание ВК, дата изготовления, контекст заказа; партии и пакеты `-ПN` (RC-13, RC-8)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0050_receiving_document_details"
down_revision: str | None = "0049_receiving_supplier_returns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LIVE = sa.text("status <> 'Архив ВК'")
_NEW_LIVE = sa.text("status <> 'Архив ВК' AND split_of_id IS NULL")


def upgrade() -> None:
    op.drop_index("ux_receiving_live_procurement_line", table_name="receiving_lines")
    with op.batch_alter_table("receiving_lines") as batch:
        batch.add_column(sa.Column("order_number", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("expected_arrival", sa.Date(), nullable=True))
        batch.add_column(sa.Column("document_number", sa.String(128), nullable=False, server_default=""))
        batch.add_column(sa.Column("document_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("document_name", sa.String(1024), nullable=False, server_default=""))
        batch.add_column(sa.Column("inspection_note", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("manufacture_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("split_of_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_receiving_lines_split_of_id", "receiving_lines", ["split_of_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index("ix_receiving_lines_split_of_id", "receiving_lines", ["split_of_id"])
    op.create_index(
        "ux_receiving_live_procurement_line", "receiving_lines", ["procurement_line_id"],
        unique=True, sqlite_where=_NEW_LIVE, postgresql_where=_NEW_LIVE,
    )


def downgrade() -> None:
    # Fails while batch or package lines exist: they share the procurement line of their origin.
    op.drop_index("ux_receiving_live_procurement_line", table_name="receiving_lines")
    op.drop_index("ix_receiving_lines_split_of_id", table_name="receiving_lines")
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_constraint("fk_receiving_lines_split_of_id", type_="foreignkey")
        for column in (
            "split_of_id", "manufacture_date", "inspection_note", "document_name", "document_date",
            "document_number", "expected_arrival", "order_number",
        ):
            batch.drop_column(column)
    op.create_index(
        "ux_receiving_live_procurement_line", "receiving_lines", ["procurement_line_id"],
        unique=True, sqlite_where=_OLD_LIVE, postgresql_where=_OLD_LIVE,
    )
