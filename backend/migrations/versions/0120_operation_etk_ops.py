"""Задание по полуфабрикатам (владелец + Василий, 13.10.2026): операция задания — один ПФ схемы ЭТК.

- `production_operations.etk_ops` — JSON-список операций ЭТК этого ПФ `[{position, code, stage}]` в порядке ПФ;
  NULL — операция задания и есть одна операция ЭТК (запуск без нарисованной схемы и все задания до этой миграции).

Существующие строки не меняются, заполнения нет: запущенные задания идут как шли. DESTRUCTIVE = False. Откат снимает
колонку (задания по ПФ остаются с кодом и названием своего ПФ, без списка операций ЭТК).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0120_operation_etk_ops"
down_revision: str | None = "0119_box_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_operations"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("etk_ops", sa.JSON(), nullable=True))
    print(f"0120_operation_etk_ops: колонка {TABLE}.etk_ops (пустая)")


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("etk_ops")
