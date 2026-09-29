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
    ROLE_SEED,
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
    seed_role_matrix,
)
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 12, 0)
ADMIN_ID = UUID("40000000-0000-0000-0000-000000000001")
SECOND_ADMIN_ID = UUID("40000000-0000-0000-0000-000000000002")
BUYER_ID = UUID("40000000-0000-0000-0000-000000000003")
RECEIVER_ID = UUID("40000000-0000-0000-0000-000000000004")  # a non-full-access role — the matrix still drives it


# ── Domain ─────────────────────────────────────────────────────────────────────────────────────────


def test_an_empty_cell_is_closed_and_only_explicit_cells_open():
    """SUPPLYMAP portalApplyAccess_ (01.09.2026): a section is open only when explicitly granted (a non-full role)."""
    assert role_permissions("receiver", {}) == {}
    changed = role_permissions("receiver", {**ROLE_SEED["receiver"], "warehouse": AccessMode.NONE,
                                            "procurement": AccessMode.EDIT})
    assert changed == {
        "newrequest": AccessMode.EDIT, "myrequests": AccessMode.EDIT,
        "receiving": AccessMode.EDIT, "fulfillment": AccessMode.EDIT, "handout": AccessMode.EDIT,
        "shipping": AccessMode.EDIT, "procurement": AccessMode.EDIT, "erpref": AccessMode.VIEW,
    }
    assert role_permissions("unknown", {"requests": AccessMode.EDIT}) == {"requests": AccessMode.EDIT}


def test_full_access_roles_ignore_the_matrix_and_are_never_reduced():
    """Owner 2026-09-29: admin, buyer, prodhead, management have every section on edit no matter the matrix."""
    everything = {section: AccessMode.EDIT for section in SECTION_LABELS}
    for role in ("admin", "buyer", "prodhead", "management"):
        assert role_permissions(role, {}) == everything
        assert role_permissions(role, {"warehouse": AccessMode.NONE, "staffdir": AccessMode.NONE}) == everything


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
        provision_user(session, _user(RECEIVER_ID, "receiver", "receiver"), hash_password("recv-password"))
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
    # A full-access role (buyer) is «полный» everywhere by default; a non-full role (receiver) has an empty (closed) row.
    assert body["defaults"]["receiver"] == {}
    assert body["defaults"]["buyer"] == {section: "edit" for section in SECTION_LABELS}
    assert body["defaults"]["admin"] == {section: "edit" for section in SECTION_LABELS}
    assert body["matrix"]["receiver"] == {section: mode.value for section, mode in ROLE_SEED["receiver"].items()}
    assert "admin" not in body["matrix"]
    assert [(user["login"], user["access"]) for user in body["users"]] == [
        ("admin", {}), ("buyer", {}), ("receiver", {}),
    ]


def test_matrix_change_reaches_the_next_request_and_a_cleared_cell_falls_back(access_api):
    # A non-full-access role (receiver) is driven by the matrix; a full-access role would ignore it.
    admin = _client("admin", "admin-password")
    receiver = _client("receiver", "recv-password")
    assert receiver.get("/api/auth/me").json()["permissions"]["warehouse"] == "edit"

    saved = admin.put("/api/auth/access/roles", json={"matrix": {
        "receiver": {"warehouse": "none", "shipping": "view", "procurement": ""},
    }})
    assert saved.status_code == 200, saved.text
    assert saved.json()["matrix"] == {"receiver": {"warehouse": "none", "shipping": "view"}}
    permissions = receiver.get("/api/auth/me").json()["permissions"]
    assert permissions == {"shipping": "view"}  # an empty cell is closed, the other roles' rows are gone too

    [event] = [item for item in _events(access_api, "identity.role_access_saved") if item.details["role"] == "receiver"]
    assert event.actor_id == ADMIN_ID
    changes = json.loads(event.details["changes"])
    assert changes["shipping"] == ["edit", "view"] and changes["warehouse"] == ["edit", "none"]
    assert changes["procurement"] == ["view", ""]

    # Clearing the whole row closes everything for a non-full role.
    assert admin.put("/api/auth/access/roles", json={"matrix": {}}).status_code == 200
    assert receiver.get("/api/auth/me").json()["permissions"] == {}
    with access_api() as session:
        assert session.scalars(select(IdentityRoleAccessRow)).first() is None


