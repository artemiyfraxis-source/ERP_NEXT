"""План задания и статус «Выполнено» (Цель 2, партии 2–3).

`production_tasks`: техкарта и ревизия норм (`techcard_id`, `techcard_revision`; пусто — задание до этой миграции),
режим передачи партии по этапам (`flow_mode`: sequential / parallel / mixed, по умолчанию mixed — так считал
автоплан и раньше), передаточная партия (`transfer_batch`, пусто — вся партия), «первые k раньше»
(`early_quantity`, пусто — нет); допустимые значения режима, партии, «первых k» и ревизии закреплены CHECK.
Статус получает третье значение «Выполнено» (колонка та же, String(32)); CHECK на статус не ставится: он лёг бы на
уже существующие строки, а значение и так проверяет перечисление ProductionStatus при чтении и переходы — код.

`production_task_team` — команда задания (люди из справочника сотрудников по id, порядок выбора).
`production_task_deliveries` — «Сдано N шт» площадки без нормирования (ЖУК); отмена — отметка, строка остаётся.

Не разрушающая: существующие задания сохраняют всё, новые колонки пустые или с умолчанием, новые таблицы пустые.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0089_production_job_flow"
down_revision: str | None = "0088_api_tokens_assembly_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False


def upgrade() -> None:
    with op.batch_alter_table("production_tasks") as batch:
        batch.add_column(sa.Column("techcard_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("techcard_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("flow_mode", sa.String(16), nullable=False, server_default="mixed"))
        batch.add_column(sa.Column("transfer_batch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("early_quantity", sa.Integer(), nullable=True))
        batch.create_index("ix_production_tasks_techcard_id", ["techcard_id"])

        batch.create_check_constraint(
            "ck_production_tasks_flow_mode", "flow_mode IN ('sequential', 'parallel', 'mixed')",
        )
        batch.create_check_constraint(
            "ck_production_tasks_transfer_batch",
            "transfer_batch IS NULL OR (transfer_batch >= 1 AND transfer_batch <= quantity)",
        )
        batch.create_check_constraint(
            "ck_production_tasks_early_quantity",
            "early_quantity IS NULL OR (early_quantity >= 1 AND early_quantity < quantity)",
        )
        batch.create_check_constraint(
            "ck_production_tasks_techcard_revision", "techcard_revision IS NULL OR techcard_revision >= 1",
        )
    op.create_table(
        "production_task_team",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("task_id", "operator_id"),
        sa.UniqueConstraint("task_id", "position", name="ux_production_task_team_position"),
        sa.CheckConstraint("position >= 0", name="ck_production_task_team_position"),
    )
    op.create_index("ix_production_task_team_operator_id", "production_task_team", ["operator_id"])
    op.create_table(
        "production_task_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("site", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_by", sa.String(255), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="ck_production_task_deliveries_quantity"),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by IS NULL) OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_production_task_deliveries_cancel",
        ),
    )
    op.create_index("ix_production_task_deliveries_task_id", "production_task_deliveries", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_production_task_deliveries_task_id", table_name="production_task_deliveries")
    op.drop_table("production_task_deliveries")
    op.drop_index("ix_production_task_team_operator_id", table_name="production_task_team")
    op.drop_table("production_task_team")
    # «Выполнено» не знает схема до 0089: такие задания возвращаются в «Запущено».
    op.execute("UPDATE production_tasks SET status = 'Запущено' WHERE status = 'Выполнено'")
    with op.batch_alter_table("production_tasks") as batch:
        batch.drop_constraint("ck_production_tasks_techcard_revision", type_="check")
        batch.drop_constraint("ck_production_tasks_early_quantity", type_="check")
        batch.drop_constraint("ck_production_tasks_transfer_batch", type_="check")
        batch.drop_constraint("ck_production_tasks_flow_mode", type_="check")
        batch.drop_index("ix_production_tasks_techcard_id")
        batch.drop_column("early_quantity")
        batch.drop_column("transfer_batch")
        batch.drop_column("flow_mode")
        batch.drop_column("techcard_revision")
        batch.drop_column("techcard_id")
