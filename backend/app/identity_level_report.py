"""«Было / стало» before the access-level transfer (docs/migration/access-levels-plan.md) — read-only.

Usage, from backend/:
    ERP_DATABASE_URL=postgresql+psycopg://… .venv/bin/python -m app.identity_level_report [--all-gates] [--json]
    .venv/bin/python -m app.identity_level_report --database-url sqlite+pysqlite:///preview.db

Per person: old role → level, flags, job title, the compensating personal exceptions, the dormant exceptions that go to
the audit, and every route gate allowed before / after (by default only the changed ones; `--all-gates` lists all).
Then the notification recipients per audience before / after and the administrators who can still log in.

It reads identity_users, identity_role_access, identity_user_access and identity_credentials in one transaction marked
read-only (PostgreSQL SET TRANSACTION READ ONLY, SQLite PRAGMA query_only) and rolls it back: it never writes. It works
on the schema before migration 0086 and after it (then it also says whether the stored transfer matches the computed
one).
Exit code 0 — no gate changes except the planned ones and an administrator remains; 2 — otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

from app.modules.identity.domain import ACCESS_LEVEL_LABELS, ACCESS_LEVELS, FULL_ACCESS_LEVELS, LEVEL_SECTION_LABELS
from app.modules.identity.level_migration import (
    OLD_ROLE_LABELS,
    Person,
    Snapshot,
    Transfer,
    Verification,
    transfer,
    verify,
)

_MODE = {"edit": "правка", "view": "просмотр", "none": "закрыт"}
_SECTION_NAMES = {**LEVEL_SECTION_LABELS, "staffdir": "Сотрудники"}


@dataclass(frozen=True)
class StoredTransfer:
    """What migration 0086 left in the database, when it ran."""

    levels: dict[str, str]  # user id → access_level
    level_rows: dict[str, dict[str, str]]
    exceptions: dict[str, dict[str, str]]


def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _bool(value) -> bool:
    return bool(value) and str(value).strip().lower() not in ("0", "false", "f")


def _cells(rows) -> dict:
    result: dict = {}
    for key, section, mode in rows:
        result.setdefault(key, {})[section] = mode
    return result


def read_database(connection: Connection) -> tuple[Snapshot, StoredTransfer | None, str]:
    """The role-era data (and the level data, if migration 0086 ran); SELECTs only."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("identity_users")}
    wanted = ["id", "login", "email", "name", "department", "role", "site", "active"]
    wanted += [name for name in ("job_title", "access_level") if name in columns]
    users = connection.execute(text(f"SELECT {', '.join(wanted)} FROM identity_users")).mappings().all()
    credentials: dict[UUID, bool] = {}
    if "identity_credentials" in tables:
        has_pin_column = "pin_hash" in {column["name"] for column in inspector.get_columns("identity_credentials")}
        query = "SELECT user_id, pin_hash FROM identity_credentials" if has_pin_column else \
            "SELECT user_id, NULL FROM identity_credentials"
        credentials = {_uuid(user_id): bool(pin) for user_id, pin in connection.execute(text(query))}
    people = tuple(
        Person(
            id=_uuid(row["id"]), login=row["login"], name=row["name"], email=row["email"],
            department=row["department"] or "", role=row["role"] or "", site=row["site"], active=_bool(row["active"]),
            job_title=row.get("job_title") or "", has_credential=_uuid(row["id"]) in credentials,
            has_pin=credentials.get(_uuid(row["id"]), False),
        )
        for row in users
    )
    role_matrix = _cells(connection.execute(text("SELECT role, section, mode FROM identity_role_access")))
    user_access = {
        _uuid(user_id): row
        for user_id, row in _cells(connection.execute(
            text("SELECT user_id, section, mode FROM identity_user_access")
        )).items()
    }
    stored = None
    if "identity_level_access" in tables and "access_level" in columns:
        stored = StoredTransfer(
            levels={str(_uuid(row["id"])): row["access_level"] for row in users},
            level_rows=_cells(connection.execute(text("SELECT level, section, mode FROM identity_level_access"))),
            exceptions={
                str(_uuid(user_id)): row
                for user_id, row in _cells(connection.execute(
                    text("SELECT user_id, section, mode FROM identity_user_level_access")
                )).items()
            },
        )
    version = ""
    if "alembic_version" in tables:
        version = ", ".join(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    return Snapshot(people=people, role_matrix=role_matrix, user_access=user_access), stored, version


def read_only(database_url: str) -> tuple[Snapshot, StoredTransfer | None, str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                elif connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("PRAGMA query_only = ON")
                return read_database(connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def stored_differences(result: Transfer, stored: StoredTransfer) -> list[str]:
    plain = result.plain()
    differences = [
        f"уровень {key}: в базе {stored.levels.get(key)}, по расчёту {item['access_level']}"
        for key, item in plain["people"].items()
        if stored.levels.get(key) != item["access_level"]
    ]
    differences += [
        f"строка уровня {level}" for level, row in plain["levels"].items() if stored.level_rows.get(level, {}) != row
    ]
    differences += [
        f"исключения {key}"
        for key in sorted(set(plain["exceptions"]) | set(stored.exceptions))
        if plain["exceptions"].get(key, {}) != stored.exceptions.get(key, {})
    ]
    return differences


# ── Text ─────────────────────────────────────────────────────────────────────────────────────────────


def _cells_text(row: Mapping[str, str]) -> str:
    if not row:
        return "—"
    order = list(_SECTION_NAMES)
    ordered = sorted(row.items(), key=lambda item: order.index(item[0]) if item[0] in order else len(order))
    return ", ".join(f"{_SECTION_NAMES.get(section, section)} — {_MODE.get(mode, mode)}" for section, mode in ordered)


def _yes(value: bool) -> str:
    return "да" if value else "нет"


def _names(ids, people: Mapping[UUID, Person]) -> str:
    return ", ".join(sorted(people[user_id].name for user_id in ids)) or "никого"


def render_text(
    snapshot: Snapshot, result: Transfer, checks: Verification, stored: StoredTransfer | None, version: str,
    *, all_gates: bool = False,
) -> str:
    people = {person.id: person for person in snapshot.people}
    lines = ["Перенос ролей в уровни доступа — отчёт «было / стало» (только чтение, база не меняется)"]
    active = sum(person.active for person in snapshot.people)
    lines.append(f"Миграция базы: {version or 'неизвестна'} · людей {len(people)} (активных {active})")
    if stored is None:
        lines.append("Перенос в базе: ещё не применён (миграция 0086)")
    else:
        differences = stored_differences(result, stored)
        lines.append("Перенос в базе: применён, совпадает с расчётом" if not differences
                     else f"Перенос в базе: применён, расходится с расчётом ({len(differences)}): "
                          + "; ".join(differences[:10]))

    lines += ["", "Матрица уровней (строка = пересечение того, что реально могли люди уровня)"]
    for level in ACCESS_LEVELS:
        members = sum(item.access_level == level for item in result.people.values())
        row = "всегда полный доступ" if level in FULL_ACCESS_LEVELS else _cells_text(result.levels[level])
        lines.append(f"  {ACCESS_LEVEL_LABELS[level]} ({members}): {row}")

    lines += ["", "Люди"]
    for check in checks.people:
        person, item = check.person, check.assignment
        if check.admin_mode:
            changed = [gate for gate in check.gates if gate.changed]
            lines.append(f"    в «режиме админа»: {'без изменений' if not changed else f'изменений {len(changed)}'}")
            continue
        state = "" if person.active else " · отключён"
        role = OLD_ROLE_LABELS.get(person.role, person.role)
        lines.append(f"  {person.name} ({person.login}) · {role} → {ACCESS_LEVEL_LABELS[item.access_level]}{state}")
        title = f"«{item.job_title}»" + (" (заполнена по роли)" if item.job_title != person.job_title else "")
        lines.append(
            f"    должность: {title} · монтажник: {_yes(item.shop_worker)} · мастер площадки: {_yes(item.site_master)}"
        )
        lines.append(f"    личные исключения: {_cells_text(result.exceptions.get(person.id, {}))}")
        if person.id in result.archived:
            lines.append(f"    спящие исключения → в аудит: {_cells_text(result.archived[person.id])}")
        if all_gates:
            for gate in check.gates:
                mark = "!" if gate.changed and not gate.planned else "~" if gate.planned else "="
                lines.append(f"    [{mark}] {gate.gate.label}: {_yes(gate.before)} → {_yes(gate.after)}")
            continue
        same = sum(not gate.changed for gate in check.gates)
        summary = f"    шлюзы: без изменений {same} из {len(check.gates)}"
        if check.planned:
            summary += "; по плану (только суперадмин/админ): " + ", ".join(
                ("+ " if gate.after else "− ") + gate.gate.label for gate in check.planned
            )
        lines.append(summary)
        if check.mismatches:
            lines.append("    РАСХОЖДЕНИЯ: " + ", ".join(
                f"{gate.gate.label} ({_yes(gate.before)} → {_yes(gate.after)})" for gate in check.mismatches
            ))

    lines += ["", "Получатели уведомлений"]
    for audience in checks.audiences:
        added = [user_id for user_id in audience.after if user_id not in audience.before]
        removed = [user_id for user_id in audience.before if user_id not in audience.after]
        lines.append(f"  {audience.label}: было — роль «{OLD_ROLE_LABELS.get(audience.key, audience.key)}», "
                     f"станет — {audience.rule}")
        lines.append(f"    было: {_names(audience.before, people)}")
        lines.append(f"    станет: {_names(audience.after, people)}")
        if added or removed:
            lines.append(f"    + {_names(added, people) if added else '—'} · "
                         f"− {_names(removed, people) if removed else '—'}")

    def admin(user_id: UUID) -> str:
        person = people[user_id]
        ways = [way for way, present in (("пароль", person.has_credential), ("PIN", person.has_pin)) if present]
        return f"{person.name} ({person.login}; {', '.join(ways)})"

    lines += ["", "Администраторы, которые смогут войти и вернуть права (активны, есть пароль или PIN)"]
    lines.append(f"  было (роль «Технолог», «Доступы»): {', '.join(map(admin, checks.admins_before)) or 'НИКОГО'}")
    lines.append("  станет (суперадминистратор, администратор): "
                 f"{', '.join(map(admin, checks.admins_after)) or 'НИКОГО'}")

    lines += ["", (
        f"Итог: людей {len(people)}, шлюзов {len({gate.gate.key for row in checks.people for gate in row.gates})}; "
        f"расхождений {len(checks.mismatches)}; изменений по плану {len(checks.planned)}; "
        f"администраторов после переноса {len(checks.admins_after)}."
    )]
    return "\n".join(lines)


def render_json(
    snapshot: Snapshot, result: Transfer, checks: Verification, stored: StoredTransfer | None, version: str,
) -> dict:
    people = {person.id: person for person in snapshot.people}
    return {
        "database_revision": version,
        "stored": None if stored is None else {"differences": stored_differences(result, stored)},
        "levels": result.levels,
        "people": [
            {
                "id": str(check.person.id), "login": check.person.login, "name": check.person.name,
                "active": check.person.active, "admin_mode": check.admin_mode, "role": check.person.role,
                "access_level": check.assignment.access_level, "shop_worker": check.assignment.shop_worker,
                "site_master": check.assignment.site_master, "job_title_before": check.person.job_title,
                "job_title_after": check.assignment.job_title,
                "exceptions": result.exceptions.get(check.person.id, {}),
                "archived": result.archived.get(check.person.id, {}),
                "gates": [
                    {"gate": gate.gate.key, "label": gate.gate.label, "before": gate.before, "after": gate.after,
                     "planned": gate.planned}
                    for gate in check.gates
                ],
            }
            for check in checks.people
        ],
        "audiences": [
            {"key": audience.key, "label": audience.label, "rule": audience.rule,
             "before": [people[user_id].login for user_id in audience.before],
             "after": [people[user_id].login for user_id in audience.after]}
            for audience in checks.audiences
        ],
        "admins_before": [people[user_id].login for user_id in checks.admins_before],
        "admins_after": [people[user_id].login for user_id in checks.admins_after],
        "mismatches": len(checks.mismatches),
        "planned": len(checks.planned),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт «было / стало» перед переносом ролей в уровни доступа.")
    parser.add_argument("--database-url", default=os.getenv("ERP_DATABASE_URL", ""))
    parser.add_argument("--all-gates", action="store_true", help="каждый шлюз каждого человека, а не только изменения")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    args = parser.parse_args(argv)
    if not args.database_url:
        raise SystemExit("Set ERP_DATABASE_URL or pass --database-url")
    snapshot, stored, version = read_only(args.database_url)
    result = transfer(snapshot)
    checks = verify(snapshot, result)
    if args.json:
        output = json.dumps(render_json(snapshot, result, checks, stored, version), ensure_ascii=False, indent=2)
    else:
        output = render_text(snapshot, result, checks, stored, version, all_gates=args.all_gates)
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:  # UTF-8 whatever the console code page (Windows)
        sys.stdout.flush()
        stream.write((output + "\n").encode("utf-8"))
        stream.flush()
    else:
        print(output)
    return 0 if not checks.mismatches and checks.admins_after else 2


if __name__ == "__main__":
    raise SystemExit(main())
