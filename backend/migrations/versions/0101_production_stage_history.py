"""История ручной смены этапа проекта/КБ (владелец 10.10.2026: «быстро руками сменить… пропуская этап»).

`production_plan_events` получает:
- `from_value` — этап до смены у записи «Этап КБ» (закрытый статус, из которого вернули, у «Статус проекта»);
  у старых записей пусто;
- `reason` — причина ручной смены (назад — обязательна), у старых записей пусто;
- CHECK: вид записи — из подписей PLAN_KINDS; значение «Этап КБ» — этап лестницы или «авто»; значение «Статус
  проекта» — Отказ/Слился/Отменён или «авто»; `from_value` — пусто или этап/статус своего вида;
- триггер, запрещающий UPDATE строк журнала (журнал только дополняется; DELETE остаётся — им сносится журнал
  удалённого проекта). На SQLite — такой же триггер; пересоздание таблицы в будущей batch-миграции его снимет —
  такую миграцию надо дописать созданием триггера заново.

Старые строки не меняются. Сначала считаются строки, нарушающие новые проверки:
- нет нарушений — проверки ставятся обычные;
- есть нарушения — список (до 20 строк и общее число) печатается в лог миграции, а проверки ставятся так, чтобы
  старые строки остались как есть: на PostgreSQL — `NOT VALID` (новые строки проверяются, старые — нет), на SQLite
  (тесты, предпросмотр, демо) проверки не ставятся. Остановить миграцию на нарушениях вместо этого:
  `alembic -x on_violation=stop upgrade head` (или ERP_MIGRATION_ON_VIOLATION=stop).
Посчитать нарушения на копии прода заранее — SQL в `VIOLATIONS_SQL` ниже.

Downgrade снимает триггер, проверки и обе колонки (история «было → стало» и причины теряются).
"""

import os
from typing import Sequence

from alembic import context, op
import sqlalchemy as sa


revision: str = "0101_production_stage_history"
down_revision: str | None = "0100_identity_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "production_plan_events"
# Literal copies of app.modules.production.plan (a migration never imports app code that moves on).
KINDS = (
    "Комментарий", "Блокер", "Блокер снят", "Дедлайн", "Этап КБ", "Проверка ТК", "Статус проекта", "Отметка плана",
)
STAGE_KIND, STATUS_KIND = "Этап КБ", "Статус проекта"
STAGES = ("Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено")
TERMINALS = ("Отказ", "Слился", "Отменён")
AUTO = "авто"
TRIGGER = "production_plan_events_no_update"
MESSAGE = "Журнал проекта только дополняется: запись не меняется"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr_sql(value) for value in values)})"


def repr_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first literal.
CHECKS = {
    "ck_production_plan_events_kind": _in("kind", KINDS),
    "ck_production_plan_events_stage_value": f"{_in('value', (*STAGES, AUTO))} OR kind <> {repr_sql(STAGE_KIND)}",
    "ck_production_plan_events_status_value": f"{_in('value', (*TERMINALS, AUTO))} OR kind <> {repr_sql(STATUS_KIND)}",
    "ck_production_plan_events_from_value": (
        f"from_value = '' OR (kind = {repr_sql(STAGE_KIND)} AND {_in('from_value', STAGES)})"
        f" OR (kind = {repr_sql(STATUS_KIND)} AND {_in('from_value', TERMINALS)})"
    ),
}

# Rows the new checks would refuse (before the upgrade `from_value` does not exist: only kind and value can break).
VIOLATIONS_SQL = (
    f"SELECT id, project, sequence, occurred_at, kind, value FROM {TABLE}"
    f" WHERE NOT ({CHECKS['ck_production_plan_events_kind']})"
    f" OR NOT ({CHECKS['ck_production_plan_events_stage_value']})"
    f" OR NOT ({CHECKS['ck_production_plan_events_status_value']})"
    " ORDER BY project, sequence"
)


def _on_violation() -> str:
    arguments = context.get_x_argument(as_dictionary=True)
    return str(arguments.get("on_violation") or os.environ.get("ERP_MIGRATION_ON_VIOLATION") or "keep").strip()


def violations(bind) -> list[tuple]:
    return [tuple(row) for row in bind.execute(sa.text(VIOLATIONS_SQL)).fetchall()]


def _report(rows: list[tuple]) -> str:
    listed = "\n".join(
        f"  {row[1]} №{row[2]} ({row[3]}): вид «{row[4]}», значение «{row[5]}» — id {row[0]}" for row in rows[:20]
    )
    more = f"\n  … и ещё {len(rows) - 20}" if len(rows) > 20 else ""
    return f"{TABLE}: {len(rows)} строк(и) нарушают новые проверки журнала:\n{listed}{more}"


def upgrade() -> None:
    bind = op.get_bind()
    found = violations(bind)
    if found:
        report = _report(found)
        if _on_violation() == "stop":
            raise RuntimeError(
                f"Миграция {revision} остановлена, данные не тронуты.\n{report}\n"
                "Исправьте эти строки вручную (резервная копия → репетиция) или запустите без on_violation=stop."
            )
        print(f"ВНИМАНИЕ ({revision}): {report}\nСтарые строки оставлены как есть; новые строки проверяются"
              + (" (NOT VALID)." if bind.dialect.name == "postgresql" else " — на SQLite проверки не поставлены."))
    postgres = bind.dialect.name == "postgresql"
    if postgres:
        op.add_column(TABLE, sa.Column("from_value", sa.String(64), nullable=False, server_default=""))
        op.add_column(TABLE, sa.Column("reason", sa.Text(), nullable=False, server_default=""))
        for name, sql in CHECKS.items():
            op.execute(sa.text(
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({sql})" + (" NOT VALID" if found else "")
            ))
        op.execute(sa.text(
            f"CREATE FUNCTION {TRIGGER}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{MESSAGE}' USING ERRCODE = 'check_violation'; END $$"
        ))
        op.execute(sa.text(
            f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
        ))
        return
    with op.batch_alter_table(TABLE, recreate="always" if not found else "auto") as batch:
        batch.add_column(sa.Column("from_value", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("reason", sa.Text(), nullable=False, server_default=""))
        if not found:
            for name, sql in CHECKS.items():
                batch.create_check_constraint(name, sql)
    op.execute(sa.text(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}"))
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {TRIGGER}()"))
        for name in CHECKS:
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
        op.drop_column(TABLE, "reason")
        op.drop_column(TABLE, "from_value")
        return
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER}"))
    present = {check["name"] for check in sa.inspect(bind).get_check_constraints(TABLE)}
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        for name in CHECKS:
            if name in present:
                batch.drop_constraint(name, type_="check")
        batch.drop_column("reason")
        batch.drop_column("from_value")
