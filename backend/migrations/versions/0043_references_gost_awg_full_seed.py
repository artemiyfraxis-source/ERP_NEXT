"""Complete the ГОСТ section → AWG seed with the rest of SUPPLYMAP СПР.КАБ (0025 had only three rows)."""

from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0043_references_gost_awg_full_seed"
down_revision: str | None = "0042_shipping_project_addressees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (key as shown, normalized_section_key, AWG) — СПР.КАБ AWG table in sheet order, the rows `gostAwgMap_`
# (TcGenData.gs) keeps: numeric ГОСТ and an integer AWG, so 1/0…3/0 stay out. 0,35 / 0,75 / 1 came with 0025.
_SEED = (
    ("0,01", "0.01", "36"),
    ("0,02", "0.02", "34"),
    ("0,03", "0.03", "32"),
    ("0,05", "0.05", "30"),
    ("0,08", "0.08", "28"),
    ("0,12", "0.12", "26"),
    ("0,2", "0.2", "24"),
    ("0,5", "0.5", "20"),
    ("1,5", "1.5", "16"),
    ("2,5", "2.5", "14"),
    ("4", "4", "12"),
    ("6", "6", "10"),
    ("10", "10", "8"),
    ("16", "16", "6"),
    ("25", "25", "4"),
    ("35", "35", "2"),
)


def upgrade() -> None:
    # Same rule as 0025: a row the admin already keeps for the section wins; seeded rows go after it.
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
    # Only rows still holding the seeded value; an admin's own AWG for the section stays.
    statement = sa.text(
        "DELETE FROM reference_entries WHERE kind = 'gost_awg' AND normalized_key = :normalized_key AND value = :value"
    )
    for _key, normalized_key, value in _SEED:
        op.get_bind().execute(statement, {"normalized_key": normalized_key, "value": value})
