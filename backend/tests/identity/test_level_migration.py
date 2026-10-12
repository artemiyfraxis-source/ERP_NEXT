"""Уровни доступа вместо ролей (docs/migration/access-levels-plan.md, T1): the transfer keeps every route gate exactly
as it was for every person — only the administrator's tools move to superadmin/admin, as the plan says — the frozen
copy in migration 0086 computes the same, and the report CLI reads without writing."""

from __future__ import annotations

import importlib.util
import json
import random
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import identity_level_report
from app.modules.identity import domain
from app.modules.identity.domain import (
    ADMIN_MODE_EMAIL,
    LEVEL_SECTION_LABELS,
    LEVEL_SEED,
    ROLE_SEED,
    IdentityUser,
    hash_password,
)
from app.modules.identity.infrastructure import IdentityUserRow, SqlAlchemyIdentityRepository, provision_user
from app.modules.identity.level_migration import (
    NOTIFICATION_AUDIENCES,
    OLD_FULL_ACCESS_ROLES,
    OLD_ROLE_LABELS,
    OLD_SECTIONS,
    TRANSFER_SECTIONS,
    ROLE_TO_LEVEL,
    Person,
    Snapshot,
    old_permissions,
    person_permissions_after,
    transfer,
    usable,
    verify,
)
from app.shared.persistence import create_schema

BACKEND = Path(__file__).parents[2]
MIGRATION = BACKEND / "migrations" / "versions" / "0086_identity_access_levels.py"
ADMIN_TOOLS = {
    "tctemplates_admin", "warehouse_admin", "warehouse_journal", "fulfillment_admin", "references_import", "staffdir",
    "staffdir_view",
}


def _id(number: int) -> UUID:
    return UUID(int=number)


# Every old role, plus the cases the transfer must get right: dormant exceptions of full-access roles (admin, buyer),
# an edit a role list blocks (a master with `warehouse = edit`), an inactive member with less than the rest, the
# «Режим админа» account, installers by site and by department, a job title already filled.
PEOPLE = (
    Person(_id(1), "tech", "Технолог Т.", "tech@x.test", "Технология", "admin"),
    Person(_id(2), "gd", "Директор Г.", "gd@x.test", "Дирекция", "management", job_title="Генеральный директор"),
    Person(_id(3), "buyer", "Закупщик В.", ADMIN_MODE_EMAIL, "Снабжение", "buyer"),
    Person(_id(4), "prodhead", "Начальник П.", "prodhead@x.test", "Производство", "prodhead"),
    Person(_id(5), "engineer", "Инженер И.", "engineer@x.test", "Технология", "engineer"),
    Person(_id(6), "pm", "Менеджер М.", "pm@x.test", "Продажи", "pm"),
    Person(_id(7), "pm-old", "Менеджер Старый", "pm-old@x.test", "Продажи", "pm", active=False),
    Person(_id(8), "estimator", "Сметчик С.", "estimator@x.test", "Продажи", "estimator"),
    Person(_id(9), "master", "Мастер ЦАР", "master@x.test", "Производство", "master", "ЦАР"),
    Person(_id(10), "master-zhuk", "Мастер ЖУК", "master2@x.test", "Производство", "master", "ЖУК"),
    Person(_id(11), "receiver", "Кладовщик К.", "receiver@x.test", "Склад", "receiver"),
    Person(_id(12), "qc", "Контролёр О.", "qc@x.test", "ОТК", "qc"),
    Person(_id(13), "fitter", "Монтажник Ц.", "fitter@x.test", "Производство", "staff", "ЦАР", has_pin=True),
    Person(_id(14), "office", "Офис О.", "office@x.test", "Офис", "staff"),
    Person(_id(15), "montage", "Монтажник М.", "montage@x.test", "Монтажный участок", "staff"),
    Person(_id(16), "fitter-plus", "Монтажник Плюс", "plus@x.test", "Производство", "staff", "ЖУК"),
)
ROLE_MATRIX = {
    **{role: {section: mode.value for section, mode in row.items()} for role, row in ROLE_SEED.items()},
    "admin": {"operations": "view"},
}
USER_ACCESS = {
    _id(1): {"operations": "none"},  # dormant: a full-access role ignores it
    _id(3): {"warehouse": "none", "staffdir": "none"},  # dormant
    _id(7): {"erpref": "none"},  # the inactive manager had less than the active ones
    _id(10): {"warehouse": "edit"},  # warehouse_edit is receiver/admin only: the edit was never usable
    _id(16): {"production": "edit"},  # staff passes production_view only
}
SNAPSHOT = Snapshot(people=PEOPLE, role_matrix=ROLE_MATRIX, user_access=USER_ACCESS)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0086", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen(snapshot: Snapshot) -> dict:
    people = [
        {"id": person.id, "role": person.role, "site": person.site, "department": person.department,
         "active": person.active, "job_title": person.job_title}
        for person in snapshot.people
    ]
    return _migration().compute(people, snapshot.role_matrix, snapshot.user_access)


