"""NAV-SECTIONS: «Оформить заявку» (`newrequest`), «Мои заявки» (`myrequests`) and «Выдача со списанием» (`handout`)
are sections of their own (SUPPLYMAP Portal.gs PORTAL_SECTIONS_, Issue.gs portalIssueGuard_)."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app
from app.modules.identity.domain import ROLE_DEFAULTS, ROLE_SEED, ROLES, SECTION_LABELS, AccessMode, role_permissions
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor


ANY_ID = "99999999-9999-9999-9999-999999999999"
client = TestClient(app)


# ── Defaults ───────────────────────────────────────────────────────────────────────────────────────


def test_every_role_files_requests_and_sees_its_own_by_default():
    for role in ROLES:
        assert (ROLE_SEED.get(role) or ROLE_DEFAULTS[role])["newrequest"] is AccessMode.EDIT, role
        assert (ROLE_SEED.get(role) or ROLE_DEFAULTS[role])["myrequests"] is AccessMode.EDIT, role


def test_handout_defaults_follow_who_hands_out_and_who_reads_fulfillment():
    handout = {role: (ROLE_SEED.get(role) or ROLE_DEFAULTS[role]).get("handout") for role in ROLES}
    assert handout == {
        "admin": AccessMode.EDIT, "receiver": AccessMode.EDIT,
        "engineer": AccessMode.VIEW, "prodhead": AccessMode.VIEW, "master": AccessMode.VIEW,
        "buyer": None, "management": None, "pm": None, "staff": None, "estimator": None, "qc": None,
    }


def test_access_screen_lists_the_three_keys_next_to_their_neighbours():
    keys = list(SECTION_LABELS)
    assert keys.index("newrequest") < keys.index("myrequests") < keys.index("requests")
    assert keys.index("handout") == keys.index("fulfillment") + 1
    assert (SECTION_LABELS["newrequest"], SECTION_LABELS["myrequests"], SECTION_LABELS["handout"]) == (
        "Оформить заявку", "Мои заявки", "Выдача со списанием",
    )


def test_closing_requests_keeps_filing_and_own_rows():
    staff = role_permissions("staff", {**ROLE_SEED["staff"], "requests": AccessMode.NONE})
    assert "requests" not in staff
    assert staff["newrequest"] is AccessMode.EDIT and staff["myrequests"] is AccessMode.EDIT


# ── Guards ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def as_principal():
    def use(permissions: dict[str, str], role: str = "staff") -> None:
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(name="Сотрудник", department="Цех"),
            email="employee@example.test",
            roles=frozenset({role}),
            permissions=permissions,
        )

    yield use
    app.dependency_overrides.pop(authenticated_principal, None)


CREATE_ROUTES = [
    ("POST", "/api/requests"),
    ("POST", "/api/requests/import/parse"),
    ("POST", "/api/requests/import"),
]


def _status(method: str, path: str) -> int:
    return client.request(method, path, json={}).status_code


def test_filing_takes_newrequest_edit_not_requests(as_principal):
    as_principal({"requests": "edit"})
    for method, path in CREATE_ROUTES:
        assert _status(method, path) == 403, path
    as_principal({"newrequest": "view"})
    for method, path in CREATE_ROUTES:
        assert _status(method, path) == 403, path
    as_principal({"newrequest": "edit"})
    for method, path in CREATE_ROUTES:
        assert _status(method, path) != 403, path


def test_form_reference_data_opens_from_newrequest_or_requests(as_principal):
    for permissions in ({"newrequest": "edit"}, {"requests": "view"}):
        as_principal(permissions)
        assert client.get("/api/requests/schema").status_code == 200
        assert _status("POST", "/api/requests/validate") != 403
    as_principal({"myrequests": "edit"})
    assert client.get("/api/requests/schema").status_code == 403


def test_own_rows_take_myrequests_and_every_row_takes_requests(as_principal):
    as_principal({"requests": "edit", "newrequest": "edit"})
    assert client.get("/api/requests/mine").status_code == 403
    as_principal({"myrequests": "view", "newrequest": "edit"})
    assert client.get("/api/requests/mine").status_code != 403
    assert client.get("/api/requests").status_code == 403


HANDOUT_ROUTES = ["ready", "issue", "returned", "complete-return"]
PICK_ROUTES = [
    ("POST", f"/api/issues/{ANY_ID}/lines/{ANY_ID}/bind"),
    ("DELETE", f"/api/issues/{ANY_ID}/lines/{ANY_ID}/bind"),
    ("POST", f"/api/issues/{ANY_ID}/cancel"),
    ("POST", "/api/issues"),
]


def test_handout_takes_handout_edit_and_picking_stays_on_fulfillment(as_principal):
    as_principal({"fulfillment": "edit"}, role="receiver")
    for route in HANDOUT_ROUTES:
        assert _status("POST", f"/api/issues/{ANY_ID}/{route}") == 403, route
    for method, path in PICK_ROUTES:
        assert _status(method, path) != 403, path

    as_principal({"handout": "edit"}, role="receiver")
    for route in HANDOUT_ROUTES:
        assert _status("POST", f"/api/issues/{ANY_ID}/{route}") != 403, route
    for method, path in PICK_ROUTES:
        assert _status(method, path) == 403, path

    as_principal({"handout": "view"}, role="receiver")
    for route in HANDOUT_ROUTES:
        assert _status("POST", f"/api/issues/{ANY_ID}/{route}") == 403, route


def test_the_shared_list_reads_from_either_section(as_principal):
    for permissions in ({"fulfillment": "view"}, {"handout": "view"}):
        as_principal(permissions, role="master")
        assert client.get("/api/issues").status_code != 403, permissions
        assert client.get(f"/api/issues/{ANY_ID}/print").status_code != 403, permissions
    as_principal({"newrequest": "edit"}, role="master")
    assert client.get("/api/issues").status_code == 403


# ── Migration 0041 ─────────────────────────────────────────────────────────────────────────────────


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


USER_A = "00000000000000000000000000000001"
USER_B = "00000000000000000000000000000002"


def test_migration_carries_stored_requests_and_fulfillment_values(tmp_path):
    database_path = tmp_path / "identity.db"
    config = _config(database_path)
    command.upgrade(config, "0040_identity_role_overrides")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM identity_role_access")
        connection.executemany(
            "INSERT INTO identity_users (id, login, email, name, department, role, site, active) "
            "VALUES (?, ?, ?, ?, '', ?, NULL, 1)",
            [(USER_A, "a", "a@x.test", "A", "receiver"), (USER_B, "b", "b@x.test", "B", "staff")],
        )
        connection.executemany("INSERT INTO identity_role_access VALUES (?, ?, ?)", [
            ("staff", "requests", "none"),  # today staff neither files nor sees own rows
            ("buyer", "requests", "edit"),  # equal to the new defaults — nothing to store
            ("management", "requests", "view"),  # can read, cannot file
            ("receiver", "fulfillment", "view"),  # today the receiver cannot hand out
            ("engineer", "fulfillment", "none"),
            ("pm", "requests", "none"), ("pm", "newrequest", "edit"),  # already set — left alone
        ])
        connection.executemany("INSERT INTO identity_user_access VALUES (?, ?, ?)", [
            (USER_A, "requests", "none"), (USER_A, "fulfillment", "edit"),
            (USER_B, "requests", "edit"),
        ])

    command.upgrade(config, "0041_identity_own_sections")
    with sqlite3.connect(database_path) as connection:
        roles = {(r, s): m for r, s, m in connection.execute("SELECT role, section, mode FROM identity_role_access")}
        users = {(u, s): m for u, s, m in connection.execute("SELECT user_id, section, mode FROM identity_user_access")}
    assert {key: mode for key, mode in roles.items() if key[1] in ("newrequest", "myrequests", "handout")} == {
        ("staff", "newrequest"): "none", ("staff", "myrequests"): "none",
        ("management", "newrequest"): "view", ("management", "myrequests"): "view",
        ("receiver", "handout"): "view",
        ("engineer", "handout"): "none",
        ("pm", "newrequest"): "edit", ("pm", "myrequests"): "none",
    }
    assert users == {
        (USER_A, "requests"): "none", (USER_A, "newrequest"): "none", (USER_A, "myrequests"): "none",
        (USER_A, "fulfillment"): "edit", (USER_A, "handout"): "edit",
        (USER_B, "requests"): "edit", (USER_B, "newrequest"): "edit", (USER_B, "myrequests"): "edit",
    }

    command.downgrade(config, "0040_identity_role_overrides")
    with sqlite3.connect(database_path) as connection:
        sections = {row[0] for row in connection.execute(
            "SELECT section FROM identity_role_access UNION SELECT section FROM identity_user_access"
        )}
    assert sections.isdisjoint({"newrequest", "myrequests", "handout"})
    assert "requests" in sections and "fulfillment" in sections
