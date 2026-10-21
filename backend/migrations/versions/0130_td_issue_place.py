"""Комплект ТД: цех и участок выпуска (21.10.2026).

В МК графы «Цех» и «Уч.» заполняются в каждой строке «А», ТЛ печатает цех и участок под организацией, сопроводительный
лист — участок. Выпуск хранит их сам (`workshop`, `site`): форма копирует их из прежнего выпуска, как подписантов.

Что держит база: участок — '' (не указан, нормоконтроль блокирует подпись) или ЦАР / ЖУК; обе колонки NOT NULL с
пустой строкой по умолчанию — у прежних выпусков они пусты.

DESTRUCTIVE = False: добавляются две колонки, строки не меняются. Откат удаляет колонки.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0130_td_issue_place"
down_revision: str | None = "0129_td_issues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "td_issues"
INDEX = "ux_td_issues_active_card"
ACTIVE = sa.text("status = 'active'")


def _count() -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0)


def _active_index() -> None:
    op.create_index(INDEX, TABLE, ["project_card_id"], unique=True, sqlite_where=ACTIVE, postgresql_where=ACTIVE)


def upgrade() -> None:
    before = _count()
    # Пересборка таблицы на SQLite теряет условие частичного индекса — снимаем его и ставим заново.
    op.drop_index(INDEX, table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("workshop", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("site", sa.Text(), nullable=False, server_default=""))
        batch.create_check_constraint("ck_td_issues_site", "site IN ('', 'ЦАР', 'ЖУК')")
    _active_index()
    print(f"0130_td_issue_place: td_issues {before} -> {_count()}")


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_td_issues_site", type_="check")
        batch.drop_column("site")
        batch.drop_column("workshop")
    _active_index()
