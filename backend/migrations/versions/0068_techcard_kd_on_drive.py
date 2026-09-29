"""КД проекта — на Google Диске, как в SUPPLYMAP TcKd.gs (лист «КД» + папка «Проекты/<проект>/КД»).

`techcard_kitset_documents` становится реестром вложений: имя, ссылка и ID файла на Диске, кто и когда
загрузил. Байты файла (`content`), тип и размер из ERP уходят. Строки, загруженные до переноса, остаются в
реестре без ссылки (`file_id`/`url` пустые) — их файл нужно вложить заново.

Revision ID: 0068_techcard_kd_on_drive
Revises: 0067_procurement_invoice_files
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0068_techcard_kd_on_drive"
down_revision: str | None = "0067_procurement_invoice_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("techcard_kitset_documents") as batch:
        batch.add_column(sa.Column("file_id", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("url", sa.String(1024), nullable=False, server_default=""))
        batch.drop_column("content")
        batch.drop_column("content_type")
        batch.drop_column("size")


def downgrade() -> None:
    with op.batch_alter_table("techcard_kitset_documents") as batch:
        batch.add_column(sa.Column("size", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column(
            "content_type", sa.String(255), nullable=False, server_default="application/octet-stream",
        ))
        batch.add_column(sa.Column("content", sa.LargeBinary(), nullable=False, server_default=sa.text("''")))
        batch.drop_column("url")
        batch.drop_column("file_id")
