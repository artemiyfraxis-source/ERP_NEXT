"""Накопительные количества КБ в неизменяемом журнале, без заполнения старой истории.

Новые записи хранят тираж и четыре количества; NULL — количество ещё не вводили.
Проверяется только целое число от нуля до записанного тиража, порядок этапов не ограничивается.
Откат допустим на копии без новых записей: существующие количества никогда не удаляются молча.
"""

from alembic import op
import sqlalchemy as sa

revision = "0166_kb_progress"
down_revision = "0165_split_lug_cable_tabs"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_plan_events"
TRIGGER = "production_plan_events_no_update"
KIND_CHECK = "ck_production_plan_events_kind"
PROGRESS_CHECK = "ck_production_plan_events_progress"
OLD_KINDS = ("Комментарий", "Блокер", "Блокер снят", "Дедлайн", "Этап КБ", "Проверка ТК", "Статус проекта", "Отметка плана")
FIELDS = ("started", "done", "qc", "passed")
COLUMNS = ("progress_run", *(f"progress_{name}" for name in FIELDS))
PROGRESS_SQL = (
    "(kind <> 'Количество КБ' AND progress_run IS NULL AND "
    + " AND ".join(f"progress_{name} IS NULL" for name in FIELDS)
    + ") OR (kind = 'Количество КБ' AND item_index <> '' AND progress_run IS NOT NULL AND progress_run > 0 AND progress_run = CAST(progress_run AS INTEGER) AND ("
    + " OR ".join(f"progress_{name} IS NOT NULL" for name in FIELDS) + ") AND "
    + " AND ".join(
        f"(progress_{name} IS NULL OR (progress_{name} >= 0 AND progress_{name} <= progress_run "
        f"AND progress_{name} = CAST(progress_{name} AS INTEGER)))" for name in FIELDS
    ) + ")"
)


def _kind_sql(kinds):
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


CHECKS = {KIND_CHECK: _kind_sql((*OLD_KINDS, "Количество КБ"))}


def _sqlite_trigger():
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER}"))
    op.execute(sa.text(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} BEGIN "
        "SELECT RAISE(ABORT, 'Журнал проекта только дополняется: запись не меняется'); END"
    ))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in COLUMNS:
            op.add_column(TABLE, sa.Column(name, sa.Integer(), nullable=True))
        op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT {KIND_CHECK}"))
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {KIND_CHECK} CHECK ({CHECKS[KIND_CHECK]}) NOT VALID"))
        op.create_check_constraint(PROGRESS_CHECK, TABLE, PROGRESS_SQL)
        return  # ALTER preserves the append-only trigger.
    # Historical legacy words remain exactly as recorded (same preservation rule as 0164).
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint(KIND_CHECK, type_="check")
            for name in COLUMNS:
                batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
            batch.create_check_constraint(KIND_CHECK, CHECKS[KIND_CHECK])
            batch.create_check_constraint(PROGRESS_CHECK, PROGRESS_SQL)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    _sqlite_trigger()


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE kind = 'Количество КБ'")).scalar():
        raise RuntimeError("Откат удалит количества КБ из истории — используйте копию до первой записи количеств.")
    if bind.dialect.name == "postgresql":
        op.drop_constraint(PROGRESS_CHECK, TABLE, type_="check")
        op.drop_constraint(KIND_CHECK, TABLE, type_="check")
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {KIND_CHECK} CHECK ({_kind_sql(OLD_KINDS)}) NOT VALID"))
        for name in reversed(COLUMNS):
            op.drop_column(TABLE, name)
        return
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint(PROGRESS_CHECK, type_="check")
            batch.drop_constraint(KIND_CHECK, type_="check")
            batch.create_check_constraint(KIND_CHECK, _kind_sql(OLD_KINDS))
            for name in reversed(COLUMNS):
                batch.drop_column(name)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    _sqlite_trigger()
