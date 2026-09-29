"""ID-6: migration 0038 adds the default view grants without touching rows an administrator already set."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.modules.identity.domain import ROLE_SEED, AccessMode

MIGRATION_GRANTS = {
    ("buyer", "warehouse"),
    ("receiver", "procurement"),
    ("prodhead", "procurement"), ("prodhead", "receiving"), ("prodhead", "warehouse"),
    ("management", "procurement"), ("management", "receiving"), ("management", "warehouse"),
    ("master", "receiving"), ("master", "warehouse"),
    ("staff", "receiving"), ("staff", "warehouse"),
}


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _access(database_path: Path) -> dict[tuple[str, str], str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT role, section, mode FROM identity_role_access")
        return {(role, section): mode for role, section, mode in rows}


def test_migration_grants_match_role_defaults():
    """The migration is a frozen copy; it must stay equal to the view grants ROLE_DEFAULTS seeds for new roles."""
    sections = {"procurement", "receiving", "warehouse"}
    defaults = {
        (role, section)
        for role, access in ROLE_SEED.items()
        for section, mode in access.items()
        if section in sections and mode is AccessMode.VIEW
    }
    assert defaults == MIGRATION_GRANTS


def test_migration_adds_view_grants_and_keeps_manual_settings(tmp_path):
    database_path = tmp_path / "identity.db"
    config = _config(database_path)
    command.upgrade(config, "0037_procurement_replacement_permission")
    with sqlite3.connect(database_path) as connection:
        # An administrator already decided: management edits the warehouse, staff sees no receiving.
        connection.execute("DELETE FROM identity_role_access WHERE role IN ('management', 'staff')")
        connection.execute("INSERT INTO identity_role_access VALUES ('management', 'warehouse', 'edit')")
        connection.execute("INSERT INTO identity_role_access VALUES ('staff', 'receiving', 'none')")

    command.upgrade(config, "0038_identity_view_defaults")
    access = _access(database_path)

    assert access[("management", "warehouse")] == "edit"
    assert access[("staff", "receiving")] == "none"
    for grant in MIGRATION_GRANTS - {("management", "warehouse"), ("staff", "receiving")}:
        assert access[grant] == "view", grant

    command.downgrade(config, "0037_procurement_replacement_permission")
    access = _access(database_path)
    assert access[("management", "warehouse")] == "edit"
    assert access[("staff", "receiving")] == "none"
    assert ("buyer", "warehouse") not in access
