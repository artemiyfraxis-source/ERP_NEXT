"""Этапы «Готово» и «ОТК пройден» на лестнице проекта / КБ (владелец 29.10.2026).

«Не могу руками теперь поставить Готово … ОТК и Готово — это разные этапы»; «Готово — это то, что производство
закончено; ОТК — то, что на проверке»; «сделаем заглушку после Готово — на ОТК и ОТК пройден».

Лестница: Новый → В работе → Готово → ОТК → ОТК пройден → Принято складом → Упаковано → Отгружено.

Что делает миграция:
- проверки `production_plan_events` «значение Этап КБ» и «было» расширяются двумя словами — «Готово» и «ОТК пройден»;
  проверка блокера и триггер «журнал только дополняется» — как в 0161 (триггер пересоздаётся после перестройки таблицы
  на SQLite). Строки журнала не трогаются: старые слова прежних списков (до 0161) остаются как записаны — на PostgreSQL
  проверки ставятся `NOT VALID`, если такие строки есть, на SQLite таблица копируется при `PRAGMA ignore_check_constraints`.
DESTRUCTIVE = False: ничего не удаляется и не меняется, кроме двух проверок (они только шире). Downgrade возвращает
проверки 0161; строки с «Готово» / «ОТК пройден» остаются как записаны (на PostgreSQL проверки — `NOT VALID`).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0164_stage_ready_qc_passed"
down_revision: str | None = "0163_approval_pin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_plan_events"
# Literal copies of app.modules.production.plan (a migration never imports app code that moves on).
STAGE_KIND, STATUS_KIND = "Этап КБ", "Статус проекта"
STAGES = ("Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено")
OLD_STAGES = ("Новый", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено")
NEW_STAGES = ("Готово", "ОТК пройден")
TERMINALS = ("Отменён",)
AUTO = "авто"
TRIGGER = "production_plan_events_no_update"
MESSAGE = "Журнал проекта только дополняется: запись не меняется"
STAGE_CHECK = "ck_production_plan_events_stage_value"
FROM_CHECK = "ck_production_plan_events_from_value"


def repr_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr_sql(value) for value in values)})"


def _stage_checks(stages: tuple[str, ...]) -> dict[str, str]:
    # The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first literal.
    return {
        STAGE_CHECK: f"{_in('value', (*stages, AUTO))} OR kind <> {repr_sql(STAGE_KIND)}",
        FROM_CHECK: (
            f"from_value = '' OR (kind = {repr_sql(STAGE_KIND)} AND {_in('from_value', stages)})"
            f" OR (kind = {repr_sql(STATUS_KIND)} AND {_in('from_value', TERMINALS)})"
        ),
    }


CHECKS = _stage_checks(STAGES)
OLD_CHECKS = _stage_checks(OLD_STAGES)


def _outside(bind, checks: dict[str, str]) -> int:
    """Rows the checks would refuse (old words of an immutable journal): they stay, unchecked."""
    where = " OR ".join(f"NOT ({sql})" for sql in checks.values())
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {where}")).scalar() or 0)


def _postgres_trigger() -> None:
    op.execute(sa.text(
        f"CREATE OR REPLACE FUNCTION {TRIGGER}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        f"RAISE EXCEPTION '{MESSAGE}' USING ERRCODE = 'check_violation'; END $$"
    ))
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}"))
    op.execute(sa.text(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} FOR EACH ROW EXECUTE FUNCTION {TRIGGER}()"
    ))


def _sqlite_trigger() -> None:
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {TRIGGER}"))
    op.execute(sa.text(
        f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END"
    ))


def _swap(new: dict[str, str]) -> None:
    """Puts ``new`` in place of the two stage checks; the rows stay as written."""
    bind = op.get_bind()
    outside = _outside(bind, new)
    if outside:
        print(f"{revision}: {TABLE} — {outside} строк(и) со словами прежних списков оставлены как есть"
              " (журнал неизменяем); новые строки проверяются.")
    if bind.dialect.name == "postgresql":
        for name, sql in new.items():
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
            op.execute(sa.text(
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({sql})" + (" NOT VALID" if outside else "")
            ))
        _postgres_trigger()
        return
    present = {check["name"] for check in sa.inspect(bind).get_check_constraints(TABLE)}
    # The copy into the rebuilt table must keep the old rows: SQLite's NOT VALID is ignoring checks while copying.
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            for name, sql in new.items():
                if name in present:
                    batch.drop_constraint(name, type_="check")
                batch.create_check_constraint(name, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    # The rebuild dropped the table's trigger: the journal stays append-only.
    _sqlite_trigger()


def upgrade() -> None:
    _swap(CHECKS)


def downgrade() -> None:
    bind = op.get_bind()
    for word in NEW_STAGES:
        count = bind.execute(
            sa.text(f"SELECT count(*) FROM {TABLE} WHERE kind = :kind AND (value = :word OR from_value = :word)"),
            {"kind": STAGE_KIND, "word": word},
        ).scalar() or 0
        print(f"{revision}: {TABLE} — «{STAGE_KIND}» со словом «{word}»: {count} строк(и) оставлены как есть.")
    _swap(OLD_CHECKS)
