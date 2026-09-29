"""Изготовитель, страна, тип/№/дата документа качества и стандарт строки приёмки — для акта ВК."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0057_receiving_quality_details"
down_revision: str | None = "0056_identity_explicit_access"
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
    with op.batch_alter_table("receiving_lines") as batch:
        for name, length in TEXT:
            batch.add_column(sa.Column(name, sa.String(length), nullable=False, server_default=""))
        batch.add_column(sa.Column("quality_document_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_column("quality_document_date")
        for name, _ in reversed(TEXT):
            batch.drop_column(name)
