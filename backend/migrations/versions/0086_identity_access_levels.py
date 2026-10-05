"""Уровни доступа вместо ролей-должностей (owner 04.10.2026, docs/migration/access-levels-plan.md, batch T1).

Schema:
- `identity_users.access_level` — superadmin · admin · commercial · tech_lead · foreman · operator, NOT NULL after the
  backfill, indexed; `shop_worker` «Монтажник (работает в цеху)»; `site_master` «Мастер площадки»; `legacy_role` — the
  role at the transfer, kept one release;
- `identity_level_access` (level × section → mode) — the level matrix, cells stored like `identity_role_access`;
- `identity_user_level_access` (user × section → mode) — personal exceptions on top of the level row.

Data — a frozen copy of `app/modules/identity/level_migration.transfer` (a migration must not import application code;
tests/identity/test_level_migration.py keeps the two equal):
- level by role: management → superadmin · admin → admin · prodhead, buyer, engineer → tech_lead · pm, estimator →
  commercial · master, receiver, qc → foreman · staff (and anything unknown) → operator; shop_worker = today's installer
  rule (staff with a site or a «монтаж» department), site_master = role master; an empty job title ← the role's label;
- per person, what they can EFFECTIVELY use today per new section (the matrix cell restricted by the route role lists;
  a full-access role — everything); the level row = the intersection of that over the level's active members; each
  person gets the exceptions that bring the row back to exactly what they had; superadmin/admin rows are edit
  everywhere;
- the dormant exceptions of a former full-access role (admin, buyer, prodhead, management) go to the audit
  (`identity.dormant_access_archived`), they are not carried over; one `identity.access_levels_migrated` event sums up
  (none on a database without people).
Running the transfer again changes nothing. The gates still read the role tables (`identity_role_access`,
`identity_user_access`, untouched here), so nobody's access changes until they are switched.

Downgrade drops the new tables and columns. The job titles filled in stay: they are ordinary values of the 0085 column.
"""

import json
from datetime import datetime, timezone
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0086_identity_access_levels"
down_revision: str | None = "0085_identity_personnel_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── Frozen copy of level_migration (04.10.2026) ──────────────────────────────────────────────────────

VIEW, EDIT, NONE = "view", "edit", "none"
_RANK = {NONE: 0, VIEW: 1, EDIT: 2}
_BY_RANK = {1: VIEW, 2: EDIT}

LEVELS = ("superadmin", "admin", "commercial", "tech_lead", "foreman", "operator")
FULL_LEVELS = frozenset({"superadmin", "admin"})
LEVEL_SECTIONS = (
    "projects", "techcards", "erptctpl", "tcgen", "erpref", "production", "myops", "quality", "newrequest",
    "myrequests", "requests", "procurement", "receiving", "warehouse", "fulfillment", "handout", "shipping",
    "shipping_goods", "operations",
)
_FILE = {"newrequest": EDIT, "myrequests": EDIT}
LEVEL_SEED: dict[str, dict[str, str]] = {
    "commercial": {
        **_FILE, "projects": VIEW, "shipping": VIEW, "procurement": VIEW, "warehouse": VIEW, "production": VIEW,
        "requests": EDIT,
    },
    "tech_lead": {
        **_FILE, "projects": EDIT, "techcards": EDIT, "erptctpl": EDIT, "tcgen": EDIT, "erpref": EDIT,
        "production": EDIT, "procurement": EDIT, "requests": EDIT, "fulfillment": EDIT, "receiving": VIEW,
        "warehouse": VIEW, "handout": VIEW, "quality": VIEW, "shipping": VIEW,
    },
    "foreman": {
        **_FILE, "production": EDIT, "myops": EDIT, "receiving": EDIT, "warehouse": EDIT, "fulfillment": EDIT,
        "handout": EDIT, "shipping_goods": EDIT, "procurement": VIEW, "quality": VIEW,
    },
    "operator": {**_FILE, "myops": EDIT, "receiving": VIEW, "warehouse": VIEW, "erpref": VIEW},
}

OLD_FULL_ROLES = frozenset({"admin", "buyer", "prodhead", "management"})
OLD_SECTIONS = (
    "projects", "techcards", "erptctpl", "tcgen", "erpref", "production", "newrequest", "myrequests", "requests",
    "procurement", "receiving", "warehouse", "fulfillment", "handout", "shipping", "staffdir", "operations",
)
ROLE_LABELS = {
    "admin": "Технолог", "buyer": "Специалист по снабжению", "receiver": "Кладовщик", "estimator": "Сметчик",
    "prodhead": "Директор производства", "master": "Мастер", "management": "Генеральный директор",
    "pm": "Менеджер по проектам", "qc": "ОТК", "engineer": "Инженер", "staff": "Сотрудник",
}
ROLE_TO_LEVEL = {
    "management": "superadmin", "admin": "admin", "prodhead": "tech_lead", "buyer": "tech_lead",
    "engineer": "tech_lead", "pm": "commercial", "estimator": "commercial", "master": "foreman",
    "receiver": "foreman", "qc": "foreman", "staff": "operator",
}