# ── The transfer ───────────────────────────────────────────────────────────────────────────────────


def test_no_gate_changes_for_anyone_except_the_planned_admin_tools():
    checks = verify(SNAPSHOT, transfer(SNAPSHOT))
    assert checks.mismatches == ()
    planned = {(row.person.login, row.admin_mode, check.gate.key, check.after) for row, check in checks.planned}
    # The plan: only superadmin/admin see «Доступы», «Сотрудники» and the admin tools. The general director (now
    # superadmin) gains «Доступы»; the former full-access buyer and head of production (now tech_lead) lose the tools.
    assert planned == (
        {("gd", False, "access_admin", True)}
        | {(login, False, gate, False) for login in ("buyer", "prodhead") for gate in ADMIN_TOOLS}
    )
    # «Режим админа» stays full: the role admin before, the level admin after.
    lifted = [row for row in checks.people if row.admin_mode]
    assert [row.person.login for row in lifted] == ["buyer"]
    assert all(check.before and check.after for check in lifted[0].gates)


def test_levels_flags_and_job_titles():
    result = transfer(SNAPSHOT)
    levels = {person.login: result.people[person.id].access_level for person in PEOPLE}
    assert levels == {
        "tech": "admin", "gd": "superadmin", "buyer": "tech_lead", "prodhead": "tech_lead", "engineer": "tech_lead",
        "pm": "commercial", "pm-old": "commercial", "estimator": "commercial", "master": "foreman",
        "master-zhuk": "foreman", "receiver": "foreman", "qc": "foreman", "fitter": "operator", "office": "operator",
        "montage": "operator", "fitter-plus": "operator",
    }
    by_login = {person.login: result.people[person.id] for person in PEOPLE}
    assert {login for login, item in by_login.items() if item.shop_worker} == {"fitter", "montage", "fitter-plus"}
    assert {login for login, item in by_login.items() if item.site_master} == {"master", "master-zhuk"}
    assert by_login["tech"].job_title == "Технолог"
    assert by_login["gd"].job_title == "Генеральный директор"  # filled before: kept
    assert by_login["fitter"].job_title == "Сотрудник"
    assert all(item.legacy_role == person.role for person, item in zip(PEOPLE, by_login.values()))


def test_level_row_is_the_intersection_and_exceptions_restore_each_person_exactly():
    result = transfer(SNAPSHOT)
    for person in PEOPLE:
        level = result.people[person.id].access_level
        assert person_permissions_after(result, person.id) == (
            {section: "edit" for section in TRANSFER_SECTIONS} if level in ("superadmin", "admin")
            else result.usable[person.id]
        ), person.login
    # Masters, the storekeeper and the controller share only filing and the reference book.
    assert result.levels["foreman"] == {"newrequest": "edit", "myrequests": "edit", "erpref": "view"}
    by_login = {person.login: person.id for person in PEOPLE}
    assert result.exceptions[by_login["qc"]] == {"production": "view", "myops": "edit", "quality": "edit"}
    assert result.exceptions[by_login["receiver"]]["shipping_goods"] == "edit"
    assert result.exceptions[by_login["receiver"]]["shipping"] == "view"
    # The master whose `warehouse = edit` was blocked by the route keeps exactly what he could use: view.
    assert result.usable[by_login["master-zhuk"]]["warehouse"] == "view"
    assert result.exceptions[by_login["master-zhuk"]] == result.exceptions[by_login["master"]]
    # The former full-access roles keep everything through exceptions (the plan's risk: shown by the report).
    assert result.exceptions[by_login["buyer"]]["procurement"] == "edit"
    assert result.exceptions[by_login["prodhead"]]["shipping"] == "edit"
    everything = {section: "edit" for section in TRANSFER_SECTIONS}
    assert result.levels["superadmin"] == result.levels["admin"] == everything
    assert result.exceptions[by_login["tech"]] == result.exceptions[by_login["gd"]] == {}


