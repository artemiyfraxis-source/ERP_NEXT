"""NAV-ACCESS «Доступы»: role × section matrix on top of ROLE_DEFAULTS and per-person exceptions
(SUPPLYMAP Portal.gs portalAccessGet / portalAccessSave / portalAccessClean_, Staff.gs staffAccessClean_)."""

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
    SECTION_LABELS,
    AccessMode,
    IdentityUser,
    effective_permissions,
    hash_password,
    role_permissions,
)
from app.modules.identity.infrastructure import (
    IdentityRoleAccessRow,
    IdentityUserAccessRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
)
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 12, 0)
ADMIN_ID = UUID("40000000-0000-0000-0000-000000000001")
SECOND_ADMIN_ID = UUID("40000000-0000-0000-0000-000000000002")
BUYER_ID = UUID("40000000-0000-0000-0000-000000000003")


# ── Domain ─────────────────────────────────────────────────────────────────────────────────────────


def test_matrix_sits_on_top_of_role_defaults():
    assert role_permissions("buyer", {}) == ROLE_DEFAULTS["buyer"]
    changed = role_permissions("buyer", {"warehouse": AccessMode.NONE, "shipping": AccessMode.VIEW,
                                         "procurement": AccessMode.VIEW})
    assert changed == {
        "requests": AccessMode.EDIT, "procurement": AccessMode.VIEW, "references": AccessMode.VIEW,
        "shipping": AccessMode.VIEW,
    }
    assert role_permissions("unknown", {"requests": AccessMode.EDIT}) == {"requests": AccessMode.EDIT}


def test_none_never_leaks_into_permissions():
    assert effective_permissions({"requests": AccessMode.NONE}, {}) == {}
    assert effective_permissions({"requests": AccessMode.EDIT}, {"requests": AccessMode.NONE}) == {}


def test_matrix_manages_every_section_the_administrator_has():
    assert set(SECTION_LABELS) == set(ROLE_DEFAULTS["admin"])


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────────


def _user(user_id: UUID, login: str, role: str) -> IdentityUser:
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=login.title(), department="ИТ", role=role,
        site=None, active=True,
    )


@pytest.fixture()
def access_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(session, _user(ADMIN_ID, "admin", "admin"), hash_password("admin-password"))
        provision_user(session, _user(BUYER_ID, "buyer", "buyer"), hash_password("buyer-password"))
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


def _add_second_admin(sessions) -> None:
    with sessions() as session:
        provision_user(session, _user(SECOND_ADMIN_ID, "admin2", "admin"), hash_password("admin2-password"))
        session.commit()


def test_only_admin_opens_access(access_api):
    assert _client("buyer", "buyer-password").get("/api/auth/access").status_code == 403
    assert _client("buyer", "buyer-password").put("/api/auth/access/roles", json={"matrix": {}}).status_code == 403
    assert TestClient(app).get("/api/auth/access").status_code == 401

    body = _client("admin", "admin-password").get("/api/auth/access").json()
    assert [section["key"] for section in body["sections"]] == list(SECTION_LABELS)
    assert {"key": "buyer", "label": "Специалист по снабжению"} in body["roles"]
    assert body["defaults"]["buyer"] == {"requests": "edit", "procurement": "edit", "warehouse": "view", "references": "view"}
    assert body["matrix"] == {}  # nothing changed yet — every role follows ROLE_DEFAULTS
    assert [(user["login"], user["access"]) for user in body["users"]] == [("admin", {}), ("buyer", {})]