_PROCUREMENT_VIEW = frozenset({"buyer", "receiver", "prodhead", "management", "admin"})
_RECEIVING_VIEW = frozenset({"receiver", "prodhead", "management", "master", "staff", "admin"})
_WAREHOUSE_VIEW = frozenset({"receiver", "buyer", "prodhead", "management", "master", "staff", "admin"})
_FULFILLMENT_VIEW = frozenset({"engineer", "receiver", "prodhead", "master", "admin"})
_PRODUCTION_VIEW = frozenset({"staff", "master", "prodhead", "management", "qc", "admin"})
_PRODUCTION_OVERVIEW = frozenset({"master", "prodhead", "management", "qc", "admin"})
_SHIPPING_VIEW = frozenset({"pm", "receiver", "master", "prodhead", "management", "admin"})
_RECEIVER_ADMIN = frozenset({"receiver", "admin"})
_OPERATIONS = frozenset({"management", "admin"})
# new section → (view rule, edit rule); a rule = (old sections, minimum, route role list)
WITNESSES = {
    "procurement": (
        (("procurement",), VIEW, _PROCUREMENT_VIEW), (("procurement",), EDIT, frozenset({"buyer", "admin"})),
    ),
    "receiving": ((("receiving",), VIEW, _RECEIVING_VIEW), (("receiving",), EDIT, _RECEIVER_ADMIN)),
    "warehouse": ((("warehouse",), VIEW, _WAREHOUSE_VIEW), (("warehouse",), EDIT, _RECEIVER_ADMIN)),
    "fulfillment": (
        (("fulfillment",), VIEW, _FULFILLMENT_VIEW),
        (("fulfillment",), EDIT, frozenset({"engineer", "receiver", "admin"})),
    ),
    "handout": ((("handout",), VIEW, _FULFILLMENT_VIEW), (("handout",), EDIT, _RECEIVER_ADMIN)),
    "production": (
        (("production",), VIEW, _PRODUCTION_OVERVIEW), (("production",), EDIT, frozenset({"master", "admin"})),
    ),
    "myops": ((("production",), VIEW, _PRODUCTION_VIEW), (("production",), VIEW, _PRODUCTION_VIEW)),
    "quality": ((("production",), EDIT, frozenset({"qc", "admin"})),) * 2,
    "shipping": ((("shipping",), VIEW, _SHIPPING_VIEW), (("shipping",), EDIT, frozenset({"pm", "admin"}))),
    "shipping_goods": ((("shipping",), EDIT, _RECEIVER_ADMIN), (("shipping",), EDIT, _RECEIVER_ADMIN)),
    "operations": ((("operations",), VIEW, _OPERATIONS), (("operations",), EDIT, _OPERATIONS)),
}


def _overlay(base, overrides) -> dict:
    result = {section: mode for section, mode in base.items() if mode in (VIEW, EDIT)}
    for section, mode in overrides.items():
        if mode in (VIEW, EDIT):
            result[section] = mode
        else:
            result.pop(section, None)
    return result


def _passes(role: str, permissions: dict, rule) -> bool:
    sections, minimum, roles = rule
    modes = [permissions.get(section) for section in sections]
    allowed = EDIT in modes or (minimum == VIEW and VIEW in modes)
    if roles is not None and role not in roles and role not in OLD_FULL_ROLES:
        allowed = False
    return allowed


def _usable(role: str, role_row, exceptions) -> dict:
    if role in OLD_FULL_ROLES:
        return {section: EDIT for section in LEVEL_SECTIONS}
    permissions = _overlay(_overlay({}, role_row), exceptions)
    result = {}
    for section in LEVEL_SECTIONS:
        witness = WITNESSES.get(section)
        if witness is None:
            mode = permissions.get(section)
        elif _passes(role, permissions, witness[1]):
            mode = EDIT
        elif _passes(role, permissions, witness[0]):
            mode = VIEW
        else:
            mode = None
        if mode in (VIEW, EDIT):
            result[section] = mode
    return result


