"""Монтажные провода: материал жилы и толщина изоляции (владелец 12.10.2026: «нормальная база монтажных проводов …,
полный набор параметров»).

Schema only, nothing moves (the production database is live):
- `hookup_wires.conductor_material` — the conductor («медь луженая», «медь посеребрённая»), text, NOT NULL, '' for
  every existing row;
- `hookup_wires.insulation_thickness_mm` — the insulation wall, mm, empty for every existing row, > 0 when given.
Both are filled by `python -m app.modules.references.load_cables --hookups` from `backend/data/cables/hookup-wires.json`.

Downgrade drops the two columns with their values.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0103_hookup_wire_conductor_insulation"
down_revision: str | None = "0102_techcard_generator_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_hookup_wires_insulation_thickness_positive"


def upgrade() -> None:
    with op.batch_alter_table("hookup_wires") as batch:
        batch.add_column(sa.Column("conductor_material", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("insulation_thickness_mm", sa.Numeric(8, 3), nullable=True))
        batch.create_check_constraint(CHECK, "insulation_thickness_mm IS NULL OR insulation_thickness_mm > 0")


def downgrade() -> None:
    with op.batch_alter_table("hookup_wires") as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.drop_column("insulation_thickness_mm")
        batch.drop_column("conductor_material")
