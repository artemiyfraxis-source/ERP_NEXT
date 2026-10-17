"""Номер запуска КБ — `production_tasks.launch_seq` (владелец, 13.10.2026).

Автоплан и кнопка «Создать задание» могли в один момент запустить одну КБ и создать два задания. Теперь каждое задание
по техкарте получает номер запуска этой КБ: 1, 2, 3… (следующий после последнего, который запускающий видел).
Два запуска, видевшие одно и то же состояние, берут один номер — второй упирается в уникальность и не проходит
(ручной запуск — 409 «Задание по этой КБ уже создано — обновите», автоплан — «пересчитайте автоплан»). Запуски части
тиража один за другим законны: каждый следующий видит предыдущий и берёт следующий номер.

Правила в базе:
- `launch_seq` — целое, NULL допустим: задание без техкарты номера не имеет;
- уникальный индекс `ux_production_tasks_techcard_launch_seq` (techcard_id, launch_seq): один номер запуска на КБ
  (NULL не сравниваются — задания без техкарты не мешают друг другу).

Заполнение: существующие задания с техкартой нумеруются по порядку создания внутри своей техкарты
(row_number() по created_at, затем номеру ПЗ); задания без техкарты остаются NULL. Числа печатаются в лог.
DESTRUCTIVE = False: ничего не удаляется. Откат снимает индекс и колонку (номера запусков теряются, задания остаются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0113_task_launch_seq"
down_revision: str | None = "0112_harness_route_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_tasks"
COLUMN = "launch_seq"
INDEX = "ux_production_tasks_techcard_launch_seq"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Integer(), nullable=True))
    bind = op.get_bind()
    # Window function in a correlated subquery: the same SQL on PostgreSQL and SQLite (3.25+), no UPDATE … FROM.
    bind.execute(sa.text(
        f"UPDATE {TABLE} SET {COLUMN} = ("
        f"SELECT ranked.seq FROM (SELECT id, row_number() OVER (PARTITION BY techcard_id ORDER BY created_at, number) AS seq"
        f" FROM {TABLE} WHERE techcard_id IS NOT NULL) ranked WHERE ranked.id = {TABLE}.id"
        f") WHERE techcard_id IS NOT NULL"
    ))
    op.create_index(INDEX, TABLE, ["techcard_id", COLUMN], unique=True)
    total, numbered = bind.execute(sa.text(f"SELECT count(*), count({COLUMN}) FROM {TABLE}")).one()
    print(f"{revision}: {TABLE} — строк {total}, с номером запуска {numbered}, без техкарты (NULL) {total - numbered}")


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    # SQLite: native DROP COLUMN (3.35+) — a batch rebuild of production_tasks would touch every table referencing it.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
    else:
        op.drop_column(TABLE, COLUMN)
