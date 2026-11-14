"""Structured RF interfaces of existing registry items; no equipment is seeded."""

from alembic import op
import sqlalchemy as sa

revision = "0179_rf_interfaces"
down_revision = "0176_vna_capture_jobs"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    op.create_table("production_rf_details",
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("confirmed", sa.Boolean(create_constraint=True, name="ck_rf_details_bool"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("f_min_ghz", sa.Float(), nullable=True),
        sa.Column("max_points", sa.Integer(), nullable=True),
        sa.Column("kit_definition", sa.String(64), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_rf_details_version"),
        sa.CheckConstraint("role IN ('unknown','vna','adapter','test_cable','auto_calibration','solt_kit','load','load_kit')", name="ck_rf_details_role"),
        sa.CheckConstraint("f_min_ghz IS NULL OR (f_min_ghz >= 0 AND f_min_ghz <= 1000)", name="ck_rf_details_min"),
        sa.CheckConstraint("max_points IS NULL OR (role = 'vna' AND max_points BETWEEN 2 AND 10000001)", name="ck_rf_details_points"),
        sa.CheckConstraint("kit_definition = '' OR (role = 'solt_kit' AND kit_definition = '6650F27-full') OR (role = 'load_kit' AND kit_definition = 'SMA-MF06-loads')", name="ck_rf_details_kit"),
        sa.CheckConstraint("NOT confirmed OR (role <> 'unknown' AND length(trim(source)) > 0)", name="ck_rf_details_confirmed"),
    )
    op.create_table("production_rf_interfaces",
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_rf_details.instrument_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("series", sa.String(32), nullable=False),
        sa.Column("body", sa.String(16), nullable=False),
        sa.Column("center", sa.String(16), nullable=False),
        sa.Column("impedance_ohm", sa.Float(), nullable=True),
        sa.Column("f_max_ghz", sa.Float(), nullable=True),
        sa.Column("f_confirmed", sa.Boolean(create_constraint=True, name="ck_rf_interfaces_bool"), nullable=False),
        sa.Column("f_source", sa.Text(), nullable=False),
        sa.CheckConstraint("position IN (1, 2)", name="ck_rf_interfaces_position"),
        sa.CheckConstraint("series IN ('SMA','3.5mm','N','TNC','BNC','F','RP-SMA','тип III')", name="ck_rf_interfaces_series"),
        sa.CheckConstraint("body IN ('вилка','розетка')", name="ck_rf_interfaces_body"),
        sa.CheckConstraint("center IN ('штырь','гнездо')", name="ck_rf_interfaces_center"),
        sa.CheckConstraint("impedance_ohm IS NULL OR (impedance_ohm > 0 AND impedance_ohm <= 1000)", name="ck_rf_interfaces_impedance"),
        sa.CheckConstraint("f_max_ghz IS NULL OR (f_max_ghz > 0 AND f_max_ghz <= 1000)", name="ck_rf_interfaces_max"),
        sa.CheckConstraint("NOT f_confirmed OR (impedance_ohm IS NOT NULL AND f_max_ghz IS NOT NULL AND length(trim(f_source)) > 0)", name="ck_rf_interfaces_confirmed"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM production_rf_details LIMIT 1")).first():
        raise RuntimeError("Сохраните ВЧ-данные реестра перед откатом миграции")
    op.drop_table("production_rf_interfaces")
    op.drop_table("production_rf_details")
