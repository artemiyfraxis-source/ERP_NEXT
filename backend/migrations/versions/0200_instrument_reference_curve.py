"""«Эталонная кривая» of an instrument: one measurement file per instrument the weekly control check compares with.

СТО ФРАК-002-2026, разд. 14: the control standard (СИ.009) is checked weekly against its stored reference curve. The mark
lives on the file row; a partial UNIQUE index allows one per instrument, a CHECK allows only a Touchstone file (.s2p).
Every existing file starts unmarked. Not destructive.
"""

from alembic import op
import sqlalchemy as sa

revision = "0200_instrument_reference_curve"
down_revision = "0199_equipment_setup_model"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copy of infrastructure.REFERENCE_CURVE_CHECK as of this revision.
REFERENCE_CURVE_CHECK = "NOT is_reference OR lower(file_name) LIKE '%.s2p'"


def upgrade() -> None:
    with op.batch_alter_table("production_instrument_files") as batch:
        batch.add_column(sa.Column("is_reference", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_check_constraint("ck_production_instrument_files_reference", REFERENCE_CURVE_CHECK)
    op.create_index("uq_production_instrument_files_reference", "production_instrument_files", ["instrument_id"], unique=True,
                    sqlite_where=sa.text("is_reference"), postgresql_where=sa.text("is_reference"))


def downgrade() -> None:
    op.drop_index("uq_production_instrument_files_reference", table_name="production_instrument_files")
    with op.batch_alter_table("production_instrument_files") as batch:
        batch.drop_constraint("ck_production_instrument_files_reference", type_="check")
        batch.drop_column("is_reference")
