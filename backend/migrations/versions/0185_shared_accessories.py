"""Allow shared accessories without changing existing instrument links."""

from alembic import op
import sqlalchemy as sa

revision = "0185_shared_accessories"
down_revision = "0184_shipping_item_addressees"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_instruments"
CONSTRAINT = "ck_production_instruments_accessory"
EVIDENCE = (
    "verification_number IS NULL AND verified_at IS NULL AND verification_due IS NULL "
    "AND status IN ('Действует', 'Не используется')"
)


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"kind <> 'Принадлежность' OR ({EVIDENCE})")


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE kind = 'Принадлежность' AND parent_id IS NULL"
    )).scalar():
        raise RuntimeError("Нельзя отменить миграцию: есть общие принадлежности без родительского СИ.")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, f"kind <> 'Принадлежность' OR (parent_id IS NOT NULL AND {EVIDENCE})")
