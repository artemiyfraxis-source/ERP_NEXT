"""Number cutting groups in creation order and seed their global transactional series.

The existing UNIQUE constraint on number remains in force throughout the backfill.
IDs, recipe keys, idempotency keys and member history are unchanged.
"""

from alembic import op
import sqlalchemy as sa

revision = "0189_cutting_group_numbers"
down_revision = "0188_instrument_labels"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    bind = op.get_bind()
    identifiers = bind.execute(sa.text(
        "SELECT id FROM production_cutting_groups ORDER BY created_at, id"
    )).scalars().all()
    # Free the destination numbers first, including databases already containing a human number.
    for identifier in identifiers:
        bind.execute(sa.text("UPDATE production_cutting_groups SET number = :number WHERE id = :id"),
                     {"id": identifier, "number": f"cutting-number-{identifier}"})
    for sequence, identifier in enumerate(identifiers, 1):
        bind.execute(sa.text("UPDATE production_cutting_groups SET number = :number WHERE id = :id"),
                     {"id": identifier, "number": f"РЗ-{sequence:04d}"})
    bind.execute(sa.text("INSERT INTO production_sequences (name, value) VALUES ('cutting_group', :value) "
                         "ON CONFLICT (name) DO UPDATE SET value = excluded.value"),
                 {"value": len(identifiers)})
    print(f"{revision}: numbered {len(identifiers)} cutting groups; next number {len(identifiers) + 1}")


def downgrade():
    # Issued human numbers and their counter are history; do not restore hashes or reuse numbers.
    pass