def test_unchanged_matrix_writes_no_audit(access_api):
    admin = _client("admin", "admin-password")
    stored = admin.get("/api/auth/access").json()["matrix"]
    assert admin.put("/api/auth/access/roles", json={"matrix": stored}).status_code == 200
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
def test_a_full_access_role_stays_full_when_the_matrix_tries_to_close_it(access_api, mode):
    # Owner 2026-09-29: admin (a full-access role) is non-reducible. The matrix write is accepted but changes nothing.
    admin = _client("admin", "admin-password")
    assert admin.put("/api/auth/access/roles", json={"matrix": {"admin": {"staffdir": mode}}}).status_code == 200
    assert admin.get("/api/auth/staff").status_code == 200
    assert admin.get("/api/auth/me").json()["permissions"]["staffdir"] == "edit"


def test_a_full_access_role_ignores_a_personal_exception(access_api):
    # A personal «none» on a full-access user (buyer) is accepted but does not take the section away.
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={
        "access": {"warehouse": "none", "staffdir": "none"},
    }).status_code == 200
    perms = _client("buyer", "buyer-password").get("/api/auth/me").json()["permissions"]
    assert perms == {section: "edit" for section in SECTION_LABELS}


def test_personal_exception_is_stronger_than_the_role(access_api):
    # A non-full role (receiver): the personal exception overrides the role (full-access roles would ignore it).
    admin = _client("admin", "admin-password")
    receiver = _client("receiver", "recv-password")
    response = admin.put(f"/api/auth/access/users/{RECEIVER_ID}", json={"access": {
        "warehouse": "none", "receiving": "view", "procurement": "edit", "handout": "",
    }})
    assert response.status_code == 200, response.text
    assert response.json()["access"] == {"warehouse": "none", "receiving": "view", "procurement": "edit"}
    assert receiver.get("/api/auth/me").json()["permissions"] == {
        "newrequest": "edit", "myrequests": "edit",  # filing and own rows stay (NAV-SECTIONS)
        "receiving": "view", "fulfillment": "edit", "handout": "edit", "shipping": "edit",
        "procurement": "edit", "erpref": "view",  # warehouse taken away, receiving downgraded, procurement raised
    }
    [event] = _events(access_api, "identity.user_access_saved")
    assert event.aggregate_id == RECEIVER_ID and event.details["login"] == "receiver"
    assert json.loads(event.details["changes"]) == {
        "warehouse": ["", "none"], "receiving": ["", "view"], "procurement": ["", "edit"],
    }
    listed = {user["login"]: user["access"] for user in admin.get("/api/auth/access").json()["users"]}
    assert listed["receiver"] == {"warehouse": "none", "receiving": "view", "procurement": "edit"}

    # The same exceptions again change nothing and write no audit; an empty set removes them.
    admin.put(f"/api/auth/access/users/{RECEIVER_ID}", json={"access": {
        "warehouse": "none", "receiving": "view", "procurement": "edit",
    }})
    assert len(_events(access_api, "identity.user_access_saved")) == 1
    assert admin.put(f"/api/auth/access/users/{RECEIVER_ID}", json={"access": {}}).status_code == 200
    with access_api() as session:
        assert session.scalars(select(IdentityUserAccessRow)).first() is None


def test_admin_cannot_close_identity_to_the_only_admin_personally(access_api):
    admin = _client("admin", "admin-password")
    response = admin.put(f"/api/auth/access/users/{ADMIN_ID}", json={"access": {"staffdir": "view"}})
    assert response.status_code == 409
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"staffdir": "edit"}}).status_code == 200


def test_exceptions_refuse_unknown_user_section_and_mode(access_api):
    admin = _client("admin", "admin-password")
    assert admin.put("/api/auth/access/users/40000000-0000-0000-0000-00000000dead", json={"access": {}}).status_code == 404
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"nowhere": "view"}}).status_code == 422
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"requests": "full"}}).status_code == 422


def test_demoting_an_admin_counts_personal_exceptions(access_api):
    # admin2 has «Доступы» closed personally, so admin is the only keeper and may not step down.
    _add_second_admin(access_api)
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/access/users/{SECOND_ADMIN_ID}", json={"access": {"staffdir": "none"}}).status_code == 200
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
    # A non-full role's row is interpreted as saved (a full-access role would be edit everywhere regardless).
    staffed = role_permissions("receiver", {**ROLE_SEED["receiver"], "erpref": AccessMode.NONE})
    assert {section: mode.value for section, mode in staffed.items()} == {
        "newrequest": "edit", "myrequests": "edit",  # NAV-SECTIONS defaults, not stored
        "receiving": "edit", "warehouse": "edit", "fulfillment": "edit", "handout": "edit",
        "shipping": "edit", "procurement": "view",
    }

    command.downgrade(config, "0039_notification_recipients")
    access = _access(database_path)
    assert access[("buyer", "references")] == "none"
    assert access[("buyer", "requests")] == "edit" and access[("buyer", "warehouse")] == "view"
    assert access[("management", "warehouse")] == "edit"
    assert ("management", "procurement") not in access  # no users: seeded with the first one, as before
