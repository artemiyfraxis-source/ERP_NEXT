"""Комплектация: отметка «нет на складе» на строке заявки (владелец 12.10.2026: «нужна кнопка „нет на складе“»).

Schema only, nothing moves (the production database is live):
- `fulfillment_lines.absent_at` / `absent_by` — when and who confirmed the part is physically absent; empty for every
  existing line;
- CHECK `ck_fulfillment_lines_absent_who_when` — both or neither;
- CHECK `ck_fulfillment_lines_absent_alone` — a marked line has no «есть, но не заведено», no lot, no reserve and
  nothing issued (existing lines have no mark, so they all pass).

Downgrade drops the two checks and the two columns with their marks.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0104_fulfillment_line_absent"
down_revision: str | None = "0103_hookup_wire_conductor_insulation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WHO_WHEN = "ck_fulfillment_lines_absent_who_when"
ALONE = "ck_fulfillment_lines_absent_alone"


def upgrade() -> None:
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.add_column(sa.Column("absent_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("absent_by", sa.String(255), nullable=True))
        batch.create_check_constraint(
            WHO_WHEN, "(absent_at IS NULL AND absent_by IS NULL) OR (absent_at IS NOT NULL AND absent_by IS NOT NULL)",
        )
        batch.create_check_constraint(
            ALONE, "absent_at IS NULL OR (NOT off_book AND lot_id IS NULL AND reserved = 0 AND issued = 0)",
        )


def downgrade() -> None:
    with op.batch_alter_table("fulfillment_lines") as batch:
        batch.drop_constraint(ALONE, type_="check")
        batch.drop_constraint(WHO_WHEN, type_="check")
        batch.drop_column("absent_by")
        batch.drop_column("absent_at")
