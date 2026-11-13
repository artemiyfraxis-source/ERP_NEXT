"""Remember device presence separately from agent queue polling."""

from alembic import op
import sqlalchemy as sa

revision = "0177_agent_devices"
down_revision = "0176_vna_capture_jobs"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.add_column(sa.Column("requester_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_warehouse_scan_jobs_requester", "identity_users", ["requester_id"], ["id"], ondelete="SET NULL")
    # Old jobs have no provable requester. Close them before any agent can claim/upload them.
    op.execute(sa.text("UPDATE warehouse_scan_jobs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
                      "error = :reason WHERE requester_id IS NULL AND status IN ('pending', 'claimed')")
               .bindparams(reason="Прервано обновлением ERP — запустите сканирование ещё раз"))
    # A fresh heartbeat supplies the first inventory; do not infer devices from old queue touches.
    op.create_table("warehouse_agent_devices",
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("device_id", sa.String(300), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('scan', 's2vna', 'arinst')", name="ck_warehouse_agent_devices_kind"),
        sa.CheckConstraint("device_id <> ''", name="ck_warehouse_agent_devices_id"),
    )
    op.create_table("warehouse_vna_bindings",
        sa.Column("serial", sa.String(300), primary_key=True),
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id"), nullable=False),
        sa.CheckConstraint("trim(serial) <> ''", name="ck_warehouse_vna_bindings_serial"),
    )


def downgrade():
    op.drop_table("warehouse_vna_bindings")
    op.drop_table("warehouse_agent_devices")
    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.drop_constraint("fk_warehouse_scan_jobs_requester", type_="foreignkey")
        batch.drop_column("requester_id")
