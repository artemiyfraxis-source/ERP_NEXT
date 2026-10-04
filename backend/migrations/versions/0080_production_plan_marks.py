"""Отметки плана: колонка листа «ЗАГРУЗ» у записи журнала проекта (владелец 04.10: «тут вот такое представление я хочу»).

`production_plan_events.field` — какая колонка листа («comment», «status», «kit_arrival», … или «day:ГГГГ-ММ-ДД»)
получила ручное значение записью «Отметка плана». Существующие записи — пусто."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0080_production_plan_marks"
down_revision: str | None = "0079_procurement_stock_covers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "production_plan_events",
        sa.Column("field", sa.String(32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    with op.batch_alter_table("production_plan_events") as batch:
        batch.drop_column("field")
