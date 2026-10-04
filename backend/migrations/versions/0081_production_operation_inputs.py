"""Явный граф входов операций задания (ARCH-TRACK-2, B1): сетевой график вместо неявной цепочки «предыдущий этап».

`production_operations.stage_key` — ключ блока схемы полуфабрикатов ЭТК (пусто — блок не известен).
`production_operation_inputs` — ребро «операция берёт полуфабрикат операции input_operation_id»: per_unit штук на одну
свою (вручную на схеме, по умолчанию 1), transfer_batch — передаточная партия ребра, пусто = партия задания (решение 4).

Существующие задания получают ровно сегодняшнюю цепочку: каждая операция, кроме первой, берёт операцию предыдущего
номера того же задания, per_unit 1, партия пустая. Финальная операция — та, которую никто не берёт (= последний номер)."""

from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0081_production_operation_inputs"
down_revision: str | None = "0080_production_plan_marks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_operations = sa.table(
    "production_operations",
    sa.column("id", sa.Uuid()),
    sa.column("task_id", sa.Uuid()),
    sa.column("position", sa.Integer()),
)


def upgrade() -> None:
    op.add_column("production_operations", sa.Column("stage_key", sa.String(128), nullable=True))
    inputs = op.create_table(
        "production_operation_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("input_operation_id", sa.Uuid(), nullable=False),
        sa.Column("per_unit", sa.Numeric(18, 6), nullable=False, server_default="1"),
        sa.Column("transfer_batch", sa.Numeric(18, 6), nullable=True),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["input_operation_id"], ["production_operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "input_operation_id", name="ux_production_operation_inputs_edge"),
    )
    op.create_index(
        "ix_production_operation_inputs_input_operation_id", "production_operation_inputs", ["input_operation_id"],
    )
    rows = op.get_bind().execute(
        sa.select(_operations.c.id, _operations.c.task_id, _operations.c.position)
        .order_by(_operations.c.task_id, _operations.c.position)
    ).all()
    edges = [
        {"id": uuid4(), "operation_id": current.id, "input_operation_id": previous.id, "per_unit": 1, "transfer_batch": None}
        for previous, current in zip(rows, rows[1:])
        if previous.task_id == current.task_id
    ]
    if edges:
        op.bulk_insert(inputs, edges)


def downgrade() -> None:
    op.drop_index("ix_production_operation_inputs_input_operation_id", table_name="production_operation_inputs")
    op.drop_table("production_operation_inputs")
    with op.batch_alter_table("production_operations") as batch:
        batch.drop_column("stage_key")
