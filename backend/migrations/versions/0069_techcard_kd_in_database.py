"""КД проекта — в базе ERP (решение владельца 29.09.2026: уходим от Google Диска).

`techcard_kitset_documents` снова хранит байты файла (`content`), тип и размер. `file_id` и `url` остаются —
ссылка на Диск у строк, вложенных при 0068, только для чтения; их байты приносит разовый импорт
(`python -m app.modules.techcards.import_kd_from_drive`). Новые колонки допускают NULL: у такой строки байтов нет,
пока импорт не прошёл. Откат снимает байты — файлы, вложенные после 0069, останутся только строкой реестра.

Revision ID: 0069_techcard_kd_in_database
Revises: 0068_techcard_kd_on_drive
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0069_techcard_kd_in_database"
down_revision: str | None = "0068_techcard_kd_on_drive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("techcard_kitset_documents") as batch:
        batch.add_column(sa.Column("content_type", sa.String(255), nullable=True))
        batch.add_column(sa.Column("size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("content", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("techcard_kitset_documents") as batch:
        batch.drop_column("content")
        batch.drop_column("size")
        batch.drop_column("content_type")
