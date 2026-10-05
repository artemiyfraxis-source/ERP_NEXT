"""ID-3 / ID-5 / ID-4 and the own profile: SUPPLYMAP PORTAL_SECTIONS_ keys (erptctpl, tcgen, erpref, staffdir),
portalAdminAccess_, the global login throttle (Portal.gs:252-306) and the employee's own password."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.identity.application import (
    AuthenticationError,
    AuthenticationRateLimited,
    change_own_password,
    login,
)
from app.modules.identity.domain import FULL_LEVEL_SECTIONS, ROLE_SEED, SECTION_LABELS, AccessMode, level_permissions
from app.modules.identity.infrastructure import IdentityLoginThrottleRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.factories import Actor
from tests.identity.test_access import _client, access_api  # noqa: F401  (fixture)
from tests.identity.test_application import NOW, USER_ID, factory, identity_database  # noqa: F401  (fixture)


ANY_ID = "99999999-9999-9999-9999-999999999999"
client = TestClient(app)


# ── ID-5: the administrator has every section unless told otherwise ────────────────────────────────


def test_admin_has_every_section_on_edit_without_a_matrix():
    everything = {section: AccessMode.EDIT for section in FULL_LEVEL_SECTIONS}
    assert level_permissions("admin", {}, {}) == everything
    # A full level ignores the matrix: a none/view cell cannot reduce it (owner 2026-09-29, access levels 04.10).
    assert level_permissions("admin", {"tcgen": AccessMode.NONE, "erpref": AccessMode.VIEW}, {}) == everything


def test_new_keys_are_sections_of_their_own():
    assert {"erptctpl", "tcgen", "erpref", "staffdir"} <= set(SECTION_LABELS)
    assert not {"references", "identity"} & set(SECTION_LABELS)
    assert (SECTION_LABELS["erptctpl"], SECTION_LABELS["tcgen"]) == ("Шаблоны техкарт", "Генератор техкарт")
    for role, defaults in ROLE_SEED.items():
        assert "erpref" in defaults and "tcgen" not in defaults and "staffdir" not in defaults, role


# ── ID-3: each route asks its own key, without role lists ──────────────────────────────────────────


@pytest.fixture()
def as_principal():
    def use(permissions: dict[str, str], role: str = "staff") -> None:
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(name="Сотрудник", department="КБ"),
            email="employee@example.test",
            roles=frozenset({role}),
            permissions=permissions,
        )

    yield use
    app.dependency_overrides.pop(authenticated_principal, None)


GENERATOR_ROUTES = [
    ("POST", "/api/route/harness"),
    ("POST", "/api/route/coax"),
    ("POST", "/api/techcards/grapher/import"),
    ("POST", f"/api/techcards/projects/{ANY_ID}/build"),
]
TEMPLATE_ROUTES = [("GET", "/api/techcards/templates"), ("GET", "/api/techcards/templates/any")]
PROJECT_ROUTES = [("GET", "/api/techcards/projects"), ("GET", f"/api/techcards/projects/{ANY_ID}")]


def _status(method: str, path: str) -> int:
    return client.request(method, path, json={}).status_code


def test_generator_opens_from_tcgen_alone_for_any_role(as_principal):
    as_principal({"tcgen": "view"}, role="staff")
    for method, path in GENERATOR_ROUTES:
        assert _status(method, path) != 403, path
    for method, path in TEMPLATE_ROUTES + PROJECT_ROUTES:
        assert _status(method, path) == 403, path


def test_templates_take_erptctpl_and_projects_take_techcards(as_principal):
    as_principal({"erptctpl": "view"}, role="master")
    for method, path in TEMPLATE_ROUTES:
        assert _status(method, path) != 403, path
    for method, path in GENERATOR_ROUTES + PROJECT_ROUTES:
        assert _status(method, path) == 403, path
    as_principal({"techcards": "edit"}, role="staff")  # no engineer/admin role needed any more
    for method, path in PROJECT_ROUTES:
        assert _status(method, path) != 403, path
    assert _status("POST", "/api/techcards/projects") != 403
    for method, path in GENERATOR_ROUTES + TEMPLATE_ROUTES:
        assert _status(method, path) == 403, path


def test_staff_directory_and_access_are_superadmin_and_admin_only(as_principal):
    # Access levels: «Сотрудники» and «Доступы» are the full levels' own; no matrix cell opens them below.
    for level in ("superadmin", "admin"):
        as_principal({}, role=level)
        assert client.get("/api/auth/staff").status_code != 403
        assert client.get("/api/auth/access").status_code != 403
    for level in ("commercial", "tech_lead", "foreman", "operator"):
        as_principal({"staffdir": "edit", "operations": "edit", "erpref": "edit"}, role=level)
        assert client.get("/api/auth/staff").status_code == 403
        assert client.get("/api/auth/access").status_code == 403


# ── Migration ──────────────────────────────────────────────────────────────────────────────────────


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


USER_A = "00000000000000000000000000000001"


def test_migration_renames_keys_and_copies_techcards_to_templates_and_generator(tmp_path):
    database_path = tmp_path / "identity.db"
    config = _config(database_path)
    command.upgrade(config, "0042_shipping_project_addressees")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM identity_role_access")
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, site, active) "
            "VALUES (?, 'a', 'a@x.test', 'A', '', 'engineer', NULL, 1)", (USER_A,),
        )
        connection.executemany("INSERT INTO identity_role_access VALUES (?, ?, ?)", [
            ("engineer", "techcards", "edit"),
            ("admin", "techcards", "edit"),  # equal to the admin default — not copied
            ("buyer", "references", "none"),
            ("admin", "identity", "view"),
        ])
        connection.executemany("INSERT INTO identity_user_access VALUES (?, ?, ?)", [
            (USER_A, "techcards", "view"), (USER_A, "references", "edit"), (USER_A, "identity", "none"),
        ])

    command.upgrade(config, "0055_identity_split_sections")
    with sqlite3.connect(database_path) as connection:
        roles = {(r, s): m for r, s, m in connection.execute("SELECT role, section, mode FROM identity_role_access")}
        users = {(u, s): m for u, s, m in connection.execute("SELECT user_id, section, mode FROM identity_user_access")}
    assert roles == {
        ("engineer", "techcards"): "edit", ("engineer", "erptctpl"): "edit", ("engineer", "tcgen"): "edit",
        ("admin", "techcards"): "edit",
        ("buyer", "erpref"): "none",
        ("admin", "staffdir"): "view",
    }
    assert users == {
        (USER_A, "techcards"): "view", (USER_A, "erptctpl"): "view", (USER_A, "tcgen"): "view",
        (USER_A, "erpref"): "edit", (USER_A, "staffdir"): "none",
    }

    command.downgrade(config, "0042_shipping_project_addressees")
    with sqlite3.connect(database_path) as connection:
        sections = {s for (s,) in connection.execute("SELECT section FROM identity_role_access")}
    assert sections == {"techcards", "references", "identity"}


# ── ID-4: global throttle, failure journal, alert to the administrators ─────────────────────────────


def _fail(uow_factory, who: str, client_key: str = "client:test") -> None:
    with pytest.raises(AuthenticationError):
        login(who, "wrong-password", NOW, uow_factory(), client_key=client_key)


def test_thirty_failures_over_all_accounts_close_the_login_and_alert_admins(identity_database):
    uow_factory = factory(identity_database)
    for index in range(29):
        _fail(uow_factory, f"nobody{index}", client_key=f"client:{index}")
    with identity_database() as session:
        assert session.scalars(select(OutboxMessageRow)).first() is None
    _fail(uow_factory, "nobody-last", client_key="client:last")

    with pytest.raises(AuthenticationRateLimited, match="Вход временно закрыт"):
        login("petrov", "correct-password", NOW, uow_factory(), client_key="client:fresh")
    with identity_database() as session:
        assert session.get(IdentityLoginThrottleRow, "global:login").failures == 30
        alerts = list(session.scalars(select(OutboxMessageRow)))
        failed = list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "identity.login_failed")))
    assert len(alerts) == 1
    assert alerts[0].payload["recipient_audiences"] == "technologist" and alerts[0].payload["type"] == "security"
    assert "всего — 30" in alerts[0].payload["body"]
    assert len(failed) == 30 and failed[-1].details["total"] == "30"


def test_ten_failures_on_one_account_alert_once(identity_database):
    uow_factory = factory(identity_database)
    for _ in range(10):
        _fail(uow_factory, "petrov")
    with pytest.raises(AuthenticationRateLimited, match="Слишком много попыток"):
        login("petrov", "correct-password", NOW, uow_factory(), client_key="client:test")
    with identity_database() as session:
        alerts = list(session.scalars(select(OutboxMessageRow)))
    assert len(alerts) == 1 and "«petrov» — 10" in alerts[0].payload["body"]


# ── Own password and profile ───────────────────────────────────────────────────────────────────────


def test_own_password_change_needs_the_current_one(identity_database):
    uow_factory = factory(identity_database)
    actor = Actor(id=USER_ID, name="Петров", department="Закупки")
    with pytest.raises(AuthenticationError):
        change_own_password(USER_ID, "wrong-password", "brand-new-password", actor, NOW, uow_factory())
    change_own_password(USER_ID, "correct-password", "brand-new-password", actor, NOW, uow_factory())

    assert login("petrov", "brand-new-password", NOW, uow_factory(), client_key="client:test").principal
    _fail(uow_factory, "petrov")  # the old one is gone


def test_profile_and_password_over_http(access_api):  # noqa: F811
    buyer = _client("buyer", "buyer-password")
    profile = buyer.get("/api/auth/profile").json()
    # The header shows the access level now; `role` stays the legacy role key for one release.
    assert (profile["access_level"], profile["access_level_label"], profile["role"]) == (
        "tech_lead", "Технический руководитель", "buyer",
    )
    assert profile["role_label"] == "Технический руководитель" and "department" in profile and "site_label" in profile

    wrong = buyer.post("/api/auth/password", json={"current": "nope-nope", "new": "another-password"})
    short = buyer.post("/api/auth/password", json={"current": "buyer-password", "new": "short"})
    done = buyer.post("/api/auth/password", json={"current": "buyer-password", "new": "another-password"})

    assert wrong.status_code == 422 and "Текущий пароль" in wrong.json()["detail"]
    assert short.status_code == 422
    assert done.status_code == 204
    assert _client("buyer", "another-password").get("/api/auth/me").status_code == 200
