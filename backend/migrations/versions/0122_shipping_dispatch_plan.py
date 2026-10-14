"""План отгрузки и место хранения ГП (владелец, 14.10.2026).

Склад (Алексей) и менеджер (Ульяна) заносят данные отгрузки заранее, пока КБ ещё в цехе; склад принимает ГП частями
и кладёт на место хранения; отправка может уйти без УПД (новый тип «БЕЗ УПД»).

Что делает миграция:
- `shipping_project_addressees` (карточка отгрузки проекта): `plan_date` (дата отгрузки по плану, NULL — не задана)
  и `note` (комментарий, TEXT NOT NULL DEFAULT '');
- `finished_goods`: `location` — место хранения КБ на складе ГП (VARCHAR(255) NOT NULL DEFAULT '').
Тип УПД (`upd_type`, VARCHAR(32)) ни CHECK, ни enum не имеет — новое значение «БЕЗ УПД» схемы не меняет.

Существующие строки не меняются (новые колонки — пустые). Печатается сверка: строк в каждой таблице до и после.
DESTRUCTIVE = False. Откат снимает колонки; тип «БЕЗ УПД» в отправках и карточках откат делает пустым (старый код
его не знает; пустой тип = нужен файл УПД) и печатает, сколько таких строк.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0122_shipping_dispatch_plan"
down_revision: str | None = "0121_kitset_folder_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

ADDRESSEES = "shipping_project_addressees"
SHIPMENTS = "shipments"
GOODS = "finished_goods"


def _count(table: str) -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def upgrade() -> None:
    before = {table: _count(table) for table in (ADDRESSEES, GOODS)}
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.add_column(sa.Column("plan_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("note", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table(GOODS) as batch:
        batch.add_column(sa.Column("location", sa.String(255), nullable=False, server_default=""))
    after = {table: _count(table) for table in before}
    print("0122_shipping_dispatch_plan: строк было → стало: " + ", ".join(
        f"{table} {before[table]} → {after[table]}" for table in before
    ))


def downgrade() -> None:
    bind = op.get_bind()
    cleared = 0
    for table in (SHIPMENTS, ADDRESSEES):
        cleared += bind.execute(sa.text(f"UPDATE {table} SET upd_type = NULL WHERE upd_type = 'БЕЗ УПД'")).rowcount or 0
    with op.batch_alter_table(GOODS) as batch:
        batch.drop_column("location")
    with op.batch_alter_table(ADDRESSEES) as batch:
        batch.drop_column("note")
        batch.drop_column("plan_date")
    print(f"0122_shipping_dispatch_plan откат: тип «БЕЗ УПД» снят у {cleared} строк (отправки и карточки)")
