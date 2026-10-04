"""NAV-ACCESS «Доступы» on the access levels (docs/migration/access-levels-plan.md, T2): level × section matrix and
per-person exceptions on top of the level (SUPPLYMAP Portal.gs portalAccessGet / portalAccessSave / portalAccessClean_,
Staff.gs staffAccessClean_); superadmin/admin are always full and alone open the screen."""

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
    FULL_LEVEL_SECTIONS,
    LEVEL_SECTION_LABELS,
    LEVEL_SEED,
    ROLE_SEED,
    AccessMode,
    IdentityUser,
    effective_permissions,
    hash_password,
    level_permissions,
)
from app.modules.identity.infrastructure import (
    IdentityLevelAccessRow,
    IdentityUserLevelAccessRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.identity.level_migration import old_permissions
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 12, 0)
ADMIN_ID = UUID("40000000-0000-0000-0000-000000000001")
SECOND_ADMIN_ID = UUID("40000000-0000-0000-0000-000000000002")
BUYER_ID = UUID("40000000-0000-0000-0000-000000000003")  # role buyer → Технический руководитель
RECEIVER_ID = UUID("40000000-0000-0000-0000-000000000004")  # role receiver → Бригадир: the matrix drives it
FOREMAN_SEED = {section: mode.value for section, mode in LEVEL_SEED["foreman"].items()}


# ── Domain ─────────────────────────────────────────────────────────────────────────────────────────


def test_an_empty_cell_is_closed_and_only_explicit_cells_open():
    """SUPPLYMAP portalApplyAccess_ (01.09.2026): a section is open only when explicitly granted (a matrix level)."""
    assert level_permissions("foreman", {}, {}) == {}
    changed = level_permissions(
        "foreman", {**LEVEL_SEED["foreman"], "warehouse": AccessMode.NONE}, {"procurement": AccessMode.EDIT},
    )
    expected = {**FOREMAN_SEED, "procurement": "edit"}
    del expected["warehouse"]
    assert {section: mode.value for section, mode in changed.items()} == expected
    # A section outside the level matrix («Сотрудники») never reaches a matrix level, not even by an exception.
    assert level_permissions("operator", {}, {"staffdir": AccessMode.EDIT}) == {}


def test_superadmin_and_admin_ignore_the_matrix_and_their_exceptions():
    everything = {section: AccessMode.EDIT for section in FULL_LEVEL_SECTIONS}
    for level in ("superadmin", "admin"):
        assert level_permissions(level, {}, {}) == everything
        assert level_permissions(level, {"warehouse": AccessMode.NONE}, {"warehouse": AccessMode.NONE}) == everything


def test_none_never_leaks_into_permissions():
    assert effective_permissions({"requests": AccessMode.NONE}, {}) == {}
    assert effective_permissions({"requests": AccessMode.EDIT}, {"requests": AccessMode.NONE}) == {}
    assert level_permissions("operator", {"requests": AccessMode.NONE}, {}) == {}


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


def test_only_superadmin_and_admin_open_access(access_api):
    buyer = _client("buyer", "buyer-password")  # a former full-access role: now a matrix level
    assert buyer.get("/api/auth/access").status_code == 403
    assert buyer.put("/api/auth/access/levels", json={"matrix": {}}).status_code == 403
    assert buyer.put("/api/auth/access/roles", json={"matrix": {}}).status_code == 403
    assert TestClient(app).get("/api/auth/access").status_code == 401

    body = _client("admin", "admin-password").get("/api/auth/access").json()
    assert [section["key"] for section in body["sections"]] == list(LEVEL_SECTION_LABELS)
    assert {"key": "tech_lead", "label": "Технический руководитель", "full": False} in body["levels"]
    assert {"key": "superadmin", "label": "Суперадминистратор", "full": True} in body["levels"]
    assert body["roles"] == body["levels"]  # the legacy name, one release
    # superadmin/admin are «полный» everywhere by default; a matrix level has an empty (closed) row.
    assert body["defaults"]["foreman"] == {}
    assert body["defaults"]["admin"] == body["defaults"]["superadmin"] == {s: "edit" for s in FULL_LEVEL_SECTIONS}
    assert body["seed"]["foreman"] == FOREMAN_SEED
    assert body["matrix"]["foreman"] == FOREMAN_SEED
    assert "admin" not in body["matrix"] and "superadmin" not in body["matrix"]
    assert [(user["login"], user["access_level"], user["access"]) for user in body["users"]] == [
        ("admin", "admin", {}), ("buyer", "tech_lead", {}), ("receiver", "foreman", {}),
    ]


