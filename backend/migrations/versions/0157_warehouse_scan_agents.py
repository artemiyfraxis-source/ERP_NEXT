"""Сканер с любого ПК (owner 28.10.2026): the registry of scan agents, the agent install tokens, a job's target.

- `warehouse_scan_agents` — one row per PC with the agent (`machine_id` = the Windows MachineGuid, PK): its name,
  agent version, the address the server saw it from (`ip_seen`, through Caddy's X-Forwarded-For) and the LAN address
  it reports (`ip_lan`), the WIA scanners it currently sees (`scanners`, JSON text, '[]' — none) and `last_seen`. An
  agent of the old version has no id: its claims keep one row `legacy:<PC name>` (`legacy` = true) so the screen knows
  an old agent is alive and queues a job for «any agent».
- `warehouse_scan_agent_installs` — one-time install tokens (only the SHA-256 is stored): an administrator issues one,
  the install script on the new PC redeems it once (`used_at` + `used_by_machine`) for a real API token. UNIQUE hash.
- `warehouse_scan_jobs.target_machine_id` — the agent a job was created for (FK → warehouse_scan_agents, RESTRICT);
  NULL — an untargeted legacy job any agent may claim.

Non-destructive: two new empty tables and one nullable column. DESTRUCTIVE = False.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0157_warehouse_scan_agents"
down_revision: str | None = "0156_invoice_procurement_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False


def upgrade() -> None:
    op.create_table(
        "warehouse_scan_agents",
        sa.Column("machine_id", sa.String(80), primary_key=True),
        sa.Column("pc_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default=""),
        sa.Column("ip_seen", sa.String(64), nullable=False, server_default=""),
        sa.Column("ip_lan", sa.String(64), nullable=False, server_default=""),
        sa.Column("scanners", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("legacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("machine_id <> ''", name="ck_warehouse_scan_agents_machine_id"),
    )
    op.create_table(
        "warehouse_scan_agent_installs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),  # identity_users.id: another module's table, no FK
        sa.Column("created_by_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_machine", sa.String(80), nullable=True),
        sa.CheckConstraint(
            "(used_at IS NULL AND used_by_machine IS NULL) OR (used_at IS NOT NULL AND used_by_machine IS NOT NULL)",
            name="ck_warehouse_scan_agent_installs_used_pair",
        ),
    )
    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.add_column(sa.Column("target_machine_id", sa.String(80), nullable=True))
        batch.create_foreign_key(
            "fk_warehouse_scan_jobs_target_machine", "warehouse_scan_agents", ["target_machine_id"], ["machine_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("warehouse_scan_jobs") as batch:
        batch.drop_constraint("fk_warehouse_scan_jobs_target_machine", type_="foreignkey")
        batch.drop_column("target_machine_id")
    op.drop_table("warehouse_scan_agent_installs")
    op.drop_table("warehouse_scan_agents")