def test_dormant_exceptions_are_archived_not_activated():
    result = transfer(SNAPSHOT)
    assert result.archived == {_id(1): {"operations": "none"}, _id(3): {"staffdir": "none", "warehouse": "none"}}
    assert person_permissions_after(result, _id(3))["warehouse"] == "edit"


def test_an_inactive_member_does_not_narrow_the_row_and_keeps_his_own_access():
    result = transfer(SNAPSHOT)
    assert result.levels["commercial"] == {"newrequest": "edit", "myrequests": "edit", "erpref": "view"}
    assert result.exceptions[_id(7)] == {"erpref": "none", "shipping": "edit"}
    assert "erpref" not in person_permissions_after(result, _id(7))


def test_an_empty_level_starts_from_the_plan_default():
    snapshot = Snapshot(people=PEOPLE[:2], role_matrix=ROLE_MATRIX, user_access={})
    result = transfer(snapshot)
    for level in ("commercial", "tech_lead", "foreman", "operator"):
        assert result.levels[level] == {section: mode.value for section, mode in LEVEL_SEED[level].items()}


def test_notification_audiences_and_administrators():
    checks = verify(SNAPSHOT, transfer(SNAPSHOT))
    logins = {person.id: person.login for person in PEOPLE}
    audiences = {
        audience.key: ({logins[i] for i in audience.before}, {logins[i] for i in audience.after})
        for audience in checks.audiences
    }
    # The administrators (tech, gd) are full everywhere but join a section's audience only by a personal exception, so
    # the section audiences hold the old role's people plus the former full-access buyer and head of production, whose
    # compensating exceptions give them the edit (the risk the plan names).
    assert audiences == {
        "admin": ({"tech"}, {"tech", "gd"}),
        "buyer": ({"buyer"}, {"buyer", "prodhead"}),
        "pm": ({"pm"}, {"buyer", "prodhead", "pm"}),
        "receiver": ({"receiver"}, {"buyer", "prodhead", "receiver"}),
    }
    assert {logins[i] for i in checks.admins_before} == {"tech"}
    assert {logins[i] for i in checks.admins_after} == {"tech", "gd"}


def test_a_matrix_exception_changes_a_gate_only_where_a_matrix_only_route_reads_a_role_listed_section():
    """Exhaustive: every non-full role × every old section × every mode as a personal exception. Exact everywhere,
    except a route that reads a role-listed section straight off the matrix (project tiles read `production`, the
    supplier maps read `procurement`) for a person the section's role lists shut out: «matrix ∩ role list» wins."""
    edge_gates = {"kitset_tiles", "references_maps_view", "references_maps_edit", "references_suppliers"}
    found = set()
    for role in sorted(set(ROLE_TO_LEVEL) - OLD_FULL_ACCESS_ROLES):
        for section in OLD_SECTIONS:
            for mode in ("none", "view", "edit"):
                person = Person(_id(100), "p", "P", role=role)
                snapshot = Snapshot((person,), ROLE_MATRIX, {person.id: {section: mode}})
                for row, check in verify(snapshot, transfer(snapshot)).mismatches:
                    assert check.gate.key in edge_gates, (role, section, mode, check.gate.key)
                    assert section in ("production", "procurement"), (role, section, mode)
                    found.add((role, section, mode, check.gate.key))
    assert ("pm", "procurement", "edit", "references_suppliers") in found


# ── The frozen copy in the migration ───────────────────────────────────────────────────────────────


