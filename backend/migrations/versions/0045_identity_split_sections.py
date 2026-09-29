"""ID-3: «Шаблоны техкарт», «Генератор», «Справочник» and «Сотрудники» get the access keys of SUPPLYMAP PORTAL_SECTIONS_.

Keys: `erptctpl` (Шаблоны техкарт) and `tcgen` (Генератор техкарт) split off `techcards`; `references` becomes `erpref`,
`identity` becomes `staffdir` («Доступы» is no section any more — always the administrator's). Nobody's access changes
on upgrade:
- stored `references` / `identity` values (role matrix and personal exceptions) are renamed to `erpref` / `staffdir`;
- a stored `techcards` value is copied to `erptctpl` and `tcgen` (before, templates and the generator lived under it).
A copied role row equal to the new key's default is not stored (the table keeps only changes, NAV-ACCESS); a key that
already has a stored value is left alone. ID-5: the administrator's defaults are «edit everywhere», so a stored admin
row equal to `edit` is redundant but harmless and is kept. Downgrade renames back and drops the two new keys.
"""

from typing import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0045_identity_split_sections"
down_revision: str | None = "0042_shipping_project_addressees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAME = {"references": "erpref", "identity": "staffdir"}
CARRY: dict[str, tuple[str, ...]] = {"techcards": ("erptctpl", "tcgen")}
# Frozen defaults of the new keys as of this migration: only the administrator has them (edit).
DEFAULTS: dict[str, dict[str, str]] = {"admin": {"erptctpl": "edit", "tcgen": "edit"}}


def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def upgrade() -> None:
    connection = op.get_bind()
    for table in ("identity_role_access", "identity_user_access"):
        for old, new in RENAME.items():
            connection.execute(
                sa.text(f"UPDATE {table} SET section = :new WHERE section = :old"), {"new": new, "old": old}
            )

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
        (_uuid(user_id), section): mode
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
        connection.execute(sa.text(f"DELETE FROM {table} WHERE section IN ('erptctpl', 'tcgen')"))
        for old, new in RENAME.items():
            connection.execute(
                sa.text(f"UPDATE {table} SET section = :old WHERE section = :new"), {"new": new, "old": old}
            )
