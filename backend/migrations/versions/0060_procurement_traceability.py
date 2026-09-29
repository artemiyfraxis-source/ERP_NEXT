"""Прослеживаемость строки закупки: изготовитель, страна, тип/№/дата документа качества, стандарт.

«Лист закупки» SUPPLYMAP (TRACE_COLUMNS.PURCHASE, Config.gs:269) — закупщик заносит их у себя, в приёмку они
уходят со снимком поставки (receiving.expected).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0060_procurement_traceability"
down_revision: str | None = "0059_receiving_quality_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = (
    ("manufacturer", 255),
    ("country", 128),
    ("quality_document_type", 128),
    ("quality_document_number", 128),
    ("standard", 255),
)


def upgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        for name, length in TEXT:
            batch.add_column(sa.Column(name, sa.String(length), nullable=True))
        batch.add_column(sa.Column("quality_document_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_column("quality_document_date")
        for name, _length in reversed(TEXT):
            batch.drop_column(name)
