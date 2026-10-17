"""Праздники РФ в календаре цеха (владелец, 14.10.2026: «заведи производственный календарь, чтобы знать, когда праздники»).

Добавляет в `production_holidays` нерабочие будние дни производственного календаря РФ:
- 2026 — по постановлению о переносе выходных (3 и 4 января → 31 декабря и 9 января);
- 2027 — праздники ст. 112 ТК РФ и перенос праздника с выходного на ближайший понедельник. Перенос 2 и 3 января
  2027 (выпадают на сб/вс) задаёт постановление правительства — его дни добавить в «Календарь цеха» вручную.
Субботы и воскресенья не пишутся: они нерабочие по графику смены.

Только вставка недостающих дней: уже внесённые и чужие дни не трогаются. Печатается сверка: строк было → стало.
DESTRUCTIVE = False. Откат ничего не удаляет — те же дни мог внести человек, их не отличить.
"""

from datetime import date
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0123_ru_holidays"
down_revision: str | None = "0122_shipping_dispatch_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

HOLIDAYS = (
    # 2026
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8),
    date(2026, 1, 9), date(2026, 2, 23), date(2026, 3, 9), date(2026, 5, 1), date(2026, 5, 11), date(2026, 6, 12),
    date(2026, 11, 4), date(2026, 12, 31),
    # 2027 (без переноса 2–3 января — по постановлению)
    date(2027, 1, 1), date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6), date(2027, 1, 7), date(2027, 1, 8),
    date(2027, 2, 23), date(2027, 3, 8), date(2027, 5, 3), date(2027, 5, 10), date(2027, 6, 14), date(2027, 11, 4),
)


def _count() -> int:
    return int(op.get_bind().execute(sa.text("SELECT COUNT(*) FROM production_holidays")).scalar() or 0)


def upgrade() -> None:
    bind = op.get_bind()
    before = _count()
    for day in HOLIDAYS:
        bind.execute(
            sa.text("INSERT INTO production_holidays (day) SELECT :day WHERE NOT EXISTS "
                    "(SELECT 1 FROM production_holidays WHERE day = :day)"),
            {"day": day},
        )
    print(f"0123_ru_holidays: production_holidays {before} → {_count()}")


def downgrade() -> None:
    pass
