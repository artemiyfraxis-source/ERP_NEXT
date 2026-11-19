"""Assembly VSWR decision rule of СТО ФРАК-002-2026 п. 13.3 (owner 19.11): «на границе» and the 3-connection repeat.

Adds `uncertainty` (VNA U at the peak), `repeat_of` (the border attempt a repeat belongs to) and `repeat_set` (on the attempt
that decided the repeat: every connection with its peak). The verdict may now be 'border'; a repeat is judged against
norm + U. Existing rows are untouched and keep satisfying the widened constraint (repeat_of IS NULL, simple acceptance).
"""

from alembic import op
import sqlalchemy as sa

revision = "0196_vswr_border_repeat"
down_revision = "0195_vna_check_journal"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_vswr_measurements"
OLD_AUTO = (
    "verdict_manual OR (complete IS FALSE AND verdict = 'fail') OR "
    "(norm_vswr_max IS NOT NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
    " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)))"
)
NEW_AUTO = (
    "verdict_manual OR (complete IS FALSE AND verdict = 'fail') OR (norm_vswr_max IS NOT NULL AND ("
    "(repeat_of IS NULL AND ((verdict = 'pass' AND max_vswr <= norm_vswr_max)"
    " OR (verdict = 'fail' AND max_vswr > norm_vswr_max)"
    " OR (verdict = 'border' AND uncertainty IS NOT NULL AND abs(max_vswr - norm_vswr_max) <= uncertainty + 0.000001)))"
    " OR (repeat_of IS NOT NULL AND uncertainty IS NOT NULL AND ((verdict = 'fail' AND max_vswr > norm_vswr_max + uncertainty - 0.000001)"
    " OR (verdict IN ('pass', 'border') AND max_vswr <= norm_vswr_max + uncertainty + 0.000001)))))"
)


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("uncertainty", sa.Numeric(9, 4), nullable=True))
        batch.add_column(sa.Column("repeat_of", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("repeat_set", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_production_vswr_repeat_of", TABLE, ["repeat_of"], ["id"], ondelete="RESTRICT")
        batch.drop_constraint("ck_production_vswr_verdict", type_="check")
        batch.create_check_constraint("ck_production_vswr_verdict", "verdict IN ('pass', 'fail', 'border')")
        batch.drop_constraint("ck_production_vswr_auto_verdict", type_="check")
        batch.create_check_constraint("ck_production_vswr_auto_verdict", NEW_AUTO)
        batch.create_check_constraint("ck_production_vswr_repeat_set", "repeat_set IS NULL OR repeat_of IS NOT NULL")
        batch.create_check_constraint("ck_production_vswr_uncertainty", "uncertainty IS NULL OR uncertainty > 0")


def downgrade() -> None:
    # Border and repeat-judged attempts cannot be expressed in the old pass/fail-by-norm model.
    blocking = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE verdict = 'border' OR repeat_of IS NOT NULL")).scalar()
    if blocking:
        raise RuntimeError(f"{blocking} VSWR attempts use the border/repeat rule — downgrade would lose their decision")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_production_vswr_uncertainty", type_="check")
        batch.drop_constraint("ck_production_vswr_repeat_set", type_="check")
        batch.drop_constraint("ck_production_vswr_auto_verdict", type_="check")
        batch.create_check_constraint("ck_production_vswr_auto_verdict", OLD_AUTO)
        batch.drop_constraint("ck_production_vswr_verdict", type_="check")
        batch.create_check_constraint("ck_production_vswr_verdict", "verdict IN ('pass', 'fail')")
        batch.drop_constraint("fk_production_vswr_repeat_of", type_="foreignkey")
        batch.drop_column("repeat_set")
        batch.drop_column("repeat_of")
        batch.drop_column("uncertainty")