def compute(people: list[dict], role_matrix: dict, user_access: dict) -> dict:
    """people: dicts with id (UUID), role, site, department, active, job_title. Returns the plain form of
    level_migration.Transfer.plain()."""
    people = sorted(people, key=lambda person: str(person["id"]))
    assigned, usable, archived = {}, {}, {}
    for person in people:
        role = str(person["role"] or "").strip()
        title = str(person["job_title"] or "")
        assigned[person["id"]] = {
            "access_level": ROLE_TO_LEVEL.get(role, "operator"),
            "shop_worker": role == "staff"
            and (bool(person["site"]) or "монтаж" in str(person["department"] or "").lower()),
            "site_master": role == "master",
            "legacy_role": role,
            "job_title": title if title.strip() else ROLE_LABELS.get(role, ""),
        }
        exceptions = user_access.get(person["id"], {})
        usable[person["id"]] = _usable(role, role_matrix.get(role, {}), exceptions)
        if role in OLD_FULL_ROLES and exceptions:
            archived[str(person["id"])] = dict(sorted(exceptions.items()))

    levels = {}
    for level in LEVELS:
        if level in FULL_LEVELS:
            levels[level] = {section: EDIT for section in LEVEL_SECTIONS}
            continue
        members = [person for person in people if assigned[person["id"]]["access_level"] == level]
        pool = [person for person in members if person["active"]] or members
        if not pool:
            seed = LEVEL_SEED[level]
            levels[level] = {section: seed[section] for section in LEVEL_SECTIONS if section in seed}
            continue
        row = {}
        for section in LEVEL_SECTIONS:
            lowest = min(_RANK[usable[person["id"]].get(section, NONE)] for person in pool)
            if lowest:
                row[section] = _BY_RANK[lowest]
        levels[level] = row

    exceptions = {}
    for person in people:
        level = assigned[person["id"]]["access_level"]
        if level in FULL_LEVELS:
            continue
        target, row = usable[person["id"]], levels[level]
        diff = {
            section: target.get(section, NONE)
            for section in LEVEL_SECTIONS
            if target.get(section, NONE) != row.get(section, NONE)
        }
        if diff:
            exceptions[str(person["id"])] = diff
    return {
        "people": {str(user_id): item for user_id, item in assigned.items()},
        "levels": levels,
        "exceptions": exceptions,
        "archived": archived,
    }


# ── Reading and writing ──────────────────────────────────────────────────────────────────────────────

_users = sa.table(
    "identity_users",
    sa.column("id", sa.Uuid()), sa.column("login", sa.String()), sa.column("name", sa.String()),
    sa.column("department", sa.String()), sa.column("role", sa.String()), sa.column("site", sa.String()),
    sa.column("active", sa.Boolean()), sa.column("job_title", sa.String()), sa.column("access_level", sa.String()),
    sa.column("shop_worker", sa.Boolean()), sa.column("site_master", sa.Boolean()),
    sa.column("legacy_role", sa.String()),
)
_role_access = sa.table("identity_role_access", sa.column("role", sa.String()), sa.column("section", sa.String()),
                        sa.column("mode", sa.String()))
_user_access = sa.table("identity_user_access", sa.column("user_id", sa.Uuid()), sa.column("section", sa.String()),
                        sa.column("mode", sa.String()))
_level_access = sa.table("identity_level_access", sa.column("level", sa.String()), sa.column("section", sa.String()),
                         sa.column("mode", sa.String()))
_user_level_access = sa.table("identity_user_level_access", sa.column("user_id", sa.Uuid()),
                              sa.column("section", sa.String()), sa.column("mode", sa.String()))
_audit = sa.table(
    "audit_events",
    sa.column("id", sa.Uuid()), sa.column("aggregate_id", sa.Uuid()), sa.column("event_type", sa.String()),
    sa.column("actor_id", sa.Uuid()), sa.column("actor_name", sa.String()), sa.column("actor_department", sa.String()),
    sa.column("occurred_at", sa.DateTime(timezone=True)), sa.column("details", sa.JSON()),
)
ACCESS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:access-matrix")  # access.py ACCESS_AGGREGATE
ARCHIVED_EVENT = "identity.dormant_access_archived"
SUMMARY_EVENT = "identity.access_levels_migrated"


def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _cells(rows) -> dict:
    result: dict = {}
    for key, section, mode in rows:
        result.setdefault(key, {})[section] = mode
    return result


def _event(aggregate_id: UUID, event_type: str, now: datetime, details: dict) -> dict:
    return {
        "id": uuid4(), "aggregate_id": aggregate_id, "event_type": event_type, "actor_id": UUID(int=0),
        "actor_name": "Перенос уровней доступа", "actor_department": "Система", "occurred_at": now, "details": details,
    }


