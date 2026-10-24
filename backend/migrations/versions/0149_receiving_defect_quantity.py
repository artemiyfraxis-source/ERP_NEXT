"""Приёмка: КОЛ-ВО БРАКА строки (owner 24.10.2026, «Снабжение → Статистика → Показатели», «Брак, ₽»).

`receiving_lines.defect_quantity` — сколько из фактического количества брак. Его вводит приёмщик (ВК, «Провести
поставку») вместе с отметкой «Механическое повреждение / брак»; показатель «Брак, ₽» = кол-во брака × цена оплаты.
Это запись: принятое количество и склад от неё не меняются.

Что держит база, а не код: `ck_receiving_lines_defect_quantity` — брак либо не указан (NULL), либо больше нуля, при
указанном факте и не больше него. Обязательность при отметке «брак» — правило домена (у старых строк брака нет
количества, они остаются NULL = «не указано»).

DESTRUCTIVE = False: столбец добавляется пустым, строки не переписываются. SQLite пересобирает таблицу через
`batch_alter_table` (частичный уникальный индекс живой строки снимается и ставится заново с тем же условием, как в
0052 — пересборка SQLite теряет его WHERE); PostgreSQL добавляет столбец и CHECK. Откат удаляет CHECK и столбец
(введённые количества брака теряются — это только запись к показателю, склад их не использует).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0149_receiving_defect_quantity"
down_revision: str | None = "0148_techcard_std_figures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

_NAME = "ck_receiving_lines_defect_quantity"
_CHECK = (
    "defect_quantity IS NULL OR (defect_quantity > 0 AND fact_quantity IS NOT NULL AND defect_quantity <= fact_quantity)"
)
_LIVE_INDEX = "ux_receiving_live_procurement_line"
_LIVE = sa.text("status <> 'Архив ВК' AND split_of_id IS NULL")


def _live_index() -> None:
    op.create_index(
        _LIVE_INDEX, "receiving_lines", ["procurement_line_id"], unique=True, sqlite_where=_LIVE, postgresql_where=_LIVE,
    )


def upgrade() -> None:
    op.drop_index(_LIVE_INDEX, table_name="receiving_lines")
    with op.batch_alter_table("receiving_lines") as batch:
        batch.add_column(sa.Column("defect_quantity", sa.Numeric(18, 6), nullable=True))
        batch.create_check_constraint(_NAME, _CHECK)
    _live_index()


def downgrade() -> None:
    op.drop_index(_LIVE_INDEX, table_name="receiving_lines")
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_constraint(_NAME, type_="check")
        batch.drop_column("defect_quantity")
    _live_index()
