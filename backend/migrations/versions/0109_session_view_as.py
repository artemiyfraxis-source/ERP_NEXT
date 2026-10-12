"""«Смотреть как сотрудник» (владелец 12.10.2026: «прям полностью смотреть от чьего-то лица … из моей карточки»).

Schema only, nothing moves (the production database is live):
- `identity_sessions.view_as_user_id` — whose eyes this session looks through (read-only); empty for every existing
  session. FK to `identity_users` ON DELETE SET NULL: deleting the viewed employee ends the view, not the session;
- `identity_sessions.view_as_until` — when the view ends by itself (2 h after the start);
- CHECK `ck_identity_sessions_view_as_until` — a viewed employee always comes with an end;
- CHECK `ck_identity_sessions_view_as_not_self` — a session never views its own user.

DESTRUCTIVE = False. Downgrade drops the checks, the FK and the two columns (the open views end).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0109_session_view_as"
down_revision: str | None = "0108_coax_cable_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

FK = "fk_identity_sessions_view_as_user_id"
UNTIL = "ck_identity_sessions_view_as_until"
NOT_SELF = "ck_identity_sessions_view_as_not_self"


def upgrade() -> None:
    with op.batch_alter_table("identity_sessions") as batch:
        batch.add_column(sa.Column("view_as_user_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("view_as_until", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(FK, "identity_users", ["view_as_user_id"], ["id"], ondelete="SET NULL")
        batch.create_check_constraint(UNTIL, "view_as_user_id IS NULL OR view_as_until IS NOT NULL")
        batch.create_check_constraint(NOT_SELF, "view_as_user_id IS NULL OR view_as_user_id <> user_id")


def downgrade() -> None:
    with op.batch_alter_table("identity_sessions") as batch:
        batch.drop_constraint(NOT_SELF, type_="check")
        batch.drop_constraint(UNTIL, type_="check")
        batch.drop_constraint(FK, type_="foreignkey")
        batch.drop_column("view_as_until")
        batch.drop_column("view_as_user_id")
