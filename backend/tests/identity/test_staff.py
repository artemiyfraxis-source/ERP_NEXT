"""ID-8 «Сотрудники»: the administrator creates / edits employees and keeps the department list
(SUPPLYMAP Staff.gs portalStaffGet / portalStaffSave / staffDepts_)."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import (
    ROLE_DEFAULTS,
    ROLE_SEED,
    IdentityUser,
    IdentityValidationError,
    department_is_role_like,
    department_list,
    hash_password,
    normalize_email,
    normalize_site,
)
from app.modules.identity.infrastructure import (
    IdentityRoleAccessRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_role_matrix,
)
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 10, 0)
ADMIN_ID = UUID("30000000-0000-0000-0000-000000000001")
BUYER_ID = UUID("30000000-0000-0000-0000-000000000002")


# ── Domain ─────────────────────────────────────────────────────────────────────────────────────────


def test_department_list_is_union_without_roles_duplicates_and_blanks():
    saved = ["Цех", "  Склад ", "", "Кладовщик"]
    in_use = ["цех", "Администратор", "Ёлочный", "Снабжение", "admin"]
    assert department_list(saved, in_use) == ["Ёлочный", "Склад", "Снабжение", "Цех"]


@pytest.mark.parametrize("value", ["Технолог", "технолог", "receiver", "Приёмка/склад", "Закупщик", "Сотрудник"])
def test_role_labels_and_keys_are_not_departments(value):
    assert department_is_role_like(value) is True


def test_real_department_is_not_role_like():
    assert department_is_role_like("Отдел снабжения") is False
    assert department_is_role_like("") is False


def test_email_and_site_are_checked():
    assert normalize_email("  Ivanov@Example.TEST ") == "ivanov@example.test"
    with pytest.raises(IdentityValidationError):
        normalize_email("not-an-email")
    assert normalize_site(" жук ") == "ЖУК"
    assert normalize_site("") is None
    with pytest.raises(IdentityValidationError):
        normalize_site("МСК")


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────────


def _user(user_id: UUID, login: str, role: str, department: str) -> IdentityUser:
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=login.title(), department=department,
        role=role, site=None, active=True,
    )


@pytest.fixture()
def staff_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(session, _user(ADMIN_ID, "admin", "admin", "ИТ"), hash_password("admin-password"),
                       role_access=ROLE_DEFAULTS["admin"])
        provision_user(session, _user(BUYER_ID, "buyer", "buyer", "Снабжение"), hash_password("buyer-password"),
                       role_access=ROLE_SEED["buyer"])
        seed_role_matrix(session)  # a migrated database: every role has its explicit cells (0046)
        session.commit()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield sessions
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("identity_uow_factory", "cookie_secure", "identity_clock"):
        delattr(app.state, name)
    engine.dispose()


def _client(login: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = client.cookies.get("erp_csrf")
    return client


def _events(sessions, event_type: str) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)))


NEW = {
    "email": "Sidorov@Example.test", "name": " Сидоров С. С. ", "department": "Цех", "role": "master",
    "site": "жук", "password": "first-password",
}


def test_only_admin_opens_the_directory(staff_api):
    assert _client("buyer", "buyer-password").get("/api/auth/staff").status_code == 403
    assert TestClient(app).get("/api/auth/staff").status_code == 401

    body = _client("admin", "admin-password").get("/api/auth/staff").json()
    assert [user["login"] for user in body["users"]] == ["admin", "buyer"]
    assert body["departments"] == ["ИТ", "Снабжение"]
    assert {"key": "admin", "label": "Технолог"} in body["roles"]
    assert [site["code"] for site in body["sites"]] == ["ЦАР", "ЖУК", "АДМ"]
    assert all("password" not in json.dumps(user) for user in body["users"])


def test_admin_creates_an_employee_who_can_log_in(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.post("/api/auth/staff", json=NEW)

    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert user["login"] == "sidorov@example.test"  # empty login → the e-mail
    assert (user["name"], user["department"], user["role"], user["site"], user["active"]) == (
        "Сидоров С. С.", "Цех", "master", "ЖУК", True,
    )
    assert "first-password" not in response.text
    worker = _client("sidorov@example.test", "first-password")
    assert worker.get("/api/auth/me").json()["permissions"]["production"] == "edit"
    [event] = _events(staff_api, "identity.user_created")
    assert event.actor_id == ADMIN_ID and event.details["role"] == "master"
    assert "first-password" not in json.dumps(event.details)
    with staff_api() as session:
        # The role's sections come from its matrix row; creating a person writes nothing into it.
        assert session.get(IdentityRoleAccessRow, ("master", "production")).mode == "edit"


def test_create_issues_and_returns_a_pin_when_the_field_is_blank(staff_api):
    # Owner 2026-09-29 «все пины мне … чтобы я мог заводить новых сам»: a blank PIN is generated and shown once.
    admin = _client("admin", "admin-password")
    body = {"email": "novichok@example.test", "name": "Новичок", "department": "Цех", "role": "staff"}
    response = admin.post("/api/auth/staff", json=body)

    assert response.status_code == 201, response.text
    payload = response.json()
    pin = payload["pin"]
    assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"
    assert payload["user"]["pin_set"] is True
    # The employee can sign in by name (their id) + the shown PIN right away — no first-login reveal step.
    who = payload["user"]["id"]
    assert TestClient(app).post("/api/auth/pin-login", json={"who": who, "pin": pin}).status_code == 200


def test_create_returns_the_pin_the_admin_typed(staff_api):
    admin = _client("admin", "admin-password")
    body = {"email": "svoi@example.test", "name": "Свой", "department": "Цех", "role": "staff", "pin": "246802"}
    payload = admin.post("/api/auth/staff", json=body).json()
    assert payload["pin"] == "246802"


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"pin": "12a456"}, 422),
        ({"pin": "12345"}, 422),
        ({"email": "buyer@example.test"}, 409),
        ({"login": "buyer"}, 409),
        ({"login": "admin@example.test"}, 409),  # login and e-mail share one namespace
        ({"role": "boss"}, 422),
        ({"site": "МСК"}, 422),
        ({"email": "без-почты"}, 422),
        ({"password": "short"}, 422),
        ({"name": "   "}, 422),
    ],
)
def test_create_refuses_bad_or_duplicate_rows(staff_api, change, status):
    response = _client("admin", "admin-password").post("/api/auth/staff", json={**NEW, **change})
    assert response.status_code == status, response.text
    assert _events(staff_api, "identity.user_created") == []


def test_create_is_refused_to_non_admin_and_without_csrf(staff_api):
    assert _client("buyer", "buyer-password").post("/api/auth/staff", json=NEW).status_code == 403
    admin = _client("admin", "admin-password")
    del admin.headers["X-CSRF-Token"]
    assert admin.post("/api/auth/staff", json=NEW).status_code == 403


def _edit(**change) -> dict:
    return {"name": "Buyer", "department": "Снабжение", "role": "buyer", "site": "", "active": True, **change}


def test_admin_edits_role_department_and_site(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(role="receiver", department="Склад", site="ЦАР"))

    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == "receiver"
    buyer = _client("buyer", "buyer-password")
    assert buyer.get("/api/auth/me").json()["permissions"].get("warehouse") == "edit"
    [event] = _events(staff_api, "identity.user_updated")
    assert json.loads(event.details["changes"]) == {
        "department": ["Снабжение", "Склад"], "role": ["buyer", "receiver"], "site": ["", "ЦАР"],
    }
    assert "password" not in event.details


def test_an_unchanged_row_writes_no_audit(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit()).status_code == 200
    assert _events(staff_api, "identity.user_updated") == []


def test_new_password_revokes_sessions_and_is_never_echoed(staff_api):
    buyer = _client("buyer", "buyer-password")
    admin = _client("admin", "admin-password")
    response = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(password="second-password"))

    assert response.status_code == 200 and "second-password" not in response.text
    assert buyer.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"login": "buyer", "password": "buyer-password"}).status_code == 401
    _client("buyer", "second-password")
    [event] = _events(staff_api, "identity.user_updated")
    assert event.details["password"] == "сменён" and "second-password" not in json.dumps(event.details)


def test_deactivation_blocks_login_and_reactivation_restores_it(staff_api):
    buyer = _client("buyer", "buyer-password")
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(active=False)).status_code == 200
    assert buyer.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"login": "buyer", "password": "buyer-password"}).status_code == 401

    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(active=True)).status_code == 200
    _client("buyer", "buyer-password")


@pytest.mark.parametrize("change", [{"role": "buyer"}, {"active": False}])
def test_last_admin_cannot_lose_the_role_or_the_login(staff_api, change):
    admin = _client("admin", "admin-password")
    body = {"name": "Admin", "department": "ИТ", "role": "admin", "site": "", "active": True, **change}
    response = admin.put(f"/api/auth/staff/{ADMIN_ID}", json=body)
    assert response.status_code == 409
    assert "хотя бы один администратор" in response.json()["detail"]
    assert admin.post(f"/api/auth/users/{ADMIN_ID}/deactivate").status_code == 409

    # With a second admin the first one may step down.
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(role="admin")).status_code == 200
    assert admin.put(f"/api/auth/staff/{ADMIN_ID}", json=body).status_code == 200


def test_edit_of_unknown_employee_is_404(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put("/api/auth/staff/30000000-0000-0000-0000-00000000dead", json=_edit())
    assert response.status_code == 404


def test_department_list_keeps_departments_in_use_and_drops_roles(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put("/api/auth/departments", json={"departments": ["Бухгалтерия", "Кладовщик", "ОТК", "цех", "Цех", ""]})

    assert response.status_code == 200, response.text
    # «Снабжение» and «ИТ» stay: people have them. Role labels («Кладовщик», «ОТК») are not departments.
    assert response.json()["departments"] == ["Бухгалтерия", "ИТ", "Снабжение", "цех"]
    assert admin.get("/api/auth/staff").json()["departments"] == ["Бухгалтерия", "ИТ", "Снабжение", "цех"]
    [event] = _events(staff_api, "identity.departments_saved")
    assert event.actor_id == ADMIN_ID
    assert _client("buyer", "buyer-password").put("/api/auth/departments", json={"departments": []}).status_code == 403


def test_department_rename_moves_employees(staff_api):
    admin = _client("admin", "admin-password")
    admin.put("/api/auth/departments", json={"departments": ["Закупки-старое"]})
    response = admin.post("/api/auth/departments/rename", json={"source": "снабжение", "target": "Отдел снабжения"})

    assert response.status_code == 200, response.text
    assert response.json() == {"departments": ["Закупки-старое", "ИТ", "Отдел снабжения"], "users": 1}
    users = {user["login"]: user for user in admin.get("/api/auth/staff").json()["users"]}
    assert users["buyer"]["department"] == "Отдел снабжения"
    [event] = _events(staff_api, "identity.department_renamed")
    assert event.details == {"source": "снабжение", "target": "Отдел снабжения", "users": "1"}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"source": "Нет такого", "target": "Новый"}, 404),
        ({"source": "Снабжение", "target": "Кладовщик"}, 422),
        ({"source": "Снабжение", "target": "Снабжение"}, 422),
    ],
)
def test_department_rename_refusals(staff_api, payload, status):
    response = _client("admin", "admin-password").post("/api/auth/departments/rename", json=payload)
    assert response.status_code == status, response.text


# ── Migration ──────────────────────────────────────────────────────────────────────────────────────


def test_departments_migration_adds_an_empty_list_and_downgrades(tmp_path):
    database_path = tmp_path / "departments.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0036_identity_departments")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM identity_departments").fetchone() == (0,)
        connection.execute("INSERT INTO identity_departments (position, name) VALUES (0, 'Цех')")

    command.downgrade(config, "0035_procurement_replacement")
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "identity_departments" not in tables
    assert "identity_users" in tables
