"""ID-8 «Сотрудники»: the administrator creates / edits employees and keeps the department list
(SUPPLYMAP Staff.gs portalStaffGet / portalStaffSave / staffDepts_)."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import (
    IdentityUser,
    IdentityValidationError,
    check_manager_chain,
    department_is_role_like,
    department_list,
    hash_password,
    normalize_email,
    normalize_job_text,
    normalize_personnel_number,
    normalize_site,
)
from app.modules.identity.infrastructure import (
    IdentityDepartmentRow,
    IdentityLevelAccessRow,
    IdentityUserRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.production.infrastructure import ProductionOperatorSkillRow
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 10, 0)
ADMIN_ID = UUID("30000000-0000-0000-0000-000000000001")  # login «admin»: the superadministrator
BUYER_ID = UUID("30000000-0000-0000-0000-000000000002")  # role buyer → Технический руководитель
RECEIVER_ID = UUID("30000000-0000-0000-0000-000000000003")  # role receiver → Бригадир, for the «refused» cases
ADMIN2_ID = UUID("30000000-0000-0000-0000-000000000004")  # an administrator (not super), added where a test needs one


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
        # The superadministrator (legacy role admin): only a superadmin gives and takes the full levels.
        provision_user(session, replace(_user(ADMIN_ID, "admin", "admin", "ИТ"), access_level="superadmin"),
                       hash_password("admin-password"))
        provision_user(session, _user(BUYER_ID, "buyer", "buyer", "Снабжение"), hash_password("buyer-password"))
        # No department, so it perturbs no department-list assertion; it exists only for the «refused» cases.
        provision_user(session, _user(RECEIVER_ID, "receiver", "receiver", ""), hash_password("recv-password"))
        seed_level_matrix(session)  # a migrated database: every matrix level has its explicit cells (0086)
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


def test_only_superadmin_and_admin_open_the_directory(staff_api):
    # Access levels: «Сотрудники» is superadmin/admin only; the former full-access buyer (now a matrix level) is refused.
    assert _client("receiver", "recv-password").get("/api/auth/staff").status_code == 403
    assert _client("buyer", "buyer-password").get("/api/auth/staff").status_code == 403
    assert TestClient(app).get("/api/auth/staff").status_code == 401

    body = _client("admin", "admin-password").get("/api/auth/staff").json()
    assert [user["login"] for user in body["users"]] == ["admin", "buyer", "receiver"]
    assert [(user["access_level"], user["access_level_label"], user["role"]) for user in body["users"]] == [
        ("superadmin", "Суперадминистратор", "admin"), ("tech_lead", "Технический руководитель", "buyer"),
        ("foreman", "Бригадир", "receiver"),
    ]
    assert body["departments"] == ["ИТ", "Снабжение"]
    assert {"key": "operator", "label": "Оператор", "full": False} in body["levels"]
    assert {"key": "admin", "label": "Технолог"} in body["roles"]  # legacy, one release
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
    assert user["access_level"] == "foreman"  # an old screen sends the role: the level follows it
    assert "first-password" not in response.text
    worker = _client("sidorov@example.test", "first-password")
    assert worker.get("/api/auth/me").json()["permissions"]["production"] == "edit"
    [event] = _events(staff_api, "identity.user_created")
    assert event.actor_id == ADMIN_ID and event.details["role"] == "master"
    assert event.details["access_level"] == "foreman"
    assert "first-password" not in json.dumps(event.details)
    with staff_api() as session:
        # The level's sections come from its matrix row; creating a person writes nothing into it.
        assert session.get(IdentityLevelAccessRow, ("foreman", "production")).mode == "edit"
        stored = session.get(IdentityUserRow, UUID(user["id"]))
        assert (stored.site_master, stored.shop_worker) == (True, False)


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
        ({"access_level": "boss"}, 422),
        ({"access_level": "", "role": ""}, 422),
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
    # Access levels: only superadmin/admin create; the former full-access buyer (a matrix level now) is refused.
    assert _client("buyer", "buyer-password").post("/api/auth/staff", json=NEW).status_code == 403
    assert _client("receiver", "recv-password").post("/api/auth/staff", json=NEW).status_code == 403
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
        "department": ["Снабжение", "Склад"], "role": ["buyer", "receiver"], "access_level": ["tech_lead", "foreman"],
        "site": ["", "ЦАР"],
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


@pytest.mark.parametrize("change", [{"role": "buyer"}, {"access_level": "tech_lead"}, {"active": False}])
def test_last_admin_cannot_lose_the_level_or_the_login(staff_api, change):
    # The only superadmin/admin (here: the superadministrator) keeps the level and the login.
    admin = _client("admin", "admin-password")
    body = {"name": "Admin", "department": "ИТ", "role": "admin", "site": "", "active": True, **change}
    response = admin.put(f"/api/auth/staff/{ADMIN_ID}", json=body)
    assert response.status_code == 409
    assert "хотя бы один активный суперадминистратор или администратор" in response.json()["detail"]
    assert admin.post(f"/api/auth/users/{ADMIN_ID}/deactivate").status_code == 409

    # With a second admin the first one may step down.
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(role="admin")).status_code == 200
    assert admin.put(f"/api/auth/staff/{ADMIN_ID}", json=body).status_code == 200


def test_edit_of_unknown_employee_is_404(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put("/api/auth/staff/30000000-0000-0000-0000-00000000dead", json=_edit())
    assert response.status_code == 404


def _user_row(sessions, user_id: UUID):
    with sessions() as session:
        return session.get(IdentityUserRow, user_id)


def _add_admin(sessions) -> None:
    """A second full-level person: an administrator, not a superadministrator."""
    with sessions() as session:
        provision_user(session, _user(ADMIN2_ID, "admin2", "admin", ""), hash_password("admin2-password"))
        session.commit()


def test_only_a_superadmin_gives_and_takes_the_full_levels(staff_api):
    _add_admin(staff_api)
    admin2 = _client("admin2", "admin2-password")
    boss = _client("admin", "admin-password")

    # The administrator manages the matrix levels…
    created = admin2.post("/api/auth/staff", json={**NEW, "role": "", "access_level": "commercial"})
    assert created.status_code == 201, created.text
    assert (created.json()["user"]["access_level"], created.json()["user"]["role"]) == ("commercial", "")
    assert admin2.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(access_level="foreman")).status_code == 200
    # …but neither gives a full level, nor takes one, nor switches a full-level holder off or deletes them.
    for level in ("admin", "superadmin"):
        body = {"email": f"{level}-new@example.test", "name": "Новый", "access_level": level}
        refused = admin2.post("/api/auth/staff", json=body)
        assert refused.status_code == 403 and "только суперадминистратор" in refused.json()["detail"]
        assert admin2.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(access_level=level)).status_code == 403
    boss_row = {"name": "Admin", "department": "ИТ", "role": "admin", "site": "", "active": True}
    assert admin2.put(f"/api/auth/staff/{ADMIN_ID}", json={**boss_row, "access_level": "tech_lead"}).status_code == 403
    assert admin2.put(f"/api/auth/staff/{ADMIN_ID}", json={**boss_row, "active": False}).status_code == 403
    assert admin2.post(f"/api/auth/users/{ADMIN_ID}/deactivate").status_code == 403
    assert admin2.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(role="admin")).status_code == 403  # via the role
    assert _user_row(staff_api, BUYER_ID).access_level == "foreman"

    # The superadministrator does all of it.
    assert boss.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(access_level="admin")).status_code == 200
    assert _client("buyer", "buyer-password").get("/api/auth/staff").status_code == 200
    assert boss.put(f"/api/auth/staff/{ADMIN2_ID}", json={
        "name": "Admin2", "department": "", "access_level": "operator", "active": True,
    }).status_code == 200
    assert admin2.get("/api/auth/staff").status_code == 403
    row = _user_row(staff_api, ADMIN2_ID)
    assert (row.access_level, row.role) == ("operator", "admin")  # the legacy role stays as stored
    [event] = [e for e in _events(staff_api, "identity.user_updated") if e.aggregate_id == ADMIN2_ID]
    assert json.loads(event.details["changes"]) == {"access_level": ["admin", "operator"]}


def test_delete_erases_an_employee_without_history_with_their_settings(staff_api):
    _client("receiver", "recv-password")  # signing in alone is not history
    with staff_api() as session:
        session.add(ProductionOperatorSkillRow(
            operator_id=RECEIVER_ID, operation_code="ОП-1", allowed=True, stars=3, coefficient=Decimal("1"),
        ))
        session.commit()
    admin = _client("admin", "admin-password")

    response = admin.delete(f"/api/auth/staff/{RECEIVER_ID}")

    assert response.status_code == 200 and response.json()["deleted"] is True
    assert _user_row(staff_api, RECEIVER_ID) is None
    with staff_api() as session:
        assert session.scalars(select(ProductionOperatorSkillRow)).all() == []
    assert TestClient(app).post("/api/auth/login", json={"login": "receiver", "password": "recv-password"}).status_code == 401
    [event] = _events(staff_api, "identity.user_deleted")
    assert event.details["login"] == "receiver"
    assert all(user["id"] != str(RECEIVER_ID) for user in admin.get("/api/auth/staff").json()["users"])


def test_delete_of_an_employee_with_history_switches_them_off(staff_api):
    buyer = _client("buyer", "buyer-password")
    with staff_api() as session:  # work done: the buyer is named in the journal
        session.add(AuditEventRow(
            id=uuid4(), aggregate_id=uuid4(), event_type="procurement.line_taken", actor_id=BUYER_ID,
            actor_name="Buyer", actor_department="Снабжение", occurred_at=NOW, details={},
        ))
        session.commit()
    admin = _client("admin", "admin-password")

    response = admin.delete(f"/api/auth/staff/{BUYER_ID}")

    assert response.status_code == 200 and response.json()["deleted"] is False
    assert _user_row(staff_api, BUYER_ID).active is False
    assert buyer.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"login": "buyer", "password": "buyer-password"}).status_code == 401
    again = admin.delete(f"/api/auth/staff/{BUYER_ID}")
    assert again.status_code == 409 and "уже отключён" in again.json()["detail"]


def test_delete_refuses_self_a_superadmin_unknown_and_non_admin(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.delete(f"/api/auth/staff/{ADMIN_ID}").status_code == 409
    _add_admin(staff_api)
    refused = _client("admin2", "admin2-password").delete(f"/api/auth/staff/{ADMIN_ID}")
    assert refused.status_code == 403 and "только суперадминистратор" in refused.json()["detail"]
    assert _user_row(staff_api, ADMIN_ID) is not None
    assert admin.delete("/api/auth/staff/30000000-0000-0000-0000-00000000dead").status_code == 404
    assert _client("receiver", "recv-password").delete(f"/api/auth/staff/{BUYER_ID}").status_code == 403
    assert _user_row(staff_api, BUYER_ID).active is True


def test_department_list_keeps_departments_in_use_and_drops_roles(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put("/api/auth/departments", json={"departments": ["Бухгалтерия", "Кладовщик", "ОТК", "цех", "Цех", ""]})

    assert response.status_code == 200, response.text
    # «Снабжение» and «ИТ» stay: people have them. Role labels («Кладовщик», «ОТК») are not departments.
    assert response.json()["departments"] == ["Бухгалтерия", "ИТ", "Снабжение", "цех"]
    assert admin.get("/api/auth/staff").json()["departments"] == ["Бухгалтерия", "ИТ", "Снабжение", "цех"]
    [event] = _events(staff_api, "identity.departments_saved")
    assert event.actor_id == ADMIN_ID
    assert _client("receiver", "recv-password").put("/api/auth/departments", json={"departments": []}).status_code == 403


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


# ── Кадровые поля «Личной карточки» ───────────────────────────────────────────────────────────────


def test_personnel_number_and_job_text_are_normalized():
    assert normalize_personnel_number("  frx-0017 ") == "FRX-0017"
    assert normalize_personnel_number("ФРХ  17") == "ФРХ 17"
    assert normalize_personnel_number(None) == ""
    with pytest.raises(IdentityValidationError, match="Табельный номер длиннее"):
        normalize_personnel_number("X" * 33)
    assert normalize_job_text("  Монтажник   РЭА ", "Должность") == "Монтажник РЭА"
    with pytest.raises(IdentityValidationError, match="Должность длиннее"):
        normalize_job_text("Я" * 256, "Должность")


def test_manager_chain_refuses_self_and_a_closed_loop():
    a, b, c = (UUID(int=index) for index in (1, 2, 3))
    managers = {a: None, b: a, c: b}  # c → b → a
    names = {a: "Анна", b: "Борис", c: "Вера"}
    check_manager_chain(c, a, managers, names)  # skipping a level is fine
    check_manager_chain(a, None, managers, names)
    with pytest.raises(IdentityValidationError, match="сам себе"):
        check_manager_chain(b, b, managers, names)
    with pytest.raises(IdentityValidationError, match="Замкнутый круг подчинения: «Анна» уже стоит в цепочке руководителей «Вера»"):
        check_manager_chain(a, c, managers, names)


PERSONNEL = {
    "personnel_number": " frx-0017 ", "job_title": "Монтажник РЭА", "qualification": "4 разряд",
    "job_since": "2024-03-01", "manager_id": str(ADMIN_ID),
}


def test_admin_sets_personnel_fields_and_they_come_back(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(**PERSONNEL))

    assert response.status_code == 200, response.text
    user = response.json()["user"]
    assert {key: user[key] for key in PERSONNEL} == {
        "personnel_number": "FRX-0017", "job_title": "Монтажник РЭА", "qualification": "4 разряд",
        "job_since": "2024-03-01", "manager_id": str(ADMIN_ID),
    }
    listed = {row["login"]: row for row in admin.get("/api/auth/staff").json()["users"]}
    assert listed["buyer"]["personnel_number"] == "FRX-0017"
    assert listed["admin"]["personnel_number"] == "" and listed["admin"]["manager_id"] is None
    [event] = _events(staff_api, "identity.user_updated")
    assert json.loads(event.details["changes"]) == {
        "personnel_number": ["", "FRX-0017"], "job_title": ["", "Монтажник РЭА"], "qualification": ["", "4 разряд"],
        "job_since": ["", "01.03.2024"], "manager": ["", "Admin"],
    }


def test_personnel_fields_left_out_of_the_request_stay_and_blank_ones_clear(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(**PERSONNEL)).status_code == 200

    kept = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(site="ЦАР")).json()["user"]
    assert (kept["personnel_number"], kept["manager_id"], kept["job_since"]) == ("FRX-0017", str(ADMIN_ID), "2024-03-01")

    cleared = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(
        site="ЦАР", personnel_number="", job_title="", qualification="", job_since=None, manager_id=None,
    )).json()["user"]
    assert (cleared["personnel_number"], cleared["job_title"], cleared["manager_id"], cleared["job_since"]) == (
        "", "", None, None,
    )
    with staff_api() as session:
        assert session.get(IdentityUserRow, BUYER_ID).personnel_number is None  # NULL, so blanks may repeat


def test_personnel_number_is_unique_case_insensitively(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(personnel_number="FRX-0017")).status_code == 200

    taken = admin.put(f"/api/auth/staff/{RECEIVER_ID}", json={
        "name": "Receiver", "department": "", "role": "receiver", "site": "", "active": True,
        "personnel_number": "frx-0017",
    })
    assert taken.status_code == 409
    assert taken.json()["detail"] == "Табельный номер FRX-0017 уже занят: Buyer."
    created = admin.post("/api/auth/staff", json={**NEW, "personnel_number": "FRX-0017"})
    assert created.status_code == 409 and "уже занят" in created.json()["detail"]
    # Re-saving the owner with their own number is not a clash; two employees without a number are fine.
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(personnel_number="FRX-0017")).status_code == 200
    assert admin.post("/api/auth/staff", json=NEW).status_code == 201


def test_employee_cannot_be_their_own_manager(staff_api):
    response = _client("admin", "admin-password").put(
        f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id=str(BUYER_ID)),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Сотрудник не может быть руководителем сам себе."


def test_manager_chain_cannot_close_a_loop(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id=str(ADMIN_ID))).status_code == 200
    receiver = {"name": "Receiver", "department": "", "role": "receiver", "site": "", "active": True}
    assert admin.put(f"/api/auth/staff/{RECEIVER_ID}", json={**receiver, "manager_id": str(BUYER_ID)}).status_code == 200

    loop = admin.put(f"/api/auth/staff/{ADMIN_ID}", json={
        "name": "Admin", "department": "ИТ", "role": "admin", "site": "", "active": True, "manager_id": str(RECEIVER_ID),
    })

    assert loop.status_code == 422
    assert loop.json()["detail"].startswith("Замкнутый круг подчинения: «Admin» уже стоит в цепочке руководителей «Receiver»")
    assert _user_row(staff_api, ADMIN_ID).manager_id is None


def test_manager_must_exist_and_be_switched_on(staff_api):
    admin = _client("admin", "admin-password")
    unknown = admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id="30000000-0000-0000-0000-00000000dead"))
    assert unknown.status_code == 422 and unknown.json()["detail"] == "Руководитель не найден в справочнике сотрудников."

    receiver = {"name": "Receiver", "department": "", "role": "receiver", "site": ""}
    assert admin.put(f"/api/auth/staff/{RECEIVER_ID}", json={**receiver, "active": True}).status_code == 200
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id=str(RECEIVER_ID))).status_code == 200
    assert admin.put(f"/api/auth/staff/{RECEIVER_ID}", json={**receiver, "active": False}).status_code == 200
    # A manager switched off later stays on their people; choosing a switched-off one anew is refused.
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id=str(RECEIVER_ID), site="ЦАР")).status_code == 200
    refused = admin.post("/api/auth/staff", json={**NEW, "manager_id": str(RECEIVER_ID)})
    assert refused.status_code == 422 and refused.json()["detail"] == "Руководитель «Receiver» отключён — выберите другого."


def test_create_takes_personnel_fields_into_the_row_and_the_audit(staff_api):
    admin = _client("admin", "admin-password")
    response = admin.post("/api/auth/staff", json={**NEW, **PERSONNEL})

    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert (user["personnel_number"], user["job_since"], user["manager_id"]) == ("FRX-0017", "2024-03-01", str(ADMIN_ID))
    [event] = _events(staff_api, "identity.user_created")
    assert (event.details["personnel_number"], event.details["job_since"], event.details["manager"]) == (
        "FRX-0017", "01.03.2024", "Admin",
    )


def _head(client: TestClient, department: str, head: UUID | None):
    return client.put("/api/auth/departments/head", json={"department": department, "head_id": str(head) if head else None})


def test_department_gets_a_head_kept_by_save_and_moved_by_rename(staff_api):
    admin = _client("admin", "admin-password")
    response = _head(admin, "снабжение", BUYER_ID)  # in use, never saved: the list is written with the head

    assert response.status_code == 200, response.text
    assert response.json() == {"departments": ["ИТ", "Снабжение"], "department_heads": {"Снабжение": str(BUYER_ID)}}
    assert admin.get("/api/auth/staff").json()["department_heads"] == {"Снабжение": str(BUYER_ID)}
    [event] = _events(staff_api, "identity.department_head_set")
    assert event.details == {"department": "Снабжение", "head": "Buyer", "previous": ""}
    assert _head(admin, "Снабжение", BUYER_ID).status_code == 200
    assert len(_events(staff_api, "identity.department_head_set")) == 1  # the same head again writes nothing

    admin.put("/api/auth/departments", json={"departments": ["Цех", "Снабжение"]})
    assert admin.get("/api/auth/staff").json()["department_heads"] == {"Снабжение": str(BUYER_ID)}
    admin.post("/api/auth/departments/rename", json={"source": "Снабжение", "target": "Отдел снабжения"})
    assert admin.get("/api/auth/staff").json()["department_heads"] == {"Отдел снабжения": str(BUYER_ID)}

    cleared = _head(admin, "Отдел снабжения", None)
    assert cleared.status_code == 200 and cleared.json()["department_heads"] == {}
    assert _events(staff_api, "identity.department_head_set")[-1].details == {
        "department": "Отдел снабжения", "head": "", "previous": "Buyer",
    }


def test_rename_into_a_department_with_its_own_head_keeps_that_head(staff_api):
    admin = _client("admin", "admin-password")
    _head(admin, "ИТ", ADMIN_ID)
    _head(admin, "Снабжение", BUYER_ID)
    admin.post("/api/auth/departments/rename", json={"source": "Снабжение", "target": "ит"})
    assert admin.get("/api/auth/staff").json()["department_heads"] == {"ИТ": str(ADMIN_ID)}


@pytest.mark.parametrize(
    ("department", "head", "status"),
    [("Нет такого", BUYER_ID, 404), ("ИТ", UUID("30000000-0000-0000-0000-00000000dead"), 422)],
)
def test_department_head_refusals(staff_api, department, head, status):
    assert _head(_client("admin", "admin-password"), department, head).status_code == status


def test_department_head_is_refused_to_non_admin_and_must_be_switched_on(staff_api):
    assert _head(_client("receiver", "recv-password"), "ИТ", BUYER_ID).status_code == 403
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(active=False)).status_code == 200
    refused = _head(admin, "ИТ", BUYER_ID)
    assert refused.status_code == 422 and "отключён" in refused.json()["detail"]


def test_erasing_an_employee_clears_them_as_manager_and_department_head(staff_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(manager_id=str(RECEIVER_ID))).status_code == 200
    assert _head(admin, "ИТ", RECEIVER_ID).status_code == 200

    assert admin.delete(f"/api/auth/staff/{RECEIVER_ID}").json()["deleted"] is True

    assert _user_row(staff_api, BUYER_ID).manager_id is None
    with staff_api() as session:
        assert session.scalars(select(IdentityDepartmentRow.head_user_id)).all() == [None, None]
    assert admin.get("/api/auth/staff").json()["department_heads"] == {}


def test_profile_shows_personnel_fields_manager_and_department_head(staff_api):
    admin = _client("admin", "admin-password")
    admin.put(f"/api/auth/staff/{BUYER_ID}", json=_edit(**PERSONNEL))
    _head(admin, "Снабжение", ADMIN_ID)

    profile = _client("buyer", "buyer-password").get("/api/auth/profile").json()

    assert {key: profile[key] for key in ("personnel_number", "job_title", "qualification", "job_since")} == {
        "personnel_number": "FRX-0017", "job_title": "Монтажник РЭА", "qualification": "4 разряд", "job_since": "2024-03-01",
    }
    assert profile["manager"] == {"id": str(ADMIN_ID), "name": "Admin"}
    assert profile["department_head"] == {"id": str(ADMIN_ID), "name": "Admin"}
    assert profile["manager_id"] == str(ADMIN_ID)


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


def test_personnel_fields_migration_keeps_users_with_empty_fields_and_downgrades(tmp_path):
    database_path = tmp_path / "personnel.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0084_kit_piece_scrap_zero")
    with sqlite3.connect(database_path) as connection:
        for index in (1, 2):
            connection.execute(
                "INSERT INTO identity_users (id, login, email, name, department, role, active) "
                f"VALUES ('{index:032x}', 'u{index}', 'u{index}@example.test', 'Сотрудник {index}', 'Цех', 'staff', 1)"
            )
        connection.execute("INSERT INTO identity_departments (position, name) VALUES (0, 'Цех')")

    command.upgrade(config, "0085_identity_personnel_fields")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT personnel_number, job_title, qualification, job_since, manager_id FROM identity_users ORDER BY login"
        ).fetchall()
        assert rows == [(None, "", "", None, None), (None, "", "", None, None)]  # two blank numbers coexist
        assert connection.execute("SELECT name, head_user_id FROM identity_departments").fetchall() == [("Цех", None)]
        connection.execute(f"UPDATE identity_users SET personnel_number = 'FRX-1' WHERE id = '{1:032x}'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"UPDATE identity_users SET personnel_number = 'FRX-1' WHERE id = '{2:032x}'")

    command.downgrade(config, "0084_kit_piece_scrap_zero")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(identity_users)")}
        assert connection.execute("SELECT count(*) FROM identity_users").fetchone() == (2,)
        assert not {"personnel_number", "job_title", "qualification", "job_since", "manager_id"} & columns
        assert "head_user_id" not in {row[1] for row in connection.execute("PRAGMA table_info(identity_departments)")}
