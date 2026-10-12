"""Лестница этапов из 8 ступеней и один способ закрыть проект (владелец 12.10.2026).

Новый → ТК готова → КБ набрана → Распланировано → В работе → ОТК → Принято складом → Упаковано → Отгружено.
«Готово к отгрузке» убрано (читается как «Принято складом»), «На отгрузке» — как «Упаковано», закрыть проект можно только
статусом «Отменён» («Отказ» и «Слился» читаются как «Отменён»).

Что делает миграция:
- три проверки `production_plan_events` (значение «Этап КБ», значение «Статус проекта», «было») переводятся на новые
  списки; триггер, запрещающий UPDATE (журнал только дополняется), пересоздаётся. Старые по форме строки (слова прежней
  лестницы, «Отказ», «Слился») остаются как записаны: на PostgreSQL проверки ставятся `NOT VALID`, на SQLite таблица
  копируется при `PRAGMA ignore_check_constraints`. Новые строки проверяются по-новому;
- этапы КБ в журнале НЕ переписываются: журнал неизменяем, а приложение читает старые слова как этапы новой лестницы
  (`stage_alias`), поэтому «Готово к отгрузке» показывается как «Принято складом», «На отгрузке» — как «Упаковано»;
- проект, закрытый словом «Отказ» или «Слился» (последняя запись журнала «Статус проекта» или, если записей нет, снимок
  реестра), получает новую запись журнала «Статус проекта» = «Отменён» с причиной «было «Отказ»» — прежнее слово не
  теряется; снимок реестра `production_plan_projects` с этими словами переписывается на «Отменён».
  Повторный запуск ничего не меняет: после первого проект уже «Отменён».
Числа затронутых строк печатаются в лог.

«Распланировано» не хранится — выводится из ресурсного плана, миграция в него ничего не переносит.
DESTRUCTIVE = False: ничего не удаляется. Downgrade возвращает проверки 0107 (записи «Отменён» и новые этапы остаются).
"""

from datetime import datetime, timezone
from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0108_stage_warehouse_accepted"
down_revision: str | None = "0107_stage_ladder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "production_plan_events"
PROJECTS = "production_plan_projects"
# Literal copies of app.modules.production.plan (a migration never imports app code that moves on).
STAGE_KIND, STATUS_KIND = "Этап КБ", "Статус проекта"
STAGES = (
    "Новый", "ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено",
)
OLD_STAGES = ("Новый", "ТК готова", "КБ набрана", "В работе", "ОТК", "Готово к отгрузке", "На отгрузке", "Отгружено")
TERMINALS = ("Отменён",)
OLD_TERMINALS = ("Отказ", "Слился", "Отменён")
LEGACY_TERMINALS = ("Отказ", "Слился")
AUTO = "авто"
TRIGGER = "production_plan_events_no_update"
MESSAGE = "Журнал проекта только дополняется: запись не меняется"
CHECK_NAMES = (
    "ck_production_plan_events_stage_value", "ck_production_plan_events_status_value",
    "ck_production_plan_events_from_value",
)


def repr_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr_sql(value) for value in values)})"


def _checks(stages: tuple[str, ...], terminals: tuple[str, ...]) -> dict[str, str]:
    # The value column comes first in each check: the head-1 seeder of CI fills a checked column with the first literal.
    return {
        "ck_production_plan_events_stage_value": f"{_in('value', (*stages, AUTO))} OR kind <> {repr_sql(STAGE_KIND)}",
        "ck_production_plan_events_status_value": f"{_in('value', (*terminals, AUTO))} OR kind <> {repr_sql(STATUS_KIND)}",
        "ck_production_plan_events_from_value": (
            f"from_value = '' OR (kind = {repr_sql(STAGE_KIND)} AND {_in('from_value', stages)})"
            f" OR (kind = {repr_sql(STATUS_KIND)} AND {_in('from_value', terminals)})"
        ),
    }


CHECKS = _checks(STAGES, TERMINALS)
OLD_CHECKS = _checks(OLD_STAGES, OLD_TERMINALS)


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
            for name in CHECK_NAMES:
                if name in present:
                    batch.drop_constraint(name, type_="check")
            for name, sql in new.items():
                batch.create_check_constraint(name, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    # The rebuild dropped the table's trigger: the journal stays append-only.
    _sqlite_trigger()


def _close_legacy_projects(bind) -> None:
    events = sa.table(
        TABLE,
        sa.column("id", sa.Uuid()), sa.column("project", sa.String()), sa.column("sequence", sa.Integer()),
        sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("who", sa.String()),
        sa.column("kind", sa.String()), sa.column("blocker", sa.String()), sa.column("text", sa.Text()),
        sa.column("value", sa.String()), sa.column("reason", sa.Text()),
        sa.column("item_index", sa.String()), sa.column("field", sa.String()), sa.column("from_value", sa.String()),
    )
    snapshots = sa.table(PROJECTS, sa.column("project", sa.String()), sa.column("status", sa.String()),
                         sa.column("terminal", sa.Boolean()))
    # Current word of each project: its last «Статус проекта» row, else the registry snapshot.
    current: dict[str, str] = {}
    for project, value in bind.execute(
        sa.select(events.c.project, events.c.value).where(events.c.kind == STATUS_KIND)
        .order_by(events.c.project, events.c.sequence)
    ):
        current[project] = value
    from_snapshot = {
        project: status for project, status in bind.execute(
            sa.select(snapshots.c.project, snapshots.c.status)
            .where(snapshots.c.terminal.is_(True), snapshots.c.status.in_(LEGACY_TERMINALS))
        ) if project not in current
    }
    todo = {project: word for project, word in current.items() if word in LEGACY_TERMINALS} | from_snapshot
    last = {
        project: sequence for project, sequence in bind.execute(
            sa.select(events.c.project, sa.func.max(events.c.sequence)).group_by(events.c.project)
        )
    }
    now = datetime.now(timezone.utc)
    for project, word in sorted(todo.items()):
        bind.execute(sa.insert(events).values(
            id=uuid4(), project=project, sequence=last.get(project, 0) + 1, occurred_at=now, who="миграция 0108",
            kind=STATUS_KIND, blocker="", text=f"было «{word}», теперь «Отменён»: закрыть проект можно только так",
            value="Отменён", reason=f"было «{word}»", item_index="", field="", from_value="",
        ))
    renamed = bind.execute(
        sa.update(snapshots).where(snapshots.c.status.in_(LEGACY_TERMINALS)).values(status="Отменён")
    ).rowcount
    print(f"{revision}: проектов, закрытых словами «Отказ» / «Слился», переведено на «Отменён» записью журнала: "
          f"{len(todo)}; строк реестра {PROJECTS} переписано: {renamed}.")


def upgrade() -> None:
    _swap(CHECKS)
    _close_legacy_projects(op.get_bind())


def downgrade() -> None:
    _swap(OLD_CHECKS)
