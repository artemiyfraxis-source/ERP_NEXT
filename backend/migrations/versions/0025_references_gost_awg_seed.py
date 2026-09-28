"""Seed the workshop ГОСТ section → AWG map with SUPPLYMAP СПР.КАБ values."""

from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0025_references_gost_awg_seed"
down_revision: str | None = "0024_procurement_money"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (key as shown, normalized_section_key, AWG) — СПР.КАБ.
_SEED = (
    ("0,35", "0.35", "22"),
    ("0,75", "0.75", "18"),
    ("1,0", "1", "17"),
)


def upgrade() -> None:
    # A row the admin already keeps for the same section wins; seeded rows go after the admin's own.
    # `WHERE 1 = 1` resolves SQLite's INSERT … SELECT … ON CONFLICT parsing ambiguity.
    statement = sa.text("""
        INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)
        SELECT :id, 'gost_awg', :key, :normalized_key, :value,
            COALESCE((SELECT MAX(position) FROM reference_entries WHERE kind = 'gost_awg'), -1) + 1
        WHERE 1 = 1
        ON CONFLICT (kind, normalized_key) DO NOTHING
    """).bindparams(sa.bindparam("id", type_=sa.Uuid()))
    for key, normalized_key, value in _SEED:
        op.get_bind().execute(
            statement, {"id": uuid4(), "key": key, "normalized_key": normalized_key, "value": value},
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM reference_entries WHERE kind = 'gost_awg'"))
