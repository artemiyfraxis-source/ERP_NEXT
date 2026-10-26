"""Личные настройки экранов (owner 26.10.2026: «настройка показываемых столбцов у каждого своя»).

- `identity_ui_settings` — one row per person and screen view («stock.grouped», «stock.flat», «stock.goods»): which
  columns, in what order, widths — a JSON object as text. The person's own: the key is (user_id, view), the row goes
  with the person (ON DELETE CASCADE, like their sessions and credentials). The size is held by a CHECK
  (`length(value) <= 16384`), the view name by a CHECK on its length; the name pattern and the count per person are
  checked by the application.

Non-destructive: one new empty table. DESTRUCTIVE = False. Downgrade drops it (the browsers keep their local copy).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0154_user_ui_settings"
down_revision: str | None = "0153_upd_scan_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False
TABLE = "identity_ui_settings"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "user_id", sa.Uuid(),
            sa.ForeignKey("identity_users.id", ondelete="CASCADE", name="fk_identity_ui_settings_user_id"),
            primary_key=True,
        ),
        sa.Column("view", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(view) >= 1", name="ck_identity_ui_settings_view"),
        sa.CheckConstraint("length(value) <= 16384", name="ck_identity_ui_settings_value_size"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
