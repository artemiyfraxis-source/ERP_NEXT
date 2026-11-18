"""«Станки» (owner 18.11.2026), slice 1: ОБ connected to ERP through a bridge on its PC and their latest snapshot.

Two new tables, nothing existing changes: `production_machine_links` (one bridge per ОБ, one ОБ per token, the PC bound
on the first report) and `production_machine_snapshots` (the latest allow-listed PLC words per ОБ)."""

from alembic import op
import sqlalchemy as sa

revision = "0192_machines"
down_revision = "0190_godex_marking"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    op.create_table(
        "production_machine_links",
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_production_machine_links_equipment"),
                  primary_key=True),
        sa.Column("inventory_number", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("token_id", sa.Uuid(), sa.ForeignKey("identity_api_tokens.id", ondelete="CASCADE",
                                                       name="fk_production_machine_links_token"),
                  nullable=False, unique=True),
        sa.Column("machine_id", sa.String(80), nullable=False),
        sa.Column("pc_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("ip_lan", sa.String(64), nullable=False, server_default=""),
        sa.Column("ip_seen", sa.String(64), nullable=False, server_default=""),
        sa.Column("bridge_version", sa.String(32), nullable=False, server_default=""),
        sa.Column("service_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('ob019_modbus')", name="ck_production_machine_links_kind"),
        sa.CheckConstraint("trim(inventory_number) <> ''", name="ck_production_machine_links_inventory"),
        sa.CheckConstraint("trim(machine_id) <> ''", name="ck_production_machine_links_machine"),
        sa.CheckConstraint("version >= 1", name="ck_production_machine_links_version"),
    )
    op.create_table(
        "production_machine_snapshots",
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("production_machine_links.equipment_id", ondelete="CASCADE",
                                                           name="fk_production_machine_snapshots_link"),
                  primary_key=True),
        sa.Column("link_state", sa.String(16), nullable=False),
        sa.Column("boot", sa.String(64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("proto", sa.Integer(), nullable=False),
        sa.Column("registers", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("proto = 1", name="ck_production_machine_snapshots_proto"),
        sa.CheckConstraint("link_state IN ('ok', 'port_busy', 'no_plc', 'no_service')",
                           name="ck_production_machine_snapshots_link"),
        sa.CheckConstraint("seq >= 0", name="ck_production_machine_snapshots_seq"),
        sa.CheckConstraint("trim(boot) <> ''", name="ck_production_machine_snapshots_boot"),
    )


def downgrade():
    op.drop_table("production_machine_snapshots")
    op.drop_table("production_machine_links")