def test_matrix_change_reaches_the_next_request_and_a_cleared_cell_falls_back(access_api):
    admin = _client("admin", "admin-password")
    buyer = _client("buyer", "buyer-password")
    assert buyer.get("/api/auth/me").json()["permissions"]["warehouse"] == "view"

    saved = admin.put("/api/auth/access/roles", json={"matrix": {
        "buyer": {"warehouse": "none", "shipping": "view", "procurement": ""},
    }})
    assert saved.status_code == 200, saved.text
    assert saved.json()["matrix"] == {"buyer": {"warehouse": "none", "shipping": "view"}}
    permissions = buyer.get("/api/auth/me").json()["permissions"]
    assert "warehouse" not in permissions and permissions["shipping"] == "view"
    assert permissions["procurement"] == "edit"  # "" = как задумано ролью

    [event] = _events(access_api, "identity.role_access_saved")
    assert event.actor_id == ADMIN_ID and event.details["role"] == "buyer"
    assert json.loads(event.details["changes"]) == {"shipping": ["", "view"], "warehouse": ["", "none"]}

    # Clearing the whole row brings the role back to ROLE_DEFAULTS, not to «closed».
    assert admin.put("/api/auth/access/roles", json={"matrix": {}}).status_code == 200
    assert buyer.get("/api/auth/me").json()["permissions"] == {
        "requests": "edit", "procurement": "edit", "warehouse": "view", "references": "view",
    }
    with access_api() as session:
        assert session.scalars(select(IdentityRoleAccessRow)).first() is None
    assert len(_events(access_api, "identity.role_access_saved")) == 2


def test_unchanged_matrix_writes_no_audit(access_api):
    admin = _client("admin", "admin-password")
    assert admin.put("/api/auth/access/roles", json={"matrix": {"pm": {}}}).status_code == 200
    assert _events(access_api, "identity.role_access_saved") == []


@pytest.mark.parametrize("matrix", [
    {"nobody": {"requests": "view"}},
    {"buyer": {"nowhere": "view"}},
    {"buyer": {"requests": "admin"}},
])
def test_unknown_role_section_or_mode_is_refused(access_api, matrix):
    response = _client("admin", "admin-password").put("/api/auth/access/roles", json={"matrix": matrix})
    assert response.status_code == 422
    assert _events(access_api, "identity.role_access_saved") == []


@pytest.mark.parametrize("mode", ["none", "view"])
def test_admin_role_cannot_close_access_to_itself(access_api, mode):
    admin = _client("admin", "admin-password")
    response = admin.put("/api/auth/access/roles", json={"matrix": {"admin": {"identity": mode}}})
    assert response.status_code == 409
    assert "хотя бы один администратор" in response.json()["detail"]
    assert admin.get("/api/auth/access").status_code == 200


def test_admin_matrix_may_close_identity_while_one_admin_keeps_it_personally(access_api):
    _add_second_admin(access_api)
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/access/users/{SECOND_ADMIN_ID}", json={"access": {"identity": "edit"}}).status_code == 200
    assert admin.put("/api/auth/access/roles", json={"matrix": {"admin": {"identity": "none"}}}).status_code == 200
    assert admin.get("/api/auth/access").status_code == 403
    second = _client("admin2", "admin2-password")
    assert second.get("/api/auth/access").status_code == 200
    # …and that last keeper cannot drop the exception or step down.
    assert second.put(f"/api/auth/access/users/{SECOND_ADMIN_ID}", json={"access": {}}).status_code == 409
    body = {"name": "Admin2", "department": "ИТ", "role": "buyer", "site": "", "active": True}
    assert second.put(f"/api/auth/staff/{SECOND_ADMIN_ID}", json=body).status_code == 409
    assert second.post(f"/api/auth/users/{SECOND_ADMIN_ID}/deactivate").status_code == 409


