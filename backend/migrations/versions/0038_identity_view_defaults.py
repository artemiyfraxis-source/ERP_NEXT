"""«Режим просмотра» (ID-6): default view grants on procurement, receiving and warehouse.

Mirrors the ROLE_DEFAULTS additions for databases whose roles were seeded earlier. ON CONFLICT DO NOTHING:
a row an administrator already set (any mode) stays as it is.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0038_identity_view_defaults"
down_revision: str | None = "0037_procurement_replacement_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the ID-6 grants (a migration must not import application code).
VIEW_GRANTS: tuple[tuple[str, str], ...] = (
    ("buyer", "warehouse"),
    ("receiver", "procurement"),
    ("prodhead", "procurement"),
    ("prodhead", "receiving"),
    ("prodhead", "warehouse"),
    ("management", "procurement"),
    ("management", "receiving"),
    ("management", "warehouse"),
    ("master", "receiving"),
    ("master", "warehouse"),
    ("staff", "receiving"),
    ("staff", "warehouse"),
)


def upgrade() -> None:
    for role, section in VIEW_GRANTS:
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, :section, 'view')
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, section=section))


def downgrade() -> None:
    # Only the exact default rows go; a grant an administrator raised to «edit» is left alone.
    for role, section in VIEW_GRANTS:
        op.execute(sa.text("""
            DELETE FROM identity_role_access
            WHERE role = :role AND section = :section AND mode = 'view'
        """).bindparams(role=role, section=section))
