"""Простои с причинами — таблица `production_downtimes` (владелец, 13.10.2026; идея — «причины свободных интервалов»
PRODUCTION_PLANNER Василия, с разрешения автора).

Монтажник или мастер отмечает остановку работы по заданию с причиной из списка (нет материала, нет чертежа / ТК,
оборудование, ждёт ОТК, другое + комментарий); пишутся начало, конец и кто нажал. В строке — копия задания на момент
начала (проект, номер, позиция, этап), чтобы отчёт не зависел от переделки задания.

Правила в базе:
- причина из списка; у «Другое» комментарий не пустой; комментарий не длиннее 500 символов;
- конец не раньше начала и пишется вместе с тем, кто закончил (и наоборот);
- у задания не больше одного открытого простоя — частичный UNIQUE(task_id) WHERE ended_at IS NULL;
- FK: задание и операция — ON DELETE SET NULL (задание без следа работы удаляется при пересборке КБ; простой остаётся
  в отчёте по своей копии), сотрудники (чей простой, кто начал, кто закончил) — ON DELETE RESTRICT.

Ничего существующего не меняется: новая пустая таблица, заполнения нет. DESTRUCTIVE = False.
Откат снимает таблицу (записанные простои теряются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0117_production_downtimes"
down_revision: str | None = "0112_harness_route_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_downtimes"
# Literal copy of app.modules.production.downtime.DowntimeReason (a migration never imports app code that moves on).
REASONS = ("no_material", "no_drawing", "equipment", "waiting_qc", "other")
OPEN_INDEX = "uq_production_downtimes_open_task"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("operator_id", sa.Uuid(), nullable=True),
        sa.Column("operator_name", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("comment", sa.String(500), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_by_id", sa.Uuid(), nullable=False),
        sa.Column("started_by_name", sa.String(255), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("task_number", sa.String(64), nullable=False),
        sa.Column("item", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(255), nullable=False, server_default=""),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by_id", sa.Uuid(), nullable=True),
        sa.Column("ended_by_name", sa.String(255), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="SET NULL", name="fk_production_downtimes_task_id"),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["production_operations.id"], ondelete="SET NULL", name="fk_production_downtimes_operation_id",
        ),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT", name="fk_production_downtimes_operator_id"),
        sa.ForeignKeyConstraint(
            ["started_by_id"], ["identity_users.id"], ondelete="RESTRICT", name="fk_production_downtimes_started_by_id",
        ),
        sa.ForeignKeyConstraint(["ended_by_id"], ["identity_users.id"], ondelete="RESTRICT", name="fk_production_downtimes_ended_by_id"),
        # The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first
        # literal of an `IN` list and leaves a nullable column empty under `col IS NULL OR`.
        sa.CheckConstraint(f"reason IN ({', '.join(repr(item) for item in REASONS)})", name="ck_production_downtimes_reason"),
        sa.CheckConstraint("reason <> 'other' OR length(trim(comment)) > 0", name="ck_production_downtimes_other_comment"),
        sa.CheckConstraint("length(comment) <= 500", name="ck_production_downtimes_comment_length"),
        sa.CheckConstraint(
            "ended_at IS NULL OR (ended_at >= started_at AND ended_by_id IS NOT NULL)", name="ck_production_downtimes_end",
        ),
        sa.CheckConstraint("ended_by_id IS NULL OR ended_at IS NOT NULL", name="ck_production_downtimes_ended_by"),
    )
    op.create_index(
        OPEN_INDEX, TABLE, ["task_id"], unique=True,
        sqlite_where=sa.text("ended_at IS NULL"), postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index("ix_production_downtimes_started_at", TABLE, ["started_at"])
    print(f"0117_production_downtimes: создана таблица {TABLE} (пустая)")


def downgrade() -> None:
    op.drop_index("ix_production_downtimes_started_at", table_name=TABLE)
    op.drop_index(OPEN_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
