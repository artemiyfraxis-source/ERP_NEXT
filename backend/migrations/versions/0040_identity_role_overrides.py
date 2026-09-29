"""«Доступы» (NAV-ACCESS): `identity_role_access` keeps only the administrator's changes on top of ROLE_DEFAULTS.

Before, the defaults were copied into the table when a role got its first user, and the rows were the whole truth.
Now the code adds ROLE_DEFAULTS at runtime, so an empty «Доступы» cell means «как задумано ролью». Nobody's access
changes on upgrade:
1. a role that has users and lacks a row for one of its default sections had that section closed — it gets an
   explicit `none` row (the screen shows it as «закрыт», the administrator may clear it);
2. rows equal to the default are removed — the role follows ROLE_DEFAULTS there.
A role without users keeps only its non-default rows: it used to receive the defaults with its first user anyway.
Downgrade copies the defaults back into roles that have users (ON CONFLICT DO NOTHING); `none` rows mean «closed»
in the old reading too.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0040_identity_role_overrides"
down_revision: str | None = "0039_notification_recipients"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of ROLE_DEFAULTS as of this migration (a migration must not import application code).
DEFAULTS: dict[str, dict[str, str]] = {
    "admin": {
        "identity": "edit", "projects": "edit", "calculation": "edit", "techcards": "edit", "requests": "edit",
        "procurement": "edit", "receiving": "edit", "warehouse": "edit", "fulfillment": "edit",
        "production": "edit", "shipping": "edit", "references": "edit", "operations": "edit",
    },
    "buyer": {"requests": "edit", "procurement": "edit", "warehouse": "view", "references": "view"},
    "receiver": {
        "receiving": "edit", "warehouse": "edit", "fulfillment": "edit", "shipping": "edit", "procurement": "view",
        "references": "view",
    },
    "engineer": {"fulfillment": "edit", "references": "view"},
    "prodhead": {
        "procurement": "view", "receiving": "view", "warehouse": "view", "fulfillment": "view", "production": "view",
        "shipping": "view", "references": "view",
    },
    "master": {
        "receiving": "view", "warehouse": "view", "fulfillment": "view", "production": "edit", "shipping": "view",
        "references": "view",
    },
    "management": {
        "procurement": "view", "receiving": "view", "warehouse": "view", "production": "view", "shipping": "view",
        "references": "view", "operations": "view",
    },
    "pm": {"shipping": "edit", "references": "view"},
    "staff": {
        "requests": "edit", "receiving": "view", "warehouse": "view", "production": "view", "references": "view",
    },
    "estimator": {"references": "view"},
    "qc": {"production": "edit", "references": "view"},
}


def _roles_with_users(connection) -> set[str]:
    return {row[0] for row in connection.execute(sa.text("SELECT DISTINCT role FROM identity_users"))}


def _rows(connection) -> dict[tuple[str, str], str]:
    rows = connection.execute(sa.text("SELECT role, section, mode FROM identity_role_access"))
    return {(role, section): mode for role, section, mode in rows}


def upgrade() -> None:
    connection = op.get_bind()
    staffed = _roles_with_users(connection)
    rows = _rows(connection)
    insert = sa.text("INSERT INTO identity_role_access (role, section, mode) VALUES (:role, :section, 'none')")
    remove = sa.text("DELETE FROM identity_role_access WHERE role = :role AND section = :section AND mode = :mode")
    for role, access in DEFAULTS.items():
        for section, mode in access.items():
            current = rows.get((role, section))
            if current is None and role in staffed:
                connection.execute(insert.bindparams(role=role, section=section))
            elif current == mode:
                connection.execute(remove.bindparams(role=role, section=section, mode=mode))


def downgrade() -> None:
    connection = op.get_bind()
    for role in sorted(_roles_with_users(connection) & set(DEFAULTS)):
        for section, mode in DEFAULTS[role].items():
            connection.execute(sa.text("""
                INSERT INTO identity_role_access (role, section, mode)
                VALUES (:role, :section, :mode)
                ON CONFLICT (role, section) DO NOTHING
            """).bindparams(role=role, section=section, mode=mode))
