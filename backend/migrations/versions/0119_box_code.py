"""Код коробки для подписи маркером (владелец, 13.10.2026): «180К.11·КБ1·13.10·Б07·3».

- `production_boxes.code` — проект·КБ·дата·код сотрудника·порядковый номер, NULL у коробок до этой миграции
  (их код не восстановить: код сотрудника выдаётся при сдаче). UNIQUE-индекс `ux_production_boxes_code`:
  два кода на одну коробку не выдать; NULL повторяется свободно.
- `production_operator_codes` — короткий код сотрудника («Б07»): PK = сотрудник (FK на identity_users, CASCADE),
  код UNIQUE и непустой. Выдаётся при первой сдаче и больше не меняется — подписанные коробки остаются читаемыми.

Существующие строки не меняются, заполнения нет. DESTRUCTIVE = False. Откат снимает индекс, колонку и таблицу
(коды коробок теряются, номера и коробки остаются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0119_box_code"
down_revision: str | None = "0118_plan_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

BOXES = "production_boxes"
CODES = "production_operator_codes"


def upgrade() -> None:
    op.add_column(BOXES, sa.Column("code", sa.String(64), nullable=True))
    op.create_index("ux_production_boxes_code", BOXES, ["code"], unique=True)
    op.create_table(
        CODES,
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("operator_id", name="pk_production_operator_codes"),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["identity_users.id"], ondelete="CASCADE",
            name="fk_production_operator_codes_operator_id",
        ),
        sa.UniqueConstraint("code", name="uq_production_operator_codes_code"),
        sa.CheckConstraint("code <> ''", name="ck_production_operator_codes_code"),
    )
    print(f"0119_box_code: колонка {BOXES}.code (пустая), таблица {CODES} (пустая)")


def downgrade() -> None:
    op.drop_table(CODES)
    op.drop_index("ux_production_boxes_code", table_name=BOXES)
    with op.batch_alter_table(BOXES) as batch:
        batch.drop_column("code")