def _random_snapshot(seed: int) -> Snapshot:
    rng = random.Random(seed)
    roles = sorted(ROLE_TO_LEVEL) + ["unknown"]
    sections = list(OLD_SECTIONS) + ["calculation"]
    modes = ("none", "view", "edit")
    people = tuple(
        Person(
            _id(1000 + index), f"u{index}", f"U{index}", f"u{index}@x.test",
            rng.choice(["Цех", "Монтажный цех", "Офис", ""]), rng.choice(roles), rng.choice([None, "", "ЦАР", "ЖУК"]),
            rng.random() > 0.2, rng.choice(["", "  ", "Инженер-технолог"]),
        )
        for index in range(rng.randint(0, 14))
    )
    matrix = {role: {s: rng.choice(modes) for s in rng.sample(sections, rng.randint(0, 8))} for role in roles}
    access = {
        person.id: {s: rng.choice(modes) for s in rng.sample(sections, rng.randint(1, 4))}
        for person in people if rng.random() < 0.5
    }
    return Snapshot(people, matrix, access)


def test_the_migration_computes_the_same_as_the_module():
    assert _frozen(SNAPSHOT) == transfer(SNAPSHOT).plain()
    for seed in range(300):
        snapshot = _random_snapshot(seed)
        result = transfer(snapshot)
        assert _frozen(snapshot) == result.plain(), seed
        for person in snapshot.people:  # exact for everyone, every time
            if result.people[person.id].access_level not in ("superadmin", "admin"):
                assert person_permissions_after(result, person.id) == result.usable[person.id]


# ── The gates and the legacy keys ───────────────────────────────────────────────────────────────────
# T2 switched the routes to the contract (GATES.after): tests/identity/test_level_gates.py checks every route
# dependency against it. GATES.before stays the frozen old model this transfer and the report are measured with.


def test_frozen_old_model_matches_the_legacy_data():
    from app.shared import notifications
    from app.workflows import owner_alerts

    # The role column and the role tables are still stored (legacy, one release): same keys, labels and sections.
    assert OLD_SECTIONS == tuple(domain.SECTION_LABELS)
    assert OLD_ROLE_LABELS == domain.ROLE_LABELS
    assert set(ROLE_TO_LEVEL) == domain.ROLES
    # The role keys modules addressed before audiences: the old outbox rows still carry them.
    keys = {key for key, _label, _rule in NOTIFICATION_AUDIENCES}
    assert keys == set(notifications.LEGACY_ROLE_AUDIENCES) == {"admin", "buyer", "pm", "receiver"}
    assert set(notifications.LEGACY_ROLE_AUDIENCES.values()) == notifications.AUDIENCES
    assert set(owner_alerts.OWNER_AUDIENCES) | set(owner_alerts.DISPOSAL_AUDIENCES) <= notifications.AUDIENCES
    new_sections = {"myops", "quality", "shipping_goods"}
    assert set(TRANSFER_SECTIONS) == (set(domain.SECTION_LABELS) - {"staffdir"}) | new_sections
    assert set(LEVEL_SECTION_LABELS) == set(TRANSFER_SECTIONS) | {"hr"}


# ── Migration 0086 on SQLite ───────────────────────────────────────────────────────────────────────


def _config(database_path: Path) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _database_at_0085(tmp_path: Path) -> tuple[Path, Config]:
    database_path = tmp_path / "levels.db"
    config = _config(database_path)
    command.upgrade(config, "0085_identity_personnel_fields")
    with sqlite3.connect(database_path) as connection:
        for person in PEOPLE:
            connection.execute(
                "INSERT INTO identity_users (id, login, email, name, department, role, site, active, job_title) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (person.id.hex, person.login, person.email, person.name, person.department, person.role,
                 person.site, int(person.active), person.job_title),
            )
            connection.execute(
                "INSERT INTO identity_credentials (user_id, password_hash, pin_hash) VALUES (?, 'x', ?)",
                (person.id.hex, "pin" if person.has_pin else None),
            )
        connection.execute("DELETE FROM identity_role_access")
        connection.executemany("INSERT INTO identity_role_access VALUES (?, ?, ?)", [
            (role, section, mode) for role, row in ROLE_MATRIX.items() for section, mode in row.items()
        ])
        connection.executemany("INSERT INTO identity_user_access VALUES (?, ?, ?)", [
            (user_id.hex, section, mode) for user_id, row in USER_ACCESS.items() for section, mode in row.items()
        ])
    return database_path, config


