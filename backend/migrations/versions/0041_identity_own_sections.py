"""NAV-SECTIONS: «Оформить заявку», «Мои заявки» and «Выдача со списанием» get section keys of their own.

The new keys reach every role through ROLE_DEFAULTS, so nothing is seeded. Only what the administrator already stored
is carried over, so that nobody's access changes on upgrade:
- a stored `requests` value (role matrix or personal exception) is copied to `newrequest` and `myrequests` — before,
  filing and one's own rows lived under `requests`, so an administrator's `requests: none` kept closing them;
- a stored `fulfillment` value is copied to `handout` — before, handing out needed `fulfillment: edit`.
A role row equal to the new key's default is not stored (the table keeps only changes, NAV-ACCESS); a key that already
has a stored value is left alone. Personal exceptions are copied as they are. Downgrade drops the three keys.
"""

from typing import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0041_identity_own_sections"
down_revision: str | None = "0040_identity_role_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the new keys in ROLE_DEFAULTS as of this migration (a migration must not import application code).
_EVERYONE = {"newrequest": "edit", "myrequests": "edit"}
DEFAULTS: dict[str, dict[str, str]] = {
    "admin": {**_EVERYONE, "handout": "edit"},
    "buyer": dict(_EVERYONE),
    "receiver": {**_EVERYONE, "handout": "edit"},
    "engineer": {**_EVERYONE, "handout": "view"},
    "prodhead": {**_EVERYONE, "handout": "view"},
    "master": {**_EVERYONE, "handout": "view"},
    "management": dict(_EVERYONE),
    "pm": dict(_EVERYONE),
    "staff": dict(_EVERYONE),
    "estimator": dict(_EVERYONE),
    "qc": dict(_EVERYONE),
}
CARRY: dict[str, tuple[str, ...]] = {"requests": ("newrequest", "myrequests"), "fulfillment": ("handout",)}
NEW_SECTIONS = ("newrequest", "myrequests", "handout")


def upgrade() -> None:
    connection = op.get_bind()
    role_rows = {
        (role, section): mode
        for role, section, mode in connection.execute(sa.text("SELECT role, section, mode FROM identity_role_access"))
    }
    insert_role = sa.text("INSERT INTO identity_role_access (role, section, mode) VALUES (:role, :section, :mode)")
    for (role, old), mode in sorted(role_rows.items()):
        for new in CARRY.get(old, ()):
            if (role, new) in role_rows or DEFAULTS.get(role, {}).get(new, "none") == mode:
                continue
            connection.execute(insert_role.bindparams(role=role, section=new, mode=mode))

    user_rows = {
        (user_id if isinstance(user_id, UUID) else UUID(str(user_id)), section): mode
        for user_id, section, mode in connection.execute(
            sa.text("SELECT user_id, section, mode FROM identity_user_access")
        )
    }
    insert_user = sa.text(
        "INSERT INTO identity_user_access (user_id, section, mode) VALUES (:user_id, :section, :mode)"
    ).bindparams(sa.bindparam("user_id", type_=sa.Uuid(as_uuid=True)))
    for (user_id, old), mode in sorted(user_rows.items(), key=lambda item: (item[0][0].hex, item[0][1])):
        for new in CARRY.get(old, ()):
            if (user_id, new) in user_rows:
                continue
            connection.execute(insert_user, {"user_id": user_id, "section": new, "mode": mode})


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("identity_role_access", "identity_user_access"):
        connection.execute(
            sa.text(f"DELETE FROM {table} WHERE section IN ('newrequest', 'myrequests', 'handout')")
        )
