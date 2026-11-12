"""VNA capability and capture payload on the existing PC-agent queue."""

from alembic import op
import sqlalchemy as sa

revision = "0176_vna_capture_jobs"
down_revision = "0175_instrument_kinds_files"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "warehouse_scan_jobs"
CHECKS = {
    "ck_warehouse_scan_jobs_done_file": "status <> 'done' OR (kind = 'scan' AND file_id IS NOT NULL) OR (kind = 'vna' AND capture_content IS NOT NULL)",
    "ck_warehouse_scan_jobs_kind": "kind IN ('scan', 'vna')",
    "ck_warehouse_scan_jobs_source": "source IN ('s2vna', 'arinst')",
    "ck_warehouse_scan_jobs_kind_payload": "(kind = 'scan' AND capture_content IS NULL AND capture_settings IS NULL AND instrument_id IS NULL AND vna_name = '' AND capture_file_name = '' AND source = 's2vna') OR (kind = 'vna' AND file_id IS NULL AND result = '' AND target_machine_id IS NOT NULL AND target_machine_id <> '' AND target_scanner IS NULL)",
    "ck_warehouse_scan_jobs_capture_size": "capture_content IS NULL OR (status = 'done' AND length(capture_content) BETWEEN 1 AND 2097152)",
}


def upgrade():
    # No backfill from IP or token names: legacy credentials bind at their first heartbeat (TOFU).
    op.create_table("warehouse_agent_tokens",
        sa.Column("token_id", sa.Uuid(), sa.ForeignKey("identity_api_tokens.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("machine_id", sa.String(80), nullable=False),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("trim(machine_id) <> ''", name="ck_warehouse_agent_tokens_machine"),
    )
    op.create_index("uq_warehouse_agent_tokens_machine", "warehouse_agent_tokens", ["machine_id"], unique=True,
                    sqlite_where=sa.text("retired = 0"), postgresql_where=sa.text("retired = false"))
    op.create_table("warehouse_vna_preferences",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("identity_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), nullable=False),
    )
    with op.batch_alter_table("production_instruments") as batch:
        batch.add_column(sa.Column("vna_app", sa.String(16), nullable=False, server_default=""))
        batch.create_check_constraint("ck_production_instruments_vna_app", "vna_app IN ('', 'S2VNA', 'Arinst VL')")
        batch.create_check_constraint("ck_production_instruments_vna_measuring", "vna_app = '' OR kind = 'Средство измерений'")
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("kind", sa.String(8), nullable=False, server_default="scan"))
        batch.add_column(sa.Column("capture_content", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("vna_name", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("source", sa.String(8), nullable=False, server_default="s2vna"))
        batch.add_column(sa.Column("capture_file_name", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("instrument_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_warehouse_scan_jobs_instrument", "production_instruments", ["instrument_id"], ["id"], ondelete="RESTRICT")
        batch.add_column(sa.Column("capture_settings", sa.JSON(none_as_null=True), nullable=True))
        batch.drop_constraint("ck_warehouse_scan_jobs_done_file", type_="check")
        for name, expression in CHECKS.items():
            batch.create_check_constraint(name, expression)
    op.create_table("warehouse_agent_vna",
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False, server_default=""),
        sa.Column("sources", sa.JSON(), nullable=False, server_default='["s2vna"]'),
    )


def downgrade():
    # Never silently discard captured evidence when rolling the schema back.
    if op.get_bind().execute(sa.text("SELECT 1 FROM warehouse_scan_jobs WHERE kind = 'vna' LIMIT 1")).first():
        raise RuntimeError("VNA jobs exist; preserve their evidence before downgrading")
    op.drop_table("warehouse_agent_vna")
    op.drop_table("warehouse_vna_preferences")
    op.drop_table("warehouse_agent_tokens")
    with op.batch_alter_table(TABLE) as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_column("vna_name")
        batch.drop_column("source")
        batch.drop_column("capture_file_name")
        batch.drop_constraint("fk_warehouse_scan_jobs_instrument", type_="foreignkey")
        batch.drop_column("instrument_id")
        batch.drop_column("capture_settings")
        batch.drop_column("capture_content")
        batch.drop_column("kind")
        batch.create_check_constraint("ck_warehouse_scan_jobs_done_file", "status <> 'done' OR file_id IS NOT NULL")
    with op.batch_alter_table("production_instruments") as batch:
        batch.drop_constraint("ck_production_instruments_vna_app", type_="check")
        batch.drop_constraint("ck_production_instruments_vna_measuring", type_="check")
        batch.drop_column("vna_app")
