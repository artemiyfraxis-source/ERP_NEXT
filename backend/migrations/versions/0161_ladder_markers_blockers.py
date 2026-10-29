"""Короткая лестница этапов, две отметки «ТК готова» / «КБ набрана», один блокер «СТОП» (владелец 29.10.2026).

«Чем стоп от холда отличается и стоп от паузы — не нужно раздувать статусы»; «можно убрать распланировано пока что и
сделать возможно двух статусов одновременно КБ набрана и ТК готова».

Лестница: Новый → В работе → ОТК → Принято складом → Упаковано → Отгружено. «ТК готова» и «КБ набрана» — не этапы, а
две независимые отметки да/нет; «Распланировано» убрано. Блокеры: СТОП · ОЖИД ИНФ · ОЖИД КОМПЛ.

Что делает миграция:
- проверки `production_plan_events` «значение Этап КБ» и «было» переводятся на короткую лестницу; добавляется проверка
  блокера записи «Блокер» (СТОП, ОЖИД ИНФ, ОЖИД КОМПЛ). Старые строки (этапы «ТК готова» / «КБ набрана» /
  «Распланировано», блокеры ХОЛД / ПАУЗА) остаются как записаны: журнал неизменяем (триггер 0101), на PostgreSQL
  проверки ставятся `NOT VALID`, на SQLite таблица копируется при `PRAGMA ignore_check_constraints`. Новые строки
  проверяются по-новому. Триггер, запрещающий UPDATE, пересоздаётся (как в 0110);
- журнал НЕ переписывается: приложение читает старый этап «ТК готова» / «КБ набрана» / «Распланировано» как «ручного
  этапа нет, решают факты» и включённые отметки КБ, ХОЛД / ПАУЗА — как «СТОП».
Числа строк со старыми словами печатаются в лог (по каждому слову).
DESTRUCTIVE = False: ничего не удаляется и не меняется, кроме проверок. Downgrade возвращает проверки 0110 и снимает
проверку блокера (новые строки старым проверкам подходят).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0161_ladder_markers_blockers"
down_revision: str | None = "0160_cancel_stale_new_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_plan_events"
# Literal copies of app.modules.production.plan (a migration never imports app code that moves on).
STAGE_KIND, STATUS_KIND, BLOCK_KIND = "Этап КБ", "Статус проекта", "Блокер"
STAGES = ("Новый", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено")
OLD_STAGES = (
    "Новый", "ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено",
)
TERMINALS = ("Отменён",)
BLOCKERS = ("СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ")
REMOVED_STAGES = ("ТК готова", "КБ набрана", "Распланировано")
REMOVED_BLOCKERS = ("ХОЛД", "ПАУЗА")
AUTO = "авто"
TRIGGER = "production_plan_events_no_update"
MESSAGE = "Журнал проекта только дополняется: запись не меняется"
STAGE_CHECK = "ck_production_plan_events_stage_value"
FROM_CHECK = "ck_production_plan_events_from_value"
BLOCKER_CHECK = "ck_production_plan_events_blocker"


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


CHECKS = {**_stage_checks(STAGES), BLOCKER_CHECK: f"{_in('blocker', BLOCKERS)} OR kind <> {repr_sql(BLOCK_KIND)}"}
OLD_CHECKS = _stage_checks(OLD_STAGES)


def _report(bind) -> None:
    """Rows with the removed words, per word: they stay as written (immutable journal) and read as the new model."""
    for column, kind, words in (("value", STAGE_KIND, REMOVED_STAGES), ("blocker", BLOCK_KIND, REMOVED_BLOCKERS)):
        for word in words:
            count = bind.execute(
                sa.text(f"SELECT count(*) FROM {TABLE} WHERE kind = :kind AND {column} = :word"),
                {"kind": kind, "word": word},
            ).scalar() or 0
            print(f"{revision}: {TABLE} — «{kind}» = «{word}»: {count} строк(и) оставлены как есть (журнал неизменяем).")


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


def _swap(new: dict[str, str], drop: tuple[str, ...]) -> None:
    """Drops the checks named in ``drop`` and puts ``new`` in their place; old rows stay as written."""
    bind = op.get_bind()
    outside = _outside(bind, new)
    if outside:
        print(f"{revision}: {TABLE} — {outside} строк(и) со словами прежних списков оставлены как есть"
              " (журнал неизменяем); новые строки проверяются.")
    if bind.dialect.name == "postgresql":
        for name in drop:
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
        for name, sql in new.items():
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
            for name in drop:
                if name in present:
                    batch.drop_constraint(name, type_="check")
            for name, sql in new.items():
                batch.create_check_constraint(name, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    # The rebuild dropped the table's trigger: the journal stays append-only.
    _sqlite_trigger()


def upgrade() -> None:
    _report(op.get_bind())
    _swap(CHECKS, (STAGE_CHECK, FROM_CHECK, BLOCKER_CHECK))


def downgrade() -> None:
    _swap(OLD_CHECKS, (STAGE_CHECK, FROM_CHECK, BLOCKER_CHECK))