def test_personal_exception_is_stronger_than_the_role(access_api):
    admin = _client("admin", "admin-password")
    buyer = _client("buyer", "buyer-password")
    response = admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {
        "procurement": "view", "requests": "none", "shipping": "edit", "warehouse": "",
    }})
    assert response.status_code == 200, response.text
    assert response.json()["access"] == {"procurement": "view", "requests": "none", "shipping": "edit"}
    assert buyer.get("/api/auth/me").json()["permissions"] == {
        "procurement": "view", "shipping": "edit", "warehouse": "view", "references": "view",
    }
    [event] = _events(access_api, "identity.user_access_saved")
    assert event.aggregate_id == BUYER_ID and event.details["login"] == "buyer"
    assert json.loads(event.details["changes"]) == {
        "procurement": ["", "view"], "requests": ["", "none"], "shipping": ["", "edit"],
    }
    listed = {user["login"]: user["access"] for user in admin.get("/api/auth/access").json()["users"]}
    assert listed["buyer"] == {"procurement": "view", "requests": "none", "shipping": "edit"}

    # The same exceptions again change nothing and write no audit; an empty set removes them.
    admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {
        "procurement": "view", "requests": "none", "shipping": "edit",
    }})
    assert len(_events(access_api, "identity.user_access_saved")) == 1
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {}}).status_code == 200
    with access_api() as session:
        assert session.scalars(select(IdentityUserAccessRow)).first() is None


def test_admin_cannot_close_identity_to_the_only_admin_personally(access_api):
    admin = _client("admin", "admin-password")
    response = admin.put(f"/api/auth/access/users/{ADMIN_ID}", json={"access": {"identity": "view"}})
    assert response.status_code == 409
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"identity": "edit"}}).status_code == 200


def test_exceptions_refuse_unknown_user_section_and_mode(access_api):
    admin = _client("admin", "admin-password")
    assert admin.put("/api/auth/access/users/40000000-0000-0000-0000-00000000dead", json={"access": {}}).status_code == 404
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"nowhere": "view"}}).status_code == 422
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"requests": "full"}}).status_code == 422


def test_demoting_an_admin_counts_personal_exceptions(access_api):
    # admin2 has «Доступы» closed personally, so admin is the only keeper and may not step down.
    _add_second_admin(access_api)
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/access/users/{SECOND_ADMIN_ID}", json={"access": {"identity": "none"}}).status_code == 200
    body = {"name": "Admin", "department": "ИТ", "role": "buyer", "site": "", "active": True}
    response = admin.put(f"/api/auth/staff/{ADMIN_ID}", json=body)
    assert response.status_code == 409
    assert "хотя бы один администратор" in response.json()["detail"]


# ── Migration 0040 ─────────────────────────────────────────────────────────────────────────────────


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _access(database_path: Path) -> dict[tuple[str, str], str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT role, section, mode FROM identity_role_access")
        return {(role, section): mode for role, section, mode in rows}


def test_migration_folds_seeded_defaults_back_without_changing_anyones_access(tmp_path):
    database_path = tmp_path / "identity.db"
    config = _config(database_path)
    command.upgrade(config, "0039_notification_recipients")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM identity_role_access")
        # buyer has a user and was seeded before `references` became a default; management edits the warehouse.
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, site, active) "
            "VALUES ('00000000000000000000000000000001', 'b', 'b@x.test', 'B', '', 'buyer', NULL, 1)"
        )
        connection.executemany("INSERT INTO identity_role_access VALUES (?, ?, ?)", [
            ("buyer", "requests", "edit"), ("buyer", "procurement", "edit"), ("buyer", "warehouse", "view"),
            ("management", "warehouse", "edit"), ("management", "procurement", "view"),
            ("admin", "receiving", "edit"),
        ])

    command.upgrade(config, "0040_identity_role_overrides")
    assert _access(database_path) == {
        ("buyer", "references"): "none",  # closed before, stays closed
        ("management", "warehouse"): "edit",  # the administrator's change stays
    }
    staffed = role_permissions("buyer", {"references": AccessMode.NONE})
    assert {section: mode.value for section, mode in staffed.items()} == {
        "requests": "edit", "procurement": "edit", "warehouse": "view",
    }

    command.downgrade(config, "0039_notification_recipients")
    access = _access(database_path)
    assert access[("buyer", "references")] == "none"
    assert access[("buyer", "requests")] == "edit" and access[("buyer", "warehouse")] == "view"
    assert access[("management", "warehouse")] == "edit"
    assert ("management", "procurement") not in access  # no users: seeded with the first one, as before
