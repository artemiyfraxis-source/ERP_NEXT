"""Смена владельца (30.09): 9:00–17:30, обед 30 минут и ещё час на туалет и перекуры — чистых 7 ч.

Сохранённая смена с прежним значением по умолчанию (9:00–17:30, обед 13:00–13:30, 8 ч) переводится на 7 ч;
смену, которую уже меняли в «Календаре цеха», не трогаем. Без сохранённой смены действует новое значение
по умолчанию из кода.
"""

from typing import Sequence

from alembic import op


revision: str = "0074_shift_effective_seven_hours"
down_revision: str | None = "0073_receiving_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE production_shift SET effective_seconds = 25200 "
        "WHERE effective_seconds = 28800 AND start_minutes = 540 AND finish_minutes = 1050 "
        "AND REPLACE(breaks_json, ' ', '') = '[[780,810]]'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE production_shift SET effective_seconds = 28800 "
        "WHERE effective_seconds = 25200 AND start_minutes = 540 AND finish_minutes = 1050 "
        "AND REPLACE(breaks_json, ' ', '') = '[[780,810]]'"
    )