def _stored(database_path: Path) -> dict:
    with sqlite3.connect(database_path) as connection:
        people = {
            str(UUID(user_id)): {
                "access_level": level, "shop_worker": bool(shop), "site_master": bool(master), "legacy_role": legacy,
                "job_title": title,
            }
            for user_id, level, shop, master, legacy, title in connection.execute(
                "SELECT id, access_level, shop_worker, site_master, legacy_role, job_title FROM identity_users"
            )
        }
        levels: dict = {}
        for level, section, mode in connection.execute("SELECT level, section, mode FROM identity_level_access"):
            levels.setdefault(level, {})[section] = mode
        exceptions: dict = {}
        for user_id, section, mode in connection.execute(
            "SELECT user_id, section, mode FROM identity_user_level_access"
        ):
            exceptions.setdefault(str(UUID(user_id)), {})[section] = mode
        archived = {}
        for aggregate_id, details in connection.execute(
            "SELECT aggregate_id, details FROM audit_events WHERE event_type = 'identity.dormant_access_archived'"
        ):
            archived[str(UUID(aggregate_id))] = json.loads(json.loads(details)["exceptions"])
    return {"people": people, "levels": levels, "exceptions": exceptions, "archived": archived}


def _audit_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return connection.execute("SELECT count(*) FROM audit_events").fetchone()[0]


def test_migration_stores_the_transfer_runs_once_and_downgrades(tmp_path):
    database_path, config = _database_at_0085(tmp_path)

    command.upgrade(config, "0086_identity_access_levels")
    assert _stored(database_path) == transfer(SNAPSHOT).plain()
    with sqlite3.connect(database_path) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(identity_users)")}
        assert columns["access_level"][3] == 1  # NOT NULL after the backfill
        summary = connection.execute(
            "SELECT count(*) FROM audit_events WHERE event_type = 'identity.access_levels_migrated'"
        ).fetchone()[0]
    assert summary == 1
    assert _audit_count(database_path) == 3  # two archived dormant sets + the summary

    # A second run changes nothing: no rows, no audit.
    before = _stored(database_path)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        assert _migration().transfer(connection) == 0
    engine.dispose()
    assert _stored(database_path) == before
    assert _audit_count(database_path) == 3

    command.downgrade(config, "0085_identity_personnel_fields")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(identity_users)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"access_level", "shop_worker", "site_master", "legacy_role"} & columns
        assert not {"identity_level_access", "identity_user_level_access"} & tables
        assert connection.execute("SELECT count(*) FROM identity_user_access").fetchone() == (6,)
        assert connection.execute("SELECT role FROM identity_users WHERE login = 'buyer'").fetchone() == ("buyer",)
        # The job titles filled in stay: ordinary values of the 0085 column.
        assert connection.execute("SELECT job_title FROM identity_users WHERE login = 'tech'").fetchone() == (
            "Технолог",
        )

    command.upgrade(config, "0086_identity_access_levels")  # again after a downgrade: the same result
    assert _stored(database_path) == transfer(SNAPSHOT).plain()


def test_migration_on_an_empty_database_seeds_the_plan_defaults(tmp_path):
    database_path = tmp_path / "empty.db"
    command.upgrade(_config(database_path), "0086_identity_access_levels")
    stored = _stored(database_path)
    assert stored["people"] == {} and stored["exceptions"] == {}
    assert _audit_count(database_path) == 0  # nothing to record without people
    for level in ("commercial", "tech_lead", "foreman", "operator"):
        assert stored["levels"][level] == {section: mode.value for section, mode in LEVEL_SEED[level].items()}


# ── The report CLI ─────────────────────────────────────────────────────────────────────────────────


def _dump(database_path: Path) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        return list(connection.iterdump())


