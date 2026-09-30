"""Доли операции по монтажникам (владелец, 30.09: «дробить операцию между людьми», план корректируется по факту).

У каждого исполнителя операции — свои штуки, своё плановое окно и факт «взял»/«закончил». Уже назначенные
операции остаются бригадой (штуки NULL) с окном операции."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0076_production_operation_shares"
down_revision: str | None = "0075_production_handed_to"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("production_operation_operators") as batch:
        batch.add_column(sa.Column("quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("planned_finish", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text(
        "UPDATE production_operation_operators SET "
        "planned_start = (SELECT o.planned_start FROM production_operations o "
        "WHERE o.id = production_operation_operators.operation_id), "
        "planned_finish = (SELECT o.planned_finish FROM production_operations o "
        "WHERE o.id = production_operation_operators.operation_id)"
    ))


def downgrade() -> None:
    with op.batch_alter_table("production_operation_operators") as batch:
        batch.drop_column("finished_at")
        batch.drop_column("started_at")
        batch.drop_column("planned_finish")
        batch.drop_column("planned_start")
        batch.drop_column("quantity")
