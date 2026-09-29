"""ID-3 / ID-5: SUPPLYMAP PORTAL_SECTIONS_ keys (erptctpl, tcgen, erpref, staffdir) and portalAdminAccess_."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.domain import ROLE_DEFAULTS, SECTION_LABELS, AccessMode, role_permissions
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor


ANY_ID = "99999999-9999-9999-9999-999999999999"
client = TestClient(app)


# ── ID-5: the administrator has every section unless told otherwise ────────────────────────────────


def test_admin_has_every_section_on_edit_without_a_matrix():
    assert role_permissions("admin", {}) == {section: AccessMode.EDIT for section in SECTION_LABELS}
    closed = role_permissions("admin", {"tcgen": AccessMode.NONE, "erpref": AccessMode.VIEW})
    assert "tcgen" not in closed and closed["erpref"] is AccessMode.VIEW


def test_new_keys_are_sections_of_their_own():
    assert {"erptctpl", "tcgen", "erpref", "staffdir"} <= set(SECTION_LABELS)
    assert not {"references", "identity"} & set(SECTION_LABELS)
    assert (SECTION_LABELS["erptctpl"], SECTION_LABELS["tcgen"]) == ("Шаблоны техкарт", "Генератор техкарт")
    for role, defaults in ROLE_DEFAULTS.items():
        if role != "admin":
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


def test_staff_directory_takes_staffdir_and_access_takes_the_admin_role(as_principal):
    as_principal({"staffdir": "edit"}, role="admin")
    assert client.get("/api/auth/staff").status_code != 403
    as_principal({}, role="admin")
    assert client.get("/api/auth/staff").status_code == 403
    assert client.get("/api/auth/access").status_code != 403
    as_principal({"staffdir": "edit"}, role="buyer")
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

    command.upgrade(config, "0045_identity_split_sections")
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