def test_report_reads_without_writing_before_and_after_the_migration(tmp_path, capsys):
    database_path, config = _database_at_0085(tmp_path)
    url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    before = _dump(database_path)

    assert identity_level_report.main(["--database-url", url]) == 0
    text = capsys.readouterr().out
    assert _dump(database_path) == before
    assert "Перенос в базе: ещё не применён" in text
    assert "Закупщик В. (buyer) · Специалист по снабжению → Технический руководитель" in text
    assert "спящие исключения → в аудит: Склад — закрыт, Сотрудники — закрыт" in text
    assert "− Сотрудники" in text and "+ Доступы" in text
    assert "в «режиме админа»: без изменений" in text
    assert "РАСХОЖДЕНИЯ" not in text
    assert "станет (суперадминистратор, администратор): Директор Г. (gd; пароль), Технолог Т. (tech; пароль)" in text
    assert "расхождений 0; изменений по плану 15; администраторов после переноса 2." in text

    assert identity_level_report.main(["--database-url", url, "--all-gates"]) == 0
    assert "[~] Сотрудники: да → нет" in capsys.readouterr().out

    command.upgrade(config, "0086_identity_access_levels")
    after = _dump(database_path)
    assert identity_level_report.main(["--database-url", url, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert _dump(database_path) == after
    assert report["stored"] == {"differences": []}
    assert report["mismatches"] == 0 and report["planned"] == 15
    assert sorted(report["admins_after"]) == ["gd", "tech"]
    buyer = next(row for row in report["people"] if row["login"] == "buyer" and not row["admin_mode"])
    assert buyer["access_level"] == "tech_lead" and buyer["archived"] == {"staffdir": "none", "warehouse": "none"}


def test_report_exits_2_when_no_administrator_would_remain(tmp_path, capsys):
    database_path, _config_ = _database_at_0085(tmp_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE identity_users SET active = 0 WHERE role IN ('admin', 'management')")
    assert identity_level_report.main(["--database-url", f"sqlite+pysqlite:///{database_path.as_posix()}"]) == 2
    assert "станет (суперадминистратор, администратор): НИКОГО" in capsys.readouterr().out


# ── An employee written without a level follows the legacy role; one written with a level keeps it ──


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def test_a_new_or_re_roled_employee_without_a_level_gets_the_level_of_the_role(sessions):
    master = IdentityUser(_id(1), "m", "m@x.test", "Мастер", "Производство", "master", "ЦАР", True)
    with sessions() as session:
        provision_user(session, master, hash_password("password-123"))
        session.add(IdentityUserRow(  # an ORM row without the level, as test fixtures write them
            id=_id(2), login="f", email="f@x.test", name="Монтажник", department="Монтаж", role="staff", active=True,
        ))
        session.commit()
    with sessions() as session:
        repository = SqlAlchemyIdentityRepository(session)
        stored = repository.user_for_update(_id(1))
        assert (stored.access_level, stored.site_master, stored.shop_worker) == ("foreman", True, False)
        fitter = repository.user_for_update(_id(2))
        assert (fitter.access_level, fitter.site_master, fitter.shop_worker) == ("operator", False, True)
        repository.update_user(replace(stored, role="staff", access_level=""))  # an old caller: the role only
        repository.update_user(replace(fitter, role="master"))  # carries its level: the level stays
        session.commit()
    with sessions() as session:
        row = session.scalar(select(IdentityUserRow).where(IdentityUserRow.id == _id(1)))
        assert (row.access_level, row.site_master, row.shop_worker) == ("operator", False, True)
        row = session.scalar(select(IdentityUserRow).where(IdentityUserRow.id == _id(2)))
        assert (row.access_level, row.role, row.site_master) == ("operator", "master", True)


def test_usable_follows_the_role_lists():
    master = old_permissions("master", ROLE_MATRIX["master"], {"warehouse": "edit", "procurement": "edit"})
    assert usable("master", master)["warehouse"] == "view"
    assert "procurement" not in usable("master", master)
    staff = old_permissions("staff", ROLE_MATRIX["staff"], {})
    assert usable("staff", staff) == {
        "newrequest": "edit", "myrequests": "edit", "requests": "edit", "receiving": "view", "warehouse": "view",
        "myops": "edit", "erpref": "view",
    }
    assert usable("buyer", {}) == {section: "edit" for section in TRANSFER_SECTIONS}
