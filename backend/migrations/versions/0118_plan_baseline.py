"""Базовый план производственного плана — «план / факт» (идея PRODUCTION_PLANNER Василия, с разрешения автора).

Две новые пустые таблицы:
- `production_plan_baselines` — снимок: номер (UNIQUE, ≥ 1; две одновременные фиксации не получат один номер),
  когда и кто зафиксировал (id без FK — фиксировать может и API-ключ, его id не сотрудник), сколько запущенных
  заданий не вошло (без плановых сроков, ≥ 0);
- `production_plan_baseline_lines` — по заданию снимка: плановое начало, конец (не раньше начала), тираж (> 0) и
  номер / проект / изделие на момент фиксации. PK (снимок, задание). FK на снимок — RESTRICT; FK на задание — CASCADE:
  задание без следа работы удаляется только вместе с проектом (`purge_project_tails`), и его строка уходит с ним.

Снимок неизменяем: триггеры запрещают UPDATE обеих таблиц (как у журнала времени 0105). DELETE не запрещён — иначе
каскад удаления задания с проектом встал бы; в коде пути удаления нет. На SQLite — такие же триггеры; пересоздание
таблицы в будущей batch-миграции их снимет — такую миграцию надо дописать созданием триггеров заново.

Существующие таблицы не меняются, заполнения нет. DESTRUCTIVE = False. Откат снимает триггеры и таблицы (снимки
теряются, задания остаются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0118_plan_baseline"
down_revision: str | None = "0117_production_downtimes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

HEADERS = "production_plan_baselines"
LINES = "production_plan_baseline_lines"
MESSAGE = "Базовый план не меняется: зафиксируйте новый"


def _trigger(table: str) -> str:
    return f"{table}_no_update"


def upgrade() -> None:
    op.create_table(
        HEADERS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.UniqueConstraint("number", name="uq_production_plan_baselines_number"),
        sa.CheckConstraint("number >= 1", name="ck_production_plan_baselines_number"),
        sa.CheckConstraint("skipped >= 0", name="ck_production_plan_baselines_skipped"),
        sa.CheckConstraint("created_by <> ''", name="ck_production_plan_baselines_created_by"),
    )
    op.create_table(
        LINES,
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("task_number", sa.String(64), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item", sa.String(255), nullable=False),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_finish", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id", "task_id", name="pk_production_plan_baseline_lines"),
        sa.ForeignKeyConstraint(
            ["baseline_id"], [f"{HEADERS}.id"], ondelete="RESTRICT",
            name="fk_production_plan_baseline_lines_baseline_id",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["production_tasks.id"], ondelete="CASCADE",
            name="fk_production_plan_baseline_lines_task_id",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_production_plan_baseline_lines_quantity"),
        sa.CheckConstraint("planned_finish >= planned_start", name="ck_production_plan_baseline_lines_window"),
    )
    op.create_index("ix_production_plan_baseline_lines_task_id", LINES, ["task_id"])
    postgres = op.get_bind().dialect.name == "postgresql"
    for table in (HEADERS, LINES):
        trigger = _trigger(table)
        if postgres:
            op.execute(sa.text(
                f"CREATE FUNCTION {trigger}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                f"RAISE EXCEPTION '{MESSAGE}' USING ERRCODE = 'check_violation'; END $$"
            ))
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION {trigger}()"
            ))
        else:
            op.execute(sa.text(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END"
            ))
    print(f"0118_plan_baseline: созданы таблицы {HEADERS}, {LINES} (пустые)")


def downgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table in (LINES, HEADERS):
        trigger = _trigger(table)
        if postgres:
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}"))
            op.execute(sa.text(f"DROP FUNCTION IF EXISTS {trigger}()"))
        else:
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
    op.drop_index("ix_production_plan_baseline_lines_task_id", table_name=LINES)
    op.drop_table(LINES)
    op.drop_table(HEADERS)
