"""Per-device VNA capabilities and explicit instrument/Arinst UID association.

Only the ephemeral capability cache is rebuilt. Jobs, sessions, artifacts and registry history
are untouched. Ambiguous or stale singleton capabilities require a fresh heartbeat.
"""

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0219_arinst_control"
down_revision = "0217_unify_instrument_catalog"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

CONTROL = "warehouse_vna_control"
IDENTITIES = "warehouse_vna_identities"


def _legacy_table():
    return op.create_table(CONTROL,
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("methods", sa.JSON(), nullable=False),
        sa.Column("ops", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_vna_control_version"))


def upgrade():
    bind = op.get_bind()
    old = sa.Table(CONTROL, sa.MetaData(), autoload_with=bind)
    devices = sa.Table("warehouse_agent_devices", sa.MetaData(), autoload_with=bind)
    bindings = sa.Table("warehouse_vna_bindings", sa.MetaData(), autoload_with=bind)
    agents = sa.Table("warehouse_scan_agents", sa.MetaData(), autoload_with=bind)
    # SQLite stores the ERP's Moscow wall time; PostgreSQL retains the offset.
    cutoff = datetime.now(timezone(timedelta(hours=3))) - timedelta(minutes=3)
    cached = []
    for item in bind.execute(sa.select(old).where(old.c.reported_at >= cutoff)).mappings():
        serials = list(bind.execute(sa.select(devices.c.device_id).join(bindings,
            (bindings.c.serial == devices.c.device_id) & (bindings.c.machine_id == devices.c.machine_id))
            .join(agents, agents.c.machine_id == devices.c.machine_id).where(
                devices.c.machine_id == item["machine_id"], devices.c.kind == "s2vna",
                devices.c.present.is_(True), devices.c.last_seen >= cutoff,
                agents.c.last_seen >= cutoff, agents.c.legacy.is_(False),
                ~sa.func.lower(devices.c.device_id).like("arinst:%"))).scalars())
        if len(serials) == 1:
            cached.append({**dict(item), "serial": serials[0], "source": "s2vna",
                           "identity_verified": True, "control_ready": True})
    op.drop_table(CONTROL)
    table = op.create_table(CONTROL,
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("serial", sa.String(300), primary_key=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("methods", sa.JSON(), nullable=False),
        sa.Column("ops", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(80), nullable=False, server_default=""),
        sa.Column("firmware", sa.String(80), nullable=False, server_default=""),
        sa.Column("hardware_uid", sa.String(100), nullable=False, server_default=""),
        sa.Column("identity_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("license_state", sa.String(12), nullable=False, server_default="unknown"),
        sa.Column("trial_remaining_seconds", sa.Integer(), nullable=True),
        sa.Column("control_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.CheckConstraint("version >= 1", name="ck_vna_control_version"),
        sa.CheckConstraint("source IN ('s2vna','arinst')", name="ck_vna_control_source"),
        sa.CheckConstraint("trim(serial) <> '' AND ((source = 'arinst' AND serial = 'arinst:' || hardware_uid AND hardware_uid <> '') OR (source = 's2vna' AND lower(serial) NOT LIKE 'arinst:%'))", name="ck_vna_control_serial"),
        sa.CheckConstraint("license_state IN ('unknown','not_required','TRIAL','LOCKED','ACTIVATED')", name="ck_vna_control_license"),
        sa.CheckConstraint("(license_state = 'TRIAL' AND trial_remaining_seconds IS NOT NULL AND trial_remaining_seconds BETWEEN 0 AND 600) OR (license_state <> 'TRIAL' AND trial_remaining_seconds IS NULL)", name="ck_vna_control_trial"),
        sa.CheckConstraint("source <> 'arinst' OR NOT control_ready OR (identity_verified AND version = 3 AND (license_state IN ('not_required','ACTIVATED') OR (license_state = 'TRIAL' AND trial_remaining_seconds > 0)))", name="ck_vna_control_ready"))
    if cached:
        bind.execute(table.insert(), cached)
    op.create_table(IDENTITIES,
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("serial", sa.String(300), nullable=False),
        sa.Column("hardware_uid", sa.String(100), nullable=False),
        sa.Column("firmware", sa.String(80), nullable=False),
        sa.Column("verified_machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("source", "hardware_uid", name="uq_vna_identity_uid"),
        sa.UniqueConstraint("serial", name="uq_vna_identity_serial"),
        sa.CheckConstraint("source = 'arinst' AND hardware_uid <> '' AND serial = 'arinst:' || hardware_uid", name="ck_vna_identity_serial"),
        sa.CheckConstraint("trim(firmware) <> ''", name="ck_vna_identity_firmware"))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {IDENTITIES} LIMIT 1")).first():
        raise RuntimeError("Arinst identity associations exist; refusing to erase verified instrument history")
    if bind.execute(sa.text(f"SELECT 1 FROM {CONTROL} WHERE source <> 's2vna' LIMIT 1")).first() or bind.execute(
            sa.text(f"SELECT machine_id FROM {CONTROL} GROUP BY machine_id HAVING COUNT(*) > 1 LIMIT 1")).first():
        raise RuntimeError("Multiple-device/Arinst capabilities exist; refusing to collapse the control inventory")
    table = sa.Table(CONTROL, sa.MetaData(), autoload_with=bind)
    old = [dict(item) for item in bind.execute(sa.select(
        table.c.machine_id, table.c.version, table.c.methods, table.c.ops, table.c.reported_at)).mappings()]
    op.drop_table(IDENTITIES)
    op.drop_table(CONTROL)
    table = _legacy_table()
    if old:
        bind.execute(table.insert(), old)
