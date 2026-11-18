"""«Журнал проверок ВАЦ» (СТО ФРАК-002-2026, 11.4, 14, form В.1): an append-only table of VNA checks.

Each record — the session check after calibration (ACM confidence test + LOAD on both ports), the bend check, the
weekly control standard and the monthly 6650F27 kit check — is tied to its verdict by CHECK constraints. New table
only; nothing existing changes. Not destructive.
"""

from alembic import op
import sqlalchemy as sa

revision = "0195_vna_check_journal"
down_revision = "0194_si_norm_assignment"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copy of vna_check_infrastructure.VNA_CHECKS as of this revision: never import app code here.
VNA_CHECKS = {
    "ck_vna_check_kind": "kind IN ('session','flex','control_standard','kit_standards')",
    "ck_vna_check_source": "length(trim(source)) > 0",
    "ck_vna_check_hash": "length(payload_hash) = 64",
    "ck_vna_check_temperatures": "(vna_temperature_c IS NULL OR vna_temperature_c BETWEEN -40 AND 125) AND "
                                 "(acm_temperature_c IS NULL OR acm_temperature_c BETWEEN -40 AND 125)",
    "ck_vna_check_values": "(confidence_tx_dev_db IS NULL OR confidence_tx_dev_db >= 0) AND "
                           "(confidence_rx_dev_db IS NULL OR confidence_rx_dev_db >= 0) AND "
                           "(load_p1_vswr IS NULL OR load_p1_vswr >= 1) AND (load_p2_vswr IS NULL OR load_p2_vswr >= 1) AND "
                           "(vswr_dev IS NULL OR vswr_dev >= 0) AND (vswr_limit IS NULL OR vswr_limit > 0) AND "
                           "(s21_dev_db IS NULL OR s21_dev_db >= 0) AND (s21_limit_db IS NULL OR s21_limit_db > 0) AND "
                           "(kit_ratio IS NULL OR kit_ratio >= 0)",
    "ck_vna_check_standard": "standard_inventory IS NULL OR length(trim(standard_inventory)) > 0",
    "ck_vna_check_session_only": "kind = 'session' OR (confidence_passed IS NULL AND confidence_tx_dev_db IS NULL)",
    "ck_vna_check_loads": "kind IN ('session','flex') OR (load_p1_vswr IS NULL AND load_p2_vswr IS NULL)",
    "ck_vna_check_session": "kind <> 'session' OR (confidence_passed IS NOT NULL AND confidence_tx_dev_db IS NOT NULL AND "
                            "(NOT confidence_passed OR (confidence_tx_dev_db <= 0.1 AND load_p1_vswr IS NOT NULL AND "
                            "load_p2_vswr IS NOT NULL)) AND passed = (CASE WHEN confidence_passed AND load_p1_vswr <= 1.06 "
                            "AND load_p2_vswr <= 1.06 THEN TRUE ELSE FALSE END))",
    "ck_vna_check_flex": "kind <> 'flex' OR (s21_dev_db IS NOT NULL AND vswr_dev IS NOT NULL AND load_p1_vswr IS NOT NULL AND "
                         "load_p2_vswr IS NOT NULL AND passed = (CASE WHEN s21_dev_db <= 0.05 AND load_p1_vswr <= 1.06 AND "
                         "load_p2_vswr <= 1.06 THEN TRUE ELSE FALSE END))",
    "ck_vna_check_control": "kind <> 'control_standard' OR (standard_inventory IS NOT NULL AND vswr_dev IS NOT NULL AND "
                            "vswr_limit IS NOT NULL AND s21_dev_db IS NOT NULL AND s21_limit_db IS NOT NULL AND "
                            "passed = (CASE WHEN vswr_dev <= vswr_limit AND s21_dev_db <= s21_limit_db THEN TRUE ELSE FALSE END))",
    "ck_vna_check_kit": "kind <> 'kit_standards' OR (standard_inventory IS NOT NULL AND kit_ratio IS NOT NULL AND "
                        "passed = (CASE WHEN kit_ratio <= 1 THEN TRUE ELSE FALSE END))",
}


def upgrade() -> None:
    op.create_table(
        "production_vna_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(200), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("operator", sa.String(200), nullable=False),
        sa.Column("actor", sa.String(300), nullable=False),
        sa.Column("calibration_id", sa.String(64), nullable=False),
        sa.Column("acm_serial", sa.String(128), nullable=False),
        sa.Column("profile", sa.String(300), nullable=False),
        sa.Column("vna_temperature_c", sa.Float(), nullable=True),
        sa.Column("acm_temperature_c", sa.Float(), nullable=True),
        sa.Column("confidence_passed", sa.Boolean(), nullable=True),
        sa.Column("confidence_tx_dev_db", sa.Float(), nullable=True),
        sa.Column("confidence_rx_dev_db", sa.Float(), nullable=True),
        sa.Column("load_p1_vswr", sa.Float(), nullable=True),
        sa.Column("load_p2_vswr", sa.Float(), nullable=True),
        sa.Column("standard_inventory", sa.String(100), nullable=True),
        sa.Column("reference", sa.String(300), nullable=False),
        sa.Column("vswr_dev", sa.Float(), nullable=True),
        sa.Column("vswr_limit", sa.Float(), nullable=True),
        sa.Column("s21_dev_db", sa.Float(), nullable=True),
        sa.Column("s21_limit_db", sa.Float(), nullable=True),
        sa.Column("kit_ratio", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        *(sa.CheckConstraint(sql, name=name) for name, sql in VNA_CHECKS.items()),
    )
    op.create_index("ix_vna_check_instrument_kind", "production_vna_checks", ["instrument_id", "kind", "performed_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM production_vna_checks")).first():
        raise RuntimeError("Нельзя отменить миграцию: в журнале проверок ВАЦ есть записи")
    op.drop_index("ix_vna_check_instrument_kind", table_name="production_vna_checks")
    op.drop_table("production_vna_checks")
