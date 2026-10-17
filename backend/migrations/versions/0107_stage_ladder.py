"""Одна лестница этапов на всё ERP (владелец 12.10.2026, «Загрузка цеха»: «сам придумай норм порядок»).

Новый → ТК готова → КБ набрана → В работе → ОТК → Готово к отгрузке → На отгрузке → Отгружено.

В `production_plan_events` меняются две проверки 0101:
- `ck_production_plan_events_stage_value` — значение «Этап КБ»: этап новой лестницы или «авто»;
- `ck_production_plan_events_from_value` — «было» у «Этап КБ»: этап новой лестницы.
Проверки вида записи и «Статус проекта» не меняются. Триггер, запрещающий UPDATE (журнал только дополняется),
после миграции есть на обеих базах: на PostgreSQL он пересоздаётся тем же определением, на SQLite таблица
пересоздаётся (так меняется CHECK) и триггер создаётся заново.

Строки не меняются. Старые записи со словом «КБ на складе» (до 12.10) остаются как записаны — журнал неизменяем;
приложение читает их как «Готово к отгрузке» (`app.modules.production.plan.stage_alias`), новая запись со старым
словом отклоняется (422 в приложении, CHECK в базе). Чтобы старые строки не мешали:
- на PostgreSQL новые проверки ставятся `NOT VALID`, если такие строки есть (новые строки проверяются, старые — нет);
- на SQLite (тесты, предпросмотр, демо) таблица копируется при `PRAGMA ignore_check_constraints` — то же самое.
Число таких строк печатается в лог миграции.

DESTRUCTIVE = False: данные не удаляются и не переписываются. Downgrade возвращает проверки 0101 (так же: старые по
форме строки не проверяются) и триггер.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0107_stage_ladder"
down_revision: str | None = "0106_fulfillment_line_absent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_plan_events"
# Literal copies of app.modules.production.plan (a migration never imports app code that moves on).
STAGE_KIND, STATUS_KIND = "Этап КБ", "Статус проекта"
STAGES = ("Новый", "ТК готова", "КБ набрана", "В работе", "ОТК", "Готово к отгрузке", "На отгрузке", "Отгружено")
OLD_STAGES = ("Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено")
TERMINALS = ("Отказ", "Слился", "Отменён")
AUTO = "авто"
TRIGGER = "production_plan_events_no_update"
MESSAGE = "Журнал проекта только дополняется: запись не меняется"


def repr_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr_sql(value) for value in values)})"


def _checks(stages: tuple[str, ...]) -> dict[str, str]:
    # The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first literal.
    return {
        "ck_production_plan_events_stage_value": f"{_in('value', (*stages, AUTO))} OR kind <> {repr_sql(STAGE_KIND)}",
        "ck_production_plan_events_from_value": (
            f"from_value = '' OR (kind = {repr_sql(STAGE_KIND)} AND {_in('from_value', stages)})"
            f" OR (kind = {repr_sql(STATUS_KIND)} AND {_in('from_value', TERMINALS)})"
        ),
    }


CHECKS = _checks(STAGES)
OLD_CHECKS = _checks(OLD_STAGES)


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


def _swap(old: dict[str, str], new: dict[str, str]) -> None:
    bind = op.get_bind()
    outside = _outside(bind, new)
    if outside:
        print(f"{revision}: {TABLE} — {outside} строк(и) со словами прежней лестницы оставлены как есть"
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
            for name in old:
                if name in present:
                    batch.drop_constraint(name, type_="check")
            for name, sql in new.items():
                batch.create_check_constraint(name, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    # The rebuild dropped the table's trigger: the journal stays append-only.
    _sqlite_trigger()


def upgrade() -> None:
    _swap(OLD_CHECKS, CHECKS)


def downgrade() -> None:
    _swap(CHECKS, OLD_CHECKS)