def transfer(connection, now: datetime | None = None) -> int:
    """Brings the level data in line with `compute` over the role data; returns how many rows it changed (0 on a
    second run)."""
    now = now or datetime.now(timezone.utc)
    users = connection.execute(sa.select(_users)).mappings().all()
    role_matrix = _cells(connection.execute(
        sa.select(_role_access.c.role, _role_access.c.section, _role_access.c.mode)
    ))
    user_access = {
        _uuid(user_id): row
        for user_id, row in _cells(connection.execute(
            sa.select(_user_access.c.user_id, _user_access.c.section, _user_access.c.mode)
        )).items()
    }
    people = [{**dict(user), "id": _uuid(user["id"])} for user in users]
    result = compute(people, role_matrix, user_access)
    changes = 0

    for person in people:
        target = result["people"][str(person["id"])]
        stored = {
            "access_level": person["access_level"], "shop_worker": bool(person["shop_worker"]),
            "site_master": bool(person["site_master"]), "legacy_role": person["legacy_role"],
            "job_title": person["job_title"],
        }
        if stored != target:
            connection.execute(_users.update().where(_users.c.id == person["id"]).values(**target))
            changes += 1

    stored_levels = _cells(connection.execute(
        sa.select(_level_access.c.level, _level_access.c.section, _level_access.c.mode)
    ))
    for level, row in result["levels"].items():
        if stored_levels.get(level, {}) != row:
            connection.execute(_level_access.delete().where(_level_access.c.level == level))
            if row:
                connection.execute(_level_access.insert(), [
                    {"level": level, "section": section, "mode": mode} for section, mode in row.items()
                ])
            changes += 1

    stored_exceptions = {
        str(_uuid(user_id)): row
        for user_id, row in _cells(connection.execute(
            sa.select(_user_level_access.c.user_id, _user_level_access.c.section, _user_level_access.c.mode)
        )).items()
    }
    for person in people:
        key = str(person["id"])
        row = result["exceptions"].get(key, {})
        if stored_exceptions.get(key, {}) != row:
            connection.execute(_user_level_access.delete().where(_user_level_access.c.user_id == person["id"]))
            if row:
                connection.execute(_user_level_access.insert(), [
                    {"user_id": person["id"], "section": section, "mode": mode} for section, mode in row.items()
                ])
            changes += 1

    by_id = {str(person["id"]): person for person in people}
    already = {
        str(_uuid(aggregate_id))
        for aggregate_id in connection.execute(
            sa.select(_audit.c.aggregate_id).where(_audit.c.event_type == ARCHIVED_EVENT)
        ).scalars()
    }
    events = [
        _event(by_id[key]["id"], ARCHIVED_EVENT, now, {
            "login": by_id[key]["login"], "role": by_id[key]["role"],
            "access_level": result["people"][key]["access_level"],
            "exceptions": json.dumps(row, ensure_ascii=False, sort_keys=True),
            "note": "исключения роли с полным доступом не действовали и в уровень не перенесены",
        })
        for key, row in sorted(result["archived"].items())
        if key not in already
    ]
    changes += len(events)
    if changes and people:  # a fresh database (no people) only gets the default rows, nothing to record
        logins = {str(person["id"]): person["login"] for person in people}
        events.append(_event(ACCESS_AGGREGATE, SUMMARY_EVENT, now, {
            "levels": json.dumps(result["levels"], ensure_ascii=False),
            "people": json.dumps(
                {logins[key]: item["access_level"] for key, item in sorted(result["people"].items())},
                ensure_ascii=False,
            ),
            "exceptions": json.dumps(
                {logins[key]: row for key, row in sorted(result["exceptions"].items())}, ensure_ascii=False,
            ),
            "changes": str(changes),
        }))
    if events:
        connection.execute(_audit.insert(), events)
    return changes


def upgrade() -> None:
    with op.batch_alter_table("identity_users") as batch:
        batch.add_column(sa.Column("access_level", sa.String(16), nullable=True))
        batch.add_column(sa.Column("shop_worker", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("site_master", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("legacy_role", sa.String(64), nullable=True))
    op.create_table(
        "identity_level_access",
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.PrimaryKeyConstraint("level", "section"),
    )
    op.create_table(
        "identity_user_level_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "section"),
    )
    transfer(op.get_bind())
    with op.batch_alter_table("identity_users") as batch:
        batch.alter_column("access_level", existing_type=sa.String(16), nullable=False)
        batch.create_index("ix_identity_users_access_level", ["access_level"])


def downgrade() -> None:
    op.drop_table("identity_user_level_access")
    op.drop_table("identity_level_access")
    with op.batch_alter_table("identity_users") as batch:
        batch.drop_index("ix_identity_users_access_level")
        batch.drop_column("legacy_role")
        batch.drop_column("site_master")
        batch.drop_column("shop_worker")
        batch.drop_column("access_level")
