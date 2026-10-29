"""Разрешительный ПИН (владелец 29.10.2026): «ПИН того же, что для входа — нет; давай какой-то другой: в профиле будет
как разрешительный ПИН, может где-то ещё будем такое использовать».

Что делает миграция:
- `identity_credentials` (секреты сотрудника, рядом с паролем и PIN входа): `approval_pin_hash` — разрешительный ПИН как
  Argon2id-хеш (те же параметры, что у PIN входа), `approval_pin_set_at` — когда задан. NULL — не задан. CHECK
  `ck_identity_credentials_approval_pin`: хеш и дата — вместе или никак.
Данные не трогаются: разрешительный ПИН никому не задан, каждый задаёт его сам в своей карточке. До этого «Отгрузку без
УПД» никто разрешить не сможет — владелец знает (разрешение переходит с PIN входа на разрешительный ПИН).

DESTRUCTIVE = False: столбцы добавляются пустыми. Откат снимает CHECK и столбцы (заданные разрешительные ПИН теряются,
их зададут заново).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0163_approval_pin"
down_revision: str | None = "0162_pack_decision_no_upd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

CREDENTIALS = "identity_credentials"
CHECK = "ck_identity_credentials_approval_pin"
CHECK_SQL = (
    "(approval_pin_hash IS NULL AND approval_pin_set_at IS NULL)"
    " OR (approval_pin_hash IS NOT NULL AND approval_pin_set_at IS NOT NULL)"
)


def _count(bind) -> int:
    return int(bind.execute(sa.text(f"SELECT COUNT(*) FROM {CREDENTIALS}")).scalar() or 0)


def upgrade() -> None:
    bind = op.get_bind()
    before = _count(bind)
    with op.batch_alter_table(CREDENTIALS) as batch:
        batch.add_column(sa.Column("approval_pin_hash", sa.String(512), nullable=True))
        batch.add_column(sa.Column("approval_pin_set_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(CHECK, CHECK_SQL)
    print(f"0163_approval_pin: {CREDENTIALS} {before} → {_count(bind)}, разрешительных ПИН задано 0")


def downgrade() -> None:
    with op.batch_alter_table(CREDENTIALS) as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.drop_column("approval_pin_set_at")
        batch.drop_column("approval_pin_hash")
