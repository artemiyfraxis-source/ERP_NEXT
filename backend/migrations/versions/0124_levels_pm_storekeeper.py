"""Уровни «Менеджер по проектам» и «Кладовщик», «Администратор» упразднён (владелец, 14.10.2026: «давай новые роли:
Менеджер по проектам — Ульяне, Кладовщик — Алексею; Администратора можно удалить»).

- В матрицу «Доступы» (`identity_level_access`) добавляются строки новых уровней `pm` и `storekeeper` — замороженная
  копия их LEVEL_SEED (tests/identity/test_levels_pm_storekeeper_migration.py держит её равной домену). Только
  недостающие клетки (`ON CONFLICT DO NOTHING`): уже записанная клетка не трогается.
- Уровень `admin` больше нельзя выдать: сотрудник, у которого он записан (на 14.10 — никого), переводится на
  `superadmin` (оба — полный доступ, никто ничего не теряет); перевод пишется событием аудита, откат его возвращает.
  Строки матрицы уровня `admin` удаляются: полный уровень их никогда не читал (level_permissions).
  Ключ `admin` остаётся только сессионным уровнем «Режима админа» (identity/domain.py ADMIN_MODE_LEVEL).
- Печатается сверка: строк матрицы и людей по уровням было → стало.

DESTRUCTIVE = False. Откат удаляет строки матрицы двух новых уровней, переводит их людей обратно
(pm → operator, storekeeper → foreman) и возвращает `admin` тем, кого перевод поднял (если они всё ещё superadmin).
Удалённые строки `admin` не восстанавливаются — их никто не читал.
"""

import json
from datetime import datetime, timezone
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0124_levels_pm_storekeeper"
down_revision: str | None = "0123_ru_holidays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

# Frozen copy of LEVEL_SEED["pm"] / LEVEL_SEED["storekeeper"] (a migration must not import application code).
NEW_LEVEL_ROWS: dict[str, dict[str, str]] = {
    "pm": {
        "newrequest": "edit", "myrequests": "edit", "requests": "edit",
        "projects": "edit", "shipping": "edit",
        "techcards": "view", "production": "view", "erpref": "view", "procurement": "view", "warehouse": "view",
        "shipping_goods": "view",
    },
    "storekeeper": {
        "newrequest": "edit", "myrequests": "edit",
        "receiving": "edit", "warehouse": "edit", "fulfillment": "edit", "handout": "edit", "shipping_goods": "edit",
        "projects": "view", "erpref": "view", "procurement": "view", "shipping": "view",
    },
}
# Where the people of a new level go back on downgrade (the level they would have had before 14.10).
DOWNGRADE_LEVEL = {"pm": "operator", "storekeeper": "foreman"}
RETIRED_LEVEL, FULL_LEVEL = "admin", "superadmin"
RETIRED_EVENT = "identity.admin_level_retired"
ACCESS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:access-matrix")  # access.py ACCESS_AGGREGATE

_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)
_users = sa.table("identity_users", sa.column("id", sa.Uuid()), sa.column("access_level", sa.String()))


def _scalar(connection, sql: str, **params) -> int:
    return int(connection.execute(sa.text(sql).bindparams(**params)).scalar() or 0)


def _snapshot(connection) -> str:
    levels = ("pm", "storekeeper", RETIRED_LEVEL, FULL_LEVEL, "operator", "foreman")
    rows = _scalar(connection, "SELECT COUNT(*) FROM identity_level_access")
    matrix = ", ".join(
        f"{level} {_scalar(connection, 'SELECT COUNT(*) FROM identity_level_access WHERE level = :level', level=level)}"
        for level in ("pm", "storekeeper", RETIRED_LEVEL)
    )
    people = ", ".join(
        f"{level} {_scalar(connection, 'SELECT COUNT(*) FROM identity_users WHERE access_level = :level', level=level)}"
        for level in levels
    )
    return f"строк матрицы {rows} ({matrix}); людей по уровням: {people}"


def _details(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return value if isinstance(value, dict) else {}


def upgrade() -> None:
    connection = op.get_bind()
    before = _snapshot(connection)

    moved = [
        str(user_id) for user_id in connection.execute(
            sa.select(_users.c.id).where(_users.c.access_level == RETIRED_LEVEL).order_by(_users.c.id)
        ).scalars()
    ]
    if moved:
        connection.execute(
            sa.update(_users).where(_users.c.access_level == RETIRED_LEVEL).values(access_level=FULL_LEVEL)
        )
        connection.execute(_audit.insert(), [{
            "id": uuid4(), "aggregate_id": ACCESS_AGGREGATE, "event_type": RETIRED_EVENT, "actor_id": UUID(int=0),
            "actor_name": "Уровни доступа", "actor_department": "Система",
            "occurred_at": datetime.now(timezone.utc),
            "details": {"users": json.dumps(moved), "level": FULL_LEVEL},
        }])
    connection.execute(sa.text("DELETE FROM identity_level_access WHERE level = :level").bindparams(level=RETIRED_LEVEL))

    for level, row in NEW_LEVEL_ROWS.items():
        for section, mode in row.items():
            connection.execute(sa.text("""
                INSERT INTO identity_level_access (level, section, mode)
                VALUES (:level, :section, :mode)
                ON CONFLICT (level, section) DO NOTHING
            """).bindparams(level=level, section=section, mode=mode))

    print(f"0124_levels_pm_storekeeper: было — {before}")
    print(f"0124_levels_pm_storekeeper: стало — {_snapshot(connection)}; переведено admin → superadmin: {len(moved)}")


def downgrade() -> None:
    connection = op.get_bind()
    before = _snapshot(connection)
    for level, back in DOWNGRADE_LEVEL.items():
        connection.execute(sa.update(_users).where(_users.c.access_level == level).values(access_level=back))
        connection.execute(sa.text("DELETE FROM identity_level_access WHERE level = :level").bindparams(level=level))
    restored = 0
    for event_id, raw in connection.execute(
        sa.select(_audit.c.id, _audit.c.details).where(_audit.c.event_type == RETIRED_EVENT)
    ).all():
        for user_id in json.loads(_details(raw).get("users") or "[]"):
            restored += connection.execute(
                sa.update(_users)
                .where(_users.c.id == UUID(user_id), _users.c.access_level == FULL_LEVEL)
                .values(access_level=RETIRED_LEVEL)
            ).rowcount or 0
        connection.execute(sa.delete(_audit).where(_audit.c.id == event_id))
    print(f"0124_levels_pm_storekeeper (откат): было — {before}")
    print(f"0124_levels_pm_storekeeper (откат): стало — {_snapshot(connection)}; возвращено admin: {restored}")
