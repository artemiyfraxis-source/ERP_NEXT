"""Журнал рабочего времени (владелец 12.10.2026: «логирование времени по завершению, чтобы через пару месяцев сделать
сводку и сравнить с расчётными»).

Новая таблица `production_work_time_log`, пустая: строку пишет каждое «Начать / Пауза / Продолжить / Сдать / Отменить
начало» в «Заданиях». В строке — исполнитель (чьё время) и нажавший (он же или мастер, работающий за него), момент,
задание / КБ / код операции и копия норм на этот момент (штучное время, подготовка, плановая норма доли); у сдачи —
штуки, факт сессии без пауз и норма на сданные штуки.

Проверки: событие из списка, номера и нормы не отрицательные, сдача — со штуками, фактом и нормой; UNIQUE ключа
повтора (двойной клик не пишет второе действие) и номера записи в сессии (два одновременных действия по одной
сессии — одно уходит с отказом). Триггер запрещает UPDATE: журнал только дополняется. На SQLite — такой же триггер;
пересоздание таблицы в будущей batch-миграции его снимет — такую миграцию надо дописать созданием триггера заново.

Существующие таблицы не меняются. Downgrade удаляет триггер и таблицу (записанное время теряется).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0105_work_time_log"
down_revision: str | None = "0104_staff_departments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "production_work_time_log"
# Literal copy of app.modules.production.worklog.WorkEvent (a migration never imports app code that moves on).
EVENTS = ("start", "pause", "resume", "finish", "cancel")
TRIGGER = "production_work_time_log_no_update"
MESSAGE = "Журнал рабочего времени только дополняется: запись не меняется"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operator_name", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item", sa.String(255), nullable=False),
        sa.Column("task_number", sa.String(64), nullable=False),
        sa.Column("operation_code", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("seconds_per_unit", sa.Numeric(18, 6), nullable=False),
        sa.Column("setup_seconds", sa.Numeric(18, 6), nullable=False),
        sa.Column("planned_norm_seconds", sa.Integer(), nullable=False),
        sa.Column("good_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("defect_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("running_seconds", sa.Integer(), nullable=True),
        sa.Column("earned_norm_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["production_work_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_production_work_time_log_idempotency_key"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_production_work_time_log_session_sequence"),
        # The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first
        # literal of an `IN` list and leaves a nullable column empty under `col IS NULL OR`.
        sa.CheckConstraint(f"event IN ({', '.join(repr(item) for item in EVENTS)})", name="ck_production_work_time_log_event"),
        sa.CheckConstraint("sequence >= 0", name="ck_production_work_time_log_sequence"),
        sa.CheckConstraint("seconds_per_unit >= 0", name="ck_production_work_time_log_unit"),
        sa.CheckConstraint("setup_seconds >= 0", name="ck_production_work_time_log_setup"),
        sa.CheckConstraint("planned_norm_seconds >= 0", name="ck_production_work_time_log_planned"),
        sa.CheckConstraint("good_quantity IS NULL OR good_quantity >= 0", name="ck_production_work_time_log_good"),
        sa.CheckConstraint("defect_quantity IS NULL OR defect_quantity >= 0", name="ck_production_work_time_log_defect"),
        sa.CheckConstraint("running_seconds IS NULL OR running_seconds >= 0", name="ck_production_work_time_log_running"),
        sa.CheckConstraint("earned_norm_seconds IS NULL OR earned_norm_seconds >= 0", name="ck_production_work_time_log_earned"),
        sa.CheckConstraint(
            "event <> 'finish' OR (good_quantity IS NOT NULL AND defect_quantity IS NOT NULL"
            " AND running_seconds IS NOT NULL AND earned_norm_seconds IS NOT NULL)",
            name="ck_production_work_time_log_finish",
        ),
    )
    op.create_index("ix_production_work_time_log_task_id", TABLE, ["task_id"])
    op.create_index("ix_production_work_time_log_occurred", TABLE, ["event", "occurred_at"])
    op.create_index("ix_production_work_time_log_operator", TABLE, ["operator_id", "operation_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(
            f"CREATE FUNCTION {TRIGGER}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{MESSAGE}' USING ERRCODE = 'check_violation'; END $$"
        ))
        op.execute(sa.text(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
        ))
        return
    op.execute(sa.text(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END"
    ))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}"))
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {TRIGGER}()"))
    else:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER}"))
    op.drop_index("ix_production_work_time_log_operator", table_name=TABLE)
    op.drop_index("ix_production_work_time_log_occurred", table_name=TABLE)
    op.drop_index("ix_production_work_time_log_task_id", table_name=TABLE)
    op.drop_table(TABLE)
