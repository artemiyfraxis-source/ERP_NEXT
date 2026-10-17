"""Correction of a returned box: a sessionless output replaces one output, a replaced defect record is marked."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0026_production_output_corrections"
down_revision: str | None = "0025_references_gost_awg_seed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A correction is not work: it has no session and no time of its own.
    with op.batch_alter_table("production_outputs") as batch:
        batch.alter_column("session_id", existing_type=sa.Uuid(), nullable=True)
        batch.create_index("ux_production_outputs_correction_of_id", ["correction_of_id"], unique=True)
    with op.batch_alter_table("production_defects") as batch:
        batch.add_column(sa.Column("replaced_by_output_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_production_defects_replaced_by_output_id", "production_outputs",
            ["replaced_by_output_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ux_production_defects_replaced_by_output_id", ["replaced_by_output_id"], unique=True)


def downgrade() -> None:
    # Fails while correction outputs exist: their session_id is NULL by design.
    with op.batch_alter_table("production_defects") as batch:
        batch.drop_index("ux_production_defects_replaced_by_output_id")
        batch.drop_constraint("fk_production_defects_replaced_by_output_id", type_="foreignkey")
        batch.drop_column("replaced_by_output_id")
    with op.batch_alter_table("production_outputs") as batch:
        batch.drop_index("ux_production_outputs_correction_of_id")
        batch.alter_column("session_id", existing_type=sa.Uuid(), nullable=False)
