"""«Доступы»: an empty cell = closed (SUPPLYMAP Portal.gs portalApplyAccess_, 01.09.2026); migration 0046 writes the
former role defaults as explicit cells so nobody's access changes; «Оформить заявку» is a matrix cell like any other."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.modules.identity.domain import ROLE_DEFAULTS, ROLE_SEED, ROLES, SECTION_LABELS, AccessMode, role_permissions
from tests.identity.test_access import _client, access_api  # noqa: F401  (fixture)


def test_only_the_administrator_has_anything_without_a_cell():
    for role in ROLES - {"admin"}:
        assert role_permissions(role, {}) == {}, role
    assert role_permissions("admin", {}) == {section: AccessMode.EDIT for section in SECTION_LABELS}
    assert set(ROLE_DEFAULTS) == {"admin"}


def test_newrequest_follows_the_matrix(access_api):  # noqa: F811
    admin = _client("admin", "admin-password")
    buyer = _client("buyer", "buyer-password")
    assert buyer.post("/api/requests", json={}).status_code != 403

    matrix = admin.get("/api/auth/access").json()["matrix"]
    matrix["buyer"].pop("newrequest")  # an emptied cell closes filing
    assert admin.put("/api/auth/access/roles", json={"matrix": matrix}).status_code == 200

    assert "newrequest" not in buyer.get("/api/auth/me").json()["permissions"]
    assert buyer.post("/api/requests", json={}).status_code == 403


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _access(database_path: Path) -> dict[tuple[str, str], str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT role, section, mode FROM identity_role_access")
        return {(role, section): mode for role, section, mode in rows}


def test_migration_writes_former_defaults_and_keeps_the_administrators_cells(tmp_path):
    database_path = tmp_path / "identity.db"
    config = _config(database_path)
    command.upgrade(config, "0055_identity_split_sections")
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM identity_role_access")
        connection.executemany("INSERT INTO identity_role_access VALUES (?, ?, ?)", [
            ("buyer", "warehouse", "none"),  # closed by the administrator — stays closed
            ("staff", "shipping", "view"),  # granted on top — stays
            ("admin", "operations", "view"),
        ])

    command.upgrade(config, "0056_identity_explicit_access")
    access = _access(database_path)
    expected = {
        (role, section): mode.value for role, row in ROLE_SEED.items() for section, mode in row.items()
    }
    expected.update({("buyer", "warehouse"): "none", ("staff", "shipping"): "view", ("admin", "operations"): "view"})
    assert access == expected

    command.downgrade(config, "0055_identity_split_sections")
    assert _access(database_path) == {
        ("buyer", "warehouse"): "none", ("staff", "shipping"): "view", ("admin", "operations"): "view",
    }
