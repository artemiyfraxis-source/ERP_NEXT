"""Prevent concurrent active work and duplicate open operator boxes."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014_production_concurrency_guards"
down_revision: str | None = "0013_production_materials_finished_goods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ux_production_active_work_operator",
        "production_work_sessions",
        ["operator_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('В работе', 'Пауза')"),
        postgresql_where=sa.text("status IN ('В работе', 'Пауза')"),
    )
    op.create_index(
        "ux_production_open_box_operator",
        "production_boxes",
        ["task_id", "operation_id", "operator_id"],
        unique=True,
        sqlite_where=sa.text("status = 'Открыта'"),
        postgresql_where=sa.text("status = 'Открыта'"),
    )


def downgrade() -> None:
    op.drop_index("ux_production_open_box_operator", table_name="production_boxes")
    op.drop_index("ux_production_active_work_operator", table_name="production_work_sessions")