@pytest.mark.parametrize("path", ["/api/auth/access/levels", "/api/auth/access/roles"])
def test_matrix_change_reaches_the_next_request_and_a_cleared_cell_closes(access_api, path):
    admin = _client("admin", "admin-password")
    receiver = _client("receiver", "recv-password")
    assert receiver.get("/api/auth/me").json()["permissions"]["warehouse"] == "edit"

    saved = admin.put(path, json={"matrix": {
        "foreman": {"warehouse": "none", "shipping_goods": "view", "procurement": ""},
    }})
    assert saved.status_code == 200, saved.text
    assert saved.json()["matrix"] == {"foreman": {"warehouse": "none", "shipping_goods": "view"}}
    permissions = receiver.get("/api/auth/me").json()["permissions"]
    assert permissions == {"shipping_goods": "view"}  # an empty cell is closed, the other levels' rows are gone too

    [event] = [item for item in _events(access_api, "identity.level_access_saved") if item.details["level"] == "foreman"]
    assert event.actor_id == ADMIN_ID
    changes = json.loads(event.details["changes"])
    assert changes["shipping_goods"] == ["edit", "view"] and changes["warehouse"] == ["edit", "none"]
    assert changes["procurement"] == ["view", ""]

    # Clearing the whole matrix closes everything for a matrix level.
    assert admin.put(path, json={"matrix": {}}).status_code == 200
    assert receiver.get("/api/auth/me").json()["permissions"] == {}
    with access_api() as session:
        assert session.scalars(select(IdentityLevelAccessRow)).first() is None


def test_unchanged_matrix_writes_no_audit(access_api):
    admin = _client("admin", "admin-password")
    stored = admin.get("/api/auth/access").json()["matrix"]
    assert admin.put("/api/auth/access/levels", json={"matrix": stored}).status_code == 200
    assert _events(access_api, "identity.level_access_saved") == []


@pytest.mark.parametrize("matrix", [
    {"nobody": {"requests": "view"}},
    {"buyer": {"requests": "view"}},  # an old role key is not a level
    {"tech_lead": {"nowhere": "view"}},
    {"tech_lead": {"staffdir": "edit"}},  # «Сотрудники» is outside the matrix
    {"tech_lead": {"requests": "admin"}},
])
def test_unknown_level_section_or_mode_is_refused(access_api, matrix):
    response = _client("admin", "admin-password").put("/api/auth/access/levels", json={"matrix": matrix})
    assert response.status_code == 422
    assert _events(access_api, "identity.level_access_saved") == []


@pytest.mark.parametrize("mode", ["none", "view"])
def test_a_full_level_row_is_ignored(access_api, mode):
    admin = _client("admin", "admin-password")
    saved = admin.put("/api/auth/access/levels", json={"matrix": {"admin": {"warehouse": mode}}})
    assert saved.status_code == 200 and saved.json()["matrix"] == {}
    assert admin.get("/api/auth/staff").status_code == 200
    assert admin.get("/api/auth/me").json()["permissions"]["warehouse"] == "edit"
    with access_api() as session:
        assert session.scalars(select(IdentityLevelAccessRow).where(IdentityLevelAccessRow.level == "admin")).all() == []


def test_a_full_level_ignores_a_personal_exception(access_api):
    admin = _client("admin", "admin-password")
    assert admin.put(f"/api/auth/access/users/{ADMIN_ID}", json={"access": {"warehouse": "none"}}).status_code == 200
    perms = admin.get("/api/auth/me").json()["permissions"]
    assert perms == {section: "edit" for section in FULL_LEVEL_SECTIONS}
    assert admin.get("/api/auth/access").status_code == 200


def test_personal_exception_is_stronger_than_the_level(access_api):
    admin = _client("admin", "admin-password")
    receiver = _client("receiver", "recv-password")
    response = admin.put(f"/api/auth/access/users/{RECEIVER_ID}", json={"access": {
        "warehouse": "none", "receiving": "view", "procurement": "edit", "handout": "",
    }})
    assert response.status_code == 200, response.text
    assert response.json()["access"] == {"warehouse": "none", "receiving": "view", "procurement": "edit"}
    expected = {**FOREMAN_SEED, "receiving": "view", "procurement": "edit"}
    del expected["warehouse"]
    assert receiver.get("/api/auth/me").json()["permissions"] == expected
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
        assert session.scalars(select(IdentityUserLevelAccessRow)).first() is None


def test_exceptions_refuse_unknown_user_section_and_mode(access_api):
    admin = _client("admin", "admin-password")
    assert admin.put("/api/auth/access/users/40000000-0000-0000-0000-00000000dead", json={"access": {}}).status_code == 404
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"nowhere": "view"}}).status_code == 422
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"staffdir": "edit"}}).status_code == 422
    assert admin.put(f"/api/auth/access/users/{BUYER_ID}", json={"access": {"requests": "full"}}).status_code == 422


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
    # A non-full role's row is interpreted as saved (the legacy role model, frozen in level_migration).
    row = {section: mode.value for section, mode in {**ROLE_SEED["receiver"], "erpref": AccessMode.NONE}.items()}
    assert old_permissions("receiver", row, {}) == {
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
