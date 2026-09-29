"""STAFF-IMPORT: the SUPPLYMAP staff snapshot → ERP employees, each with a fresh PIN printed for the owner
(owner ask 2026-09-29 «перенести сотрудников из гугла … все пины мне»)."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.application import login_with_pin
from app.modules.identity.domain import IdentityUser, hash_password, hash_pin, new_pin, normalize_login
from app.modules.identity.import_staff import (
    ADDITIONAL_STAFF,
    ROLE_MAP,
    TEST_EMAILS,
    SourceEmployee,
    all_source_staff,
    import_staff,
    read_source,
    staff_id,
)
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork
from app.shared.persistence import create_schema


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

CONFIG_GS = """
// preamble
var STAFF_BY_EMAIL = {
  'ivan@example.test':   { name: 'Иван Иванов',   dept: 'Цех',  role: 'admin' },
  'petr@example.test':   { name: 'Пётр Петров',   dept: 'Склад' }, // no role → staff fallback
  'sveta@example.test':  { name: 'Света Светова', dept: 'Офис', role: 'master' },
};

var SOMETHING_ELSE = 1;
"""

ACCESS_BACKUP = '{"staff": [' \
    '{"email": "ivan@example.test", "name": "Иван Иванов", "role": "admin"},' \
    '{"email": "petr@example.test", "name": "Пётр Петров", "role": "receiver"}' \
    ']}'


@pytest.fixture
def source(tmp_path: Path) -> Path:
    (tmp_path / "Config.gs").write_text(CONFIG_GS, encoding="utf-8")
    (tmp_path / "_local").mkdir()
    (tmp_path / "_local" / "access-backup-2026-09-01.json").write_text(ACCESS_BACKUP, encoding="utf-8")
    return tmp_path


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _uow(sessions):
    return lambda: SqlAlchemyIdentityUnitOfWork(sessions)


def test_read_source_merges_config_names_with_backup_roles(source):
    people = read_source(source)
    by_email = {person.email: person for person in people}
    # Config carries name + department; the access backup's role wins over the seed's (petr: staff seed → receiver).
    # Names are read as «Фамилия Имя» for the login list (SUPPLYMAP stores «Имя Фамилия»).
    assert by_email["petr@example.test"] == SourceEmployee(
        email="petr@example.test", name="Петров Пётр", department="Склад", role="receiver",
    )
    assert by_email["ivan@example.test"].role == "admin"
    assert by_email["sveta@example.test"].role == "master"  # not in the backup → the seed's role


def test_dry_run_writes_nothing_and_issues_a_pin_each(source, sessions):
    rows = import_staff(read_source(source), _uow(sessions)(), NOW, apply=False, reset_pins=False)
    assert len(rows) == 3
    assert all(row.action == "создан" and row.pin and len(row.pin) == 6 for row in rows)
    with _uow(sessions)() as uow:  # nothing was committed
        assert uow.identity.all_users() == []


def test_apply_creates_everyone_and_login_works_then_is_idempotent(source, sessions):
    people = read_source(source)
    rows = import_staff(people, _uow(sessions)(), NOW, apply=True, reset_pins=False)
    ivan = next(row for row in rows if row.name == "Иванов Иван")

    # Sign in by name (the opaque staff id from /login-staff) + the issued PIN.
    who = str(staff_id("ivan@example.test"))
    result = login_with_pin(who, ivan.pin, NOW, _uow(sessions)(), client_key="k")
    assert result.principal.email == "ivan@example.test"

    # Re-running touches the same rows: nobody is duplicated and PINs are kept.
    again = import_staff(read_source(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    assert all(row.action == "пропущен (уже есть)" and row.pin is None for row in again)
    with _uow(sessions)() as uow:
        assert len(uow.identity.all_users()) == 3


def test_reset_pins_reissues_for_existing(source, sessions):
    import_staff(read_source(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    rows = import_staff(read_source(source), _uow(sessions)(), NOW, apply=True, reset_pins=True)
    assert all(row.action == "PIN перевыдан" and row.pin for row in rows)
    ivan = next(row for row in rows if row.name == "Иванов Иван")
    who = str(staff_id("ivan@example.test"))
    assert login_with_pin(who, ivan.pin, NOW, _uow(sessions)(), client_key="k2").principal.email == "ivan@example.test"


def test_role_map_covers_current_supplymap_roles():
    assert set(ROLE_MAP) >= {"admin", "buyer", "receiver", "prodhead", "master", "management", "staff"}


def test_names_are_read_surname_first_for_the_login_list(source):
    # SUPPLYMAP «Имя Фамилия» → «Фамилия Имя» so the login list sorts by surname (owner 2026-09-29).
    names = {person.email: person.name for person in read_source(source)}
    assert names["ivan@example.test"] == "Иванов Иван"
    assert names["sveta@example.test"] == "Светова Света"


def test_test_account_is_never_created_and_is_deactivated_if_present(sessions):
    test_email = next(iter(TEST_EMAILS))
    person = SourceEmployee(email=test_email, name="Вася Пупкин", department="бог", role="admin")
    # First run: the test account must not become an employee.
    first = import_staff([person], _uow(sessions)(), NOW, apply=True, reset_pins=False)
    assert first[0].action == "тестовый — пропущен" and first[0].pin is None
    with _uow(sessions)() as uow:
        assert uow.identity.all_users() == []

    # If an older run had created it (active), the import deactivates it so it leaves the login list.
    import_staff([replace(person, email=test_email)], _uow(sessions)(), NOW, apply=True, reset_pins=False)  # no-op
    with _uow(sessions)() as uow:
        uow.identity.add_user(
            IdentityUser(id=staff_id(test_email), login=normalize_login(test_email), email=test_email,
                         name="Артемий", department="Руководство", role="management", site=None, active=True),
            hash_password("test-password"), {}, hash_pin(new_pin()),
        )
        uow.commit()
    again = import_staff([person], _uow(sessions)(), NOW, apply=True, reset_pins=False)
    assert again[0].action == "тестовый — отключён"
    with _uow(sessions)() as uow:
        stored = next(user for user in uow.identity.all_users() if user.email == test_email)
    assert stored.active is False


def test_installers_are_added_to_the_import(source):
    # Монтажники (owner 2026-09-29) are not in SUPPLYMAP; the import adds them itself in «Монтаж» — nine at ЦАР and,
    # with the ЖУК participok added, master Степан plus four installers at ЖУК.
    everyone = all_source_staff(source)
    installers = [person for person in everyone if person in ADDITIONAL_STAFF]
    assert len(installers) == 14
    assert {"Рыбкина Ольга", "Шилов Артем", "Пузанов Александр"} <= {person.name for person in installers}
    assert all(person.department == "Монтаж" for person in installers)
    car = [person for person in installers if person.site == "ЦАР"]
    zhuk = [person for person in installers if person.site == "ЖУК"]
    assert len(car) == 9 and all(person.role == "staff" for person in car)
    assert {person.name for person in zhuk} == {"Степан", "Зингер Антон", "Наумов Александр", "Евсеев Антон", "Безменов Никита"}
    stepan = next(person for person in zhuk if person.name == "Степан")
    assert stepan.role == "master"  # Степан distributes on the ЖУК board
    assert all(person.role == "staff" for person in zhuk if person.name != "Степан")
    # They join the SUPPLYMAP directory rather than replacing it.
    assert len(everyone) == len(read_source(source)) + 14


def test_installers_get_a_pin_and_can_log_in(source, sessions):
    rows = import_staff(all_source_staff(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    olga = next(row for row in rows if row.name == "Рыбкина Ольга")
    assert olga.pin and olga.action == "создан"
    who = str(staff_id("rybkina.olga@fraxis.local"))
    assert login_with_pin(who, olga.pin, NOW, _uow(sessions)(), client_key="k").principal.email == "rybkina.olga@fraxis.local"


def test_created_employees_carry_their_site(source, sessions):
    import_staff(all_source_staff(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    with _uow(sessions)() as uow:
        by_email = {user.email: user for user in uow.identity.all_users()}
    assert by_email["rybkina.olga@fraxis.local"].site == "ЦАР"
    assert by_email["stepan.zhuk@fraxis.local"].site == "ЖУК" and by_email["stepan.zhuk@fraxis.local"].role == "master"
    assert by_email["zinger.anton@fraxis.local"].site == "ЖУК"
    # A full-access SUPPLYMAP role spans sites — no participok.
    assert by_email["ivan@example.test"].site is None


def test_reimport_updates_department_and_site_but_keeps_pin(source, sessions):
    # First run: an installer created in the old «Производство»/no-site shape (as Task 2 imported them).
    old = [SourceEmployee(email="rybkina.olga@fraxis.local", name="Рыбкина Ольга", department="Производство", role="staff")]
    rows = import_staff(old, _uow(sessions)(), NOW, apply=True, reset_pins=False)
    olga_pin = next(row for row in rows if row.name == "Рыбкина Ольга").pin

    again = import_staff(all_source_staff(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    olga = next(row for row in again if row.name == "Рыбкина Ольга")
    assert olga.action == "обновлён (имя/отдел/площадка)" and olga.pin is None
    with _uow(sessions)() as uow:
        stored = next(user for user in uow.identity.all_users() if user.email == "rybkina.olga@fraxis.local")
    assert stored.department == "Монтаж" and stored.site == "ЦАР"
    # PIN untouched: the first one still opens the door.
    who = str(staff_id("rybkina.olga@fraxis.local"))
    assert login_with_pin(who, olga_pin, NOW, _uow(sessions)(), client_key="k").principal.email == "rybkina.olga@fraxis.local"

    # A third run with nothing changed leaves the row alone.
    third = import_staff(all_source_staff(source), _uow(sessions)(), NOW, apply=True, reset_pins=False)
    assert next(row for row in third if row.name == "Рыбкина Ольга").action == "пропущен (уже есть)"
