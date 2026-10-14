"""Нормальные отделы в «Сотрудники» → «Отделы» (владелец 12.10.2026: «сделай норм отделы: Производственный отдел,
Технический отдел, Монтажный отдел, руководящий состав, маркетинг, продажи и т. д., короче сделай нормально»).

Data only, no schema. The employee's department is the text `identity_users.department`; the list is
`identity_departments` (position, name, head).
- Whole departments are renamed (list entry, every employee in it, a document aimed at it):
  «Монтаж» → «Монтажный отдел», «Производство (Цар)» → «Производственный отдел», «Руководство» → «Руководящий
  состав», «Офис» → «Административный отдел».
- Single people move, matched by their current department AND full name; a person who is not found (or found twice)
  is skipped with a warning in the migration log — never guessed: Баковский, Костромин (Офис) and Рустамов (Фраксис)
  → «Руководящий состав»; Домрачев (Офис) → «Отдел снабжения»; Вишнев (Фраксис) → «Административный отдел»; Гусев
  («Приёмка/склад») → «Склад»; the test account «Вася Пупкин» («бог») → no department.
- The list becomes, in this order: Руководящий состав, Производственный отдел, Монтажный отдел, Технический отдел,
  Отдел технического контроля (ОТК), Отдел снабжения, Склад, Отдел продаж, Маркетинг, Бухгалтерия, Административный
  отдел. A department already in the list keeps its head (a renamed one carries it). Other entries of the old list stay
  after these, except «бог» and «Фраксис», which leave the list only when nobody has them any more. Heads are not
  assigned. On a fresh database (no list, no people) only the list is written, without the audit event.
- The «Монтажник» rule (`shop_worker_rule`: «монтаж» in the department) gives the same answer for every pair here, and
  the stored `shop_worker` flag is not touched. History (`audit_events.actor_department`, `requests.actor_department`,
  procurement and receiving copies) is not rewritten. One «Отделы сохранены» audit event records the run.
Re-running is a no-op: nothing left to match, the list already equal — no writes, no event.

Downgrade, best effort: people moved here and still in the target department under the same name go back, the four
renames are reversed, the five added departments and «Отдел снабжения» / «Склад» leave the list when nobody has
them, «бог» / «Фраксис» come back to the list when someone has them again. Edits made after the upgrade (a person
moved by hand into «Склад», a renamed department) are not undone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0104_staff_departments"
down_revision: str | None = "0103_hookup_wire_conductor_insulation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

logger = logging.getLogger("alembic.runtime.migration")

RENAMES: dict[str, str] = {
    "Монтаж": "Монтажный отдел",
    "Производство (Цар)": "Производственный отдел",
    "Руководство": "Руководящий состав",
    "Офис": "Административный отдел",
}
# (current department, full name, new department) — applied before the renames, so the source is the old name.
MOVES: tuple[tuple[str, str, str], ...] = (
    ("Офис", "Баковский Всеволод", "Руководящий состав"),
    ("Офис", "Костромин Василий", "Руководящий состав"),
    ("Фраксис", "Рустамов Вадим", "Руководящий состав"),
    ("Офис", "Домрачев Виктор", "Отдел снабжения"),
    ("Фраксис", "Вишнев Александр", "Административный отдел"),
    ("Приёмка/склад", "Гусев Алексей", "Склад"),
    ("бог", "Вася Пупкин", ""),
)
FINAL: tuple[str, ...] = (
    "Руководящий состав",
    "Производственный отдел",
    "Монтажный отдел",
    "Технический отдел",
    "Отдел технического контроля (ОТК)",
    "Отдел снабжения",
    "Склад",
    "Отдел продаж",
    "Маркетинг",
    "Бухгалтерия",
    "Административный отдел",
)
DROP_WHEN_EMPTY = frozenset({"бог", "Фраксис"})
# What the downgrade takes out of the list when nobody has it: the departments this migration introduced.
ADDED = frozenset(set(FINAL) - set(RENAMES.values()))

DEPARTMENTS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:departments")  # staff.py DEPARTMENTS_AGGREGATE
SAVED_EVENT = "identity.departments_saved"

_users = sa.table(
    "identity_users",
    sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("department", sa.String()),
)
_departments = sa.table(
    "identity_departments",
    sa.column("position", sa.Integer()), sa.column("name", sa.String()), sa.column("head_user_id", sa.Uuid()),
)
_documents = sa.table(
    "identity_documents",
    sa.column("scope", sa.String()), sa.column("scope_value", sa.String()),
)
_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)


def _move_person(connection, source: str, name: str, target: str) -> int:
    found = connection.execute(
        sa.select(_users.c.id).where(_users.c.department == source, _users.c.name == name)
    ).scalars().all()
    if len(found) != 1:
        already = connection.execute(
            sa.select(sa.func.count()).select_from(_users).where(_users.c.department == target, _users.c.name == name)
        ).scalar()
        if not found and already:
            return 0  # moved by an earlier run
        logger.warning("0104: «%s» в отделе «%s» — найдено %d, не переносится в «%s»", name, source, len(found), target)
        return 0
    connection.execute(sa.update(_users).where(_users.c.id == found[0]).values(department=target))
    return 1


def _rename_department(connection, source: str, target: str) -> int:
    moved = connection.execute(
        sa.update(_users).where(_users.c.department == source).values(department=target)
    ).rowcount or 0
    connection.execute(
        sa.update(_documents).where(_documents.c.scope == "department", _documents.c.scope_value == source)
        .values(scope_value=target)
    )
    return moved


def _saved(connection) -> list[tuple[str, UUID | None]]:
    return [
        (row.name, row.head_user_id)
        for row in connection.execute(
            sa.select(_departments.c.name, _departments.c.head_user_id).order_by(_departments.c.position)
        )
    ]


def _in_use(connection) -> set[str]:
    return {str(value or "").strip().casefold() for value in connection.execute(sa.select(_users.c.department)).scalars()}


def _write_list(connection, current: list[tuple[str, UUID | None]], wanted: list[tuple[str, UUID | None]]) -> bool:
    if wanted == current:
        return False
    connection.execute(sa.delete(_departments))
    if wanted:
        connection.execute(sa.insert(_departments), [
            {"position": position, "name": name, "head_user_id": head} for position, (name, head) in enumerate(wanted)
        ])
    return True


def upgraded_list(saved: list[tuple[str, UUID | None]], in_use: set[str]) -> list[tuple[str, UUID | None]]:
    """FINAL in its order (each with the head its old or renamed entry had), then the rest of the old list."""
    renamed = [(RENAMES.get(name, name), head) for name, head in saved]
    heads: dict[str, UUID | None] = {}
    for name, head in renamed:
        if head is not None:
            heads.setdefault(name.casefold(), head)
    result = [(name, heads.get(name.casefold())) for name in FINAL]
    taken = {name.casefold() for name, _head in result}
    for name, head in renamed:
        key = name.casefold()
        if key in taken or (name in DROP_WHEN_EMPTY and key not in in_use):
            continue
        result.append((name, head))
        taken.add(key)
    return result


def downgraded_list(saved: list[tuple[str, UUID | None]], in_use: set[str]) -> list[tuple[str, UUID | None]]:
    back = {new: old for old, new in RENAMES.items()}
    result: list[tuple[str, UUID | None]] = []
    taken: set[str] = set()
    for name, head in saved:
        name = back.get(name, name)
        key = name.casefold()
        if key in taken or (name in ADDED and key not in in_use):
            continue
        result.append((name, head))
        taken.add(key)
    for name in sorted(DROP_WHEN_EMPTY):
        if name.casefold() in in_use and name.casefold() not in taken:
            result.append((name, None))
            taken.add(name.casefold())
    return result


def _record(connection, departments: list[tuple[str, UUID | None]], users: int) -> None:
    names = [name for name, _head in departments]
    connection.execute(sa.insert(_audit).values(
        id=uuid4(), aggregate_id=DEPARTMENTS_AGGREGATE, event_type=SAVED_EVENT, actor_id=UUID(int=0),
        actor_name="Перенос отделов", actor_department="Система", occurred_at=datetime.now(timezone.utc),
        details={"departments": json.dumps(names, ensure_ascii=False), "count": str(len(names)), "users": str(users)},
    ))


def upgrade() -> None:
    connection = op.get_bind()
    users = sum(_move_person(connection, source, name, target) for source, name, target in MOVES)
    users += sum(_rename_department(connection, source, target) for source, target in RENAMES.items())
    saved = _saved(connection)
    wanted = upgraded_list(saved, _in_use(connection))
    # A fresh database (no list, nobody moved) only gets the list, no audit event.
    if _write_list(connection, saved, wanted) and (saved or users) or users:
        _record(connection, wanted, users)
    logger.info("0104: отделов в списке %d, сотрудников перенесено %d", len(wanted), users)


def downgrade() -> None:
    connection = op.get_bind()
    users = sum(_move_person(connection, target, name, source) for source, name, target in MOVES)
    users += sum(_rename_department(connection, target, source) for source, target in RENAMES.items())
    saved = _saved(connection)
    _write_list(connection, saved, downgraded_list(saved, _in_use(connection)))
    logger.info("0104 откат: сотрудников возвращено %d", users)
