"""Durable VNA sessions, physical leases, command receipts and capture evidence."""

from alembic import op
import sqlalchemy as sa

revision = "0181_vna_sessions"
down_revision = "0180_equipment_catalogs_cutover"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

ACTIVE = "state NOT IN ('recorded','failed','aborted')"


def upgrade():
    op.create_table("warehouse_vna_control",
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("methods", sa.JSON(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_vna_control_version"),
    )
    op.create_table("production_vna_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), sa.ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("machine_id", sa.String(80), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("instrument_serial", sa.String(300), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("product_position", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("recorded_reference", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('draft','planned','preparing','awaiting_standard','acquiring_standard',"
                           "'saving_calibration','awaiting_dut','acquiring_dut','review','recorded','failed',"
                           "'aborting','aborted','recovery_required')", name="ck_vna_sessions_state"),
        sa.CheckConstraint("version >= 1 AND step >= 0 AND product_position >= 1", name="ck_vna_sessions_counters"),
        sa.CheckConstraint("trim(instrument_serial) <> ''", name="ck_vna_sessions_serial"),
        sa.CheckConstraint("trim(machine_id) <> ''", name="ck_vna_sessions_machine"),
    )
    for field, name in (("instrument_id", "instrument"), ("instrument_serial", "serial")):
        op.create_index(f"uq_vna_sessions_{name}_active", "production_vna_sessions", [field], unique=True,
                        sqlite_where=sa.text(ACTIVE), postgresql_where=sa.text(ACTIVE))
    op.create_table("production_vna_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("production_vna_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("expected_step", sa.Integer(), nullable=False),
        sa.Column("op", sa.String(32), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("op IN ('prepare','apply_profile','begin_calibration','measure_standard','acm_calibrate',"
                           "'save_calibration','capture','abort','restore')", name="ck_vna_commands_op"),
        sa.CheckConstraint("status IN ('pending','claimed','done','failed','cancelled','uncertain')", name="ck_vna_commands_status"),
        sa.CheckConstraint("expected_version >= 1 AND expected_step >= 0", name="ck_vna_commands_counters"),
        sa.CheckConstraint("status <> 'claimed' OR claimed_at IS NOT NULL", name="ck_vna_commands_claimed"),
        sa.UniqueConstraint("session_id", "expected_version", name="uq_vna_commands_version"),
    )
    op.create_index("uq_vna_commands_active", "production_vna_commands", ["session_id"], unique=True,
                    sqlite_where=sa.text("status IN ('pending','claimed')"),
                    postgresql_where=sa.text("status IN ('pending','claimed')"))
    op.create_table("production_vna_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("production_vna_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("command_id", sa.Uuid(), sa.ForeignKey("production_vna_commands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("file_name", sa.String(110), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 2097152", name="ck_vna_artifacts_size"),
        sa.UniqueConstraint("command_id", "file_name", name="uq_vna_artifacts_command_file"),
    )


def downgrade():
    bind = op.get_bind()
    if any(bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() for table in
           ("production_vna_artifacts", "production_vna_commands", "production_vna_sessions")):
        raise RuntimeError("Сессии ВАЦ содержат историю измерений; откат удалит доказательства")
    op.drop_table("production_vna_artifacts")
    op.drop_table("production_vna_commands")
    op.drop_table("production_vna_sessions")
    op.drop_table("warehouse_vna_control")
