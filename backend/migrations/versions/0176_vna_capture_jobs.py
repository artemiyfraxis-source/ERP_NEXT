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
    "ck_warehouse_scan_jobs_kind_payload": "(kind = 'scan' AND capture_content IS NULL AND vna_name = '') OR (kind = 'vna' AND file_id IS NULL AND result = '' AND target_machine_id IS NOT NULL AND target_machine_id <> '' AND target_scanner IS NULL)",
    "ck_warehouse_scan_jobs_capture_size": "capture_content IS NULL OR (status = 'done' AND length(capture_content) BETWEEN 1 AND 2097152)",
}


def upgrade():
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("kind", sa.String(8), nullable=False, server_default="scan"))
        batch.add_column(sa.Column("capture_content", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("vna_name", sa.String(500), nullable=False, server_default=""))
        batch.drop_constraint("ck_warehouse_scan_jobs_done_file", type_="check")
        for name, expression in CHECKS.items():
            batch.create_check_constraint(name, expression)
    op.create_table("warehouse_agent_vna",
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False, server_default=""),
    )


def downgrade():
    # Never silently discard captured evidence when rolling the schema back.
    if op.get_bind().execute(sa.text("SELECT 1 FROM warehouse_scan_jobs WHERE kind = 'vna' LIMIT 1")).first():
        raise RuntimeError("VNA jobs exist; preserve their evidence before downgrading")
    op.drop_table("warehouse_agent_vna")
    with op.batch_alter_table(TABLE) as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_column("vna_name")
        batch.drop_column("capture_content")
        batch.drop_column("kind")
        batch.create_check_constraint("ck_warehouse_scan_jobs_done_file", "status <> 'done' OR file_id IS NOT NULL")
