"""«Журнал проверок ВАЦ»: a session check may be «только калибровка» (owner, 26.11.2026).

The owner dropped the ACM confidence test and LOAD on both ports after the ACM calibration: the bench calibration is
valid once the ACM calibration, «Кор» and the device reading after it are accepted. Its journal row then holds no
confidence or LOAD numbers. `ck_vna_check_session` keeps the old form exactly and adds the second one: confidence and
both LOAD readings NULL and passed = TRUE. No data changes; not destructive.

Downgrade restores the old CHECK and refuses (loudly, before touching the table) while calibration-only rows exist —
they cannot be expressed in the old form and the journal is append-only.
"""

from alembic import op
import sqlalchemy as sa

revision = "0221_vna_calibration_only_check"
down_revision = "0220_rf_kit_characterization"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_vna_checks"
NAME = "ck_vna_check_session"
# Frozen copies: never import app code here.
OLD_FORM = ("(confidence_passed IS NOT NULL AND confidence_tx_dev_db IS NOT NULL AND "
            "(NOT confidence_passed OR (confidence_tx_dev_db <= 0.1 AND load_p1_vswr IS NOT NULL AND "
            "load_p2_vswr IS NOT NULL)) AND passed = (CASE WHEN confidence_passed AND load_p1_vswr <= 1.06 "
            "AND load_p2_vswr <= 1.06 THEN TRUE ELSE FALSE END))")
CALIBRATION_ONLY = ("(confidence_passed IS NULL AND confidence_tx_dev_db IS NULL AND confidence_rx_dev_db IS NULL AND "
                    "load_p1_vswr IS NULL AND load_p2_vswr IS NULL AND passed = TRUE)")
OLD_CHECK = f"kind <> 'session' OR {OLD_FORM}"
NEW_CHECK = f"kind <> 'session' OR {OLD_FORM} OR {CALIBRATION_ONLY}"


def _replace(check: str) -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(NAME, type_="check")
        batch.create_check_constraint(NAME, check)


def upgrade() -> None:
    _replace(NEW_CHECK)


def downgrade() -> None:
    count = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE kind = 'session' AND confidence_passed IS NULL")).scalar_one()
    if count:
        raise RuntimeError(f"{TABLE}: {count} строк(и) «только калибровка» (0221) — старое правило "
                           f"{NAME} их не допускает; журнал только дополняется, откат невозможен")
    _replace(OLD_CHECK)
