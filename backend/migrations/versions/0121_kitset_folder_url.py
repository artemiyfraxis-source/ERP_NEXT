"""Ссылка на папку проекта (владелец, 14.10.2026): `techcard_kitsets.folder_url` — http(s)-адрес папки, показывается в
шапке карточки проекта рядом со ссылкой на расчёт.

Колонка пустая (NULL — ссылки нет), существующие строки не меняются. CHECK: только http:// или https://, до 1024
символов. DESTRUCTIVE = False. Откат снимает колонку.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0121_kitset_folder_url"
down_revision: str | None = "0120_operation_etk_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "techcard_kitsets"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("folder_url", sa.String(1024), nullable=True))
        batch.create_check_constraint(
            "ck_techcard_kitsets_folder_url",
            "folder_url IS NULL OR ((folder_url LIKE 'http://%' OR folder_url LIKE 'https://%') AND LENGTH(folder_url) <= 1024)",
        )
    print(f"0121_kitset_folder_url: колонка {TABLE}.folder_url (пустая)")


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_techcard_kitsets_folder_url", type_="check")
        batch.drop_column("folder_url")
