"""«Доступы»: an empty cell = closed, as SUPPLYMAP has since 01.09.2026 (Portal.gs portalApplyAccess_).

Until now an empty cell meant «как задумано ролью» (ROLE_DEFAULTS applied at run time). From here a section is open only
when it is written in the role's matrix row or in a personal exception. So that nobody's access changes on the switch,
every role's former defaults are written into `identity_role_access` as explicit cells; a cell the administrator
already stored (including `none`) is left alone. The administrator role is not written: it keeps «edit everywhere»
unless told otherwise (ID-5, portalAdminAccess_). Downgrade removes the cells equal to the former defaults — under the
old rule they are what an empty cell meant, so access stays the same.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0052_identity_explicit_access"
down_revision: str | None = "0051_identity_split_sections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of ROLE_DEFAULTS as of 0045 (a migration must not import application code).
_EVERYONE = {"newrequest": "edit", "myrequests": "edit"}
SEED: dict[str, dict[str, str]] = {
    "buyer": {**_EVERYONE, "requests": "edit", "procurement": "edit", "warehouse": "view", "erpref": "view"},
    "receiver": {
        **_EVERYONE, "receiving": "edit", "warehouse": "edit", "fulfillment": "edit", "handout": "edit",
        "shipping": "edit", "procurement": "view", "erpref": "view",
    },
    "engineer": {**_EVERYONE, "projects": "edit", "fulfillment": "edit", "handout": "view", "erpref": "view"},
    "prodhead": {
        **_EVERYONE, "procurement": "view", "receiving": "view", "warehouse": "view", "fulfillment": "view",
        "handout": "view", "production": "view", "shipping": "view", "erpref": "view",
    },
    "master": {
        **_EVERYONE, "receiving": "view", "warehouse": "view", "fulfillment": "view", "handout": "view",
        "production": "edit", "shipping": "view", "erpref": "view",
    },
    "management": {
        **_EVERYONE, "procurement": "view", "receiving": "view", "warehouse": "view", "production": "view",
        "shipping": "view", "erpref": "view", "operations": "view",
    },
    "pm": {**_EVERYONE, "shipping": "edit", "erpref": "view"},
    "staff": {
        **_EVERYONE, "requests": "edit", "receiving": "view", "warehouse": "view", "production": "view",
        "erpref": "view",
    },
    "estimator": {**_EVERYONE, "erpref": "view"},
    "qc": {**_EVERYONE, "production": "edit", "erpref": "view"},
}


def upgrade() -> None:
    connection = op.get_bind()
    stored = {
        (role, section)
        for role, section in connection.execute(sa.text("SELECT role, section FROM identity_role_access"))
    }
    insert = sa.text("INSERT INTO identity_role_access (role, section, mode) VALUES (:role, :section, :mode)")
    for role, row in SEED.items():
        for section, mode in row.items():
            if (role, section) not in stored:
                connection.execute(insert, {"role": role, "section": section, "mode": mode})


def downgrade() -> None:
    connection = op.get_bind()
    delete = sa.text("DELETE FROM identity_role_access WHERE role = :role AND section = :section AND mode = :mode")
    for role, row in SEED.items():
        for section, mode in row.items():
            connection.execute(delete, {"role": role, "section": section, "mode": mode})
