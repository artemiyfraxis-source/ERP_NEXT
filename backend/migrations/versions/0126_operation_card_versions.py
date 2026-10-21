"""Версия шаблона операции в задании монтажника (владелец, 17.10.2026).

Карточки операций ЭТК живые: операция, которую никто не правил в ТК, показывает шаблон таким, какой он сейчас. Чтобы
знать, по какой версии монтажник начал работу, первое «▶ Начать» операции задания записывает один раз, откуда была
карточка каждой её операции ЭТК и дата шаблона/правки:

- `production_operations.card_versions` — JSON-список `[{position, source, changed_at}]` (source — template / edited /
  snapshot, как в etk_live); NULL — операцию ещё не начинали (и все операции до этой миграции). Записывается только
  когда NULL, дальше не перезаписывается (повторное начало, продолжение).

Существующие строки не меняются, заполнения нет. DESTRUCTIVE = False. Откат снимает колонку (теряется только запись
версий на момент начала; задания идут как шли).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0126_operation_card_versions"
down_revision: str | None = "0125_addressee_upd_file"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_operations"


def _count() -> int:
    return int(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0)


def upgrade() -> None:
    before = _count()
    op.add_column(TABLE, sa.Column("card_versions", sa.JSON(), nullable=True))
    print(f"0126_operation_card_versions: колонка {TABLE}.card_versions (пустая); строк {before} → {_count()}")


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("card_versions")
